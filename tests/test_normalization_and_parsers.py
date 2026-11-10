"""표기 정규화, 자연어 간이 해석, 원본 파서."""
from __future__ import annotations

import io

import openpyxl
import pytest
from replay import ReplayFetcher

from food_import import normalization as n
from food_import import parsers as p
from food_import.collectors import parse_attachments, parse_board_list

R = ReplayFetcher()


# ---------- 정규화 ----------

@pytest.mark.parametrize("name,code", [
    ("일본", "JP"), ("Japan", "JP"), ("jp", "JP"), ("베트남산", "VN"), ("Viet Nam", "VN"),
    ("노르웨이", "NO"), ("Norway", "NO"), ("페루", "PE"), ("중국", "CN"),
])
def test_country_aliases(name, code):
    assert n.country_code(name) == code


def test_unknown_country_is_none():
    assert n.country_code("아틀란티스") is None


@pytest.mark.parametrize("name,code", [
    ("홋카이도", "JP-01"), ("북해도", "JP-01"), ("Hokkaido", "JP-01"), ("홋카이도산", "JP-01"),
    ("후쿠시마현", "JP-07"), ("치바", "JP-12"), ("지바", "JP-12"), ("토치키", "JP-09"), ("도치기", "JP-09"),
    ("니이가타", "JP-15"), ("야마카타", "JP-06"), ("도쿄도", "JP-13"), ("나라현", "JP-29"),
])
def test_jp_region_aliases(name, code):
    assert n.jp_region_code(name) == code


def test_all_47_prefectures():
    assert len(n.all_jp_region_codes()) == 47


def test_generic_product_lists_candidates():
    m = n.match_product("새우")
    assert m.tag == "shrimp" and m.generic and m.candidates


def test_specific_product_is_not_generic():
    m = n.match_product("흰다리새우")
    assert m.tag == "shrimp" and not m.generic


def test_bluefin_beats_tuna():
    assert n.match_product("참다랑어").tag == "bluefin_tuna"
    assert n.match_product("참치").tag == "tuna"


@pytest.mark.parametrize("text,expect", [
    ("일본 홋카이도산 냉동 가리비 수입신고 서류 알려줘.",
     {"origin_country": "JP", "region": "JP-01", "product_name": "가리비", "state": "frozen", "product_type": "raw"}),
    ("베트남산 냉동 새우 수입할 때 위생증명서가 필요해?",
     {"origin_country": "VN", "product_name": "새우", "state": "frozen"}),
    ("중국산 냉동 오징어 수입 시 식약처 제출서류 알려줘.", {"origin_country": "CN", "product_name": "오징어"}),
    ("노르웨이산 냉동 연어 수입할 때 필요한 서류는?", {"origin_country": "NO", "product_name": "연어"}),
    ("일본 후쿠시마산 수산물 수입 가능한가?", {"origin_country": "JP", "region": "JP-07", "product_name": "수산물"}),
    ("페루산 수산물은 어떤 증명서가 필요해?", {"origin_country": "PE", "product_name": "수산물"}),
    ("일본산 조미 오징어", {"origin_country": "JP", "state": "seasoned", "product_type": "processed", "region": None}),
])
def test_parse_example_sentences(text, expect):
    q = n.parse_query_text(text)
    for k, v in expect.items():
        assert q[k] == v, (k, q)


def test_ambiguous_short_region_needs_suffix():
    # '나라'는 일반 낱말과 겹친다. '현'이 붙어야 지역으로 읽는다.
    assert n.parse_query_text("일본 어느 나라 수산물")["region"] is None
    assert n.parse_query_text("일본 나라현 수산물")["region"] == "JP-29"


def test_single_char_product_not_matched_inside_word():
    assert n.parse_query_text("어느 나라 게가 좋아")["product_name"] is None


def test_missing_values_stay_none():
    q = n.parse_query_text("수입 서류 알려줘")
    assert q["origin_country"] is None and q["product_name"] is None and q["product_type"] is None


# ---------- 게시판·첨부 ----------

def test_board_list_finds_required_docs_post():
    posts = parse_board_list(R.recorded("CFCII04F01?page=1").decode("utf-8"))
    target = next(x for x in posts if "구비서류 목록" in x["title"])
    assert target["date"] == "2026-08-04" and target["sn"] == "666431"
    assert any(a["name"].endswith(".xlsx") for a in target["attachments"])


def test_attachment_without_ecm_number_is_skipped():
    html = "<a href=\"javascript:fnAtchFileDown('https://www.foodsafetykorea.go.kr/upload/a.pdf', 'a.pdf', '가.pdf')\">x</a>"
    assert parse_attachments(html) == []


# ---------- 구비서류 목록 엑셀 ----------

