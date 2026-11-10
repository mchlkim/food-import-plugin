"""판정 엔진(수산물·농산물·축산물·가공식품 등). 규칙은 공식 사이트 녹화본으로 만든 것이다(가상 규칙을 쓰는 테스트는 따로 표시)."""
from __future__ import annotations

import copy

from conftest import doc, docs_by_status

from food_import.rules.engine import Query, evaluate

TODAY = "2026-11-10"


def ask(svc, **f):
    f.setdefault("declaration_date", TODAY)
    r = svc.get_required_documents(**f)
    assert r["ok"], r
    return r


# ---------- 일본 ----------

def test_japan_banned_prefecture_is_prohibited(svc):
    r = ask(svc, origin_country="일본", region="후쿠시마", product_name="수산물")
    assert r["importability"]["status"] == "PROHIBITED"
    assert r["documents"] == []                                   # 금지 사실이 먼저, 서류 목록은 싣지 않는다
    p = r["importability"]["prohibitions"][0]
    assert p["rule_id"] == "JP-SEAFOOD-BAN" and "radsafe.mfds.go.kr" in p["source"]["url"]
    assert "수입금지" in r["markdown"]


def test_japan_all_eight_banned_prefectures(svc):
    for region in ["후쿠시마", "군마", "도치기", "지바", "이바라키", "미야기", "이와테", "아오모리"]:
        r = ask(svc, origin_country="JP", region=region, product_name="가리비", state="냉동")
        assert r["importability"]["status"] == "PROHIBITED", region


def test_japan_radiation_certificate_region(svc):
    r = ask(svc, origin_country="일본", region="홋카이도", product_name="가리비", state="냉동")
    assert r["importability"]["status"] == "NO_PROHIBITION_FOUND"
    req = docs_by_status(r, "REQUIRED")
    assert "방사능 검사증명서(세슘·요오드)" in req and "생산지증명서" in req
    d = doc(r, "JP-SEAFOOD-RADCERT")
    assert d["issuer"] == "일본 정부" and "홋카이도" in d["source"]["quote"]
    assert doc(r, "JP-ADDITIONAL-NUCLIDE")["status"] == "CONDITIONAL"


def test_japan_origin_certificate_only_region(svc):
    r = ask(svc, origin_country="일본", region="나가사키", product_name="방어", state="냉장")
    req = docs_by_status(r, "REQUIRED")
    assert "생산지증명서" in req and "방사능 검사증명서(세슘·요오드)" not in req
    assert doc(r, "JP-SEAFOOD-ORIGINCERT") is not None and doc(r, "JP-SEAFOOD-RADCERT") is None


def test_japan_region_missing_is_unknown_not_allowed(svc):
    r = ask(svc, origin_country="일본", product_name="참돔", state="활")
    assert r["importability"]["status"] == "UNKNOWN"
    assert any("생산지역" in m for m in r["missing_info"])
    assert "생산지증명서" in docs_by_status(r, "UNKNOWN")
    assert "생산지증명서" not in docs_by_status(r, "REQUIRED")


def test_japan_processed_food_is_judged_separately_from_raw(svc):
    """일본산 가공식품은 수산물 원물 금지 규칙이 아니라 가공식품 행(13개 도·현)으로 판정한다."""
    r = ask(svc, origin_country="일본", region="후쿠시마", product_name="오징어", state="조미")
    assert r["query"]["product_type"] == "processed"
    assert r["importability"]["status"] != "PROHIBITED"
    assert doc(r, "JP-PROCESSED-RADCERT")["status"] == "REQUIRED"
    assert any("원료 수산물의 생산지" in c for c in r["needs_confirmation"])
    r2 = ask(svc, origin_country="일본", region="홋카이도", product_name="오징어", state="조미")
    assert doc(r2, "JP-PROCESSED-ORIGINCERT")["status"] == "REQUIRED" and doc(r2, "JP-PROCESSED-RADCERT") is None


def test_japan_bluefin_tuna_inspection_order_uses_export_country(svc):
    r = ask(svc, origin_country="일본", region="나가사키", product_name="참다랑어", state="냉장")
    d = doc(r, "ORDER-03")
    assert d["status"] == "CONDITIONAL" and "살모넬라" in d["name"] and d["effective_to"] == "2027-03-30"


# ---------- 일본 외 ----------

