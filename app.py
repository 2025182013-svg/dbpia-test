import streamlit as st
import requests, html, json, os, re
from datetime import datetime
from openai import OpenAI
import pandas as pd

# =====================
# 기본 설정
# =====================
st.set_page_config(page_title="RefNote AI", layout="wide")
st.title("📚 RefNote AI")
st.caption("연구 자동화 리서치 시스템 · APA7 · 날짜별 히스토리 · 주제별 저장")

# =====================
# 세션 상태
# =====================
if "results" not in st.session_state:
    st.session_state.results = None

# =====================
# API
# =====================
st.sidebar.header("🔑 API 설정")
openai_key = st.sidebar.text_input("OpenAI API Key", type="password")
naver_id = st.sidebar.text_input("Naver Client ID", type="password")
naver_secret = st.sidebar.text_input("Naver Client Secret", type="password")

if not openai_key or not naver_id or not naver_secret:
    st.warning("⬅️ 사이드바에 모든 API 키를 입력하세요.")
    st.stop()

client = OpenAI(api_key=openai_key)

# =====================
# 모드 선택
# =====================
st.sidebar.header("⚙️ 리서치 모드")
mode = st.sidebar.radio(
    "모드 선택",
    ["📰 뉴스용 모드", "🏛️ 정책자료용 모드", "📚 연구논문용 모드"]
)

MODE_CONFIG = {
    "📰 뉴스용 모드": {"limit": 80, "threshold": 0},
    "🏛️ 정책자료용 모드": {"limit": 60, "threshold": 1},
    "📚 연구논문용 모드": {"limit": 40, "threshold": 2},
}

# =====================
# 유틸
# =====================
def clean(t):
    return html.unescape(t).replace("<b>", "").replace("</b>", "").strip()

def parse_date(d):
    try:
        return datetime.strptime(d, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def format_source(domain):
    return domain.replace("www.", "").split(".")[0].capitalize()

def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text)
    text = text.strip().replace(" ", "_")
    return text

def pretty(text):
    return text.replace("_", " ")

# =====================
# APA7 뉴스
# =====================
def apa_news(row):
    author = row.get("출처", "News")
    year = row["발행일"][:4] if row["발행일"] else "n.d."
    return f"{author}. ({year}). {row['제목']}. {row['출처']}. {row['링크']}"

# =====================
# AI
# =====================
def gen_questions(topic):
    prompt = f"다음 주제에 대한 연구 질문 3개 생성:\n{topic}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.3
    )
    return [q.strip("-• ").strip() for q in r.choices[0].message.content.split("\n") if q.strip()]

def gen_keywords(topic):
    prompt = f"다음 주제의 핵심 키워드 6개를 중요도순 쉼표 출력:\n{topic}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )
    return [k.strip() for k in r.choices[0].message.content.split(",")]

def gen_trend_summary(keywords):
    prompt = f"키워드 기반 연구 동향 요약:\n{', '.join(keywords)}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )
    return r.choices[0].message.content.strip()

