# app.py
import os
import re
import math
import html
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

API_URL = "https://openapi.naver.com/v1/search/blog.json"
API_PAGE_SIZE = 100          # 네이버 API 한 번에 가져올 최대 display
API_START_MAX = 1000         # 네이버 API start 최대
DEFAULT_PAGE_SIZE = 20       # 한 화면 표시 행 수

# ========== 자격증명 유틸 ==========
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

# ========== 문자열 유틸 ==========
def strip_b_tags(text: str) -> str:
    if not isinstance(text, str):
        return text
    return re.sub(r"</?b>", "", text)

# 네이버 API가 강조한 <b>를 안전하게 <mark>로 바꾸기
def emphasize_api_b(text: str) -> str:
    escaped = html.escape(text or "")
    return escaped.replace("&lt;b&gt;", "<mark>").replace("&lt;/b&gt;", "</mark>")

# 사용자 검색어로 추가 하이라이트 (2자 이상 토큰은 대소문자 무시 하이라이트)
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

# ========== API 호출 ==========
def call_api(query: str, start: int, display: int, sort: str):
    client_id, client_secret = get_credentials()
    if not client_id or not client_secret:
        st.error(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다.\n"
            "• 방법 A: 프로젝트 루트에 `.streamlit/secrets.toml` 생성\n"
            "• 방법 B: 환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 설정"
        )
        st.stop()

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "start": start, "display": display, "sort": sort}
    resp = requests.get(API_URL, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text}
        st.error(f"API 오류: HTTP {resp.status_code}\n\n{data}")
        st.stop()
    return resp.json()

# ========== 필터 모드: 필터 결과 기준 페이지 슬라이스 수집 ==========
def fetch_filtered_page(query: str, sort: str, page_size: int, page_index: int):
    """
    정확 일치 필터가 ON일 때 사용.
    - 제목/요약의 <b> 제거 후, 검색어(query) 그대로 포함한 항목만 매칭(대소문자/공백 정확 일치).
    - API를 100개 단위로 1→1000까지 순차 조회하며, 매칭 항목을 누적.
    - 요청 페이지(page_index)에 필요한 구간 [(p-1)*page_size, p*page_size) 만큼만 반환.
    - has_next: 다음 페이지에 표시할 매칭 항목이 존재하는지 여부
    - matched_count: 이번 조회에서 누적된 매칭 항목 수(최대 1000 범위)
    """
    target_end = page_index * page_size
    target_fetch = target_end + 1  # 다음 페이지 존재 여부 판단 위해 +1
    matched = []

    for start in range(1, API_START_MAX + 1, API_PAGE_SIZE):
        data = call_api(query=query, start=start, display=API_PAGE_SIZE, sort=sort)
        items = data.get("items", [])
        if not items:
            break

        for it in items:
            title_plain = strip_b_tags(it.get("title", "") or "")
            desc_plain  = strip_b_tags(it.get("description", "") or "")
            if (query in title_plain) or (query in desc_plain):
                matched.append(it)
                if len(matched) >= target_fetch:
                    break
        if len(matched) >= target_fetch:
            break
        # API가 요청 수보다 적게 반환 → 더 이상 없음
        if len(items) < API_PAGE_SIZE:
            break

    start_idx = (page_index - 1) * page_size
    end_idx = start_idx + page_size
    page_items = matched[start_idx:end_idx] if start_idx < len(matched) else []
    has_next = len(matched) > end_idx  # 하나라도 더 있으면 다음 페이지 존재
    return page_items, has_next, len(matched)