def test_hygiene_agreement_country(svc):
    r = ask(svc, origin_country="노르웨이", product_name="연어", state="냉동")
    d = doc(r, "HYG-NO")
    assert d["status"] == "REQUIRED" and "위생증명서" in d["name"]
    assert "제27조제1항제7호" in d["legal_basis"][0]
    assert any("노르웨이 간 수입수산물" in b for b in d["legal_basis"])        # 약정 고시
    assert d["effective_from"] == "2021-09-23" and d["date_basis"] == "shipment"   # 부칙 적용례(선적일 기준)
    assert "적용 범위" in d["condition"] and "단순가공품" in d["condition"]       # 고시 제2조를 그대로 인용
    assert d["original_or_copy"].startswith("수출국 정부 발행 원본(부본")           # 식약처 고시 제9조
    assert "law.go.kr" in d["source"]["url"] and "제5조" in d["source"]["location"]


def test_hygiene_agreement_peru_generic_seafood(svc):
    r = ask(svc, text="페루산 수산물은 어떤 증명서가 필요해?")
    assert doc(r, "HYG-PE") is not None
    assert any("원물" in a for a in r["assumptions"])            # 가공 여부 미입력 → 원물로 가정했다고 밝힌다


def test_country_without_confirmed_agreement_is_unknown_not_not_required(svc):
    r = ask(svc, origin_country="인도", product_name="오징어", state="냉동")      # 약정 고시를 수록하지 않은 국가
    hyg = [d for d in r["documents"] if "위생증명서" in d["name"]]
    assert len(hyg) == 1 and hyg[0]["status"] == "UNKNOWN"
    assert "NOT_REQUIRED" not in {d["status"] for d in r["documents"]}
    assert "약정이 없다는 뜻은 아니다" in hyg[0]["reason"]


def test_hypothetical_exhaustive_list_allows_not_required(svc):
    """가상 규칙(테스트 전용): 약정국 목록이 한정 열거라고 표시된 경우에만 NOT_REQUIRED 가 나온다."""
    rs = copy.deepcopy(svc.store.load_ruleset())
    rs["coverage"]["hygiene"]["exhaustive"] = True            # 실제 데이터에서는 False 다
    q = Query(origin_country="IN", product_name="오징어", product_tag="squid", product_type="raw",
              product_class="seafood", category="seafood")
    r = evaluate(q, rs, today=TODAY)
    hyg = next(d for d in r["documents"] if "위생증명서" in d["name"])
    assert hyg["status"] == "NOT_REQUIRED" and "한정 열거" in hyg["reason"]


# ---------- 약정 고시(해양수산부·수품원 소관 포함) ----------

def test_all_twelve_agreement_countries_are_built_from_notices(svc):
    rs = svc.store.load_ruleset()
    assert rs["coverage"]["hygiene"]["confirmed_countries"] == sorted(
        ["AR", "CL", "CN", "EC", "ES", "ID", "NO", "PE", "PH", "RU", "TH", "VN"])
    assert rs["coverage"]["hygiene"]["exhaustive"] is False     # 12개국 밖의 국가는 여전히 UNKNOWN 이다
    for r in rs["rules"]:
        if r["topic"] == "hygiene":
            assert "law.go.kr" in r["source"]["url"] and r["scope_quote"] and r["effective_from"]


def test_china_frozen_squid_requires_certificate_and_mentions_wild_notice(svc):
    r = ask(svc, origin_country="중국", product_name="오징어", state="냉동")
    assert doc(r, "HYG-CN")["status"] == "REQUIRED"
    wild = doc(r, "HYG-CN-WILD")
    assert wild["status"] == "CONDITIONAL" and "야생 수산물" in wild["condition"]
    assert any(p["rule_id"] == "PRECHECK-ART17-5" for p in r["prechecks"])


def test_china_live_fish_is_outside_notice_scope(svc):
    r = ask(svc, origin_country="중국", product_name="넙치", state="활")
    d = doc(r, "HYG-CN")
    assert d["status"] == "UNKNOWN" and "적용대상에서 뺐다" in d["reason"]      # 고시 제2조: 활수생동물 제외
    assert doc(r, "ANNEX2-SURVEY-REPORT")["status"] == "CONDITIONAL"           # 활어 → 검량서(별표 2)


