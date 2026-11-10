"""원본(엑셀, HTML)을 정규화된 문서 데이터로 바꾼다.

파서는 예상한 구조를 확인하고, 다르면 ParseError 를 낸다. 구조가 바뀐 원본에서 값을 추측해
뽑지 않는다. 각 항목에는 원본 위치(시트·행, 조문·호, 표 순번)를 남긴다.
"""
from __future__ import annotations

import datetime as dt
import html
import io
import re
from typing import Any

from ..normalization import all_jp_region_codes, jp_regions_in_text


class ParseError(Exception):
    """원본 구조가 예상과 다르다."""


def html_to_text(page: str, start_marker: str | None = None) -> str:
    if start_marker:
        i = page.find(start_marker)
        if i >= 0:
            page = page[i:]
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page)
    page = re.sub(r"(?s)<!--.*?-->", "", page)
    page = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in page.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _one_line(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip() if s is not None else ""


def parse_kr_date(text: str, default_century: int = 2000) -> str | None:
    """'2026.7.1.', \"'25. 6.24.\", '2026-08-04' → ISO 날짜."""
    m = re.search(r"(?<!\d)(?:(20\d{2})|['‘’`]?(\d{2}))\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", text)
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else default_century + int(m.group(2))
    try:
        return dt.date(year, int(m.group(3)), int(m.group(4))).isoformat()
    except ValueError:
        return None


# ---------- 구비서류 목록 엑셀 ----------

_XLSX_HEADERS = ["연번", "대상국가", "대상제품", "증명내용"]


def parse_required_docs_xlsx(content: bytes) -> dict[str, Any]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:  # 손상됐거나 엑셀이 아닌 파일
        raise ParseError(f"엑셀 파일을 열지 못했다: {e}") from e
    ws = wb.worksheets[0]
    header_row = None
    for row in ws.iter_rows(min_row=1, max_row=15):
        vals = [_one_line(c.value) for c in row[:4]]
        if vals == _XLSX_HEADERS:
            header_row = row[0].row
            break
    if header_row is None:
        raise ParseError(f"머리글 행({', '.join(_XLSX_HEADERS)})을 찾지 못했다. 서식이 바뀐 것으로 보인다.")

    items: list[dict[str, Any]] = []
    footnotes: list[dict[str, Any]] = []
    seq, country, sub = None, "", 0
    for row in ws.iter_rows(min_row=header_row + 1):
        a, b, c, d, e, f = ([_one_line(x.value) for x in row[:6]] + [""] * 6)[:6]
        r = row[0].row
        if not any([a, b, c, d, e, f]):
            continue
        if a:
            if not re.fullmatch(r"\d+", a):
                raise ParseError(f"{r}행 연번이 숫자가 아니다: {a!r}")
            seq, country, sub = int(a), b, 0
        elif b and not c and not d:
            footnotes.append({"seq": seq, "text": b, "location": f"시트 '{ws.title}' {r}행 B열"})
            continue
        elif seq is None:
            raise ParseError(f"{r}행: 연번 없이 내용이 시작됐다.")
        else:
            sub += 1
        if not c or not d:
            raise ParseError(f"{r}행(연번 {seq}): 대상제품 또는 증명내용이 비어 있다.")
        items.append({
            "seq": seq, "sub": sub, "countries_text": country, "product_text": c, "certificate_text": d,
            "original_or_copy": e, "note": f, "location": f"시트 '{ws.title}' {r}행 (연번 {seq})",
        })
    if not items:
        raise ParseError("머리글 아래에 항목이 없다.")
    top = _one_line(ws.cell(row=1, column=1).value)
    return {"sheet": ws.title, "preamble": top, "items": items, "footnotes": footnotes}


# ---------- 일본산 식품 수입제한조치 ----------

_RAD_SECTIONS = {"가공식품,축산물": "processed", "농산물": "agricultural", "수산물": "seafood"}


def parse_radsafe_measures(page: str) -> dict[str, Any]:
    text = html_to_text(page, 'id="container"')
    start = text.find("일본 정부가 발행한")
    end = text.find("통관단계", start)
    if start < 0 or end < 0:
        raise ParseError("'일본 정부가 발행한 증명서 첨부' 구간을 찾지 못했다.")
    lines = text[start:end].split("\n")

    sections: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] | None = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln in _RAD_SECTIONS and i + 1 < len(lines) and lines[i + 1] == "수입금지품목":
            cur = {"prohibited_text": "", "test_and_origin": None, "origin_only": None}
            sections[_RAD_SECTIONS[ln]] = cur
            j = i + 2
            buf = []
            while j < len(lines) and not lines[j].startswith("검사증명서"):
                buf.append(lines[j])
                j += 1
            cur["prohibited_text"] = " ".join(buf)
            i = j
            continue
        if cur is not None and ln.startswith(("검사증명서", "생산지증명서")):
            key = "test_and_origin" if ln.startswith("검사증명서") else "origin_only"
            m = re.match(r"(\d+)개 ?지역", lines[i + 1]) if i + 1 < len(lines) else None
            if not m:
                raise ParseError(f"'{ln}' 다음에 지역 수가 없다.")
            declared = int(m.group(1))
            j = i + 2
            buf = []
            while j < len(lines) and not lines[j].startswith(("검사증명서", "생산지증명서", "이전", "다음")) \
                    and lines[j] not in _RAD_SECTIONS:
                buf.append(lines[j])
                j += 1
            listed = " ".join(buf)
            codes = jp_regions_in_text(listed)
            if len(codes) != declared:
                raise ParseError(f"'{ln}' 지역 수 불일치: 표기 {declared}개, 인식 {len(codes)}개 ({listed[:80]}…)")
            cur[key] = {"declared_count": declared, "regions": codes, "text": listed}
            i = j
            continue
        i += 1

    sea = sections.get("seafood")
    if not sea or not sea["test_and_origin"] or not sea["origin_only"]:
        raise ParseError("수산물 구간(수입금지, 검사증명서+생산지증명서, 생산지증명서)을 읽지 못했다.")
    banned = jp_regions_in_text(sea["prohibited_text"])
    m = re.search(r"(\d+)개현", sea["prohibited_text"])
    if not m or int(m.group(1)) != len(banned):
        raise ParseError(f"수산물 수입금지 현 수 불일치: {sea['prohibited_text'][:80]}")
    sea["banned"] = {"declared_count": len(banned), "regions": banned, "text": sea["prohibited_text"]}
    everything = banned + sea["test_and_origin"]["regions"] + sea["origin_only"]["regions"]
    if len(set(everything)) != len(everything):
        dup = sorted({c for c in everything if everything.count(c) > 1})
        raise ParseError(f"수산물 구간에서 같은 지역이 두 구분에 들어 있다: {dup}")
    missing = [c for c in all_jp_region_codes() if c not in everything]

    # 농산물: 현별 수입금지 품목 ("일본 15개현 27개 품목 농산물" 아래에 현 이름 / 수입금지품목 / 품목 목록이 이어진다)
    all_lines = text.split("\n")
    agri_bans: list[dict[str, Any]] = []
    head = next((i for i, ln in enumerate(all_lines) if re.fullmatch(r"일본 \d+개현 \d+개 품목 농산물", ln)), None)
    if head is not None:
        declared = int(re.search(r"(\d+)개현", all_lines[head]).group(1))
        i = head + 1
        while i + 2 < len(all_lines) and all_lines[i + 1] == "수입금지품목":
            codes = jp_regions_in_text(all_lines[i])
            if len(codes) != 1:
                raise ParseError(f"농산물 수입금지 현 이름을 읽지 못했다: {all_lines[i]!r}")
            items = [x.strip() for x in all_lines[i + 2].split(",") if x.strip()]
            agri_bans.append({"region": codes[0], "region_text": all_lines[i], "items": items, "text": all_lines[i + 2]})
            i += 3
        if len(agri_bans) != declared:
            raise ParseError(f"농산물 수입금지 현 수 불일치: 표기 {declared}개현, 인식 {len(agri_bans)}개현")
    elif sections.get("agricultural"):
        raise ParseError("농산물 현별 수입금지 품목 구간을 찾지 못했다.")

    # 정의·추가 핵종 문구는 근거 인용에 쓴다.
    definition = re.search(r"수산물: 식품첨가물이나.*?수산동식물", text, re.S)
    scope = re.search(r"\(수입수산물 검사에 관한 규정.*?말함\)", text, re.S)
    add_start = text.find("검사결과 방사성 세슘")
    additional = _one_line(text[add_start:text.find("<CODEX", add_start)]) if add_start >= 0 else ""
    cert = re.search(r"수입신고시 방사성 세슘\(.*?생산지증명서 첨부", text, re.S)
    ban_date = "2013-09-09" if re.search(r"2013\s*9\.9\.\s*임시특별 조치 시행\s*8개현 모든 수산물 수입금지", _one_line(text)) else None
    return {
        "seafood": sea, "processed": sections.get("processed"), "agricultural": sections.get("agricultural"),
        "seafood_unlisted_regions": missing, "agricultural_bans": agri_bans,
        "seafood_definition": _one_line(definition.group(0)) if definition else "",
        "seafood_definition_scope": _one_line(scope.group(0)) if scope else "",
        "certificate_text": _one_line(cert.group(0)) if cert else "",
        "additional_nuclide_text": additional,
        "seafood_ban_effective_from": ban_date,
    }


