# app.py
import os
import re
import math
import html
import json
import datetime as dt
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ---------- NAVER API ----------
API_BLOG = "https://openapi.naver.com/v1/search/blog.json"
API_CAFE = "https://openapi.naver.com/v1/search/cafearticle.json"
API_DATALAB = "https://openapi.naver.com/v1/datalab/search"

API_PAGE_SIZE = 100          # 검색 API: 최대 100
API_START_MAX = 1000         # 검색 API: start 최대
DEFAULT_PAGE_SIZE = 20       # 한 화면 표시 행 수

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

def _auth_headers():
    cid, csec = get_credentials()
    if not cid or not csec:
        st.error(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다.\n"
            "• 방법 A: 프로젝트 루트에 `.streamlit/secrets.toml` 생성\n"
            "• 방법 B: 환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 설정"
        )
        st.stop()
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}

# ---------- Utils ----------
def strip_b_tags(text: str) -> str:
    if not isinstance(text, str):
        return text
    return re.sub(r"</?b>", "", text)

def emphasize_api_b(text: str) -> str:
    """네이버 API의 <b>…</b>를 안전하게 <mark>로 변환"""
    escaped = html.escape(text or "")
    return escaped.replace("&lt;b&gt;", "<mark>").replace("&lt;/b&gt;", "</mark>")

def build_highlighter(raw_query: str):
    """사용자 검색어 토큰(2자 이상)을 대소문자 무시로 <mark>"""
    terms = re.findall(r"[0-9A-Za-z가-힣]+", raw_query or "")
    terms = [t for t in terms if len(t) >= 2]
    if not terms:
        return lambda s: emphasize_api_b(s or "")
    pattern = re.compile("(" + "|".join(map(re.escape, terms)) + ")", re.IGNORECASE)
    def highlight(text: str) -> str:
        base = emphasize_api_b(text or "")
        return pattern.sub(r"<mark>\1</mark>", base)
    return highlight

# ---------- Search API 공통 호출 ----------
def call_search(api_url: str, query: str, start: int, display: int, sort: str):
    headers = _auth_headers()
    params = {"query": query, "start": start, "display": display, "sort": sort}
    resp = requests.get(api_url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text}
        st.error(f"[검색 API 오류] {api_url} · HTTP {resp.status_code}\n\n{data}")
        st.stop()
    return resp.json()

# ---------- 정확 일치 필터용 페이지 수집 (블로그/카페 공용) ----------
def fetch_filtered_page(api_url: str, query: str, sort: str, page_size: int, page_index: int):
    """
    정확 일치 필터 ON:
    - 제목/요약의 <b> 제거 후, 검색어(query) '그대로'(대소문자/공백 포함) 포함 항목만 누적.
    - API를 1→1000 범위에서 100개 단위로 조회하여 필요한 페이지만 반환.
    """
    target_end = page_index * page_size
    target_fetch = target_end + 1
    matched = []

    for start in range(1, API_START_MAX + 1, API_PAGE_SIZE):
        data = call_search(api_url, query=query, start=start, display=API_PAGE_SIZE, sort=sort)
        items = data.get("items", []) or []
        if not items:
            break
        for it in items:
            title_plain = strip_b_tags(it.get("title", "") or "")
            desc_plain  = strip_b_tags(it.get("description", "") or "")
            if (query in title_plain) or (query in desc_plain):  # 대소문자/공백 정확 일치
                matched.append(it)
                if len(matched) >= target_fetch:
                    break
        if len(matched) >= target_fetch:
            break
        if len(items) < API_PAGE_SIZE:
            break

    start_idx = (page_index - 1) * page_size
    end_idx = start_idx + page_size
    page_items = matched[start_idx:end_idx] if start_idx < len(matched) else []
    has_next = len(matched) > end_idx
    return page_items, has_next, len(matched)

# ---------- DataLab: Search Trend ----------
def call_datalab_search_trend(
    keywords: list[str],
    start_date: str,
    end_date: str,
    time_unit: str = "date",
    device: str | None = None,
    ages: list[str] | None = None,
    gender: str | None = None,
):
    headers = _auth_headers() | {"Content-Type": "application/json"}
    payload = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,
        "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in keywords if kw.strip()],
    }
    if device:
        payload["device"] = device
    if ages:
        payload["ages"] = ages
    if gender:
        payload["gender"] = gender

    resp = requests.post(API_DATALAB, headers=headers, data=json.dumps(payload), timeout=20)
    if resp.status_code != 200:
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text}
        st.error(f"[데이터랩] API 오류: HTTP {resp.status_code}\n\n{data}")
        st.stop()
    return resp.json()