def test_russia_pollock_and_processed_product(svc):
    r = ask(svc, origin_country="러시아", product_name="명태", state="냉동")
    d = doc(r, "HYG-RU")
    assert d["status"] == "REQUIRED" and "수출검사증명서" in d["name"]
    p = ask(svc, origin_country="러시아", product_name="명태", product_type="processed")
    assert doc(p, "HYG-RU-PROCESSED")["status"] == "CONDITIONAL"               # 고시 제3조가 수산가공품을 포함


def test_processed_food_from_agreement_country_quotes_scope(svc):
    r = ask(svc, origin_country="노르웨이", product_name="연어", state="조미")
    hyg = next(d for d in r["documents"] if d["name"].startswith("수산물 위생증명서"))
    assert hyg["status"] == "UNKNOWN" and "적용 범위" in hyg["reason"]
    assert r["query"]["category"] == "processed" and r["query"]["product_class"] == "seafood"


def test_vietnam_shrimp_has_certificate_and_inspection_order(svc):
    r = ask(svc, origin_country="베트남", product_name="새우", state="냉동")
    assert doc(r, "HYG-VN")["status"] == "REQUIRED"
    assert doc(r, "ORDER-11")["status"] == "CONDITIONAL"


# ---------- 식약처 고시, 법 제5조, 검사강화 게시물 ----------

def test_raw_pufferfish_origin_certificate_from_annex(svc):
    r = ask(svc, origin_country="중국", product_name="참복", state="냉동")
    d = doc(r, "ANNEX2-PUFFER-RAW")
    assert d["status"] == "CONDITIONAL" and "공관장" in d["source"]["quote"] and "별표 2" in d["source"]["location"]
    assert doc(ask(svc, origin_country="중국", product_name="오징어", state="냉동"), "ANNEX2-PUFFER-RAW") is None


def test_optional_pufferfish_document_is_not_listed_for_generic_seafood(svc):
    r = ask(svc, text="페루산 수산물은 어떤 증명서가 필요해?")
    assert doc(r, "ANNEX2-PUFFER-RAW") is None                  # 품목을 모를 때 선택 서류를 미확인으로 늘어놓지 않는다
    p = ask(svc, origin_country="베트남", product_name="조미쥐치포")
    assert not any(x["rule_id"] == "PRECHECK-ART17-5" for x in p["prechecks"])   # 가공식품에는 붙이지 않는다


def test_survey_report_only_for_live_state(svc):
    assert doc(ask(svc, origin_country="칠레", product_name="홍합", state="냉동"), "ANNEX2-SURVEY-REPORT") is None
    assert doc(ask(svc, origin_country="칠레", product_name="홍합"), "ANNEX2-SURVEY-REPORT") is None   # 상태 미입력


def test_prechecks_foreign_facility_registration(svc):
    r = ask(svc, origin_country="인도", product_name="새우", state="냉동")
    p = next(x for x in r["prechecks"] if x["rule_id"] == "PRECHECK-FOREIGN-FACILITY")
    assert "수입신고 전까지" in p["quote"] and "2년" in p["quote"]
    assert not any(x["rule_id"] == "PRECHECK-ART17-5" for x in r["prechecks"])   # 약정국이 아니다
    assert "신고 전 확인사항" in r["markdown"]
    assert all(d["status"] != "REQUIRED" or "등록" not in d["name"] for d in r["documents"])   # 서류 목록에는 넣지 않는다


def test_conditional_documents_from_inspection_notice(svc):
    r = ask(svc, origin_country="인도", product_name="새우", state="냉동")
    assert doc(r, "GEN-NOTICE22-RETURNED")["status"] == "CONDITIONAL"
    assert "연구ㆍ조사계획서" in doc(r, "GEN-NOTICE23-RESEARCH")["name"]


def test_advisories_are_reference_only_and_filtered_by_country(svc):
    r = ask(svc, origin_country="일본", region="나가사키", product_name="새우", state="냉동")
    titles = [a["title"] for a in r["advisories"]["items"]]
    assert "일본산 냉동새우 검사강화" in titles and "겨울철 수입수산물 검사강화" in titles
    assert not any("튀르키예" in t or "피스타치오" in t or "김장" in t for t in titles)
    assert "제출서류 요건이 아니라" in r["advisories"]["note"]
    assert not any("검사강화" in d["name"] for d in r["documents"])
    rs = svc.store.load_ruleset()
    assert "fsk_inspection_advisories" not in rs["sources"]     # 게시물 제목은 규칙 묶음에 들어가지 않는다