# ---------- 법령 조문 ----------

def parse_law_article(page: str, article_heading: str) -> dict[str, Any]:
    text = html_to_text(page)
    head = re.search(r"\[시행 (\d{4})\. ?(\d{1,2})\. ?(\d{1,2})\.\]\s*\[([^\]]+)\]", text)
    if not head:
        raise ParseError("법령 머리말의 시행일을 찾지 못했다.")
    name = re.search(r"([^\n]+)\n\[시행 \d{4}", text)
    law_name = name.group(1).strip() if name else ""
    start = text.find(article_heading)
    if start < 0:
        raise ParseError(f"조문 '{article_heading}' 을 찾지 못했다.")
    nxt = re.search(r"\n제\d+조(의\d+)?\(", text[start + len(article_heading):])
    body = text[start: start + len(article_heading) + nxt.start()] if nxt else text[start:]
    para1 = body.split("\n②", 1)[0]
    flat = _one_line(para1)
    # 제1항 각 호: '1.', '1의2.', '2.' …
    marks = list(re.finditer(r"(?<![\d.])(\d{1,2}(?:의\d)?)\. (?=[가-힣「\[])", flat))
    items: dict[str, str] = {}
    expected = 1
    kept = []
    for m in marks:
        key = m.group(1)
        base = int(key.split("의")[0])
        if base in (expected, expected + 1) or (("의" in key) and base == expected):
            kept.append(m)
            expected = base
    for idx, m in enumerate(kept):
        end = kept[idx + 1].start() if idx + 1 < len(kept) else len(flat)
        items[m.group(1)] = flat[m.end():end].strip()
    if "1" not in items or len(items) < 8:
        raise ParseError(f"제1항 각 호를 나누지 못했다(인식 {len(items)}개).")
    return {
        "law_name": law_name,
        "effective_date": dt.date(int(head.group(1)), int(head.group(2)), int(head.group(3))).isoformat(),
        "promulgation": head.group(4),
        "article": article_heading,
        "paragraph1_intro": flat[: kept[0].start()].strip(),
        "items": items,
    }


