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
    "원문 제공은 별도 계약/권한이 필요할 수 있어요."
)

# =====================
# 실행환경 네트워크(공인 IP 확인)
# =====================
def get_public_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except:
        return "unknown"

st.sidebar.markdown("---")
st.sidebar.subheader("🌐 실행환경 네트워크")
st.sidebar.write("Public IP:", get_public_ip())
st.sidebar.caption("DBpia 키가 IP 제한(E0014)인 경우, 여기 나온 Public IP를 DBpia 키 설정에 등록해야 합니다.")

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

def safe_int(x, default=0):
    try:
        return int(x)
    except:
        return default

def normalize_pages(pages: str) -> tuple[str, str]:
    """
    DBpia pages 예: "279-309 (31 pages)" / "37-50 (14 pages)" / "279-309"
    return: (start_page, end_page) 없으면 ("","")
    """
    if not pages:
        return "", ""
    m = re.search(r"(\d+)\s*-\s*(\d+)", pages)
    if m:
        return m.group(1), m.group(2)
    return "", ""

def format_authors_apa_kor(authors_str: str) -> str:
    """
    아주 단순한 APA 저자 포맷:
    - "김철수, 이영희" -> "김철수, 이영희"
    DBpia는 보통 '성명'을 이미 정리해서 주기 때문에 큰 변환은 하지 않음.
    (원하면: 3명 이상일 때 et al. 처리 등 확장 가능)
    """
    s = (authors_str or "").strip()
    return s if s else "Unknown"

# =====================
# APA7 (뉴스/논문)
# =====================
def apa_news(row):
    author = row.get("출처", "News")
    year = row["발행일"][:4] if row.get("발행일") else "n.d."
    return f"{author}. ({year}). {row.get('제목','')}. {row.get('출처','')}. {row.get('링크','')}"

def apa_paper(row):
    """
    DBpia 논문 APA7 (업그레이드 버전)
    가능한 필드(검색 API 기준):
    - 저자
    - 연도(또는 발행일 YYYY-MM)
    - 제목
    - 학술지(publication name)
    - 권(volume = issue.range 또는 issue.num 등에 들어올 수 있음)
    - 호(issue = issue.name 또는 issue.num)
    - 페이지(시작-끝)
    - DOI (DBpia 검색 API에 직접 필드가 없는 경우가 많아, link/문자열에서 추출 시도만)
    """
    authors = format_authors_apa_kor(row.get("저자", ""))
    title = (row.get("제목", "") or "").strip()
    journal = (row.get("학술지", "") or "DBpia").strip()

    # 연도 우선, 없으면 발행일 YYYY-MM에서 연도 추출
    year = (row.get("연도", "") or "").strip()
    if not year:
        pub = (row.get("발행일", "") or "").strip()
        if len(pub) >= 4 and pub[:4].isdigit():
            year = pub[:4]
    if not year:
        year = "n.d."

    # 권/호
    volume = (row.get("권", "") or "").strip()
    issue_no = (row.get("호", "") or "").strip()

    # 페이지
    pages_raw = (row.get("페이지", "") or "").strip()
    sp, ep = normalize_pages(pages_raw)
    pages_part = ""
    if sp and ep:
        pages_part = f"{sp}–{ep}"
    elif pages_raw:
        pages_part = pages_raw

    # DOI 추출(있을 때만) - DBpia 검색 API에 DOI 필드가 없을 수 있어 heuristic
    doi = (row.get("DOI", "") or "").strip()
    if not doi:
        # 링크나 기타 필드에 DOI 패턴이 있으면 잡기 (가능하면)
        for cand in [row.get("링크", ""), row.get("link_api", ""), row.get("link_url", "")]:
            if not cand:
                continue
            m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", str(cand), flags=re.I)
            if m:
                doi = m.group(1)
                break

    url = (row.get("링크", "") or "").strip()

    # ---- APA 구성 ----
    # Journal, volume(issue), pages.
    vol_issue = ""
    if volume and issue_no:
        vol_issue = f"{volume}({issue_no})"
    elif volume:
        vol_issue = volume
    elif issue_no:
        # 권이 없고 호만 있으면 (드물지만) 호만 표시
        vol_issue = f"({issue_no})"

    parts = []
    parts.append(f"{authors}. ({year}). {title}. {journal}")

    if vol_issue:
        parts[-1] = parts[-1] + f", {vol_issue}"
    if pages_part:
        parts[-1] = parts[-1] + f", {pages_part}"

    parts[-1] = parts[-1] + "."

    # DOI가 있으면 DOI 우선, 없으면 URL
    if doi:
        parts.append(f"https://doi.org/{doi}")
    elif url:
        parts.append(url)

    return " ".join(parts)

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

