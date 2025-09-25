# app.py
import os
import re
import html
import json
import datetime as dt
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ---------- NAVER API ----------
API_BLOG  = "https://openapi.naver.com/v1/search/blog.json"
API_CAFE  = "https://openapi.naver.com/v1/search/cafearticle.json"
API_LOCAL = "https://openapi.naver.com/v1/search/local.json"
API_DATALAB_SEARCH = "https://openapi.naver.com/v1/datalab/search"
API_SHOP_CAT       = "https://openapi.naver.com/v1/datalab/shopping/categories"
API_SHOP_CAT_KW    = "https://openapi.naver.com/v1/datalab/shopping/category/keywords"

API_PAGE_SIZE  = 100          # 블로그/카페: 최대 100
API_START_MAX  = 1000         # 블로그/카페: start 최대
DEFAULT_PAGE_SIZE = 20        # 블로그/카페 한 화면 표시
LOCAL_DISPLAY_MAX = 5         # 지역: 문서상 최대 5
LOCAL_START   = 1             # 지역: start=1 (페이징 없음)

# ---------- Creds ----------
def _secret_or_none(key: str):
    try:
        return st.secrets[key]
    except Exception:
        return None

def get_credentials():
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    cid = _secret_or_none("NAVER_CLIENT_ID") or cid or ""
    csec = _secret_or_none("NAVER_CLIENT_SECRET") or csec or ""
    return cid, csec

def _auth_headers(content_json=False):
    cid, csec = get_credentials()
    if not cid or not csec:
        st.error(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다.\n"
            "• 방법 A: 프로젝트 루트에 `.streamlit/secrets.toml`\n"
            "• 방법 B: 환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET"
        )
        st.stop()
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}
    if content_json:
        headers["Content-Type"] = "application/json"
    return headers

# ---------- Utils ----------
def strip_b_tags(text: str) -> str:
    if not isinstance(text, str):
        return text
    return re.sub(r"</?b>", "", text)

def emphasize_api_b(text: str) -> str:
    escaped = html.escape(text or "")
    return escaped.replace("&lt;b&gt;", "<mark>").replace("&lt;/b&gt;", "</mark>")

def build_highlighter(raw_query: str):
    terms = re.findall(r"[0-9A-Za-z가-힣]+", raw_query or "")
    terms = [t for t in terms if len(t) >= 2]
    if not terms:
        return lambda s: emphasize_api_b(s or "")
    pattern = re.compile("(" + "|".join(map(re.escape, terms)) + ")", re.IGNORECASE)
    def highlight(text: str) -> str:
        base = emphasize_api_b(text or "")
        return pattern.sub(r"<mark>\1</mark>", base)
    return highlight

# ---------- Caching helpers ----------
@st.cache_data(show_spinner=False, ttl=600)
def cached_get(url, headers, params):
    r = requests.get(url, headers=headers, params=params, timeout=15)
    return r.status_code, (r.json() if "application/json" in r.headers.get("Content-Type","") else r.text)

@st.cache_data(show_spinner=False, ttl=600)
def cached_post(url, headers, payload):
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
    return r.status_code, (r.json() if "application/json" in r.headers.get("Content-Type","") else r.text)

# ---------- Search API 공통 호출 ----------
def call_search(api_url: str, query: str, start: int, display: int, sort: str):
    headers = _auth_headers()
    params = {"query": query, "start": start, "display": display, "sort": sort}
    code, data = cached_get(api_url, headers, params)
    if code != 200:
        st.error(f"[검색 API 오류] {api_url} · HTTP {code}\n\n{data}")
        st.stop()
    return data