# ---------- 검사명령 현황 ----------

def parse_inspection_orders(page: str) -> dict[str, Any]:
    text = html_to_text(page, 'id="container"')
    start = text.find("검사명령 대상 식품 현황")
    end = text.find("검사명령기간 종료 전", start)
    if start < 0 or end < 0:
        raise ParseError("'검사명령 대상 식품 현황' 표를 찾지 못했다.")
    lines = text[start:end].split("\n")
    labels = ["순번", "시행일자", "대상식품", "국가", "검사항목"]
    orders: list[dict[str, Any]] = []
    cur: dict[str, list[str]] | None = None
    field = None
    for ln in lines:
        if ln in labels:
            if ln == "순번":
                if cur and cur.get("시행일자"):
                    orders.append(cur)
                cur = {k: [] for k in labels}
            field = ln
            continue
        if cur is not None and field:
            cur[field].append(ln)
    if cur and cur.get("시행일자"):
        orders.append(cur)
    out = []
    for o in orders:
        if not o["순번"] or not re.fullmatch(r"\d+", o["순번"][0]):
            continue
        period = " ".join(o["시행일자"])
        m = re.search(r"['‘’](\d{2})\.(\d{1,2})\.(\d{1,2})\.?\s*~\s*['‘’](\d{2})\.(\d{1,2})\.(\d{1,2})", period)
        if not m:
            raise ParseError(f"검사명령 {o['순번'][0]}번 시행기간을 읽지 못했다: {period}")
        y1, mo1, d1, y2, mo2, d2 = (int(x) for x in m.groups())
        out.append({
            "seq": int(o["순번"][0]), "period_text": period,
            "effective_from": dt.date(2000 + y1, mo1, d1).isoformat(),
            "effective_to": dt.date(2000 + y2, mo2, d2).isoformat(),
            "product_text": " ".join(o["대상식품"]), "country_text": " ".join(o["국가"]),
            "test_items": " ".join(o["검사항목"]), "location": f"검사명령 대상 식품 현황 표 순번 {o['순번'][0]}",
        })
    if not out:
        raise ParseError("검사명령 현황 표에서 행을 읽지 못했다.")
    compliance = re.search(r"수입식품등 수입판매업자는[^\n]*구비서류로 제출[^\n]*", text)
    return {"orders": out, "compliance_text": _one_line(compliance.group(0)) if compliance else ""}