# ========== 메인 ==========
def main():
    st.set_page_config(page_title="NAVER 블로그 검색", page_icon="🔎", layout="wide")
    st.title("🔎 NAVER 블로그 검색 (Search API)")

    # 사이드바: 자격증명
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
        st.caption("참고: 검색 API 기본 호출 한도(애플리케이션 기준)는 25,000/일")

    # 검색 영역
    query = st.text_input("검색어 (UTF-8)", value="리뷰 자동화", placeholder="예: 세탁소 ERP, 이지짹, 일리 폼 생성기")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        sort = st.selectbox("정렬", options=[("sim", "정확도순"), ("date", "날짜순")],
                            index=0, format_func=lambda x: x[1])[0]
    with col2:
        page_size = st.number_input("한 페이지 결과 개수 (1~100)", min_value=1, max_value=100,
                                    value=DEFAULT_PAGE_SIZE, step=1)
    with col3:
        # ✅ 기본 ON
        exact_filter = st.toggle("정확 일치 필터", value=True,
                                 help="제목/요약에 입력한 검색어가 그대로(띄어쓰기 포함) 존재하는 항목만 표시합니다. (대소문자 구분)")

    # 페이지 상태
    if "start" not in st.session_state:
        st.session_state.start = 1  # 비필터 모드(API start)
    if "page" not in st.session_state:
        st.session_state.page = 1   # 필터 모드(클라이언트 페이지)

    # 검색 버튼 / 초회 자동 검색
    do_search = st.button("검색", type="primary") or (query and not st.session_state.get("did_first_load"))
    if do_search:
        # 검색 새로 시작 시 페이지 초기화
        st.session_state.start = 1
        st.session_state.page = 1
        st.session_state.did_first_load = True

    if not query:
        st.stop()

    # ========== 데이터 로딩 ==========
    highlighter = build_highlighter(query)

    if exact_filter:
        # ✅ 필터 모드: 필터링된 결과 기준 페이지 수집
        page_items, has_next, matched_count = fetch_filtered_page(
            query=query, sort=sort, page_size=int(page_size), page_index=st.session_state.page
        )
        # 안내 문구
        st.info(
            f"필터 모드 · 검색어 정확 일치 항목 누적 {matched_count:,}건(최대 1,000 범위 내) · "
            f"{st.session_state.page} 페이지 표시"
        )
        if not page_items:
            st.warning("표시할 결과가 없습니다. (정확 일치 필터를 끄거나 검색어를 조정해 보세요.)")
            st.stop()

        items = page_items
        # 페이지 네비게이션 (필터 결과 기준)
        prev_disabled = st.session_state.page <= 1
        next_disabled = not has_next

    else:
        # ⭕ 비필터 모드: API 기본 페이지네이션 사용
        data = call_api(query=query, start=st.session_state.start, display=int(page_size), sort=sort)
        total = data.get("total", 0)
        start_now = data.get("start", st.session_state.start)
        items = data.get("items", []) or []

        st.info(f"API 총 {total:,}건 · 이 페이지 표시 범위 {start_now}~{min(start_now + int(page_size) - 1, total):,}")

        if not items:
            st.warning("검색 결과가 없습니다.")
            st.stop()

        prev_disabled = (st.session_state.start <= 1)
        next_disabled = (st.session_state.start + int(page_size) > min(total, API_START_MAX))

    # ========== DF 구성(원문 <b> 유지 — 강조를 위해 strip하지 않음) ==========
    def to_row(item):
        return {
            "제목_raw": item.get("title", ""),         # <b> 포함 가능
            "요약_raw": item.get("description", ""),   # <b> 포함 가능
            "블로거": item.get("bloggername", ""),
            "작성일": item.get("postdate", ""),
            "URL": item.get("link", ""),
        }

    df = pd.DataFrame([to_row(it) for it in items])

    st.markdown("#### 결과")
    tab_table, tab_highlight = st.tabs(["표 보기(강조 포함)", "하이라이트 보기"])

    # ▶ 표 보기: HTML 테이블로 렌더(네이버 <b> + 사용자 하이라이트 → <mark>)
    with tab_table:
        rows_html = []
        for _, r in df.iterrows():
            title_html = highlighter(r["제목_raw"])
            desc_html  = highlighter(r["요약_raw"])
            blogger    = html.escape(r["블로거"] or "")
            date       = html.escape(r["작성일"] or "")
            url        = html.escape(r["URL"] or "")
            row = f"""
<tr>
  <td style="padding:8px 10px;vertical-align:top;min-width:240px;">
    <a href="{url}" target="_blank" style="text-decoration:none;">{title_html}</a>
  </td>
  <td style="padding:8px 10px;vertical-align:top;">{desc_html}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">{blogger}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">{date}</td>
  <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">
    <a href="{url}" target="_blank">열기</a>
  </td>
</tr>
"""
            rows_html.append(row)

        table_html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
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
        <th>블로거</th>
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
        # components.html로 렌더 → /tbody 노출 문제 해결
        rows_to_show = min(len(df), DEFAULT_PAGE_SIZE)
        table_height = int(34 * rows_to_show + 40 + 20)
        components.html(table_html, height=table_height + 200, scrolling=True)

        # CSV 다운로드(원문 텍스트 버전: <b> 제거하여 저장)
        df_csv = pd.DataFrame({
            "제목": [strip_b_tags(x) for x in df["제목_raw"]],
            "요약": [strip_b_tags(x) for x in df["요약_raw"]],
            "블로거": df["블로거"],
            "작성일": df["작성일"],
            "URL": df["URL"],
        })
        st.download_button("CSV 다운로드", data=df_csv.to_csv(index=False), file_name="naver_blog_results.csv", mime="text/csv")

    # ▶ 하이라이트 보기: 카드형
    with tab_highlight:
        for _, row in df.iterrows():
            title_html = highlighter(row["제목_raw"])
            desc_html  = highlighter(row["요약_raw"])
            blogger    = html.escape(row["블로거"] or "")
            date       = html.escape(row["작성일"] or "")
            url        = html.escape(row["URL"] or "")
            card = f"""
<div style="padding:12px 14px;border:1px solid #e5e7eb;border-radius:12px;margin-bottom:10px;">
  <div style="font-weight:700;font-size:1.02rem;line-height:1.35;margin-bottom:4px;">
    <a href="{url}" target="_blank" style="text-decoration:none;">{title_html}</a>
  </div>
  <div style="color:#374151;line-height:1.5;margin-bottom:8px;">{desc_html}</div>
  <div style="font-size:0.85rem;color:#6b7280;">
    블로거: {blogger} · 작성일: {date} · <a href="{url}" target="_blank">바로가기</a>
  </div>
</div>
"""
            components.html(f"<!doctype html><html><head><meta charset='utf-8'></head><body>{card}</body></html>", height=160)

    # ========== 페이지 이동 ==========
    left, mid, right = st.columns(3)
    with left:
        if st.button("⬅ 이전", disabled=prev_disabled):
            if exact_filter:
                st.session_state.page = max(1, st.session_state.page - 1)
            else:
                st.session_state.start = max(1, st.session_state.start - int(page_size))
            st.rerun()
    with mid:
        if exact_filter:
            st.caption(f"필터 모드 · {st.session_state.page} 페이지")
        else:
            st.caption(f"일반 모드 · start={st.session_state.start}")
    with right:
        if st.button("다음 ➡", disabled=next_disabled):
            if exact_filter:
                st.session_state.page = st.session_state.page + 1
            else:
                st.session_state.start = st.session_state.start + int(page_size)
            st.rerun()

if __name__ == "__main__":
    main()