def test_vietnam_shrimp_inspection_order_and_expiry(svc):
    r = ask(svc, origin_country="베트남", product_name="새우", state="냉동")
    d = doc(r, "ORDER-11")
    assert d["status"] == "CONDITIONAL" and "독시싸이클린" in d["name"]
    later = ask(svc, origin_country="베트남", product_name="새우", state="냉동", declaration_date="2027-07-01")
    assert doc(later, "ORDER-11") is None                       # 검사명령 기간(~2027-06-29) 이후


def test_country_specific_item_document(svc):
    r = ask(svc, origin_country="베트남", product_name="조미쥐치포")
    d = doc(r, "VN-FILEFISH-JERKY")
    assert d["status"] == "CONDITIONAL" and "NAFIQAD" in d["source"]["quote"]
    assert doc(ask(svc, origin_country="중국", product_name="쥐치포"), "VN-FILEFISH-JERKY") is None


def test_origin_and_export_country_are_not_confused(svc):
    r = ask(svc, origin_country="러시아", export_country="노르웨이", product_name="대구", state="냉동")
    assert doc(r, "HYG-NO") is not None                         # 수출국 정부 발행 증명서는 수출국 기준
    assert any("원산지(러시아)와 수출국(노르웨이)이 다르다" in c for c in r["needs_confirmation"])
    r2 = ask(svc, origin_country="노르웨이", export_country="중국", product_name="연어", state="냉동")
    assert doc(r2, "HYG-NO") is None


# ---------- 제품 유형 ----------

def test_states_frozen_chilled_live_are_raw(svc):
    for state in ["냉동", "냉장", "활", "건조", "염장"]:
        r = ask(svc, origin_country="페루", product_name="오징어", state=state)
        assert r["query"]["product_type"] == "raw", state
        assert doc(r, "HYG-PE") is not None


def test_shellfish_and_seaweed(svc):
    r = ask(svc, origin_country="일본", region="홋카이도", product_name="굴", state="냉동")
    assert r["query"]["product_category"] == "패류" and doc(r, "JP-SEAFOOD-RADCERT") is not None
    r = ask(svc, origin_country="일본", region="미야기", product_name="미역", state="건조")
    assert r["query"]["product_category"] == "해조류" and r["importability"]["status"] == "PROHIBITED"


def test_seasoned_processed_seafood_skips_raw_only_rules(svc):
    r = ask(svc, origin_country="노르웨이", product_name="연어", state="훈제")
    assert r["query"]["product_type"] == "processed"
    assert doc(r, "HYG-NO") is None and doc(r, "MFDS-LIST-SCIENTIFIC-NAME") is None
    hyg = next(d for d in r["documents"] if d["name"].startswith("수산물 위생증명서"))
    assert hyg["status"] == "UNKNOWN" and "가공식품" in hyg["reason"]


def test_pufferfish_processed_requires_three_documents(svc):
    r = ask(svc, origin_country="중국", product_name="복어", product_type="processed")
    d = doc(r, "MFDS-LIST-PUFFER-PROCESSED")
    assert d["status"] == "REQUIRED" and "복어유전자 검사성적서" in d["source"]["quote"]
    assert doc(ask(svc, origin_country="중국", product_name="복어", state="냉동"), "MFDS-LIST-PUFFER-PROCESSED") is None


def test_product_without_scientific_name(svc):
    r = ask(svc, origin_country="페루", product_name="오징어", state="냉동")
    assert r["query"]["scientific_name"] is None
    d = doc(r, "MFDS-LIST-SCIENTIFIC-NAME")
    assert d["status"] == "CONDITIONAL" and "학명" in d["name"]


def test_common_name_covering_several_species(svc):
    """통칭(참치)은 어종 단위 규칙(참다랑어 검사명령)을 확정하지 않고 UNKNOWN 으로 둔다."""
    r = ask(svc, origin_country="일본", region="나가사키", product_name="참치", state="냉장")
    assert doc(r, "ORDER-03")["status"] == "UNKNOWN"
    assert any("여러 어종" in c for c in r["needs_confirmation"])


def test_unknown_product_name_is_reported(svc):
    r = ask(svc, origin_country="페루", product_name="xyz물고기", state="냉동")
    assert any("품목 사전에서 찾지 못했다" in c for c in r["needs_confirmation"])


# ---------- 날짜 ----------