# ---------- 위생약정 공지 ----------

def parse_board_post(page: str) -> dict[str, Any]:
    """수입식품정보마루 게시물 → 제목, 게시일, 본문(수산물 여부를 따지지 않는다)."""
    n = parse_hygiene_notice(page, require_seafood=False)
    return {"title": n["title"], "publication_date": n["publication_date"], "body": n["body"]}


def parse_fsk_post(page: str) -> str:
    """식품안전나라 게시물 본문."""
    i = page.find('id="_post"')
    j = page.find("해당 정보를 무단으로", i)
    if i < 0 or j < 0:
        raise ParseError("식품안전나라 게시물 본문 구간을 찾지 못했다.")
    body = html_to_text(page[page.find(">", i) + 1: j]).split("첨부파일에 관한 표로")[0]
    return _one_line(body)


def parse_hygiene_notice(page: str, require_seafood: bool = True) -> dict[str, Any]:
    text = html_to_text(page, 'id="container"')
    lines = text.split("\n")
    date_idx = next((i for i, ln in enumerate(lines) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ln)), None)
    if date_idx is None or date_idx < 1:
        raise ParseError("공지 게시일을 찾지 못했다.")
    title_lines = [ln for ln in lines[1:date_idx] if ln != "-->"]
    if not title_lines:
        raise ParseError("공지 제목을 찾지 못했다.")
    title = title_lines[1] if len(title_lines) > 1 else title_lines[0]  # 첫 줄은 게시판 이름
    end = next((i for i, ln in enumerate(lines) if ln.startswith("해당 정보를 무단으로") or ln == "이전글"), len(lines))
    body = _one_line(" ".join(lines[date_idx + 1:end]))
    body = re.sub(r"\s+([.,)])", r"\1", re.sub(r"([(-])\s+", r"\1", body))
    body = re.sub(r"(?<=[가-힣]) -(?=[가-힣])", "-", body)
    if require_seafood and "수산물" not in (title + body):
        raise ParseError("수산물 관련 공지가 아니다.")
    eff = re.search(r"\(?\s*([‘’'`]?\d{2,4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2})\.?\s*선적분\s*부터", body + " " + title)
    return {
        "title": title, "publication_date": lines[date_idx], "body": body,
        "effective_from": parse_kr_date(eff.group(1)) if eff else None,
        "effective_basis": "shipment" if eff else None,
    }


