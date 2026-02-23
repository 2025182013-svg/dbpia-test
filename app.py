import streamlit as st
import requests, html, json, os, re
from datetime import datetime
from openai import OpenAI
import pandas as pd
import xml.etree.ElementTree as ET

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

# ✅ DBpia (추가)
st.sidebar.markdown("---")
st.sidebar.subheader("📄 DBpia 설정 (선택)")
dbpia_key = st.sidebar.text_input("DBpia OpenAPI Key", type="password")
st.sidebar.caption(
    "DBpia OpenAPI에서 **검색 API 키**를 발급받아 입력하세요. "
    "원문 제공은 별도 계약이 필요할 수 있어요."
)

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
def clean(t: str) -> str:
    return html.unescape(t).replace("<b>", "").replace("</b>", "").strip()

def parse_date(d: str):
    try:
        return datetime.strptime(d, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def format_source(domain: str) -> str:
    return domain.replace("www.", "").split(".")[0].capitalize()

def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text)
    text = text.strip().replace(" ", "_")
    return text

def pretty(text: str) -> str:
    return text.replace("_", " ")

def safe_get_text(elem, default=""):
    if elem is None:
        return default
    txt = (elem.text or "").strip()
    return txt if txt else default

# =====================
# APA7 뉴스
# =====================
def apa_news(row):
    author = row.get("출처", "News")
    year = row["발행일"][:4] if row.get("발행일") else "n.d."
    return f"{author}. ({year}). {row.get('제목','')}. {row.get('출처','')}. {row.get('링크','')}"

# =====================
# AI
# =====================
def gen_questions(topic):
    prompt = f"다음 주제에 대한 연구 질문 3개 생성:\n{topic}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return [q.strip("-• ").strip() for q in r.choices[0].message.content.split("\n") if q.strip()]

def gen_keywords(topic):
    prompt = f"다음 주제의 핵심 키워드 6개를 중요도순 쉼표 출력:\n{topic}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return [k.strip() for k in r.choices[0].message.content.split(",") if k.strip()]

def gen_trend_summary(keywords):
    prompt = f"키워드 기반 연구 동향 요약:\n{', '.join(keywords)}"
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
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
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    try:
        return int(r.choices[0].message.content.strip())
    except:
        return 0

# =====================
# 뉴스 검색 (Naver)
# =====================
def search_news(q):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": naver_id,
        "X-Naver-Client-Secret": naver_secret
    }
    params = {"query": q, "display": 40, "sort": "date"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        data = r.json()
    except Exception as e:
        st.warning(f"네이버 뉴스 API 호출 실패: {e}")
        return []

    out = []
    for i in data.get("items", []):
        link = i.get("link", "")
        domain = ""
        try:
            domain = link.split("/")[2] if link else ""
        except:
            domain = ""
        pd_dt = parse_date(i.get("pubDate", ""))
        out.append({
            "제목": clean(i.get("title", "")),
            "요약": clean(i.get("description", "")),
            "출처": format_source(domain) if domain else "News",
            "발행일": pd_dt.strftime("%Y-%m-%d") if pd_dt else "",
            "링크": link
        })
    return out

# =====================
# DBpia 검색 (연동)
# - 공식 검색 API: http://api.dbpia.co.kr/v2/search/search.xml
# - 검색 파라미터: key, target=se 또는 se_adv, searchall 등
# =====================
def dbpia_request(params: dict) -> tuple[bool, str]:
    """
    return: (ok, xml_text_or_error_message)
    """
    url = "http://api.dbpia.co.kr/v2/search/search.xml"
    try:
        r = requests.get(url, params=params, timeout=20)
        r.encoding = "utf-8"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        return True, r.text
    except Exception as e:
        return False, str(e)

def parse_dbpia_xml(xml_text: str) -> pd.DataFrame:
    """
    DBpia 검색 API 응답(XML)을 DataFrame으로 변환
    컬럼: 제목, 저자, 학술지, 연도, 발행일, 페이지, 링크, DBpiaID
    """
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return pd.DataFrame(columns=["제목", "저자", "학술지", "연도", "발행일", "페이지", "링크", "DBpiaID"])

    # 에러 체크(문서에 따라 error/code/message 구조가 다를 수 있어 방어적으로)
    err = root.find(".//error")
    if err is not None:
        code = safe_get_text(err.find("code"), "")
        msg = safe_get_text(err.find("message"), "DBpia API error")
        return pd.DataFrame([{
            "제목": "",
            "저자": "",
            "학술지": "",
            "연도": "",
            "발행일": "",
            "페이지": "",
            "링크": "",
            "DBpiaID": f"{code}: {msg}"
        }])

    rows = []
    for item in root.findall(".//item"):
        title = safe_get_text(item.find("title"), "")

        # authors: 여러 author child가 있을 수 있음
        author_names = []
        authors = item.find("authors")
        if authors is not None:
            for a in authors.findall("author"):
                name = a.get("name") or safe_get_text(a.find("name"), "")
                if name:
                    author_names.append(name)
        author_str = ", ".join(author_names) if author_names else ""

        # publication (학술지/간행물)
        pub_name = ""
        publication = item.find("publication")
        if publication is not None:
            pub_name = publication.get("name") or safe_get_text(publication.find("name"), "")

        # issue: 발행연월 등이 있을 수 있음 (yymm 등)
        year = ""
        pubdate = ""
        issue = item.find("issue")
        if issue is not None:
            yymm = issue.get("yymm") or safe_get_text(issue.find("yymm"), "")
            # yymm이 202401 같은 형식이면 연도만 추출
            if yymm and len(yymm) >= 4 and yymm[:4].isdigit():
                year = yymm[:4]
                if len(yymm) >= 6 and yymm[4:6].isdigit():
                    pubdate = f"{yymm[:4]}-{yymm[4:6]}"
            # 발행일/발행년이 별도 있을 수도 있으니 보강
            y = issue.get("year") or safe_get_text(issue.find("year"), "")
            if (not year) and y and y.isdigit():
                year = y

        pages = safe_get_text(item.find("pages"), "")

        link_url = safe_get_text(item.find("link_url"), "")
        link_api = safe_get_text(item.find("link_api"), "")
        # 가능하면 사람에게 유용한 상세 링크를 우선
        link = link_url or link_api

        # DBpia ID는 link_api에 포함될 때가 많아서 최대한 추출
        dbpia_id = ""
        if link_api:
            m = re.search(r"[?&]id=([^&]+)", link_api)
            if m:
                dbpia_id = m.group(1)

        rows.append({
            "제목": title,
            "저자": author_str,
            "학술지": pub_name,
            "연도": year,
            "발행일": pubdate,  # YYYY-MM (가능할 때)
            "페이지": pages,
            "링크": link,
            "DBpiaID": dbpia_id
        })

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["제목", "저자", "학술지", "연도", "발행일", "페이지", "링크", "DBpiaID"])
    return df