def _xlsx() -> bytes:
    return R.recorded("/file/downloadFile")


def test_required_docs_xlsx_rows():
    c = p.parse_required_docs_xlsx(_xlsx())
    assert max(i["seq"] for i in c["items"]) == 19
    puffer = next(i for i in c["items"] if i["seq"] == 3)
    assert "복어" in puffer["product_text"] and "복어독 검사성적서" in puffer["certificate_text"]
    assert [i["sub"] for i in c["items"] if i["seq"] == 9] == [0, 1, 2, 3]   # 병합 셀 아래 행
    sci = next(i for i in c["items"] if i["seq"] == 19)
    assert "2026.7.1." in sci["note"] and "30행" in sci["location"]


def test_xlsx_structure_change_raises():
    """원본 파일 구조 변경: 머리글이 바뀌면 값을 추측하지 않고 실패한다."""
    wb = openpyxl.load_workbook(io.BytesIO(_xlsx()))
    wb.worksheets[0]["C3"] = "품목"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(p.ParseError):
        p.parse_required_docs_xlsx(buf.getvalue())


def test_not_an_xlsx_raises():
    with pytest.raises(p.ParseError):
        p.parse_required_docs_xlsx(b"<html>error page</html>")


# ---------- 일본산 식품 수입제한조치 ----------

def _rad() -> str:
    return R.recorded("radsafe.mfds.go.kr/CFQCC02F01").decode("utf-8")


def test_radsafe_seafood_regions():
    c = p.parse_radsafe_measures(_rad())
    sea = c["seafood"]
    names = lambda codes: {n.jp_region_name(x) for x in codes}  # noqa: E731
    assert names(sea["banned"]["regions"]) == {"후쿠시마", "군마", "도치기", "이바라키", "미야기", "이와테", "지바", "아오모리"}
    assert names(sea["test_and_origin"]["regions"]) == {"가나가와", "도쿄", "홋카이도", "에히메", "구마모토", "미에", "가고시마", "아이치"}
    assert len(sea["origin_only"]["regions"]) == 31
    assert c["seafood_unlisted_regions"] == []          # 47개 도도부현이 모두 어느 한 구분에 들어 있다
    assert "단순히 절단·가열·숙성·건조 또는 염장" in c["seafood_definition"]
    assert "추가 핵종" in c["additional_nuclide_text"]


def test_radsafe_count_mismatch_raises():
    """표기된 지역 수와 실제 나열된 지역 수가 다르면 실패한다."""
    broken = _rad().replace("가나가와, 도쿄도, 홋카이도, 에히메", "가나가와, 도쿄도, 에히메", 1)
    with pytest.raises(p.ParseError):
        p.parse_radsafe_measures(broken)


def test_radsafe_missing_section_raises():
    with pytest.raises(p.ParseError):
        p.parse_radsafe_measures("<html><div id=\"container\">점검 중</div></html>")


# ---------- 법령, 검사명령, 공지 ----------

def test_law_article_27_items():
    c = p.parse_law_article(R.recorded("lsInfoR.do").decode("utf-8"), "제27조(수입식품등의 수입신고)")
    assert c["law_name"] == "수입식품안전관리 특별법 시행규칙"
    assert c["effective_date"] >= "2026-09-30"
    assert "한글표시" in c["items"]["1"]
    assert "위생증명서 또는 검사증명서" in c["items"]["7"] and "수산물" in c["items"]["7"]
    assert "수입신고서" in c["paragraph1_intro"]


def test_law_article_missing_raises():
    with pytest.raises(p.ParseError):
        p.parse_law_article("<html>[시행 2026. 9. 30.] [총리령]</html>", "제27조(수입식품등의 수입신고)")


def test_inspection_orders_table():
    c = p.parse_inspection_orders(R.recorded("CFBEE01F01").decode("utf-8"))
    tuna = next(o for o in c["orders"] if "참다랑어" in o["product_text"])
    assert "수출국" in tuna["country_text"] and tuna["effective_from"] == "2021-03-31"
    assert "구비서류로 제출" in c["compliance_text"]


def test_hygiene_notice_effective_date():
    c = p.parse_hygiene_notice(R.recorded("cntntsSn=607441").decode("utf-8"))
    assert "스페인" in c["title"] and c["publication_date"] == "2025-06-17"
    assert c["effective_from"] == "2025-06-24" and c["effective_basis"] == "shipment"


@pytest.mark.parametrize("text,iso", [("2026.7.1. 선적분부터", "2026-07-01"), ("'25. 6.24.", "2025-06-24"),
                                      ("2026-08-04", "2026-08-04"), ("날짜 없음", None)])
def test_parse_kr_date(text, iso):
    assert p.parse_kr_date(text) == iso


# ---------- 행정규칙(고시), PDF 별표, 법률 조문, 게시판 목록 ----------

