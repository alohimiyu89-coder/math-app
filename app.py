from datetime import datetime
import io
import json
import os
import urllib.parse
import urllib.request
from google import genai
from google.genai import types
import pandas as pd
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(page_title="数学 弱点分析＆参考書ナビ", layout="centered")
st.title("📐 数学 弱点分析＆参考書ナビ")

# クラウドの金庫（Secrets）から設定を読み込む
secret_api_key = ""
app_password = ""
gas_url = ""
try:
  secret_api_key = st.secrets.get("GEMINI_API_KEY", "")
  app_password = st.secrets.get("APP_PASSWORD", "")
  gas_url = st.secrets.get("GAS_URL", "")
except Exception:
  pass

# 合言葉ロック
if app_password:
  if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
  if not st.session_state.authenticated:
    pwd = st.text_input("🔒 アプリの合言葉を入力してください", type="password")
    if st.button("ログイン"):
      if pwd == app_password:
        st.session_state.authenticated = True
        st.rerun()
      else:
        st.error("合言葉が違います")
    st.stop()

HISTORY_FILE = "review_history.csv"
TOC_FILE = "book_toc.txt"
COLUMNS = [
    "復習完了",
    "日付",
    "単元",
    "やるべき参考書",
    "問題テーマ",
    "弱点・原因",
    "アドバイス",
]


class MathAnalysis(BaseModel):
  unit: str = Field(
      description="数学の単元名（例：2次関数、図形と計量、場合の数と確率など）"
  )
  target_problems: str = Field(
      description="参考書リストから選んだ、今すぐやるべき章・問題番号（例：第3章 28〜32）"
  )
  theme: str = Field(description="問題で問われている具体的なテーマ")
  weakness: str = Field(
      description="欠如している知識やミスの原因の簡潔な分析"
  )
  advice: str = Field(description="復習時に意識すべき具体的なアドバイス")