def datalab_to_dataframe(data: dict) -> pd.DataFrame:
    """DataLab 응답을 period 행, 키워드별 ratio 열로 변환(빈 그룹 안전 처리)"""
    results = data.get("results", [])
    frames = []
    for group in results:
        name = group.get("title") or group.get("keyword") or group.get("groupName") or "keyword"
        points = group.get("data", []) or []
        if not points:
            continue
        df = pd.DataFrame(points)  # columns: period, ratio
        if "period" not in df.columns or "ratio" not in df.columns or df.empty:
            continue
        df = df.rename(columns={"ratio": name})
        frames.append(df[["period", name]])

    if not frames:
        return pd.DataFrame()

    base = frames[0]
    for f in frames[1:]:
        base = base.merge(f, on="period", how="outer")

    if "period" in base.columns:
        base = base.sort_values("period")
    return base.reset_index(drop=True)

# ---------- 공통 렌더 함수 ----------
def render_table(items: list[dict], highlighter, author_key: str, author_label: str, show_date_key: str | None = None):
    """
    items: 검색 결과 리스트
    author_key: 블로그=bloggername, 카페=cafename
    author_label: 열 라벨 ("블로거" 또는 "카페")
    show_date_key: 날짜 키(블로그=postdate, 카페는 None 권장)
    """
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
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">
    <a href="{url}" target="_blank">열기</a>
  </td>
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
        <th>제목</th>
        <th>요약</th>
        <th>{author_label}</th>
        <th>작성일</th>
        <th>URL</th>
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

def to_csv(items: list[dict], author_key: str, date_key: str | None = None) -> bytes:
    df = pd.DataFrame({
        "제목": [strip_b_tags(it.get("title","")) for it in items],
        "요약": [strip_b_tags(it.get("description","")) for it in items],
        author_key: [it.get(author_key,"") for it in items],
        "작성일": [it.get(date_key,"") if date_key else "" for it in items],
        "URL": [it.get("link","") for it in items],
    })
    return df.to_csv(index=False).encode("utf-8")