def relevance_paper(topic, p):
    prompt = f"""
연구 주제: {topic}
논문 제목: {p.get('제목','')}
저자: {p.get('저자','')}
학술지: {p.get('학술지','')}
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
# DBpia 검색 (디버그 포함 + 권/호 파싱 강화)
# =====================
DBPIA_BASE_URLS = [
    "http://api.dbpia.co.kr/v2/search/search.xml",
    "https://api.dbpia.co.kr/v2/search/search.xml",
]

def dbpia_request(params: dict) -> tuple[bool, str]:
    headers = {
        "User-Agent": "RefNoteAI/1.0 (+streamlit)",
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    }

    last_err = None
    for url in DBPIA_BASE_URLS:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            r.encoding = "utf-8"
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            text = (r.text or "").strip()
            if not text.startswith("<"):
                last_err = "Non-XML response"
                continue
            return True, text
        except Exception as e:
            last_err = str(e)

    return False, last_err or "Unknown request error"

def extract_dbpia_error(xml_text: str) -> tuple[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return "", ""

    err = root.find(".//error")
    if err is not None:
        code = safe_get_text(err.find("code"), "")
        msg = safe_get_text(err.find("message"), "")
        return code, msg

    code = safe_get_text(root.find(".//code"), "")
    msg = safe_get_text(root.find(".//message"), "")
    if code or msg:
        return code, msg

    return "", ""

def parse_dbpia_xml(xml_text: str) -> pd.DataFrame:
    """
    컬럼 확장:
    - 권, 호, DOI (가능하면)
    """
    base_cols = ["제목", "저자", "학술지", "연도", "발행일", "권", "호", "페이지", "DOI", "링크", "DBpiaID"]

    code, msg = extract_dbpia_error(xml_text)
    if code or msg:
        if code == "E0016":
            return pd.DataFrame(columns=base_cols)
        return pd.DataFrame([{
            "제목": "",
            "저자": "",
            "학술지": "",
            "연도": "",
            "발행일": "",
            "권": "",
            "호": "",
            "페이지": "",
            "DOI": "",
            "링크": "",
            "DBpiaID": f"{code}: {msg}".strip(": ")
        }], columns=base_cols)

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return pd.DataFrame(columns=base_cols)

    rows = []
    for item in root.findall(".//item"):
        title = safe_get_text(item.find("title"), "")

        author_names = []
        authors = item.find("authors")
        if authors is not None:
            for a in authors.findall("author"):
                name = a.get("name") or safe_get_text(a.find("name"), "")
                if name:
                    author_names.append(name)
        author_str = ", ".join(author_names) if author_names else ""

        pub_name = ""
        publication = item.find("publication")
        if publication is not None:
            pub_name = publication.get("name") or safe_get_text(publication.find("name"), "")

        # issue 파싱 강화
        year = ""
        pubdate = ""
        vol = ""   # 권
        iss = ""   # 호

        issue = item.find("issue")
        if issue is not None:
            # 문서 설명 기준:
            # - issue 하위요소: range(권호범위), name(권호명), num(권(호)), yymm(발행연월)
            # 실제 응답은 attribute로 올 수도 있고 child로 올 수도 있어서 둘 다 대비
            yymm = issue.get("yymm") or safe_get_text(issue.find("yymm"), "")
            if yymm and len(yymm) >= 4 and yymm[:4].isdigit():
                year = yymm[:4]
                if len(yymm) >= 6 and yymm[4:6].isdigit():
                    pubdate = f"{yymm[:4]}-{yymm[4:6]}"

            y = issue.get("year") or safe_get_text(issue.find("year"), "")
            if (not year) and y and y.isdigit():
                year = y

            # 권/호 후보들
            # 1) num = 권(호) (예: "5(1)" or "61" 같은 형태)
            num = issue.get("num") or safe_get_text(issue.find("num"), "")
            if num:
                m = re.match(r"^\s*(\d+)\s*\(\s*(\d+)\s*\)\s*$", num)
                if m:
                    vol, iss = m.group(1), m.group(2)
                else:
                    # 그냥 권만 들어온 경우로 처리
                    if num.strip().isdigit():
                        vol = num.strip()
                    else:
                        # "61, 1" 같은 이상 케이스
                        m2 = re.search(r"(\d+)\s*\(\s*(\d+)\s*\)", num)
                        if m2:
                            vol, iss = m2.group(1), m2.group(2)

            # 2) name = 권호명에 "61" 또는 "61권 1호" 같은 텍스트가 올 수 있음
            name = issue.get("name") or safe_get_text(issue.find("name"), "")
            if name and (not vol and not iss):
                # 숫자(숫자) 패턴
                m = re.search(r"(\d+)\s*\(\s*(\d+)\s*\)", name)
                if m:
                    vol, iss = m.group(1), m.group(2)
                else:
                    # "61권 1호" 같은 형태
                    m2 = re.search(r"(\d+)\s*권.*?(\d+)\s*호", name)
                    if m2:
                        vol, iss = m2.group(1), m2.group(2)
                    else:
                        # 숫자만 있으면 권으로
                        m3 = re.search(r"\b(\d+)\b", name)
                        if m3:
                            vol = vol or m3.group(1)

            # 3) range는 간행물일 때 권호범위일 수 있으나, 혹시라도 있으면 백업
            rng = issue.get("range") or safe_get_text(issue.find("range"), "")
            # range는 보통 "1~10" 같은 범위라 권/호로 쓰기 애매해서 여기선 저장하지 않음

        pages = safe_get_text(item.find("pages"), "")

        link_url = safe_get_text(item.find("link_url"), "")
        link_api = safe_get_text(item.find("link_api"), "")
        link = link_url or link_api

        # DBpia ID
        dbpia_id = ""
        if link_api:
            m = re.search(r"[?&]id=([^&]+)", link_api)
            if m:
                dbpia_id = m.group(1)

        # DOI (검색 API에 직접 필드가 없을 수 있어, item 내부 텍스트/속성에서 탐색)
        doi = ""
        # 1) 혹시 doi 태그가 있으면
        doi = safe_get_text(item.find("doi"), "") or safe_get_text(item.find("DOI"), "")
        # 2) 없으면 link나 내부 텍스트에서 heuristic
        if not doi:
            for cand in [link_api, link_url, title]:
                if not cand:
                    continue
                m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", str(cand), flags=re.I)
                if m:
                    doi = m.group(1)
                    break

        rows.append({
            "제목": title,
            "저자": author_str,
            "학술지": pub_name,
            "연도": year,
            "발행일": pubdate,
            "권": vol,
            "호": iss,
            "페이지": pages,
            "DOI": doi,
            "링크": link,
            "DBpiaID": dbpia_id
        })

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=base_cols)
    return df

def search_dbpia(keyword: str, max_results: int = 20, sort_by_date: bool = True) -> pd.DataFrame:
    base_cols = ["제목", "저자", "학술지", "연도", "발행일", "권", "호", "페이지", "DOI", "링크", "DBpiaID"]

    if not dbpia_key:
        return pd.DataFrame(columns=base_cols)

    per_page = 20
    need = max(1, int(max_results))
    pages = (need - 1) // per_page + 1

    all_frames = []
    for p in range(1, pages + 1):
        params = {
            "key": dbpia_key,
            "target": "se_adv",
            "searchall": keyword,
            "itype": 1,
            "pagecount": per_page,
            "pagenumber": p,
            "freeyn": "yes",
            "priceyn": "no",
        }
        if sort_by_date:
            params["sorttype"] = 2
            params["sortorder"] = "desc"

        ok, xml_or_err = dbpia_request(params)

        # ✅ 디버그 expander
        with st.expander(f"🧪 DBpia debug (page={p})", expanded=False):
            st.write("request params:", params)
            if ok:
                code, msg = extract_dbpia_error(xml_or_err)
                st.write("error code:", code)
                st.write("error msg:", msg)
                st.text(xml_or_err[:1500])
            else:
                st.write("request failed:", xml_or_err)

        if not ok:
            st.warning(f"DBpia API 호출 실패(p={p}): {xml_or_err}")
            continue

        df = parse_dbpia_xml(xml_or_err)
        all_frames.append(df)

    if not all_frames:
        return pd.DataFrame(columns=base_cols)

    out = pd.concat(all_frames, ignore_index=True)

    # 에러 행 제거(정상행이 있을 때만)
    if "DBpiaID" in out.columns:
        err_mask = out["DBpiaID"].astype(str).str.match(r"^E\d{4}\s*:")
        if err_mask.any() and (~err_mask).any():
            out = out.loc[~err_mask].copy()

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

        # ---- 뉴스 ----
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

        if len(filtered) < 10:
            news_list_sorted = sorted(news_list, key=lambda x: x.get("score", 0), reverse=True)
            filtered = news_list_sorted[:10]

        news_df = pd.DataFrame(filtered).drop_duplicates(subset=["링크"])

        # ---- 논문(DBpia) ----
        paper_limit = 40 if mode == "📚 연구논문용 모드" else 20
        paper_df = search_dbpia(topic, max_results=paper_limit, sort_by_date=True)

        # ✅ 논문 관련도 점수 부여
        if not paper_df.empty:
            papers_records = paper_df.to_dict(orient="records")
            scored = []
            for p in papers_records:
                p["score"] = relevance_paper(topic, p)
                scored.append(p)
            paper_df = pd.DataFrame(scored)

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

    # -----------------
    # 뉴스 탭
    # -----------------
    with tab_news:
        sort = st.radio("정렬 기준", ["관련도순", "최신순"], horizontal=True, key="news_sort")
        df = r["news"].copy()

        if not df.empty:
            if sort == "관련도순":
                if "score" in df.columns:
                    df = df.sort_values(by="score", ascending=False)
            else:
                df = df.sort_values(by="발행일", ascending=False)

        st.dataframe(df, use_container_width=True)

        csv_news = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 뉴스 CSV 다운로드", csv_news, f"{slugify(r['topic'])}_news.csv")

        st.subheader("📎 APA 참고문헌 (Top10)")
        for _, row in df.head(10).iterrows():
            st.markdown(f"- {apa_news(row)}")

    # -----------------
    # 논문 탭
    # -----------------
    with tab_paper:
        if not dbpia_key:
            st.info("DBpia API Key를 입력하면 논문 검색 결과가 표시됩니다. (사이드바 → DBpia 설정)")

        dfp = r["papers"].copy() if isinstance(r.get("papers"), pd.DataFrame) else pd.DataFrame(r.get("papers", []))

        paper_sort = st.radio("정렬 기준", ["관련도순", "최신순"], horizontal=True, key="paper_sort")

        if not dfp.empty:
            if paper_sort == "관련도순":
                if "score" in dfp.columns:
                    dfp["score"] = dfp["score"].apply(lambda x: safe_int(x, 0))
                    dfp = dfp.sort_values(by="score", ascending=False)
            else:
                # 발행일(YYYY-MM) 우선, 없으면 연도
                if "발행일" in dfp.columns:
                    dfp = dfp.sort_values(by="발행일", ascending=False)
                elif "연도" in dfp.columns:
                    dfp = dfp.sort_values(by="연도", ascending=False)

        st.dataframe(dfp, use_container_width=True)

        if not dfp.empty:
            csv_papers = dfp.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 논문 CSV 다운로드", csv_papers, f"{slugify(r['topic'])}_papers_dbpia.csv")

            st.subheader("📎 APA 참고문헌 (Top10)")
            for _, row in dfp.head(10).iterrows():
                st.markdown(f"- {apa_paper(row)}")

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