# ---------- 정확 일치 필터용 수집 (블로그/카페 공용) ----------
@st.cache_data(show_spinner=False, ttl=600)
def fetch_filtered_page(api_url: str, query: str, sort: str, page_size: int, page_index: int):
    """
    정확 일치 ON일 때:
    - 제목/요약의 <b> 제거 후 query '그대로'(대소문자/띄어쓰기 포함) 포함 항목만 누적.
    - 1→1000 범위를 100개 단위로 가져와 필요한 페이지만 반환.
    """
    matched, target_fetch = [], page_index * page_size + 1
    headers = _auth_headers()
    for start in range(1, API_START_MAX + 1, API_PAGE_SIZE):
        code, data = cached_get(
            api_url, headers, {"query": query, "start": start, "display": API_PAGE_SIZE, "sort": sort}
        )
        if code != 200:
            break
        items = data.get("items", []) or []
        if not items: break
        for it in items:
            title_plain = strip_b_tags(it.get("title", "") or "")
            desc_plain  = strip_b_tags(it.get("description", "") or "")
            if (query in title_plain) or (query in desc_plain):
                matched.append(it)
                if len(matched) >= target_fetch: break
        if len(matched) >= target_fetch or len(items) < API_PAGE_SIZE:
            break

    s, e = (page_index - 1) * page_size, (page_index - 1) * page_size + page_size
    page_items = matched[s:e] if s < len(matched) else []
    has_next = len(matched) > e
    return page_items, has_next, len(matched)

# ---------- DataLab: 통합 검색어 트렌드 ----------
def call_datalab_search_trend(
    keywords: list[str], start_date: str, end_date: str,
    time_unit: str = "date", device: str | None = None,
    ages: list[str] | None = None, gender: str | None = None,
):
    headers = _auth_headers(content_json=True)
    payload = {
        "startDate": start_date, "endDate": end_date, "timeUnit": time_unit,
        "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in keywords if kw.strip()],
    }
    if device: payload["device"] = device
    if ages:   payload["ages"] = ages
    if gender: payload["gender"] = gender
    code, data = cached_post(API_DATALAB_SEARCH, headers, payload)
    if code != 200:
        st.error(f"[데이터랩(검색어 트렌드)] HTTP {code}\n\n{data}")
        st.stop()
    return data

def datalab_to_dataframe(data: dict) -> pd.DataFrame:
    results = data.get("results", [])
    frames = []
    for group in results:
        name = group.get("title") or group.get("keyword") or group.get("groupName") or "keyword"
        pts = group.get("data", []) or []
        if not pts: continue
        df = pd.DataFrame(pts)
        if "period" not in df.columns or "ratio" not in df.columns or df.empty: continue
        df = df.rename(columns={"ratio": name})
        frames.append(df[["period", name]])
    if not frames: return pd.DataFrame()
    base = frames[0]
    for f in frames[1:]: base = base.merge(f, on="period", how="outer")
    if "period" in base.columns: base = base.sort_values("period")
    return base.reset_index(drop=True)

# ---------- DataLab: 쇼핑인사이트 ----------
def call_shopping_categories(categories: list[tuple[str, str]], start_date: str, end_date: str,
                             time_unit: str = "date", device: str | None = None,
                             ages: list[str] | None = None, gender: str | None = None):
    headers = _auth_headers(content_json=True)
    payload = {
        "startDate": start_date, "endDate": end_date, "timeUnit": time_unit,
        "category": [{"name": nm, "param": [cid]} for (nm, cid) in categories if nm and cid],
    }
    if device: payload["device"] = device
    if ages:   payload["ages"] = ages
    if gender: payload["gender"] = gender
    code, data = cached_post(API_SHOP_CAT, headers, payload)
    if code != 200:
        st.error(f"[쇼핑인사이트-분야별] HTTP {code}\n\n{data}")
        st.stop()
    return data

def call_shopping_category_keywords(category_id: str, keyword_pairs: list[tuple[str, str]],
                                    start_date: str, end_date: str, time_unit: str = "date",
                                    device: str | None = None, ages: list[str] | None = None,
                                    gender: str | None = None):
    headers = _auth_headers(content_json=True)
    payload = {
        "startDate": start_date, "endDate": end_date, "timeUnit": time_unit,
        "category": category_id,
        "keyword": [{"name": name, "param": [kw]} for (name, kw) in keyword_pairs if name and kw],
    }
    if device: payload["device"] = device
    if ages:   payload["ages"] = ages
    if gender: payload["gender"] = gender
    code, data = cached_post(API_SHOP_CAT_KW, headers, payload)
    if code != 200:
        st.error(f"[쇼핑인사이트-카테고리 키워드] HTTP {code}\n\n{data}")
        st.stop()
    return data

