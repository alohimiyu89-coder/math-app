from datetime import datetime
import io
import json
import os
import urllib.parse
import urllib.request
from google import genai
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
      description="欠如している知識やミスの原因の詳しい分析"
  )
  advice: str = Field(description="復習時に意識すべき具体的なアドバイス")


# スマホ写真を軽量JPEGに変換＆向き補正する関数（通信を高速化）
def prepare_image(uploaded_file):
  img = Image.open(uploaded_file)
  img = ImageOps.exif_transpose(img)
  if img.mode != "RGB":
    img = img.convert("RGB")
  img.thumbnail((1200, 1200))
  buf = io.BytesIO()
  img.save(buf, format="JPEG", quality=80)
  buf.seek(0)
  return Image.open(buf)


# Googleスプレッドシート（GAS）との通信関数
def fetch_from_cloud():
  if not gas_url:
    return None
  params = urllib.parse.urlencode({"key": app_password, "action": "load_all"})
  req = urllib.request.Request(f"{gas_url}?{params}", method="GET")
  with urllib.request.urlopen(req, timeout=20) as res:
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
  with urllib.request.urlopen(req, timeout=20) as res:
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
      with st.spinner("☁️ Googleスプレッドシートからデータを読み込み中..."):
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
          st.error(
              f"スプレッドシートの読み込みに失敗しました（合言葉が一致しているか確認してください）: {cloud_data}"
          )
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
  st.caption("☁️ Googleスプレッドシート自動同期：ON（データは永久保存されます）")
else:
  st.caption(
      "⚠️ ローカル保存モード（Secretsに GAS_URL を設定すると自動保存が有効になります）"
  )

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
        "⚠️ まだ参考書の目次が登録されていません！先に右端の **「📖 参考書の登録」** タブから目次の写真を読み取ってください。"
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
      st.error("先に「📖 参考書の登録」タブで参考書の目次を登録してください！")
    elif not uploaded_files:
      st.warning("間違えた問題の写真をアップロードしてください！")
    else:
      with st.spinner("AIが問題と数式を読み取って分析中..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [prepare_image(file) for file in uploaded_files]

          prompt = f"""
あなたは経験豊富な大学受験の数学講師です。
生徒が共通テストの過去問で間違えた問題の画像（および途中計算があればその画像）を見て、簡潔かつ的確に分析・指導してください。

【生徒が使っている参考書の項目一覧】
{saved_toc}

上記の参考書リストの中から、最も優先して復習すべき章・番号・テーマを具体的に選び、指定されたJSON形式で出力してください。
"""
          response = client.models.generate_content(
              model="gemini-3.8-flash",
              contents=[prompt] + images,
              config={
                  "response_mime_type": "application/json",
                  "response_schema": MathAnalysis,
              },
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
  st.subheader("📖 参考書の目次を写真から自動登録")
  st.write(
      "使っている参考書の目次ページを写真で撮ってアップロードすると、AIが項目一覧を自動で文字起こしし、スプレッドシートに永続保存します。"
  )

  toc_images = st.file_uploader(
      "目次の写真をアップロード（1枚ずつが最速です）",
      type=["jpg", "jpeg", "png"],
      accept_multiple_files=True,
      key="toc_uploader",
  )

  if toc_images:
    cols = st.columns(len(toc_images))
    for i, file in enumerate(toc_images):
      cols[i].image(file, caption=f"目次 {i+1}")

  if st.button("✨ 写真から目次を読み取って追加する", type="primary"):
    if not api_key:
      st.error("Gemini APIキーが設定されていません！")
    elif not toc_images:
      st.warning("目次の写真をアップロードしてください！")
    else:
      with st.spinner("AIが読み取り中（10〜20秒ほど・画面を閉じずにお待ちください）..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [prepare_image(file) for file in toc_images]

          toc_prompt = """
参考書の目次画像から「章・単元名」「問題番号」「テーマ名」を簡潔な箇条書きで文字起こししてください。挨拶や説明は不要でリストのみ出力してください。
"""
          response = client.models.generate_content(
              model="gemini-3.8-flash", contents=[toc_prompt] + images
          )

          current_text = st.session_state["toc_text"]
          if current_text.strip():
            updated_text = (
                current_text.strip() + "\n\n" + response.text.strip()
            )
          else:
            updated_text = response.text.strip()

          save_toc_all(updated_text)
          st.session_state["toc_text"] = updated_text
          st.success(
              "✅ 目次の読み取りとスプレッドシートへの保存が完了しました！"
          )

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