def _admrul_pages() -> list[dict]:
    """녹화한 고시 본문 응답들(요청 주소가 같아 manifest 를 직접 훑는다)."""
    from replay import FIXTURES
    return [p.parse_admin_rule((FIXTURES / e["file"]).read_text(encoding="utf-8", errors="replace"))
            for e in R.manifest.values() if e["url"].endswith("admRulLsInfoR.do")]


def test_admin_rule_parser_reads_header_articles_addenda():
    rules = {r["name"]: r for r in _admrul_pages()}
    assert len(rules) == 14
    cn = rules["대한민국과 중화인민공화국 간 수출·입 수산물 위생관리"]
    assert cn["effective_date"] == "2023-02-28" and "해양수산부고시 제2023-34호" in cn["promulgation"]
    scope = next(a for a in cn["articles"] if a["title"] == "적용대상")
    assert scope["no"] == "2" and "활수생(活水生)동물을 제외" in scope["text"]
    no = rules["대한민국과 노르웨이 간 수입수산물 식품안전에 관한 규정"]
    assert "선적일 기준" in no["addenda"]
    mfds = rules["수입식품등 신고 및 검사에 관한 규정"]
    art17 = next(a for a in mfds["articles"] if a["no"] == "17")
    assert "등록시설명" in p.paragraph_of(art17["text"], "⑤")
    assert any(t.startswith("[별표 2]") for t in mfds["annex_titles"])


def test_admin_rule_structure_change_raises():
    with pytest.raises(p.ParseError):
        p.parse_admin_rule("<html><body><p>점검 중입니다</p></body></html>")


def test_pdf_annex_text_and_loose_span_search():
    from replay import FIXTURES
    pdfs = [p.parse_pdf_text((FIXTURES / e["file"]).read_bytes())["pages"]
            for e in R.manifest.values() if "flDownload.do" in e["url"]]
    assert len(pdfs) == 2                                        # 식약처 고시 별표 2, 시행규칙 별표 9
    pages = next(x for x in pdfs if "관능검사" in x[0])
    annex9 = next(x for x in pdfs if "별표9" in x[0].replace(" ", ""))
    assert "외화획득용" in p.find_span(annex9, "1)「대외무역법 시행령」", "제외한다.")[0]
    quote, page = p.find_span(pages, "수입신고는 폐사된 양을", "선별하게 한 후 판정한다.")
    assert "검량서(Survey Report)" in quote and page >= 1
    assert p.find_span(pages, "이 문장은 별표에 없다", "끝") is None
    with pytest.raises(p.ParseError):
        p.parse_pdf_text(b"not a pdf")


def test_law_text_parser_splits_paragraphs():
    from replay import FIXTURES
    page = next((FIXTURES / e["file"]).read_text(encoding="utf-8", errors="replace") for e in R.manifest.values()
                if e["url"].endswith("lsInfoR.do") and "제5조(해외제조업소 등록)" in (FIXTURES / e["file"]).read_text(
                    encoding="utf-8", errors="replace"))
    c = p.parse_law_text(page, "제5조(해외제조업소 등록)")
    assert c["law_name"] == "수입식품안전관리 특별법" and c["effective_date"] == "2026-10-08"
    assert "수입신고 전까지" in c["paragraphs"]["①"] and "2년" in c["paragraphs"]["⑥"]
    assert "<개정" not in c["paragraphs"]["①"]
    with pytest.raises(p.ParseError):
        p.parse_law_text(page, "제999조(없는 조문)")


def test_fsk_board_parser():
    c = p.parse_fsk_board(R.recorded("boardList.do"))
    assert c["posts"][0]["date"] >= c["posts"][-1]["date"] and all(len(x["date"]) == 10 for x in c["posts"])
    assert any("수산물" in x["title"] for x in c["posts"])
    with pytest.raises(p.ParseError):
        p.parse_fsk_board(b"<html>error</html>")
    with pytest.raises(p.ParseError):
        p.parse_fsk_board(b'{"list": [{"ntctxt_no": "1", "titl": "x", "cret_dtm": "2026/11/05"}]}')


def test_board_post_and_fsk_post_parsers():
    from replay import FIXTURES
    posts = [(e["url"], (FIXTURES / e["file"]).read_text(encoding="utf-8", errors="replace")) for e in R.manifest.values()]
    fsk = next(t for u, t in posts if "boardDetail.do" in u)
    assert len(p.parse_fsk_post(fsk)) > 30
    with pytest.raises(p.ParseError):
        p.parse_fsk_post("<html><body>오류</body></html>")
    ban_list = next(t for u, t in posts if "CFCII03F01" in u)
    assert any("수입중단" in x["title"] for x in parse_board_list(ban_list))