def shopping_to_dataframe(data: dict) -> pd.DataFrame:
    results = data.get("results", [])
    frames = []
    for group in results:
        name = group.get("title") or "series"
        pts = group.get("data", []) or []
        if not pts: continue
        df = pd.DataFrame(pts)
        if "period" not in df.columns or "ratio" not in df.columns or df.empty: continue
        df = df.rename(columns={"ratio": name})
        frames.append(df[["period", name]])
    if not frames: return pd.DataFrame()
    base = frames[0]
    for f in frames[1:]: base = base.merge(f, on="period", how="outer")
    if "period" in base.columns: base = base.sort_values("period")
    return base.reset_index(drop=True)

# ---------- 공통 렌더 ----------
def render_table(items: list[dict], highlighter, author_key: str, author_label: str, show_date_key: str | None = None):
    if not items:
        components.html("<p style='color:#666'>표시할 결과가 없습니다.</p>", height=60)
        return
    rows_html = []
    for it in items:
        title_html = highlighter(it.get("title", ""))
        desc_html  = highlighter(it.get("description", ""))
        author     = html.escape(it.get(author_key, "") or "")
        date_val   = html.escape((it.get(show_date_key, "") or "")) if show_date_key else "-"
        url        = html.escape(it.get("link", "") or "")
        row = f"""
<tr>
  <td style="padding:8px 10px;vertical-align:top;min-width:240px;">
    <a href="{url}" target="_blank" style="text-decoration:none;">{title_html}</a>
  </td>
  <td style="padding:8px 10px;vertical-align:top;">{desc_html}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">{author}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">{date_val}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;"><a href="{url}" target="_blank">열기</a></td>
</tr>
"""
        rows_html.append(row)
    table_html = f"""
<!doctype html>
<html>
<head><meta charset="utf-8"/>
<style>
  table {{ width:100%; border-collapse:collapse; border:1px solid #e5e7eb; }}
  thead tr {{ background:#f8fafc; }}
  th, td {{ text-align:left; padding:10px; border-bottom:1px solid #e5e7eb; }}
  mark {{ background: #fff3a3; padding: 0 2px; }}
</style>
</head>
<body>
<div style="max-width:100%; overflow:auto;">
  <table>
    <thead>
      <tr>
        <th>제목</th><th>요약</th><th>{author_label}</th><th>작성일</th><th>URL</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</div>
</body>
</html>
"""
    rows_to_show = min(len(items), DEFAULT_PAGE_SIZE)
    table_height = int(34 * rows_to_show + 40 + 20)
    components.html(table_html, height=table_height + 200, scrolling=True)

def render_local_table(items: list[dict], highlighter):
    if not items:
        components.html("<p style='color:#666'>표시할 결과가 없습니다.</p>", height=60)
        return
    rows_html = []
    for it in items:
        title_html = highlighter(it.get("title", ""))
        category   = emphasize_api_b(it.get("category", ""))
        desc_html  = highlighter(it.get("description", ""))
        addr       = html.escape(it.get("address", "") or "")
        road       = html.escape(it.get("roadAddress", "") or "")
        url        = html.escape(it.get("link", "") or "")
        row = f"""
<tr>
  <td style="padding:8px 10px;vertical-align:top;min-width:200px;">{title_html}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">{category}</td>
  <td style="padding:8px 10px;vertical-align:top;">{desc_html}</td>
  <td style="padding:8px 10px;vertical-align:top;"><div>{addr}</div><div style="color:#555;">{road}</div></td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;"><a href="{url}" target="_blank">열기</a></td>
</tr>
"""
        rows_html.append(row)
    table_html = f"""
<!doctype html>
<html>
<head><meta charset="utf-8"/>
<style>
  table {{ width:100%; border-collapse:collapse; border:1px solid #e5e7eb; }}
  thead tr {{ background:#f8fafc; }}
  th, td {{ text-align:left; padding:10px; border-bottom:1px solid #e5e7eb; }}
  mark {{ background: #fff3a3; padding: 0 2px; }}
</style>
</head>
<body>
<div style="max-width:100%; overflow:auto;">
  <table>
    <thead>
      <tr>
        <th>업체명</th><th>카테고리</th><th>설명</th><th>주소(지번/도로명)</th><th>URL</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</div>
</body>
</html>
"""
    rows_to_show = len(items)
    table_height = int(34 * rows_to_show + 40 + 20)
    components.html(table_html, height=table_height + 160, scrolling=True)