# AIを「即答モード（思考オフ）」で高速実行する関数
def call_gemini_fast(client, contents, schema=None):
  base_kwargs = {}
  if schema:
    base_kwargs["response_mime_type"] = "application/json"
    base_kwargs["response_schema"] = schema

  try:
    fast_config = types.GenerateContentConfig(
        **base_kwargs,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    return client.models.generate_content(
        model="gemini-3.8-flash",
        contents=contents,
        config=fast_config,
    )
  except Exception as e:
    err_str = str(e).lower()
    if "thinking" in err_str or "budget" in err_str or "invalid" in err_str:
      normal_config = (
          types.GenerateContentConfig(**base_kwargs) if base_kwargs else None
      )
      return client.models.generate_content(
          model="gemini-3.8-flash",
          contents=contents,
          config=normal_config,
      )
    raise e


# スマホ写真を軽量JPEGに変換＆向き補正（アップロードを爆速化）
def prepare_image(uploaded_file):
  img = Image.open(uploaded_file)
  img = ImageOps.exif_transpose(img)
  if img.mode != "RGB":
    img = img.convert("RGB")
  img.thumbnail((1024, 1024))
  buf = io.BytesIO()
  img.save(buf, format="JPEG", quality=75)
  buf.seek(0)
  return Image.open(buf)


# Googleスプレッドシート（GAS）との通信関数
def fetch_from_cloud():
  if not gas_url:
    return None
  params = urllib.parse.urlencode({"key": app_password, "action": "load_all"})
  req = urllib.request.Request(f"{gas_url}?{params}", method="GET")
  with urllib.request.urlopen(req, timeout=15) as res:
    return json.loads(res.read().decode("utf-8"))


def post_to_cloud(payload):
  if not gas_url:
    return None
  payload["key"] = app_password
  data = json.dumps(payload).encode("utf-8")
  req = urllib.request.Request(
      gas_url,
      data=data,
      headers={"Content-Type": "text/plain; charset=utf-8"},
      method="POST",
  )
  with urllib.request.urlopen(req, timeout=15) as res:
    return json.loads(res.read().decode("utf-8"))


def load_local_history():
  if os.path.exists(HISTORY_FILE):
    df = pd.read_csv(HISTORY_FILE, dtype=str).fillna("")
    df["復習完了"] = df["復習完了"].map(lambda x: x == "True")
    return df
  return pd.DataFrame(columns=COLUMNS)


def load_local_toc():
  if os.path.exists(TOC_FILE):
    with open(TOC_FILE, "r", encoding="utf-8") as f:
      return f.read()
  return ""


def save_toc_all(text):
  with open(TOC_FILE, "w", encoding="utf-8") as f:
    f.write(text)
  if gas_url:
    res = post_to_cloud({"action": "save_toc", "toc": text})
    if not res or res.get("status") != "ok":
      raise RuntimeError(f"スプレッドシート保存エラー: {res}")


def save_history_all(df):
  df_to_save = df.copy()
  df_to_save["復習完了"] = df_to_save["復習完了"].astype(str)
  df_to_save.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
  if gas_url:
    records = df.to_dict(orient="records")
    res = post_to_cloud({"action": "save_history", "history": records})
    if not res or res.get("status") != "ok":
      raise RuntimeError(f"スプレッドシート保存エラー: {res}")


# 初回起動時にスプレッドシートからデータを読み込む
if "cloud_loaded" not in st.session_state:
  if gas_url:
    try:
      with st.spinner("☁️ スプレッドシートからデータを読み込み中..."):
        cloud_data = fetch_from_cloud()
        if cloud_data and cloud_data.get("status") == "ok":
          st.session_state["toc_text"] = cloud_data.get("toc", "")
          hist = cloud_data.get("history", [])
          if hist:
            df_cloud = pd.DataFrame(hist)
            for col in COLUMNS:
              if col not in df_cloud.columns:
                df_cloud[col] = False if col == "復習完了" else ""
            df_cloud = df_cloud[COLUMNS]
            df_cloud["復習完了"] = df_cloud["復習完了"].astype(bool)
            st.session_state["history_df"] = df_cloud
          else:
            st.session_state["history_df"] = pd.DataFrame(columns=COLUMNS)
          st.session_state["cloud_loaded"] = True
        else:
          st.error(f"スプレッドシート読み込み失敗: {cloud_data}")
          st.session_state["toc_text"] = load_local_toc()
          st.session_state["history_df"] = load_local_history()
          st.session_state["cloud_loaded"] = True
    except Exception as e:
      st.error(f"スプレッドシート接続エラー: {e}")
      st.session_state["toc_text"] = load_local_toc()
      st.session_state["history_df"] = load_local_history()
      st.session_state["cloud_loaded"] = True
  else:
    st.session_state["toc_text"] = load_local_toc()
    st.session_state["history_df"] = load_local_history()
    st.session_state["cloud_loaded"] = True

if secret_api_key:
  api_key = secret_api_key
else:
  with st.sidebar:
    st.header("⚙️ 設定")
    api_key = st.text_input("Gemini APIキーを入力", type="password")

if gas_url:
  st.caption("☁️ Googleスプレッドシート自動同期：ON（高速モード）")
else:
  st.caption("⚠️ ローカル保存モード")

tab1, tab2, tab3 = st.tabs(
    ["🔍 弱点分析する", "📋 復習リスト・履歴", "📖 参考書の登録"]
)

# ==========================================
# タブ1：問題の分析画面
# ==========================================
with tab1:
  saved_toc = st.session_state["toc_text"]

  if not saved_toc.strip():
    st.warning(
        "⚠️ まだ参考書の目次が登録されていません！先に右端の **「📖 参考書の登録」** タブから参考書を登録してください。"
    )
  else:
    st.success(f"✅ 参考書リスト登録済み（現在 {len(saved_toc)} 文字）")
    with st.expander("📖 現在登録されている参考書リストを確認する"):
      st.text(saved_toc)

  st.subheader("間違えた問題の写真")
  uploaded_files = st.file_uploader(
      "問題や途中計算の写真をアップロード（複数枚OK）",
      type=["jpg", "jpeg", "png"],
      accept_multiple_files=True,
      key="problem_uploader",
  )

  if uploaded_files:
    cols = st.columns(len(uploaded_files))
    for i, file in enumerate(uploaded_files):
      cols[i].image(file, caption=f"画像 {i+1}")

  if st.button("🔍 分析して復習リストに追加する", type="primary"):
    if not api_key:
      st.error("Gemini APIキーが設定されていません！")
    elif not saved_toc.strip():
      st.error("先に「📖 参考書の登録」タブで参考書を登録してください！")
    elif not uploaded_files:
      st.warning("間違えた問題の写真をアップロードしてください！")
    else:
      with st.spinner("⚡ AIが高速分析中（5〜10秒）..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [prepare_image(file) for file in uploaded_files]

          prompt = f"""
あなたは大学受験の数学講師です。間違えた問題の画像を見て、以下の参考書リストから最も優先して復習すべき章・番号・テーマを選び、簡潔に分析してください。

【生徒の参考書リスト】
{saved_toc}
"""
          response = call_gemini_fast(
              client, [prompt] + images, schema=MathAnalysis
          )
          result = json.loads(response.text)

          df = st.session_state["history_df"]
          new_row = pd.DataFrame([{
              "復習完了": False,
              "日付": datetime.now().strftime("%Y/%m/%d %H:%M"),
              "単元": result["unit"],
              "やるべき参考書": result["target_problems"],
              "問題テーマ": result["theme"],
              "弱点・原因": result["weakness"],
              "アドバイス": result["advice"],
          }])
          df = pd.concat([new_row, df], ignore_index=True)
          save_history_all(df)
          st.session_state["history_df"] = df

          st.success(
              "✅ 分析が完了し、Googleスプレッドシートに自動保存しました！"
          )
          st.info(
              f"📌 **単元：** {result['unit']} ｜ 📖 **やるべき参考書：**"
              f" {result['target_problems']}"
          )
          st.markdown(f"### 1. 問われているテーマ\n{result['theme']}")
          st.markdown(f"### 2. 欠如している知識・つまずきポイント\n{result['weakness']}")
          st.markdown(f"### 3. 復習のアドバイス\n{result['advice']}")

        except Exception as e:
          st.error(f"エラーが発生しました: {e}")

# ==========================================
# タブ2：復習リスト・履歴画面
# ==========================================
with tab2:
  st.subheader("📋 あなたの復習リスト＆間違い履歴")
  df = st.session_state["history_df"]

  if df.empty:
    st.info(
        "まだ履歴がありません。「🔍 弱点分析する」タブから問題を分析すると、ここに自動で溜まっていきます！"
    )
  else:
    st.markdown("**📊 単元別のつまずき回数**")
    unit_counts = df["単元"].value_counts()
    cols = st.columns(min(len(unit_counts), 4))
    for i, (unit_name, count) in enumerate(unit_counts.items()):
      cols[i % 4].metric(label=unit_name, value=f"{count} 回")

    st.divider()

    only_unreviewed = st.checkbox("未復習の問題だけを表示する", value=False)
    display_df = df[~df["復習完了"]] if only_unreviewed else df

    st.caption(
        "※「復習完了」のチェックボックスを押したあと、下の「チェック状態を保存する」ボタンを押してください。"
    )
    edited_df = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "復習完了": st.column_config.CheckboxColumn("復習完了", default=False),
            "やるべき参考書": st.column_config.TextColumn(
                "やるべき参考書", width="medium"
            ),
            "弱点・原因": st.column_config.TextColumn("弱点・原因", width="large"),
        },
        disabled=[
            "日付",
            "単元",
            "やるべき参考書",
            "問題テーマ",
            "弱点・原因",
            "アドバイス",
        ],
    )

    if st.button("💾 チェック状態を保存する"):
      try:
        df.update(edited_df)
        df["復習完了"] = df["復習完了"].astype(bool)
        save_history_all(df)
        st.session_state["history_df"] = df
        st.success("✅ 復習状況をスプレッドシートに保存しました！")
      except Exception as e:
        st.error(f"保存エラー: {e}")