# ================== Streamlit App ==================
def main():
    st.set_page_config(page_title="NAVER 통합 검색 (블로그 / 카페글 / 데이터랩)", page_icon="🔎", layout="wide")
    st.title("🔎 NAVER 통합 검색 (블로그 / 카페글 / 데이터랩)")

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
        st.caption("검색 API/데이터랩 API의 호출 한도에 유의하세요.")

    # 공통 검색어
    query = st.text_input("공통 검색어", value="리뷰 자동화", placeholder="예: 세탁소 ERP, 이지짹, 일리 폼 생성기")

    # 영역 분리: 블로그 / 카페글 / 데이터랩
    tab_blog, tab_cafe, tab_trend = st.tabs(["블로그", "카페글", "데이터랩(검색어 트렌드)"])

    # 세션 상태(영역별 분리)
    for key, default in [
        ("blog_start", 1), ("blog_page", 1),
        ("cafe_start", 1), ("cafe_page", 1),
        ("did_first_load", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # 초회 자동 실행
    auto_run = query and not st.session_state.get("did_first_load")
    if auto_run:
        st.session_state.did_first_load = True

    # ---------- 블로그 영역 ----------
    with tab_blog:
        st.subheader("블로그 검색")
        bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 1])
        with bc1:
            blog_sort = st.selectbox("정렬", options=[("sim", "정확도순"), ("date", "날짜순")],
                                     index=0, format_func=lambda x: x[1], key="blog_sort")[0]
        with bc2:
            blog_page_size = st.number_input("한 페이지 결과(1~100)", 1, 100, DEFAULT_PAGE_SIZE, 1, key="blog_ps")
        with bc3:
            blog_exact = st.toggle("정확 일치 필터", value=True, key="blog_exact",
                                   help="제목/요약에 검색어가 그대로(띄어쓰기 포함) 존재하는 항목만 표시(대소문자 구분)")
        with bc4:
            do_blog = st.button("블로그 검색 실행", type="primary", key="blog_run") or auto_run

        if do_blog:
            st.session_state.blog_start = 1
            st.session_state.blog_page = 1

        if not query:
            st.info("검색어를 입력하세요.")
        else:
            highlighter = build_highlighter(query)

            # 데이터 로드
            if blog_exact:
                items, has_next, matched_cnt = fetch_filtered_page(
                    API_BLOG, query, blog_sort, int(blog_page_size), st.session_state.blog_page
                )
                info = f"필터 모드 · 정확 일치 누적 {matched_cnt:,}건(최대 1,000 범위) · {st.session_state.blog_page} 페이지"
                prev_disabled = st.session_state.blog_page <= 1
                next_disabled = not has_next
            else:
                data = call_search(API_BLOG, query, st.session_state.blog_start, int(blog_page_size), blog_sort)
                total = data.get("total", 0)
                start_now = data.get("start", st.session_state.blog_start)
                items = data.get("items", []) or []
                info = f"일반 모드 · API 총 {total:,}건 · 표시 범위 {start_now}~{min(start_now + int(blog_page_size) - 1, total):,}"
                prev_disabled = st.session_state.blog_start <= 1
                next_disabled = (st.session_state.blog_start + int(blog_page_size) > min(total, API_START_MAX))

            st.caption(info)
            render_table(items, highlighter, author_key="bloggername", author_label="블로거", show_date_key="postdate")

            # CSV
            if items:
                st.download_button("CSV 다운로드(블로그)", data=to_csv(items, author_key="블로거", date_key="postdate"),
                                   file_name="naver_blog_results.csv", mime="text/csv")

            # 페이지 네비
            l, m, r = st.columns(3)
            with l:
                if st.button("⬅ 이전", key="blog_prev", disabled=prev_disabled):
                    if blog_exact: st.session_state.blog_page = max(1, st.session_state.blog_page - 1)
                    else:          st.session_state.blog_start = max(1, st.session_state.blog_start - int(blog_page_size))
                    st.rerun()
            with m:
                if blog_exact: st.caption(f"필터 모드 · {st.session_state.blog_page} 페이지")
                else:          st.caption(f"일반 모드 · start={st.session_state.blog_start}")
            with r:
                if st.button("다음 ➡", key="blog_next", disabled=next_disabled):
                    if blog_exact: st.session_state.blog_page += 1
                    else:          st.session_state.blog_start += int(blog_page_size)
                    st.rerun()

    # ---------- 카페글 영역 ----------
    with tab_cafe:
        st.subheader("카페글 검색")
        cc1, cc2, cc3, cc4 = st.columns([1, 1, 1, 1])
        with cc1:
            cafe_sort = st.selectbox("정렬", options=[("sim", "정확도순"), ("date", "날짜순")],
                                     index=0, format_func=lambda x: x[1], key="cafe_sort")[0]
        with cc2:
            cafe_page_size = st.number_input("한 페이지 결과(1~100)", 1, 100, DEFAULT_PAGE_SIZE, 1, key="cafe_ps")
        with cc3:
            cafe_exact = st.toggle("정확 일치 필터", value=True, key="cafe_exact",
                                   help="제목/요약에 검색어가 그대로(띄어쓰기 포함) 존재하는 항목만 표시(대소문자 구분)")
        with cc4:
            do_cafe = st.button("카페글 검색 실행", type="primary", key="cafe_run") or auto_run

        if do_cafe:
            st.session_state.cafe_start = 1
            st.session_state.cafe_page = 1

        if not query:
            st.info("검색어를 입력하세요.")
        else:
            highlighter = build_highlighter(query)

            # 데이터 로드
            if cafe_exact:
                items, has_next, matched_cnt = fetch_filtered_page(
                    API_CAFE, query, cafe_sort, int(cafe_page_size), st.session_state.cafe_page
                )
                info = f"필터 모드 · 정확 일치 누적 {matched_cnt:,}건(최대 1,000 범위) · {st.session_state.cafe_page} 페이지"
                prev_disabled = st.session_state.cafe_page <= 1
                next_disabled = not has_next
            else:
                data = call_search(API_CAFE, query, st.session_state.cafe_start, int(cafe_page_size), cafe_sort)
                # 카페글은 total 제공되지만 postdate가 없을 수 있으니 날짜는 옵션 처리
                total = data.get("total", 0)
                start_now = data.get("start", st.session_state.cafe_start)
                items = data.get("items", []) or []
                info = f"일반 모드 · API 총 {total:,}건 · 표시 범위 {start_now}~{min(start_now + int(cafe_page_size) - 1, total):,}"
                prev_disabled = st.session_state.cafe_start <= 1
                next_disabled = (st.session_state.cafe_start + int(cafe_page_size) > min(total, API_START_MAX))

            st.caption(info)
            # 카페: author=cafename, 날짜(postdate)가 없으면 "-" 처리 (render_table에서 처리)
            render_table(items, highlighter, author_key="cafename", author_label="카페", show_date_key=None)

            # CSV
            if items:
                # CSV에서는 열 이름을 사람이 읽기 좋게 "카페"로 표기
                df = pd.DataFrame({
                    "제목": [strip_b_tags(it.get("title","")) for it in items],
                    "요약": [strip_b_tags(it.get("description","")) for it in items],
                    "카페": [it.get("cafename","") for it in items],
                    "작성일": ["" for _ in items],  # postdate 없을 가능성 높음
                    "URL": [it.get("link","") for it in items],
                })
                st.download_button("CSV 다운로드(카페글)", data=df.to_csv(index=False), file_name="naver_cafe_results.csv", mime="text/csv")

            # 페이지 네비
            l, m, r = st.columns(3)
            with l:
                if st.button("⬅ 이전", key="cafe_prev", disabled=prev_disabled):
                    if cafe_exact: st.session_state.cafe_page = max(1, st.session_state.cafe_page - 1)
                    else:          st.session_state.cafe_start = max(1, st.session_state.cafe_start - int(cafe_page_size))
                    st.rerun()
            with m:
                if cafe_exact: st.caption(f"필터 모드 · {st.session_state.cafe_page} 페이지")
                else:          st.caption(f"일반 모드 · start={st.session_state.cafe_start}")
            with r:
                if st.button("다음 ➡", key="cafe_next", disabled=next_disabled):
                    if cafe_exact: st.session_state.cafe_page += 1
                    else:          st.session_state.cafe_start += int(cafe_page_size)
                    st.rerun()

    # ---------- 데이터랩(검색어 트렌드) ----------
    with tab_trend:
        st.subheader("검색어 트렌드")
        tk1, tk2 = st.columns([2, 1])
        with tk1:
            trend_keywords_raw = st.text_input("트렌드 키워드들(쉼표로 분리) - 비우면 공통 검색어 사용", value="")
        with tk2:
            time_unit = st.selectbox("단위", options=["date", "week", "month"], index=0)

        today = dt.date.today()
        default_start = today - dt.timedelta(days=90)
        t1, t2, t3 = st.columns([1, 1, 1])
        with t1:
            start_date = st.date_input("시작일", value=default_start, max_value=today)
        with t2:
            end_date = st.date_input("종료일", value=today, max_value=today)
        with t3:
            device = st.selectbox("디바이스", options=["전체", "pc", "mo"], index=0)
        u1, u2 = st.columns([1, 1])
        with u1:
            gender = st.selectbox("성별", options=["전체", "m", "f"], index=0)
        with u2:
            ages = st.multiselect("연령대(복수 선택 가능)", options=["10","20","30","40","50","60"])

        run_trend = st.button("트렌드 조회 실행", type="primary", key="trend_run") or auto_run

        if run_trend:
            keywords = (
                [k.strip() for k in trend_keywords_raw.split(",") if k.strip()]
                if trend_keywords_raw.strip()
                else ([query.strip()] if query.strip() else [])
            )
            if not keywords:
                st.info("키워드를 입력하세요.")
            else:
                dl_device = None if device == "전체" else device
                dl_gender = None if gender == "전체" else gender
                start_str = start_date.strftime("%Y-%m-%d")
                end_str = end_date.strftime("%Y-%m-%d")

                dl_data = call_datalab_search_trend(
                    keywords=keywords,
                    start_date=start_str,
                    end_date=end_str,
                    time_unit=time_unit,
                    device=dl_device,
                    ages=ages if ages else None,
                    gender=dl_gender,
                )
                dl_df = datalab_to_dataframe(dl_data)

                if dl_df.empty or "period" not in dl_df.columns:
                    st.info("표시할 트렌드 데이터가 없습니다. (키워드/기간을 확인하세요)")
                else:
                    st.dataframe(dl_df, use_container_width=True, hide_index=True)
                    st.line_chart(dl_df.set_index("period"))

    st.caption("※ 블로그/카페: start ≤ 1000 제약 · 데이터랩: 기간/단위/조건 변경 시 ratio 스케일이 재설정됩니다.")
    
if __name__ == "__main__":
    main()
