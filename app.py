from datetime import datetime
import json
import os
from google import genai
import pandas as pd
from PIL import Image
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(page_title="数学 弱点分析＆参考書ナビ", layout="centered")
st.title("📐 数学 弱点分析＆参考書ナビ")

# クラウドの金庫（Secrets）からAPIキーと合言葉を読み込む
secret_api_key = ""
app_password = ""
try:
  secret_api_key = st.secrets.get("GEMINI_API_KEY", "")
  app_password = st.secrets.get("APP_PASSWORD", "")
except Exception:
  pass

# 合言葉ロック（APP_PASSWORDが設定されている場合のみ有効）
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


def load_history():
  if os.path.exists(HISTORY_FILE):
    df = pd.read_csv(HISTORY_FILE, dtype=str).fillna("")
    df["復習完了"] = df["復習完了"].map(lambda x: x == "True")
    return df
  return pd.DataFrame(
      columns=[
          "復習完了",
          "日付",
          "単元",
          "やるべき参考書",
          "問題テーマ",
          "弱点・原因",
          "アドバイス",
      ]
  )


def save_history(df):
  df_to_save = df.copy()
  df_to_save["復習完了"] = df_to_save["復習完了"].astype(str)
  df_to_save.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")


def load_toc():
  if os.path.exists(TOC_FILE):
    with open(TOC_FILE, "r", encoding="utf-8") as f:
      return f.read()
  return ""


def save_toc(text):
  with open(TOC_FILE, "w", encoding="utf-8") as f:
    f.write(text)


# APIキーの設定（金庫にあれば自動セット、なければ手入力）
if secret_api_key:
  api_key = secret_api_key
else:
  with st.sidebar:
    st.header("⚙️ 設定")
    api_key = st.text_input("Gemini APIキーを入力", type="password")

tab1, tab2, tab3 = st.tabs(
    ["🔍 弱点分析する", "📋 復習リスト・履歴", "📖 参考書の登録"]
)

# ==========================================
# タブ1：問題の分析画面
# ==========================================
with tab1:
  saved_toc = load_toc()

  if not saved_toc.strip():
    st.warning(
        "⚠️ まだ参考書の目次が登録されていません！先に右端の **「📖 参考書の登録」** タブから目次の写真を読み取ってください。"
    )
  else:
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
      st.error("左側のメニューから Gemini APIキー を入力してください！")
    elif not saved_toc.strip():
      st.error("先に「📖 参考書の登録」タブで参考書の目次を登録してください！")
    elif not uploaded_files:
      st.warning("間違えた問題の写真をアップロードしてください！")
    else:
      with st.spinner("AIが問題と数式を読み取って分析中..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [Image.open(file) for file in uploaded_files]

          prompt = f"""
あなたは経験豊富な大学受験の数学講師です。
生徒が共通テストの過去問で間違えた問題の画像（および途中計算があればその画像）を見て、詳しく分析・指導してください。

【生徒が使っている参考書の項目一覧】
{saved_toc}

上記の参考書リストの中から、最も優先して復習すべき章・番号・テーマを具体的に選び、指定されたJSON形式で出力してください。
"""
          response = client.models.generate_content(
              model="gemini-2.5-flash",
              contents=[prompt] + images,
              config={
                  "response_mime_type": "application/json",
                  "response_schema": MathAnalysis,
              },
          )

          result = json.loads(response.text)

          st.success("分析が完了し、「📋 復習リスト・履歴」に自動保存しました！")
          st.info(
              f"📌 **単元：** {result['unit']} ｜ 📖 **やるべき参考書：**"
              f" {result['target_problems']}"
          )
          st.markdown(f"### 1. 問われているテーマ\n{result['theme']}")
          st.markdown(f"### 2. 欠如している知識・つまずきポイント\n{result['weakness']}")
          st.markdown(f"### 3. 復習のアドバイス\n{result['advice']}")

          df = load_history()
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
          save_history(df)

        except Exception as e:
          st.error(f"エラーが発生しました: {e}")

# ==========================================
# タブ2：復習リスト・履歴画面
# ==========================================
with tab2:
  st.subheader("📋 あなたの復習リスト＆間違い履歴")
  df = load_history()

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
      df.update(edited_df)
      save_history(df)
      st.success("復習状況を保存しました！")
      st.rerun()

# ==========================================
# タブ3：参考書の目次登録画面
# ==========================================
with tab3:
  st.subheader("📖 参考書の目次を写真から自動登録")
  st.write(
      "使っている参考書の目次ページを写真で撮ってアップロードすると、AIが項目一覧を自動で文字起こしします。"
  )

  toc_images = st.file_uploader(
      "目次の写真をアップロード（複数ページまとめてOK）",
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
      st.error("左側のメニューから Gemini APIキー を入力してください！")
    elif not toc_images:
      st.warning("目次の写真をアップロードしてください！")
    else:
      with st.spinner("AIが目次の文字と問題番号を読み取り中..."):
        try:
          client = genai.Client(api_key=api_key)
          images = [Image.open(file) for file in toc_images]

          toc_prompt = """
あなたは参考書の目次データを整理するアシスタントです。
アップロードされた参考書の目次ページの画像から、「参考書名（分かれば）」「章・単元名」「問題番号（例題番号やページ番号）」「各問題のテーマ・タイトル」を漏れなく正確に読み取り、見やすいテキスト一覧にまとめてください。
複数枚の画像がある場合は、順番通りに統合してください。余計な挨拶文は不要で、整理した目次リストのみを出力してください。
"""
          response = client.models.generate_content(
              model="gemini-2.5-flash", contents=[toc_prompt] + images
          )

          current_text = load_toc()
          if current_text.strip():
            updated_text = (
                current_text.strip() + "\n\n" + response.text.strip()
            )
          else:
            updated_text = response.text.strip()

          save_toc(updated_text)
          st.success("目次の読み取りと保存が完了しました！")
          st.rerun()

        except Exception as e:
          st.error(f"エラーが発生しました: {e}")

  st.divider()
  st.subheader("📝 登録済みの参考書リスト（手動での修正・削除もOK）")
  edited_toc = st.text_area(
      "ここに保存された内容を使ってAIが分析します",
      value=load_toc(),
      height=300,
  )
  if st.button("💾 手動編集した内容を保存する"):
    save_toc(edited_toc)
    st.success("参考書リストを保存しました！")