"""공식 출처에서 원본을 받는 수집기.

수집기는 원본 바이트와 메타데이터만 돌려준다. 해석은 parsers 가 한다.
로그인·CAPTCHA·접근 제한을 우회하지 않는다. 받지 못한 자료는 FetchError 로 알리고,
운영자가 파일을 직접 내려받아 manual-import 로 넣는다.

받은 내용은 신뢰할 수 없는 외부 입력이다. 문자열로만 다루고 실행하거나 지시로 해석하지 않는다.
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class FetchError(Exception):
    """원본을 받지 못했다."""


class Fetcher(Protocol):
    def get(self, url: str, data: dict[str, str] | None = None) -> bytes: ...


class HttpFetcher:
    """호스트별 요청 간격을 지키는 단순 HTTP 클라이언트."""

    def __init__(self, user_agent: str, min_interval: float = 1.5, timeout: float = 60, max_retries: int = 2):
        self.ua = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._last: dict[str, float] = {}

    def get(self, url: str, data: dict[str, str] | None = None) -> bytes:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise FetchError(f"https 주소만 받는다: {url}")
        # 한글이 든 경로·질의는 퍼센트 인코딩한다.
        url = urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe="/%"),
            urllib.parse.quote(parts.query, safe="=&%+"), "",
        ))
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            wait = self._last.get(parts.netloc, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": self.ua})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    content = resp.read()
                self._last[parts.netloc] = time.monotonic()
                return content
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self._last[parts.netloc] = time.monotonic()
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise FetchError(f"{url}: {type(last_err).__name__}: {last_err}")


@dataclass
class RawDoc:
    """수집한 원본 하나."""
    name: str
    content: bytes
    url: str
    kind: str                      # xlsx | html | pdf …
    title: str = ""
    publication_date: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def meta(self) -> dict[str, Any]:
        return {"url": self.url, "kind": self.kind, "title": self.title,
                "publication_date": self.publication_date, **self.extra}


# ---------- 수입식품정보마루 게시판 공통 ----------

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse_board_list(page: str) -> list[dict[str, Any]]:
    """게시판 목록 HTML → [{sn, title, date, attachments}]."""
    page = html.unescape(page)
    posts = []
    for block in page.split("fnCntntsDetail('")[1:]:
        sn = block.split("'", 1)[0]
        title = re.search(r"\)[^>]*>(.*?)</a>", block, re.S)
        date = re.search(r'class="reg_date"[^>]*>\s*(\d{4}-\d{2}-\d{2})', block)
        if not (sn.isdigit() and title):
            continue
        posts.append({"sn": sn, "title": _clean(title.group(1)), "date": date.group(1) if date else None,
                      "attachments": parse_attachments(block)})
    return posts


def parse_attachments(fragment: str) -> list[dict[str, str]]:
    """첨부 링크(gfnFileDownload / fnAtchFileDown) → [{name, url}].

    주소 조립 규칙은 사이트의 공개 스크립트(common.js 의 gfnFileDownload, 게시판 페이지의
    fnAtchFileDown)가 하는 것과 같다.
    """
    fragment = html.unescape(fragment)
    out: list[dict[str, str]] = []
    for m in re.finditer(r"gfnFileDownload\('([^']*)',\s*'([^']*)'[^)]*\)\s*;?[^>]*>(.*?)</a>", fragment, re.S):
        seq, serv, label = m.group(1), m.group(2), _clean(m.group(3))
        # 링크 글자에 파일 크기('20 kb')가 붙어 있으므로 확장자까지만 파일명으로 쓴다.
        fn = re.match(r"(.*?\.(?:xlsx|xls|pdf|hwpx|hwp|docx|doc|pptx|ppt|zip))\b", label, re.I)
        label = fn.group(1) if fn else label
        url = "https://impfood.mfds.go.kr/file/downloadFile?" + urllib.parse.urlencode(
            {"fileSeq": seq, "servFileName": serv})
        out.append({"name": label or serv, "url": url})
    for m in re.finditer(r"fnAtchFileDown\('([^']*)',\s*'([^']*)',\s*'([^']*)'\)", fragment):
        src, _serv, org = m.group(1), m.group(2), m.group(3)
        q = urllib.parse.urlsplit(src).query
        ecm = urllib.parse.parse_qs(q).get("ecm_file_no", [""])[0]
        if not ecm:
            continue
        url = "https://www.foodsafetykorea.go.kr/common/downloadAttchdFile.do?" + urllib.parse.urlencode(
            {"orgFileName": org, "ecm_file_no": ecm})
        out.append({"name": org, "url": url})
    seen, uniq = set(), []
    for a in out:
        if a["url"] not in seen:
            seen.add(a["url"])
            uniq.append(a)
    return uniq


def detail_url(base: str, sn: str) -> str:
    return base + "?" + urllib.parse.urlencode(
        {"page": "1", "limit": "10", "cntntsView": "list", "cntntsSn": sn})


# ---------- 출처별 수집 ----------

def collect_required_docs(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """증명서제출 게시판에서 가장 최근의 '구비서류 목록' 공지와 엑셀 첨부를 받는다."""
    board = fetcher.get(params["board_url"] + "?page=1&limit=30").decode("utf-8", "replace")
    posts = parse_board_list(board)
    if not posts:
        raise FetchError("증명서제출 게시판 목록을 읽지 못했다(페이지 구조 변경 가능).")
    target = next((p for p in posts if params["title_keyword"] in p["title"]), None)
    if target is None:
        raise FetchError(f"제목에 '{params['title_keyword']}' 이 든 게시물을 찾지 못했다.")
    durl = detail_url(params["detail_url"], target["sn"])
    detail = fetcher.get(durl)
    atts = parse_attachments(detail.decode("utf-8", "replace")) or target["attachments"]
    xl = next((a for a in atts if a["name"].lower().endswith(".xlsx") and params["attachment_keyword"] in a["name"]), None)
    if xl is None:
        raise FetchError("구비서류 목록 엑셀 첨부를 찾지 못했다: " + ", ".join(a["name"] for a in atts))
    common = {"post_sn": target["sn"], "post_title": target["title"], "post_url": durl}
    newer = [{"sn": p["sn"], "title": p["title"], "date": p["date"]} for p in posts
             if p["date"] and target["date"] and p["date"] > target["date"]]
    newer_docs = []
    for p in newer[: int(params.get("newer_body_limit", 5))]:       # 목록 공지 뒤에 올라온 게시물의 본문
        u = detail_url(params["detail_url"], p["sn"])
        newer_docs.append(RawDoc(name=f"post_{p['sn']}.html", content=fetcher.get(u), url=u, kind="html",
                                 title=p["title"], publication_date=p["date"], extra={"role": "newer", "sn": p["sn"]}))
    return newer_docs + [
        RawDoc(name=f"post_{target['sn']}.html", content=detail, url=durl, kind="html",
               title=target["title"], publication_date=target["date"], extra={**common, "role": "notice"}),
        RawDoc(name=xl["name"], content=fetcher.get(xl["url"]), url=xl["url"], kind="xlsx",
               title=target["title"], publication_date=target["date"],
               extra={**common, "role": "attachment", "newer_posts": newer}),
    ]


def collect_page(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    url = params["url"]
    return [RawDoc(name=url.rstrip("/").rsplit("/", 1)[-1] + ".html", content=fetcher.get(url), url=url, kind="html")]


def collect_law_article(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """국가법령정보센터 한글주소 → 현행 본문. 한글주소 페이지의 iframe 주소에서 lsiSeq, efYd 를 읽는다."""
    front = fetcher.get(params["law_address"]).decode("utf-8", "replace")
    m = re.search(r"lsiSeq=(\d+)[^\"']*?efYd=(\d{8})", html.unescape(front))
    if not m:
        raise FetchError("법령 본문 주소(lsiSeq, efYd)를 찾지 못했다(페이지 구조 변경 가능).")
    lsi, efyd = m.group(1), m.group(2)
    body = fetcher.get(params["body_url"], data={
        "lsiSeq": lsi, "efYd": efyd, "chrClsCd": "010202", "urlMode": "lsInfoP", "ancYnChk": "0"})
    view = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsi}&efYd={efyd}"
    docs = [RawDoc(name=f"law_{lsi}_{efyd}.html", content=body, url=view, kind="html",
                   extra={"lsiSeq": lsi, "efYd": efyd, "law_address": params["law_address"], "role": "body"})]
    page = html.unescape(body.decode("utf-8", "replace"))
    for annex in params.get("annexes", []):
        url = _annex_pdf_url(page, annex)
        if not url:
            raise FetchError(f"{params['law_address']} {annex} 의 PDF 링크를 찾지 못했다(페이지 구조 변경 가능).")
        docs.append(RawDoc(name=f"law_{lsi}_{efyd}_{annex.replace(' ', '')}.pdf", content=fetcher.get(url), url=url,
                           kind="pdf", title=annex, extra={"role": "annex", "annex": annex}))
    return docs


def _annex_pdf_url(page: str, annex: str) -> str | None:
    """본문 페이지에서 '[별표 N]' 제목 바로 뒤에 붙은 PDF 내려받기 링크."""
    for m in re.finditer(re.escape(f"[{annex}]"), page):
        link = re.search(r'href="flDownload\.do\?([^"]*)"[^>]*>\s*<img[^>]*alt="PDF', page[m.end(): m.end() + 2500])
        if link:
            q = urllib.parse.parse_qs(link.group(1))
            if q.get("flSeq"):
                return "https://www.law.go.kr/LSW/flDownload.do?" + urllib.parse.urlencode(
                    {"flSeq": q["flSeq"][0], **({"bylClsCd": q["bylClsCd"][0]} if q.get("bylClsCd") else {})})
    return None


def collect_notices(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """등록된 위생약정 공지 본문을 받고, 검색 결과에서 등록되지 않은 공지를 찾아 함께 알린다."""
    docs: list[RawDoc] = []
    known = {n["sn"] for n in params["notices"]}
    unregistered: list[dict[str, Any]] = []
    for kw in params.get("search_keywords", []):
        url = params["search_url"] + "?" + urllib.parse.urlencode(
            {"searchCondition": "all", "searchInpText": kw, "limit": "50"})
        for p in parse_board_list(fetcher.get(url).decode("utf-8", "replace")):
            if p["sn"] not in known and all(u["sn"] != p["sn"] for u in unregistered):
                unregistered.append({"sn": p["sn"], "title": p["title"], "date": p["date"]})
    for i, n in enumerate(params["notices"]):
        durl = detail_url(n["detail_url"], n["sn"])
        docs.append(RawDoc(name=f"notice_{n['sn']}.html", content=fetcher.get(durl), url=durl, kind="html",
                           extra={"country": n["country"], "sn": n["sn"],
                                  **({"unregistered_notices": unregistered} if i == 0 else {})}))
    # 등록되지 않은 수산물 공지(서식 변경, 전자증명서 등)는 본문을 받아 참고 공지로 싣는다. 규칙은 만들지 않는다.
    base = params["notices"][0]["detail_url"] if params["notices"] else None
    for u in [u for u in unregistered if "수산" in u["title"]][: int(params.get("reference_body_limit", 15))]:
        if not base:
            break
        durl = detail_url(base, u["sn"])
        docs.append(RawDoc(name=f"notice_{u['sn']}.html", content=fetcher.get(durl), url=durl, kind="html",
                           title=u["title"], publication_date=u["date"], extra={"sn": u["sn"], "reference": True}))
    return docs


def collect_law_text(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """법률 조문 본문(호 단위로 나누지 않는 조문). 받는 방법은 collect_law_article 과 같다."""
    return collect_law_article(fetcher, params)


def collect_admin_rules(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """국가법령정보센터 행정규칙(고시). 한글주소 → 현행 일련번호(admRulSeq) → 본문.

    한글주소(https://www.law.go.kr/행정규칙/<고시명>)는 현행본으로 연결되므로 개정되면 일련번호가 바뀐다.
    params.rules[].annexes 에 적은 별표는 본문 페이지의 PDF 내려받기 링크로 받는다.
    """
    docs: list[RawDoc] = []
    for r in params["rules"]:
        front_url = params["address_base"] + r["name"].replace(" ", "")
        front = html.unescape(fetcher.get(front_url).decode("utf-8", "replace"))
        m = re.search(r"admRulSeq=(\d+)", front)
        if not m:
            raise FetchError(f"행정규칙 일련번호를 찾지 못했다(고시명 변경·폐지 가능): {r['name']}")
        seq = m.group(1)
        body = fetcher.get(params["body_url"], data={"admRulSeq": seq, "chrClsCd": "010201", "urlMode": "admRulLsInfoP"})
        view = f"https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq={seq}"
        common = {"key": r["key"], "country": r.get("country"), "rule_name": r["name"], "admRulSeq": seq,
                  "address": front_url}
        docs.append(RawDoc(name=f"admrul_{r['key']}_{seq}.html", content=body, url=view, kind="html",
                           title=r["name"], extra={**common, "role": "body"}))
        page = html.unescape(body.decode("utf-8", "replace"))
        for annex in r.get("annexes", []):
            url = _annex_pdf_url(page, annex)
            if not url:
                raise FetchError(f"{r['name']} {annex} 의 PDF 링크를 찾지 못했다(페이지 구조 변경 가능).")
            docs.append(RawDoc(name=f"admrul_{r['key']}_{seq}_{annex.replace(' ', '')}.pdf", content=fetcher.get(url),
                               url=url, kind="pdf", title=f"{r['name']} {annex}",
                               extra={**common, "role": "annex", "annex": annex}))
    return docs


def collect_fsk_board(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """식품안전나라 '수입신고 및 검사' 게시판 목록(JSON). 요청 형식은 게시판 페이지가 보내는 것과 같다."""
    form = {"menu_no": params["menu_no"], "menu_grp": "MENU_NEW01", "bbs_no": params["bbs_no"],
            "ctgry_no": params["ctgry_no"], "ctgType": "CTG_TYPE01", "ctgry_type_cd": "CTG_TYPE01",
            "start_idx": "1", "show_cnt": str(params.get("count", 50)), "bbs_type_cd": "01",
            "nticmatr_yn": "N", "ans_yn": "N"}
    listing = fetcher.get(params["list_url"], data=form)
    docs = [RawDoc(name=f"board_{params['bbs_no']}_{params['ctgry_no']}.json", content=listing,
                   url=params["page_url"], kind="json", extra={"role": "list"})]
    # 제목에 지정한 낱말이 든 최근 게시물만 본문을 받는다.
    import datetime as _dt
    import json as _json
    try:
        rows = _json.loads(listing.decode("utf-8", "replace")).get("list", [])
    except ValueError:
        rows = []            # 형식 오류는 파서가 ParseError 로 알린다
    cutoff = (_dt.date.today() - _dt.timedelta(days=int(params.get("body_days", 180)))).strftime("%Y.%m.%d")
    kws = params.get("body_keywords")          # 없으면 품목을 가리지 않는다
    picked = [x for x in rows if (not kws or any(k in str(x.get("titl") or "") for k in kws))
              and str(x.get("cret_dtm") or "") >= cutoff][: int(params.get("body_limit", 12))]
    for x in picked:
        no = str(x.get("ntctxt_no"))
        url = params["detail_url"] + no
        docs.append(RawDoc(name=f"post_{no}.html", content=fetcher.get(url), url=url, kind="html",
                           extra={"role": "body", "no": no}))
    return docs


def collect_impfood_board(fetcher: Fetcher, params: dict[str, Any]) -> list[RawDoc]:
    """수입식품정보마루 게시판 목록(제목·게시일)만 받는다."""
    url = params["board_url"] + "?page=1&limit=" + str(params.get("limit", 50))
    return [RawDoc(name=params["board_url"].rsplit("/", 1)[-1] + "_list.html", content=fetcher.get(url), url=params["board_url"],
                   kind="html")]


COLLECTORS = {
    "lawgo_text": collect_law_text,
    "lawgo_admin_rules": collect_admin_rules,
    "fsk_board": collect_fsk_board,
    "impfood_board": collect_impfood_board,
    "impfood_required_docs": collect_required_docs,
    "impfood_page": collect_page,
    "radsafe_measures": collect_page,
    "lawgo_article": collect_law_article,
    "impfood_notices": collect_notices,
}