# ================== Streamlit App ==================
def main():
    st.set_page_config(page_title="NAVER 통합 검색 (블로그/카페/지역/데이터랩/쇼핑)", page_icon="🔎", layout="wide")
    st.title("🔎 NAVER 통합 검색 (블로그 / 카페글 / 지역 / 데이터랩 / 쇼핑인사이트)")

    # Sidebar: credentials
    with st.sidebar:
        st.markdown("**자격증명 설정**")
        cid_default, csec_default = get_credentials()
        cid_input = st.text_input("NAVER_CLIENT_ID", value=cid_default, type="password")
        csec_input = st.text_input("NAVER_CLIENT_SECRET", value=csec_default, type="password")
        if cid_input and csec_input and (
            cid_input != os.environ.get("NAVER_CLIENT_ID") or
            csec_input != os.environ.get("NAVER_CLIENT_SECRET")
        ):
            os.environ["NAVER_CLIENT_ID"] = cid_input
            os.environ["NAVER_CLIENT_SECRET"] = csec_input
            st.info("현재 세션에 자격증명을 적용했습니다.")
        st.markdown("---")
        st.caption("동일 파라미터는 캐시되어 쿼터 사용을 줄입니다. (10분 TTL)")

    # 공통 검색어
    query = st.text_input("공통 검색어 (블로그/카페/지역/트렌드/쇼핑)", value="리뷰 자동화")

    # 상태 초기화: 검색어가 바뀌면 페이징/상태 리셋
    if "last_query" not in st.session_state:
        st.session_state.last_query = query
    if query != st.session_state.last_query:
        for k, v in [
            ("blog_start", 1), ("blog_page", 1),
            ("cafe_start", 1), ("cafe_page", 1),
        ]:
            st.session_state[k] = v
        st.session_state.last_query = query

    # 탭
    tab_blog, tab_cafe, tab_trend, tab_local, tab_shop = st.tabs(
        ["블로그", "카페글", "데이터랩(검색어 트렌드)", "지역", "쇼핑인사이트"]
    )

    # ===== 블로그 =====
    with tab_blog:
        st.subheader("블로그 검색")
        bc1, bc2, bc3 = st.columns([1, 1, 1])
        with bc1:
            blog_sort = st.selectbox("정렬", options=[("sim", "정확도순"), ("date", "날짜순")],
                                     index=0, format_func=lambda x: x[1], key="blog_sort")[0]
        with bc2:
            blog_page_size = st.number_input("한 페이지 결과(1~100)", 1, 100, DEFAULT_PAGE_SIZE, 1, key="blog_ps")
        with bc3:
            blog_exact = st.toggle("정확 일치 필터", value=True, key="blog_exact",
                                   help="제목/요약에 검색어가 그대로(띄어쓰기 포함) 존재하는 항목만 표시(대소문자 구분)")

        if "blog_start" not in st.session_state: st.session_state.blog_start = 1
        if "blog_page" not in st.session_state:  st.session_state.blog_page = 1

        highlighter = build_highlighter(query)
        if blog_exact:
            items, has_next, matched_cnt = fetch_filtered_page(
                API_BLOG, query, blog_sort, int(blog_page_size), st.session_state.blog_page
            )
            info = f"필터 모드 · 정확 일치 누적 {matched_cnt:,}건(≤1,000) · {st.session_state.blog_page} 페이지"
            prev_disabled = st.session_state.blog_page <= 1
            next_disabled = not has_next
        else:
            data = call_search(API_BLOG, query, st.session_state.blog_start, int(blog_page_size), blog_sort)
            total = data.get("total", 0)
            start_now = data.get("start", st.session_state.blog_start)
            items = data.get("items", []) or []
            info = f"일반 모드 · API 총 {total:,}건 · 표시 {start_now}~{min(start_now + int(blog_page_size) - 1, total):,}"
            prev_disabled = st.session_state.blog_start <= 1
            next_disabled = (st.session_state.blog_start + int(blog_page_size) > min(total, API_START_MAX))

        st.caption(info)
        render_table(items, highlighter, author_key="bloggername", author_label="블로거", show_date_key="postdate")

        l, m, r = st.columns(3)
        with l:
            if st.button("⬅ 이전", key="blog_prev", disabled=prev_disabled):
                if blog_exact: st.session_state.blog_page = max(1, st.session_state.blog_page - 1)
                else:          st.session_state.blog_start = max(1, st.session_state.blog_start - int(blog_page_size))
                st.rerun()
        with m:
            st.caption(f"{'필터' if blog_exact else '일반'} · "
                       f"{'page='+str(st.session_state.blog_page) if blog_exact else 'start='+str(st.session_state.blog_start)}")
        with r:
            if st.button("다음 ➡", key="blog_next", disabled=next_disabled):
                if blog_exact: st.session_state.blog_page += 1
                else:          st.session_state.blog_start += int(blog_page_size)
                st.rerun()

        if items:
            df = pd.DataFrame({
                "제목": [strip_b_tags(it.get("title","")) for it in items],
                "요약": [strip_b_tags(it.get("description","")) for it in items],
                "블로거": [it.get("bloggername","") for it in items],
                "작성일": [it.get("postdate","") for it in items],
                "URL": [it.get("link","") for it in items],
            })
            st.download_button("CSV 다운로드(블로그)", data=df.to_csv(index=False),
                               file_name="naver_blog_results.csv", mime="text/csv")

    # ===== 카페글 =====
    with tab_cafe:
        st.subheader("카페글 검색")
        cc1, cc2, cc3 = st.columns([1, 1, 1])
        with cc1:
            cafe_sort = st.selectbox("정렬", options=[("sim", "정확도순"), ("date", "날짜순")],
                                     index=0, format_func=lambda x: x[1], key="cafe_sort")[0]
        with cc2:
            cafe_page_size = st.number_input("한 페이지 결과(1~100)", 1, 100, DEFAULT_PAGE_SIZE, 1, key="cafe_ps")
        with cc3:
            cafe_exact = st.toggle("정확 일치 필터", value=True, key="cafe_exact",
                                   help="제목/요약에 검색어가 그대로 존재 (대소문자/띄어쓰기 포함)")

        if "cafe_start" not in st.session_state: st.session_state.cafe_start = 1
        if "cafe_page" not in st.session_state:  st.session_state.cafe_page = 1

        highlighter = build_highlighter(query)
        if cafe_exact:
            items, has_next, matched_cnt = fetch_filtered_page(
                API_CAFE, query, cafe_sort, int(cafe_page_size), st.session_state.cafe_page
            )
            info = f"필터 모드 · 정확 일치 누적 {matched_cnt:,}건(≤1,000) · {st.session_state.cafe_page} 페이지"
            prev_disabled = st.session_state.cafe_page <= 1
            next_disabled = not has_next
        else:
            data = call_search(API_CAFE, query, st.session_state.cafe_start, int(cafe_page_size), cafe_sort)
            total = data.get("total", 0)
            start_now = data.get("start", st.session_state.cafe_start)
            items = data.get("items", []) or []
            info = f"일반 모드 · API 총 {total:,}건 · 표시 {start_now}~{min(start_now + int(cafe_page_size) - 1, total):,}"
            prev_disabled = st.session_state.cafe_start <= 1
            next_disabled = (st.session_state.cafe_start + int(cafe_page_size) > min(total, API_START_MAX))

        st.caption(info)
        render_table(items, highlighter, author_key="cafename", author_label="카페", show_date_key=None)

        l, m, r = st.columns(3)
        with l:
            if st.button("⬅ 이전", key="cafe_prev", disabled=prev_disabled):
                if cafe_exact: st.session_state.cafe_page = max(1, st.session_state.cafe_page - 1)
                else:          st.session_state.cafe_start = max(1, st.session_state.cafe_start - int(cafe_page_size))
                st.rerun()
        with m:
            st.caption(f"{'필터' if cafe_exact else '일반'} · "
                       f"{'page='+str(st.session_state.cafe_page) if cafe_exact else 'start='+str(st.session_state.cafe_start)}")
        with r:
            if st.button("다음 ➡", key="cafe_next", disabled=next_disabled):
                if cafe_exact: st.session_state.cafe_page += 1
                else:          st.session_state.cafe_start += int(cafe_page_size)
                st.rerun()

        if items:
            df = pd.DataFrame({
                "제목": [strip_b_tags(it.get("title","")) for it in items],
                "요약": [strip_b_tags(it.get("description","")) for it in items],
                "카페": [it.get("cafename","") for it in items],
                "작성일": ["" for _ in items],
                "URL": [it.get("link","") for it in items],
            })
            st.download_button("CSV 다운로드(카페글)", data=df.to_csv(index=False),
                               file_name="naver_cafe_results.csv", mime="text/csv")

    # ===== 데이터랩(검색어 트렌드) =====
    with tab_trend:
        st.subheader("검색어 트렌드 (데이터랩)")
        tk1, tk2 = st.columns([2, 1])
        with tk1:
            trend_keywords_raw = st.text_input("트렌드 키워드들(쉼표로 분리) - 비우면 공통 검색어 사용", value="", key="trend_kw")
        with tk2:
            time_unit = st.selectbox("단위", options=["date", "week", "month"], index=0, key="trend_tu")

        today = dt.date.today()
        default_start = today - dt.timedelta(days=90)
        t1, t2, t3 = st.columns([1, 1, 1])
        with t1:
            start_date = st.date_input("시작일", value=default_start, max_value=today, key="trend_start")
        with t2:
            end_date = st.date_input("종료일", value=today, max_value=today, key="trend_end")
        with t3:
            device = st.selectbox("디바이스", options=["전체", "pc", "mo"], index=0, key="trend_dev")
        u1, u2 = st.columns([1, 1])
        with u1:
            gender = st.selectbox("성별", options=["전체", "m", "f"], index=0, key="trend_gender")
        with u2:
            ages = st.multiselect("연령대", options=["10","20","30","40","50","60"], key="trend_ages")

        keywords = (
            [k.strip() for k in trend_keywords_raw.split(",") if k.strip()]
            if trend_keywords_raw.strip()
            else ([query.strip()] if query.strip() else [])
        )

        if keywords:
            dl_device = None if device == "전체" else device
            dl_gender = None if gender == "전체" else gender
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
            dl_data = call_datalab_search_trend(
                keywords=keywords, start_date=start_str, end_date=end_str,
                time_unit=time_unit, device=dl_device, ages=ages if ages else None, gender=dl_gender,
            )
            dl_df = datalab_to_dataframe(dl_data)
            if dl_df.empty or "period" not in dl_df.columns:
                st.info("표시할 트렌드 데이터가 없습니다. (키워드/기간 확인)")
            else:
                st.dataframe(dl_df, use_container_width=True, hide_index=True)
                st.line_chart(dl_df.set_index("period"))
        else:
            st.info("키워드를 입력하세요.")

    # ===== 지역 =====
    with tab_local:
        st.subheader("지역 검색")
        lc1, lc2 = st.columns([1, 1])
        with lc1:
            local_sort = st.selectbox("정렬", options=[("random", "정확도순"), ("comment", "리뷰 많은 순")],
                                      index=0, format_func=lambda x: x[1], key="local_sort")[0]
        with lc2:
            st.number_input("표시 개수(최대 5)", min_value=1, max_value=5, value=LOCAL_DISPLAY_MAX, step=1, disabled=True)

        if query:
            highlighter = build_highlighter(query)
            data = call_search(API_LOCAL, query, LOCAL_START, LOCAL_DISPLAY_MAX, local_sort)
            items = data.get("items", []) or []
            st.caption("지역 API는 문서 기준으로 최대 5건만 반환하며 페이징을 제공하지 않습니다.")
            render_local_table(items, highlighter)
            if items:
                df = pd.DataFrame({
                    "업체명": [strip_b_tags(it.get("title","")) for it in items],
                    "카테고리": [strip_b_tags(it.get("category","")) for it in items],
                    "설명": [strip_b_tags(it.get("description","")) for it in items],
                    "지번주소": [it.get("address","") for it in items],
                    "도로명주소": [it.get("roadAddress","") for it in items],
                    "URL": [it.get("link","") for it in items],
                    "mapx": [it.get("mapx","") for it in items],
                    "mapy": [it.get("mapy","") for it in items],
                })
                st.download_button("CSV 다운로드(지역)", data=df.to_csv(index=False),
                                   file_name="naver_local_results.csv", mime="text/csv")
        else:
            st.info("검색어를 입력하세요.")

    # ===== 쇼핑인사이트 =====
    with tab_shop:
        st.subheader("쇼핑인사이트 (데이터랩)")
        mode = st.radio("보기 유형", ["분야별 트렌드", "카테고리 내 키워드별 트렌드"], horizontal=True, key="shop_mode")

        today = dt.date.today()
        default_start = today - dt.timedelta(days=90)
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            shop_start = st.date_input("시작일", value=default_start, max_value=today, key="shop_start")
        with c2:
            shop_end = st.date_input("종료일", value=today, max_value=today, key="shop_end")
        with c3:
            shop_timeunit = st.selectbox("단위", options=["date", "week", "month"], index=0, key="shop_tu")

        s1, s2, s3 = st.columns([1, 1, 1])
        with s1:
            shop_device = st.selectbox("디바이스", options=["전체", "pc", "mo"], index=0, key="shop_dev")
        with s2:
            shop_gender = st.selectbox("성별", options=["전체", "m", "f"], index=0, key="shop_gender")
        with s3:
            shop_ages = st.multiselect("연령대", options=["10","20","30","40","50","60"], key="shop_ages")

        if mode == "분야별 트렌드":
            st.caption("분야 코드(cat_id)는 네이버쇼핑 카테고리 URL의 cat_id 값입니다. 최대 3개.")
            raw = st.text_input("분야 이름=코드(쉼표 여러 개). 예) 패션의류=50000000, 화장품/미용=50000002", value="", key="shop_cat_raw")
            pairs = []
            for token in [t.strip() for t in raw.split(",") if t.strip()]:
                if "=" in token:
                    nm, cid = token.split("=", 1)
                    pairs.append((nm.strip(), cid.strip()))
            if pairs:
                start_str = shop_start.strftime("%Y-%m-%d")
                end_str   = shop_end.strftime("%Y-%m-%d")
                dev = None if shop_device == "전체" else shop_device
                gen = None if shop_gender == "전체" else shop_gender
                ages = shop_ages if shop_ages else None
                data = call_shopping_categories(
                    categories=pairs, start_date=start_str, end_date=end_str,
                    time_unit=shop_timeunit, device=dev, ages=ages, gender=gen
                )
                df = shopping_to_dataframe(data)
                if df.empty or "period" not in df.columns:
                    st.info("표시할 데이터가 없습니다. (분야 코드/기간 확인)")
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.line_chart(df.set_index("period"))
                    st.download_button("CSV 다운로드(쇼핑·분야별)", data=df.to_csv(index=False),
                                       file_name="naver_shopping_categories.csv", mime="text/csv")
            else:
                st.info("‘분야 이름=코드’ 형식으로 1개 이상 입력하세요.")
        else:
            st.caption("하나의 카테고리(cat_id)와 비교할 키워드(최대 5개)를 입력합니다.")
            cat_id = st.text_input("카테고리 코드(cat_id). 예) 50000000", value="", key="shop_catid")
            kw_raw = st.text_input("키워드그룹 이름=검색어 (쉼표 여러 개). 예) 정장=정장, 비즈니스캐주얼=비즈니스 캐주얼", value="", key="shop_kw_raw")
            pairs = []
            for token in [t.strip() for t in kw_raw.split(",") if t.strip()]:
                if "=" in token:
                    nm, kw = token.split("=", 1)
                    pairs.append((nm.strip(), kw.strip()))
            if cat_id and pairs:
                start_str = shop_start.strftime("%Y-%m-%d")
                end_str   = shop_end.strftime("%Y-%m-%d")
                dev = None if shop_device == "전체" else shop_device
                gen = None if shop_gender == "전체" else shop_gender
                ages = shop_ages if shop_ages else None
                data = call_shopping_category_keywords(
                    category_id=cat_id, keyword_pairs=pairs, start_date=start_str, end_date=end_str,
                    time_unit=shop_timeunit, device=dev, ages=ages, gender=gen
                )
                df = shopping_to_dataframe(data)
                if df.empty or "period" not in df.columns:
                    st.info("표시할 데이터가 없습니다. (카테고리/키워드/기간 확인)")
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.line_chart(df.set_index("period"))
                    st.download_button("CSV 다운로드(쇼핑·키워드)", data=df.to_csv(index=False),
                                       file_name="naver_shopping_keywords.csv", mime="text/csv")
            else:
                st.info("카테고리 코드와 ‘그룹이름=검색어’를 입력하세요.")

    st.caption("※ 조회는 자동 실행되며, 동일 파라미터 재호출은 캐시(10분)되어 쿼터를 절약합니다. 블로그/카페는 start≤1000 제약, 지역은 최대 5건, DataLab/쇼핑인사이트의 ratio는 집합 내 최대=100 기준 상대값입니다.")

if __name__ == "__main__":
    main()