# ==========================================
# タブ3：参考書の目次登録画面
# ==========================================
with tab3:
  st.subheader("⚡ 方法①：参考書名から目次を自動作成（写真不要・5秒）")
  st.write(
      "チャート式・基礎問題精講・Focus Goldなどの定番参考書は、名前を入れるだけでAIが目次リストを自動作成します。"
  )
  book_name = st.text_input(
      "参考書名を入力（例：青チャート 数学I+A、基礎問題精講 数学II+B）"
  )
  if st.button("⚡ 書名から目次リストを自動作成して保存", type="primary"):
    if not api_key:
      st.error("Gemini APIキーが設定されていません！")
    elif not book_name.strip():
      st.warning("参考書名を入力してください！")
    else:
      with st.spinner("⚡ 参考書の目次リストを自動生成中（5秒ほど）..."):
        try:
          client = genai.Client(api_key=api_key)
          auto_prompt = f"""
参考書「{book_name}」の章・単元名と、代表的な例題番号・テーマ一覧を簡潔な箇条書きで作成してください。余計な挨拶文は不要で、リストのみ出力してください。
"""
          response = call_gemini_fast(client, [auto_prompt])
          current_text = st.session_state["toc_text"]
          updated_text = (
              (current_text.strip() + "\n\n" + response.text.strip())
              if current_text.strip()
              else response.text.strip()
          )
          save_toc_all(updated_text)
          st.session_state["toc_text"] = updated_text
          st.success(f"✅ 「{book_name}」の目次を作成して保存しました！")
        except Exception as e:
          st.error(f"エラーが発生しました: {e}")

  st.divider()
  st.subheader("📸 方法②：目次の写真から読み取る（1枚ずつ）")
  toc_images = st.file_uploader(
      "目次の写真をアップロード",
      type=["jpg", "jpeg", "png"],
      accept_multiple_files=True,
      key="toc_uploader",
  )

  if toc_images:
    cols = st.columns(len(toc_images))
    for i, file in enumerate(toc_images):
      cols[i].image(file, caption=f"目次 {i+1}")

  if st.button("✨ 写真から目次を読み取って追加する"):
    if not api_key:
      st.error("Gemini APIキーが設定されていません！")
    elif not toc_images:
      st.warning("目次の写真をアップロードしてください！")
    else:
      with st.spinner("⚡ 高速読み取り中（5〜10秒）..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [prepare_image(file) for file in toc_images]

          toc_prompt = """
画像の目次から「章・単元名」「問題番号」「テーマ名」だけを簡潔な箇条書きで抜き出してください。挨拶は不要です。
"""
          response = call_gemini_fast(client, [toc_prompt] + images)

          current_text = st.session_state["toc_text"]
          updated_text = (
              (current_text.strip() + "\n\n" + response.text.strip())
              if current_text.strip()
              else response.text.strip()
          )

          save_toc_all(updated_text)
          st.session_state["toc_text"] = updated_text
          st.success("✅ 目次の読み取りと保存が完了しました！")

        except Exception as e:
          st.error(f"エラーが発生しました: {e}")

  st.divider()
  st.subheader("📝 登録済みの参考書リスト（手動での修正・削除もOK）")
  st.text_area(
      "ここに保存された内容を使ってAIが分析します",
      key="toc_text",
      height=300,
  )
  if st.button("💾 手動編集した内容を保存する"):
    try:
      save_toc_all(st.session_state["toc_text"])
      st.success("✅ 参考書リストをスプレッドシートに保存しました！")
    except Exception as e:
      st.error(f"保存エラー: {e}")