def test_shipment_date_before_effective_date(svc):
    r = ask(svc, origin_country="페루", product_name="오징어", state="냉동", shipment_date="2026-06-01")
    assert doc(r, "MFDS-LIST-SCIENTIFIC-NAME") is None
    assert any("MFDS-LIST-SCIENTIFIC-NAME" in c and "시행일" in c for c in r["needs_confirmation"])


def test_declaration_date_before_recorded_law_version(svc):
    r = ask(svc, origin_country="페루", product_name="오징어", state="냉동", declaration_date="2025-01-10")
    assert doc(r, "GEN-ART27-1-LABEL") is None                  # 그 시점 조문은 수록되지 않았다
    assert any("그 시점의 규정은 수록되지 않아" in c for c in r["needs_confirmation"])


def test_future_declaration_date_warns(svc):
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동", declaration_date="2099-01-01")
    assert any("조회일 이후" in c for c in r["needs_confirmation"])


# ---------- 기본 서류, 근거, 오류 ----------

def test_general_documents_and_every_document_has_source(svc):
    r = ask(svc, origin_country="페루", product_name="오징어", state="냉동")
    req = docs_by_status(r, "REQUIRED")
    assert any("수입신고서" in x for x in req) and any("한글표시" in x for x in req)
    for d in r["documents"]:
        if d["status"] in ("REQUIRED", "CONDITIONAL"):
            assert d["source"]["url"].startswith("https://") and d["source"]["location"] and d["source"]["quote"]
            assert d["legal_basis"] and d["submit_to"]
    assert r["data_basis"]["rule_version"] == 1 and r["data_basis"]["verification"] == "source_quoted"
    assert any("관세청" in c for c in r["needs_confirmation"])   # 식약처 서류와 타 기관 요건을 구분해 알린다


def test_hypothetical_conflicting_rules_block_conclusion(svc):
    """가상 규칙(테스트 전용): 같은 지역에 금지와 증명서 규칙이 함께 걸리면 결론을 내지 않는다."""
    rs = copy.deepcopy(svc.store.load_ruleset())
    ban = next(r for r in rs["rules"] if r["id"] == "JP-SEAFOOD-BAN")
    ban["when"]["region_in"].append("JP-01")                  # 가상: 홋카이도를 금지 목록에도 넣는다
    q = Query(origin_country="JP", region="JP-01", product_name="가리비", product_tag="scallop", product_type="raw",
              product_class="seafood", category="seafood")
    r = evaluate(q, rs, today=TODAY)
    assert r["importability"]["status"] == "UNKNOWN"
    assert r["conflicts"] and set(r["conflicts"][0]["rule_ids"]) == {"JP-SEAFOOD-BAN", "JP-SEAFOOD-RADCERT"}


def test_no_approved_rules(empty_svc):
    r = empty_svc.get_required_documents(origin_country="일본", product_name="가리비")
    assert not r["ok"] and r["error"]["code"] == "NO_APPROVED_RULES"


def test_invalid_inputs(svc):
    assert svc.get_required_documents(origin_country="아틀란티스", product_name="가리비")["error"]["code"] == "INVALID_INPUT"
    assert svc.get_required_documents(origin_country="일본", region="서울", product_name="가리비")["error"]["code"] == "INVALID_INPUT"
    assert svc.get_required_documents(origin_country="페루", product_name="오징어",
                                      declaration_date="2026/11/10")["error"]["code"] == "INVALID_INPUT"


def test_missing_country_asks_for_it(svc):
    r = svc.get_required_documents(product_name="가리비", state="냉동")
    assert r["ok"] and r["importability"]["status"] == "UNKNOWN" and "원산지 국가" in r["missing_info"]


# ---------- 다른 도구 ----------

def test_check_import_restrictions(svc):
    r = svc.check_import_restrictions(origin_country="일본", region="이바라키", product_name="고등어", state="냉동")
    assert r["ok"] and r["importability"]["status"] == "PROHIBITED" and "documents" not in r


def test_hygiene_tool_lists_confirmed_agreements(svc):
    r = svc.get_hygiene_certificate_requirements("스페인", product_name="오징어")
    assert r["ok"] and r["certificates"][0]["rule_id"] == "HYG-ES"
    assert len({a["country"] for a in r["confirmed_agreements"]}) == 12
    es = next(a for a in r["confirmed_agreements"] if a["country"] == "ES")
    assert es["effective_from"] == "2025-06-24" and es["related_notices"]        # 식약처 공지를 함께 싣는다
    assert r["coverage"]["exhaustive"] is False
    assert any("스페인 수산물 위생증명서 추가 변경사항" in c for c in r["needs_confirmation"])