# ---------- 법률 조문 본문(호 단위로 나누지 않는 조문) ----------

def _strip_links(page: str) -> str:
    """낱말 중간에 걸린 용어·조문 링크(<a>) 때문에 문장이 끊기지 않게 링크 태그만 없앤다."""
    return re.sub(r"(?is)</?a\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", "", page)


def parse_law_text(page: str, article_heading: str) -> dict[str, Any]:
    text = html_to_text(_strip_links(page))
    head = re.search(r"\[시행 (\d{4})\. ?(\d{1,2})\. ?(\d{1,2})\.\]\s*\[([^\]]+)\]", text)
    if not head:
        raise ParseError("법령 머리말의 시행일을 찾지 못했다.")
    lines = text[: head.start()].rstrip().split("\n")
    law_name = next((ln for ln in reversed(lines) if not ln.startswith("(") and "약칭" not in ln and ln != ")"), "")
    start = text.find(article_heading)
    if start < 0:
        raise ParseError(f"조문 '{article_heading}' 을 찾지 못했다.")
    nxt = re.search(r"\n제\d+조(의\d+)?\(", text[start + len(article_heading):])
    body = text[start + len(article_heading): start + len(article_heading) + nxt.start()] if nxt else text[start + len(article_heading):]
    body = re.sub(r"\s*\[(제목개정|본조신설|전문개정)[^\]]*\]", "", re.sub(r"\s*<(개정|신설)[^>]*>", "", _one_line(body)))
    paragraphs = {m.group(1): m.group(2).strip() for m in re.finditer(r"([①-⑳])\s*(.*?)(?=[①-⑳]|\[시행일|$)", body)}
    if not paragraphs:
        if len(body) < 20:
            raise ParseError(f"조문 '{article_heading}' 의 본문을 읽지 못했다.")
        paragraphs = {"본문": re.sub(r"\s*\[(제목개정|본조신설|전문개정)[^\]]*\]", "", body).strip()}   # 항이 없는 조문(정의 등)
    return {"law_name": law_name,
            "effective_date": dt.date(int(head.group(1)), int(head.group(2)), int(head.group(3))).isoformat(),
            "promulgation": head.group(4), "article": article_heading, "paragraphs": paragraphs}


# ---------- 행정규칙(고시) ----------

_JUNK = ('href="#AJAX"', 'src="/LSW', "체크박스", "조문목록")