def search_dbpia(keyword: str, max_results: int = 20, sort_by_date: bool = True) -> pd.DataFrame:
    """
    DBpia OpenAPI로 논문/학술자료 검색.
    - target=se_adv : 상세검색
    - itype=1 : 학술저널(논문) 중심
    - sorttype=2 : 발행일순 (문서 기준)
    """
    if not dbpia_key:
        return pd.DataFrame(columns=["제목", "저자", "학술지", "연도", "발행일", "페이지", "링크", "DBpiaID"])

    # DBpia는 pagecount 기본 20. max_results 초과는 페이지를 돌며 합치기.
    per_page = 20
    need = max(1, int(max_results))
    pages = (need - 1) // per_page + 1

    all_frames = []
    for p in range(1, pages + 1):
        params = {
            "key": dbpia_key,
            "target": "se_adv",
            "searchall": keyword,
            "itype": 1,                 # 1=학술저널
            "pagecount": per_page,
            "pagenumber": p,
            "freeyn": "yes",
            "priceyn": "no",
        }
        if sort_by_date:
            params["sorttype"] = 2      # 2=발행일순
            params["sortorder"] = "desc"

        ok, xml_or_err = dbpia_request(params)
        if not ok:
            # 페이지 하나라도 실패하면 경고만 띄우고 진행 (기능 유지)
            st.warning(f"DBpia API 호출 실패(p={p}): {xml_or_err}")
            continue

        df = parse_dbpia_xml(xml_or_err)
        all_frames.append(df)

    if not all_frames:
        return pd.DataFrame(columns=["제목", "저자", "학술지", "연도", "발행일", "페이지", "링크", "DBpiaID"])

    out = pd.concat(all_frames, ignore_index=True)

    # max_results 만큼 자르기 + 중복 제거(링크/제목 기준)
    if "링크" in out.columns:
        out = out.drop_duplicates(subset=["링크"], keep="first")
    out = out.head(need)
    return out

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

        # ✅ DBpia 연동 (모드가 연구논문용이면 더 많이, 아니면 기본 20)
        paper_limit = 40 if mode == "📚 연구논문용 모드" else 20
        paper_df = search_dbpia(topic, max_results=paper_limit, sort_by_date=True)

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

    tab_news, tab_paper = st.tabs(["📰 뉴스", "📄 논문 (DBpia)"])

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
        if not dbpia_key:
            st.info("DBpia API Key를 입력하면 논문 검색 결과가 표시됩니다. (사이드바 → DBpia 설정)")
        dfp = r["papers"].copy() if isinstance(r.get("papers"), pd.DataFrame) else pd.DataFrame(r.get("papers", []))

        st.dataframe(dfp, use_container_width=True)

        if not dfp.empty:
            csv_papers = dfp.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 논문 CSV 다운로드", csv_papers, f"{slugify(r['topic'])}_papers_dbpia.csv")

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
                label = pretty(f.replace(".json", ""))
                if st.button(label, key=f"{d}_{f}"):
                    file_path = f"history/{d}/{f}"

                    try:
                        with open(file_path, "r", encoding="utf-8") as jf:
                            data = json.load(jf)

                        data["news"] = pd.DataFrame(data.get("news", []))
                        data["papers"] = pd.DataFrame(data.get("papers", []))
                        st.session_state.results = data

                    except json.JSONDecodeError:
                        st.sidebar.warning(f"⚠️ 손상된 파일 스킵됨: {pretty(f.replace('.json', ''))}")
                    except Exception:
                        st.sidebar.warning(f"⚠️ 파일 로딩 실패: {pretty(f.replace('.json', ''))}")