def test_search_regulations_returns_location_and_url(svc):
    r = svc.search_regulations("복어 검사성적서")
    assert r["ok"] and r["count"] >= 1
    top = r["results"][0]
    assert "연번 3" in top["location"] and top["url"].startswith("https://impfood.mfds.go.kr")
    assert svc.search_regulations("  ")["error"]["code"] == "INVALID_INPUT"
    assert svc.search_regulations("존재하지않는낱말zzz")["count"] == 0


def test_query_is_logged_with_rule_version(svc):
    ask(svc, origin_country="페루", product_name="오징어", state="냉동")
    log = (svc.store.dir / "history" / "queries.jsonl").read_text(encoding="utf-8")
    assert '"rule_version": 1' in log


# ---------- 조문 원문 조건, 별표 9, 참고 게시물 본문 ----------

def test_conditions_from_article_27_are_verbatim_with_annex_9(svc):
    r = ask(svc, origin_country="인도", product_name="새우", state="냉동")
    photo = doc(r, "GEN-ART27-1-2-PHOTO")
    assert photo["condition_verbatim"] is True
    assert "천연색 사진" in photo["condition"]                    # 제1호의2 원문
    assert "별표 9 제2호가목1) 원문" in photo["condition"] and "외화획득용" in photo["condition"]
    assert "연구ㆍ조사에" in photo["condition"]
    lab = doc(r, "GEN-ART27-2-FOREIGN-LAB")
    assert "별표 9 제2호다목 원문" in lab["condition"] and "미생물학적" in lab["condition"]
    assert "조건(조문 원문)" in r["markdown"]
    sci = doc(r, "MFDS-LIST-SCIENTIFIC-NAME")
    assert sci["condition_verbatim"] is False and "조건(요약)" in r["markdown"] and "원문:" in r["markdown"]


def test_advisory_bodies_and_import_ban_titles(svc):
    r = ask(svc, origin_country="일본", region="나가사키", product_name="새우", state="냉동")
    shrimp = next(a for a in r["advisories"]["items"] if a["title"] == "일본산 냉동새우 검사강화")
    assert shrimp.get("body") and "검사" in shrimp["body"]
    assert any("일본산 수입식품 잠정 수입중단" == b["title"] for b in r["import_bans"]["items"])
    assert all("일본" in b["title"] for b in r["import_bans"]["items"])
    rs = svc.store.load_ruleset()
    assert "mfds_import_bans" not in rs["sources"]


def test_reference_notice_bodies_for_country(svc):
    r = ask(svc, origin_country="스페인", product_name="오징어", state="냉동")
    titles = [n["title"] for n in r["reference_notices"]["items"]]
    assert any("스페인 수산물 위생증명서 추가 변경사항" in t for t in titles)
    assert all(n["body"] for n in r["reference_notices"]["items"])
    assert "관련 식약처 공지" in r["markdown"]
    other = ask(svc, origin_country="인도", product_name="새우", state="냉동")
    assert other["reference_notices"]["items"] == []


# ====================================================================== 수산물 외 식품 유형

def nd(r):
    """기본 서류(GEN-)를 뺀 (상태, 규칙 ID) 목록."""
    return [(d["status"], d.get("rule_id")) for d in r["documents"] if not (d.get("rule_id") or "").startswith("GEN-")]


# ---------- 축산물 ----------

def test_livestock_requires_export_certificate_and_importability_is_unknown(svc):
    r = ask(svc, origin_country="미국", product_name="소고기", state="냉동")
    assert r["query"]["category"] == "livestock"
    d = doc(r, "LIV-EXPORT-CERT")
    assert d["status"] == "REQUIRED" and "제27조제1항제8호" in d["legal_basis"][0]
    assert d["original_or_copy"].startswith("수출국 정부 발행 원본") and "축산물 또는 동물성 식품" in d["condition"]
    assert r["importability"]["status"] == "UNKNOWN" and "수입위생요건" in r["importability"]["summary"]
    ids = [p["rule_id"] for p in r["prechecks"]]
    assert "GATE-LIVESTOCK-ALLOWED" in ids and "PRECHECK-LIVESTOCK-ESTABLISHMENT" in ids
    assert "PRECHECK-FOREIGN-FACILITY" not in ids               # 해외제조업소 정의에서 축산물은 빠진다(법 제2조제3호)
    assert not any("수산물 위생증명서" in x["name"] for x in r["documents"])