def relevance(topic, n):
    prompt = f"""
연구 주제: {topic}
뉴스 제목: {n['제목']}
요약: {n['요약']}
관련도 0~3 숫자만 출력
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )
    try:
        return int(r.choices[0].message.content.strip())
    except:
        return 0

# =====================
# 뉴스 검색
# =====================
def search_news(q):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": naver_id,
        "X-Naver-Client-Secret": naver_secret
    }
    params = {"query": q, "display": 40, "sort": "date"}
    r = requests.get(url, headers=headers, params=params).json()

    out = []
    for i in r.get("items", []):
        out.append({
            "제목": clean(i["title"]),
            "요약": clean(i["description"]),
            "출처": format_source(i["link"].split("/")[2]),
            "발행일": parse_date(i["pubDate"]).strftime("%Y-%m-%d") if parse_date(i["pubDate"]) else "",
            "링크": i["link"]
        })
    return out

# =====================
# DBpia (예정)
# =====================
def search_dbpia(keyword):
    return pd.DataFrame(columns=["제목","저자","학술지","연도","링크"])

# =====================
# 실행
# =====================
topic = st.text_input("연구 주제 입력")

if st.button("🔍 리서치 시작") and topic:
    with st.spinner("리서치 진행 중..."):
        questions = gen_questions(topic)
        keywords = gen_keywords(topic)
        trend = gen_trend_summary(keywords)

        news_list = []
        for k in keywords[:3]:
            news_list.extend(search_news(k))

        cfg = MODE_CONFIG[mode]
        news_list = news_list[:cfg["limit"]]

        filtered = []
        for n in news_list:
            n["score"] = relevance(topic, n)
            if n["score"] >= cfg["threshold"]:
                filtered.append(n)

        # 🔥 최소 10개 보장
        if len(filtered) < 10:
            news_list_sorted = sorted(news_list, key=lambda x: x.get("score", 0), reverse=True)
            filtered = news_list_sorted[:10]

        news_df = pd.DataFrame(filtered).drop_duplicates(subset=["링크"])
        paper_df = search_dbpia(topic)

        st.session_state.results = {
            "topic": topic,
            "questions": questions,
            "keywords": keywords,
            "trend": trend,
            "news": news_df,
            "papers": paper_df
        }

        # ===== 히스토리 저장 =====
        today = datetime.now().strftime("%Y-%m-%d")
        base = "history"
        os.makedirs(f"{base}/{today}", exist_ok=True)

        filename = slugify(topic) + ".json"
        path = f"{base}/{today}/{filename}"

        save_data = {
            "topic": topic,
            "questions": questions,
            "keywords": keywords,
            "trend": trend,
            "news": news_df.to_dict(orient="records"),
            "papers": paper_df.to_dict(orient="records")
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

# =====================
# 출력
# =====================
if st.session_state.results:
    r = st.session_state.results

    st.subheader("🔍 연구 질문")
    for q in r["questions"]:
        st.markdown(f"• {q}")

    st.subheader("🔑 핵심 키워드")
    st.write(", ".join(r["keywords"]))

    st.subheader("📈 연구 동향")
    st.markdown(r["trend"])

    tab_news, tab_paper = st.tabs(["📰 뉴스", "📄 논문 (DBpia 예정)"])

    with tab_news:
        sort = st.radio("정렬 기준", ["관련도순", "최신순"], horizontal=True)
        df = r["news"].copy()

        if not df.empty:
            if sort == "관련도순":
                df = df.sort_values(by="score", ascending=False)
            else:
                df = df.sort_values(by="발행일", ascending=False)

        st.dataframe(df, use_container_width=True)

        csv_news = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 뉴스 CSV 다운로드", csv_news, f"{slugify(r['topic'])}_news.csv")

        st.subheader("📎 APA 참고문헌 (Top10)")
        for _, row in df.head(10).iterrows():
            st.markdown(f"- {apa_news(row)}")

    with tab_paper:
        st.info("DBpia 연동 예정 영역입니다.")
        st.dataframe(r["papers"], use_container_width=True)

# =====================
# 히스토리 (에러 방지 구조)
# =====================
st.sidebar.header("📂 날짜별 리서치 히스토리")

if os.path.exists("history"):
    dates = sorted(os.listdir("history"), reverse=True)
    for d in dates:
        with st.sidebar.expander(f"📅 {d}"):
            files = os.listdir(f"history/{d}")
            for f in files:
                label = pretty(f.replace(".json",""))
                if st.button(label, key=f"{d}_{f}"):

                    file_path = f"history/{d}/{f}"

                    try:
                        with open(file_path, "r", encoding="utf-8") as jf:
                            data = json.load(jf)

                        data["news"] = pd.DataFrame(data.get("news", []))
                        data["papers"] = pd.DataFrame(data.get("papers", []))
                        st.session_state.results = data

                    except json.JSONDecodeError:
                        st.sidebar.warning(f"⚠️ 손상된 파일 스킵됨: {pretty(f.replace('.json',''))}")
                    except Exception:
                        st.sidebar.warning(f"⚠️ 파일 로딩 실패: {pretty(f.replace('.json',''))}")