def parse_admin_rule(page: str) -> dict[str, Any]:
    """국가법령정보센터 행정규칙 본문 → 머리말, 조문 목록, 부칙, 별표 제목."""
    lines = [ln for ln in html_to_text(_strip_links(page)).split("\n") if not any(j in ln for j in _JUNK)]
    hi = next((i for i, ln in enumerate(lines) if re.match(r"\[시행 \d{4}\.", ln)), None)
    if hi is None or hi < 1:
        raise ParseError("고시 머리말([시행 …] [… 고시 제…호])을 찾지 못했다.")
    head = re.match(r"\[시행 (\d{4})\. ?(\d{1,2})\. ?(\d{1,2})\.\]\s*\[([^\]]+)\]", lines[hi])
    if not head:
        raise ParseError(f"고시 머리말을 읽지 못했다: {lines[hi][:80]}")
    ai = next((i for i, ln in enumerate(lines) if re.fullmatch(r"부\s+칙", ln)), len(lines))
    art_re = re.compile(r"제(\d+)조(?:의(\d+))?\(([^)]*)\)")
    articles: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for ln in lines[hi + 1: ai]:
        m = art_re.fullmatch(ln)
        if m:
            cur = {"no": m.group(1) + (f"의{m.group(2)}" if m.group(2) else ""), "title": m.group(3), "lines": []}
            articles.append(cur)
        elif re.match(r"제\d+장 ", ln):
            cur = None
        elif cur is not None:
            cur["lines"].append(ln)
    for a in articles:
        a["text"] = re.sub(r"\s+([.,)」])", r"\1", re.sub(r"([(「])\s+", r"\1", _one_line(" ".join(a.pop("lines")))))
    if not articles:
        raise ParseError("고시 본문에서 조문을 찾지 못했다.")
    tail = lines[ai:]
    annexes = [ln for ln in tail if re.match(r"\[(별표|별지)", ln)]
    addenda = _one_line(" ".join(ln for ln in tail if not re.match(r"\[(별표|별지)", ln)))
    return {
        "name": lines[hi - 1],
        "effective_date": dt.date(int(head.group(1)), int(head.group(2)), int(head.group(3))).isoformat(),
        "promulgation": head.group(4), "department": next((ln for ln in lines[hi + 1: hi + 4] if not ln.startswith("[") and "조(" not in ln
                            and not ln.startswith("제")), ""),
        "articles": articles, "addenda": addenda, "annex_titles": annexes,
    }


def paragraph_of(article_text: str, mark: str) -> str | None:
    """조문 본문에서 ①, ② … 로 시작하는 한 항을 떼어 낸다."""
    m = re.search(re.escape(mark) + r"\s*(.*?)(?=[①-⑳]|$)", article_text)
    return m.group(1).strip() if m else None


def parse_pdf_text(content: bytes) -> dict[str, Any]:
    """PDF 별표 → 쪽별 글자. 글자를 뽑지 못하는 PDF(그림)면 ParseError."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as e:
        raise ParseError(f"PDF 를 읽지 못했다: {e}") from e
    if sum(len(p) for p in pages) < 200:
        raise ParseError("PDF 에서 글자를 뽑지 못했다(그림 PDF 로 보인다).")
    return {"page_count": len(pages), "pages": pages}


def find_span(pages: list[str], start: str, end: str, max_len: int = 700) -> tuple[str, int] | None:
    """PDF 글자에서 start 로 시작해 end 로 끝나는 구간과 쪽 번호. PDF 는 띄어쓰기가 불규칙해 공백을 무시하고 찾는다."""
    def loose(s: str) -> str:
        return r"\s*".join(re.escape(ch) for ch in s if not ch.isspace())
    for no, page in enumerate(pages, 1):
        m = re.search(loose(start) + r".{0," + str(max_len) + r"}?" + loose(end), page, re.S)
        if m:
            return _one_line(m.group(0)), no
    return None


# ---------- 식품안전나라 게시판 목록 ----------

def parse_fsk_board(content: bytes) -> dict[str, Any]:
    import json
    try:
        data = json.loads(content.decode("utf-8", "replace"))
        rows = data["list"]
    except (ValueError, KeyError, TypeError) as e:
        raise ParseError(f"게시판 목록(JSON) 형식이 예상과 다르다: {e}") from e
    posts = []
    for x in rows:
        no, title, date = str(x.get("ntctxt_no") or ""), _one_line(x.get("titl")), _one_line(x.get("cret_dtm"))
        m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", date)
        if not (no.isdigit() and title and m):
            raise ParseError(f"게시물 항목 형식이 예상과 다르다: {str(x)[:120]}")
        posts.append({"no": no, "title": title, "date": "-".join(m.groups())})
    if not posts:
        raise ParseError("게시판 목록이 비어 있다.")
    posts.sort(key=lambda p: (p["date"], p["no"]), reverse=True)
    return {"posts": posts}