def test_japan_meat_is_prohibited_but_cheese_needs_certificates(svc):
    beef = ask(svc, origin_country="일본", region="홋카이도", product_name="소고기")
    assert beef["importability"]["status"] == "PROHIBITED"
    assert beef["importability"]["prohibitions"][0]["rule_id"] == "JP-MEAT-MILK-BAN"
    assert "육류 및 원유" in beef["importability"]["prohibitions"][0]["reason"]
    cheese = ask(svc, origin_country="일본", region="홋카이도", product_name="치즈")
    assert doc(cheese, "JP-LIVESTOCK-ORIGINCERT")["status"] == "REQUIRED"
    assert doc(cheese, "LIV-EXPORT-CERT")["status"] == "REQUIRED"
    assert doc(ask(svc, origin_country="일본", region="도쿄", product_name="치즈"), "JP-LIVESTOCK-RADCERT")["status"] == "REQUIRED"


def test_japan_livestock_generic_cannot_rule_out_ban(svc):
    r = ask(svc, origin_country="일본", region="홋카이도", product_name="축산물")
    assert r["importability"]["status"] == "UNKNOWN" and "구체적인 품목명" in r["missing_info"]


# ---------- 농산물 ----------

def test_japan_agricultural_bans_by_prefecture_and_item(svc):
    assert ask(svc, origin_country="일본", region="후쿠시마", product_name="쌀")["importability"]["status"] == "PROHIBITED"
    mush = ask(svc, origin_country="일본", region="나가노", product_name="표고버섯")     # 원문: 버섯류
    assert mush["importability"]["prohibitions"][0]["rule_id"] == "JP-AGRI-BAN-JP-20"
    tea = ask(svc, origin_country="일본", region="가나가와", product_name="차")            # 원문: 차(茶)
    assert tea["importability"]["status"] == "PROHIBITED"
    rice = ask(svc, origin_country="일본", region="니가타", product_name="쌀")             # 니가타는 오가피만 금지
    assert rice["importability"]["status"] == "NO_PROHIBITION_FOUND"
    assert doc(rice, "JP-PROCESSED-RADCERT")["status"] == "REQUIRED"                     # 13개 도·현 → 검사성적서


def test_japan_agricultural_without_region_is_unknown(svc):
    r = ask(svc, origin_country="일본", product_name="표고버섯")
    assert r["importability"]["status"] == "UNKNOWN" and any("생산지역" in m for m in r["missing_info"])


def test_unlisted_product_with_category_uses_its_name(svc):
    r = ask(svc, origin_country="일본", region="후쿠시마", product_name="사과", category="농산물")
    assert r["importability"]["status"] == "NO_PROHIBITION_FOUND"          # 후쿠시마 금지 품목 낱말에 사과가 없다
    assert doc(r, "JP-PROCESSED-RADCERT")["status"] == "REQUIRED"
    assert any("품목 사전에서 찾지 못했다" in c for c in r["needs_confirmation"])
    assert all(s != "UNKNOWN" for s, _ in nd(r))


def test_country_specific_agricultural_rows(svc):
    assert doc(ask(svc, origin_country="중국", product_name="다진마늘"), "CN-MINCED-GARLIC")["status"] == "REQUIRED"
    assert doc(ask(svc, origin_country="베트남", product_name="다진마늘"), "CN-MINCED-GARLIC") is None
    assert doc(ask(svc, origin_country="인도", product_name="향신료"), "ORDER-01")["status"] == "CONDITIONAL"
    assert doc(ask(svc, origin_country="중국", product_name="목이버섯"), "ORDER-06") is not None


# ---------- 가공식품, 원재료 ----------

def test_ingredient_rows_depend_on_ingredient_input(svc):
    yes = ask(svc, origin_country="베트남", product_name="과자", ingredients=["돼지고기"])
    assert doc(yes, "MFDS-LIST-ASF-PORK")["status"] == "REQUIRED"           # 베트남은 ASF 발생국 각주에 있다
    unknown = ask(svc, origin_country="베트남", product_name="과자")
    d = doc(unknown, "MFDS-LIST-ASF-PORK")
    assert d["status"] == "CONDITIONAL" and "원재료를 입력하지 않았다" in d["condition"]
    none = ask(svc, origin_country="베트남", product_name="과자", ingredients=[])
    assert doc(none, "MFDS-LIST-ASF-PORK") is None and doc(none, "MFDS-LIST-RUMINANT") is None
    other = ask(svc, origin_country="미국", product_name="과자", ingredients=["돼지고기"])
    assert doc(other, "MFDS-LIST-ASF-PORK") is None                         # 미국은 각주 목록에 없다


def test_gelatin_health_functional_food(svc):
    r = ask(svc, origin_country="독일", product_name="건강기능식품", ingredients=["젤라틴"])
    assert doc(r, "MFDS-LIST-GELATIN-ORIGIN")["status"] == "REQUIRED"
    bse = doc(r, "MFDS-LIST-BSE-GELATIN")
    assert bse["status"] == "CONDITIONAL" and "36개국" in bse["condition"]   # BSE 국가 목록이 없어 조건부로만 답한다


def test_product_specific_processed_rows(svc):
    assert doc(ask(svc, origin_country="뉴질랜드", product_name="꿀"), "NZ-HONEY")["status"] == "REQUIRED"
    assert doc(ask(svc, origin_country="호주", product_name="꿀"), "NZ-HONEY") is None
    assert doc(ask(svc, origin_country="중국", product_name="천일염"), "CN-SOLAR-SALT")["status"] == "REQUIRED"
    assert doc(ask(svc, origin_country="중국", product_name="천일염"), "MFDS-LIST-BULK-SOLAR-SALT")["status"] == "CONDITIONAL"
    assert doc(ask(svc, origin_country="미국", product_name="죽염"), "MFDS-LIST-BURNT-SALT")["status"] == "REQUIRED"


def test_apparatus_and_raw_products_skip_ingredient_rows(svc):
    pet = ask(svc, origin_country="중국", product_name="PET 도시락")
    assert nd(pet) == [("CONDITIONAL", "MFDS-LIST-PET-WARE")]
    raw = ask(svc, origin_country="페루", product_name="오징어", state="냉동")
    assert not any((rid or "").startswith(("MFDS-LIST-BSE", "MFDS-LIST-ASF", "LIV-")) for _, rid in nd(raw))


def test_non_seafood_never_gets_seafood_hygiene_certificate(svc):
    for f in (dict(origin_country="노르웨이", product_name="치즈"), dict(origin_country="중국", product_name="과자"),
              dict(origin_country="베트남", product_name="고추")):
        r = ask(svc, **f)
        assert not any((rid or "").startswith("HYG-") for _, rid in nd(r))
        assert not any("수산물 위생증명서" in d["name"] for d in r["documents"])
        assert not any(p["rule_id"] == "PRECHECK-ART17-5" for p in r["prechecks"])


def test_unknown_product_without_category_asks_for_food_class(svc):
    r = ask(svc, origin_country="태국", product_name="망고스틴")
    assert any(m.startswith("식품 유형") for m in r["missing_info"])
    assert nd(r) == []                                         # 유형별 규칙을 미확인으로 늘어놓지 않는다
    assert docs_by_status(r, "REQUIRED")                        # 기본 서류는 그대로 나온다


def test_all_required_docs_rows_are_bound(svc):
    rs = svc.store.load_ruleset()
    assert not any("규칙에 연결되지 않았다" in w for w in rs["warnings"])
    assert any("리트비아" in w for w in rs["warnings"])          # 원문 표기를 국가 사전에서 찾지 못한 이름은 경고로 남긴다
    asf = next(r for r in rs["rules"] if r["id"] == "MFDS-LIST-ASF-PORK")
    assert len(asf["when"]["origin_country"]) >= 70 and "CN" in asf["when"]["origin_country"]
    assert sum(r["id"].startswith("JP-AGRI-BAN-") for r in rs["rules"]) == 15
    assert sum(r["id"].startswith("ORDER-") for r in rs["rules"]) == 12


def test_natural_language_non_seafood(svc):
    r = ask(svc, text="호주산 소고기 수입할 때 필요한 서류")
    assert r["query"]["origin_country"] == "AU" and r["query"]["category"] == "livestock"
    r = ask(svc, text="일본 가나가와현 차 수입 가능한가?")
    assert r["query"]["region"] == "JP-14" and r["importability"]["status"] == "PROHIBITED"
