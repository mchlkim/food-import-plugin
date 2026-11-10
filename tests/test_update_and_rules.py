"""업데이트 절차, 규칙 검증·비교·버전, 데이터 품질."""
from __future__ import annotations

import copy
import datetime as dt
import io
import re

import openpyxl
from conftest import bootstrap, doc, make_service
from replay import ReplayFetcher

from food_import.collectors import FetchError
from food_import.rules import diff_rulesets, validate_ruleset

R = ReplayFetcher()


def _edit_xlsx(fn) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(R.recorded("/file/downloadFile")))
    fn(wb.worksheets[0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _hokkaido_moved_to_origin_only() -> bytes:
    """가상 변경(테스트 전용): 홋카이도를 '검사증명서+생산지증명서'에서 '생산지증명서'로 옮긴 페이지."""
    page = R.recorded("radsafe.mfds.go.kr/CFQCC02F01").decode("utf-8")
    i = page.rfind("가나가와, 도쿄도, 홋카이도, 에히메")
    assert i > 0
    head, tail = page[:i], page[i:]
    tail = tail.replace("가나가와, 도쿄도, 홋카이도, 에히메", "가나가와, 도쿄도, 에히메", 1)
    tail = tail.replace("가가와, 고치, 교토", "홋카이도, 가가와, 고치, 교토", 1)
    j = head.rfind("8개 지역")
    head = head[:j] + "7개 지역" + head[j + len("8개 지역"):]
    k = tail.find("31개 지역")
    tail = tail[:k] + "32개 지역" + tail[k + len("31개 지역"):]
    return (head + tail).encode("utf-8")


# ---------- 업데이트 ----------

def test_first_update_registers_pending_and_does_not_apply(tmp_path):
    svc = make_service(tmp_path)
    report = svc.check_regulation_updates()
    assert report["rules_status"] == "CHANGE_DETECTED" and report["applied"] is False
    assert all(s["status"] == "OK" for s in report["sources"] if s["status"] != "NOT_COLLECTED")
    assert svc.store.current_version() is None                  # 감지됐을 뿐 적용되지 않았다
    assert svc.get_required_documents(origin_country="일본", product_name="가리비")["error"]["code"] == "NO_APPROVED_RULES"
    assert len(svc.list_pending()["pending"]) == 1


def test_second_update_without_change(svc):
    report = svc.check_regulation_updates()
    assert report["rules_status"] == "NO_CHANGE" and report["pending_id"] is None
    assert all(s.get("content_changed") is False for s in report["sources"] if s["status"] == "OK")


def test_raw_files_are_preserved_byte_for_byte(svc):
    recs = [r for r in svc.store.raw_index() if r["name"].endswith(".xlsx")]
    assert recs and svc.store.raw_path(recs[0]).read_bytes() == R.recorded("/file/downloadFile")
    assert recs[0]["publication_date"] == "2026-08-04" and recs[0]["url"].startswith("https://impfood.mfds.go.kr/file/")


def test_download_failure_keeps_approved_rules(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    svc._fetcher = ReplayFetcher({"radsafe.mfds.go.kr": FetchError("연결 실패(테스트)")})
    report = svc.check_regulation_updates()
    rad = next(s for s in report["sources"] if s["source_id"] == "radsafe_japan_measures")
    assert rad["status"] == "FETCH_FAILED" and report["rules_status"] == "NO_CHANGE"
    assert svc.store.current_version() == 1
    r = svc.get_required_documents(origin_country="일본", region="후쿠시마", product_name="수산물")
    assert r["importability"]["status"] == "PROHIBITED"         # 기존 검증 버전으로 계속 판정
    assert any(w["code"] == "UPDATE_FAILED" for w in r["warnings"])
    assert "radsafe_japan_measures" in svc.get_source_status()["update_failures"]


def test_source_structure_change_keeps_approved_rules(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    broken = _edit_xlsx(lambda ws: ws.__setitem__("A3", "번호"))
    svc._fetcher = ReplayFetcher({"/file/downloadFile": broken})
    report = svc.check_regulation_updates()
    src = next(s for s in report["sources"] if s["source_id"] == "mfds_required_docs")
    assert src["status"] == "PARSE_FAILED" and "머리글" in src["error"]
    assert svc.store.current_version() == 1 and report["pending_id"] is None


def test_official_content_change_goes_to_pending_then_applies_on_approval(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    svc._fetcher = ReplayFetcher({"radsafe.mfds.go.kr": _hokkaido_moved_to_origin_only()})
    report = svc.check_regulation_updates()
    assert report["rules_status"] == "CHANGE_DETECTED"
    changed = {c["id"] for c in report["diff"]["changed"]}
    assert changed == {"JP-SEAFOOD-RADCERT", "JP-SEAFOOD-ORIGINCERT"}

    q = dict(origin_country="일본", region="홋카이도", product_name="가리비", state="냉동")
    before = svc.get_required_documents(**q)
    assert doc(before, "JP-SEAFOOD-RADCERT") is not None        # 승인 전에는 기존 규칙
    assert any(w["code"] == "PENDING_CHANGES" for w in before["warnings"])

    assert svc.approve(report["pending_id"], "검토자", "expert_reviewed", "가상 변경 승인")["version"] == 2
    after = svc.get_required_documents(**q)
    assert doc(after, "JP-SEAFOOD-RADCERT") is None and doc(after, "JP-SEAFOOD-ORIGINCERT") is not None
    assert after["data_basis"]["rule_version"] == 2 and after["data_basis"]["verification"] == "expert_reviewed"

    # 이전 규칙 조회와 복구
    assert [v["version"] for v in svc.versions()["versions"]] == [1, 2]
    assert svc.rollback(1, "운영자")["ok"]
    assert doc(svc.get_required_documents(**q), "JP-SEAFOOD-RADCERT") is not None
    events = [h["event"] for h in svc.history()["history"]]
    assert "approved" in events and "rollback" in events


def test_same_change_is_not_registered_twice(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    svc._fetcher = ReplayFetcher({"radsafe.mfds.go.kr": _hokkaido_moved_to_origin_only()})
    first = svc.check_regulation_updates()
    second = svc.check_regulation_updates()
    assert first["pending_id"] == second["pending_id"] and len(svc.list_pending()["pending"]) == 1


def test_effective_date_change_is_detected(tmp_path):
    """가상 변경(테스트 전용): 학명 자료 행의 적용 시작일이 바뀐 엑셀."""
    svc = make_service(tmp_path)
    bootstrap(svc)
    changed = _edit_xlsx(lambda ws: ws.__setitem__("F30", "2027.1.1. 선적분부터 적용"))
    svc._fetcher = ReplayFetcher({"/file/downloadFile": changed})
    report = svc.check_regulation_updates()
    c = next(x for x in report["diff"]["changed"] if x["id"] == "MFDS-LIST-SCIENTIFIC-NAME")
    assert c["detail"]["effective_from"] == {"before": "2026-07-01", "after": "2027-01-01"}


def test_changed_source_text_blocks_rule_build(tmp_path):
    """결합표가 기대한 낱말이 원문에서 사라지면 규칙 후보를 만들지 않고 기존 규칙을 유지한다."""
    svc = make_service(tmp_path)
    bootstrap(svc)
    changed = _edit_xlsx(lambda ws: ws.__setitem__("C6", "가상의 다른 품목을 원료로 가공한 식품"))
    svc._fetcher = ReplayFetcher({"/file/downloadFile": changed})
    report = svc.check_regulation_updates()
    assert report["rules_status"] == "BUILD_FAILED" and "복어" in report["error"]
    assert svc.store.current_version() == 1 and report["pending_id"] is None


def test_unbound_seafood_row_is_reported(tmp_path):
    """가상 변경(테스트 전용): 결합표에 없는 수산물 행이 추가되면 경고로 드러난다."""
    def add_row(ws):
        ws["A31"], ws["B31"], ws["C31"], ws["D31"], ws["E31"] = 20, "모든국가", "가상의 해조류 가공품", "가상의 증명서", "원본"
    svc = make_service(tmp_path)
    bootstrap(svc)
    svc._fetcher = ReplayFetcher({"/file/downloadFile": _edit_xlsx(add_row)})
    report = svc.check_regulation_updates()
    assert any("해조류" in w and "연결되지 않았다" in w for w in report["diff"]["warnings_added"])


# ---------- 최신성, 출처 상태 ----------

def test_stale_data_warning(svc):
    old = (dt.datetime.now().astimezone() - dt.timedelta(days=30)).isoformat(timespec="seconds")
    svc.store.set_source_state("mfds_required_docs", {"last_success": old})
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동")
    assert any(w["code"] == "STALE_DATA" and "mfds_required_docs" in w["message"] for w in r["warnings"])
    assert "STALE_DATA" in r["markdown"]


def test_unverified_api_is_not_collected_and_has_no_endpoint(svc):
    status = svc.get_source_status()
    api = next(s for s in status["sources"] if s["source_id"] == "data_go_kr_openapi")
    assert api["enabled"] is False and api["code"] == "SOURCE_UNVERIFIED" and api["status"] == "NOT_COLLECTED"
    cfg_src = svc.cfg.source("data_go_kr_openapi")
    assert "params" not in cfg_src                              # 확인되지 않은 엔드포인트를 적지 않는다
    assert not any("data.go.kr" in c or "openapi" in c.lower() for c in svc.fetcher.calls)


def test_source_status_fields(svc):
    st = svc.get_source_status()
    s = next(x for x in st["sources"] if x["source_id"] == "mfds_required_docs")
    assert s["status"] == "OK" and s["last_success"] and s["publication_date"] == "2026-08-04"
    assert s["effective_date"] == "2026-08-03" and "구비서류 목록" in s["latest_document"]
    assert st["rules"]["current_version"] == 1 and st["rules"]["rule_count"] >= 20
    assert st["pending_reviews"] == []


# ---------- 수동 반영 ----------

def test_manual_import_of_official_file(tmp_path):
    svc = make_service(tmp_path)
    svc._fetcher = ReplayFetcher({"/file/downloadFile": FetchError("첨부 다운로드 차단(테스트)")})
    assert svc.check_regulation_updates()["rules_status"] == "BUILD_FAILED"
    f = tmp_path / "수입신고시 제출하여야 하는 구비서류 목록(2026.8.3.현재).xlsx"
    f.write_bytes(R.recorded("/file/downloadFile"))
    res = svc.manual_import("mfds_required_docs", str(f), title="(공지) 구비서류 목록", publication_date="2026-08-04",
                            url="https://impfood.mfds.go.kr/CFCII04F01")
    assert res["ok"] and res["normalized"] and res["rules_status"] == "CHANGE_DETECTED"
    assert any(r.get("manual") for r in svc.store.raw_index())


def test_manual_import_bad_file_and_unparsed_source(svc, tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not an excel file")
    assert svc.manual_import("mfds_required_docs", str(bad))["error"]["code"] == "PARSE_FAILED"
    assert svc.store.current_version() == 1
    pdf = tmp_path / "약정고시.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    res = svc.manual_import("mfds_hygiene_agreement_full_list", str(pdf), title="가상 문서(테스트)")
    assert res["ok"] and res["normalized"] is False             # 원본만 보관, 규칙에는 반영되지 않는다
    assert svc.manual_import("없는출처", str(pdf))["error"]["code"] == "NOT_FOUND"


# ---------- 규칙 검증·비교 ----------

def test_approved_ruleset_is_valid(svc):
    v = svc.validate_rules()
    assert v["ok"] and v["errors"] == []


def test_validation_catches_duplicates_missing_source_and_overlap(svc):
    """가상 규칙(테스트 전용)으로 검증기가 잡아야 하는 오류를 만든다."""
    rs = copy.deepcopy(svc.store.load_ruleset())
    rules = rs["rules"]
    dup = copy.deepcopy(next(r for r in rules if r["id"] == "HYG-PE"))
    rules.append(dup)                                           # 같은 ID
    twin = copy.deepcopy(dup)
    twin["id"] = "HYG-PE-COPY"
    rules.append(twin)                                          # ID 만 다른 중복 규칙
    nosrc = copy.deepcopy(dup)
    nosrc["id"], nosrc["when"], nosrc["source"] = "FAKE-NOSOURCE", {"export_country": ["XX"]}, {}
    rules.append(nosrc)                                         # 출처 누락
    next(r for r in rules if r["id"] == "JP-SEAFOOD-BAN")["when"]["region_in"].append("JP-01")   # 지역 겹침
    order = next(r for r in rules if r["id"] == "ORDER-11")
    order["effective_from"], order["effective_to"] = "2028-01-01", "2027-01-01"                # 날짜 역전
    errors = "\n".join(validate_ruleset(rs)["errors"])
    for needle in ["같은 ID", "중복 규칙", "출처 항목 누락", "충돌: JP-SEAFOOD-BAN", "시행 시작일"]:
        assert needle in errors, needle


def test_invalid_pending_cannot_be_approved(svc):
    rs = copy.deepcopy(svc.store.load_ruleset())
    rs["rules"][0]["source"]["quote"] = ""
    pid = svc.store.save_pending(rs, {"summary": "가상(테스트)"})
    res = svc.approve(pid, "검토자")
    assert res["error"]["code"] == "VALIDATION_FAILED" and svc.store.current_version() == 1
    assert svc.approve("없는-id", "검토자")["error"]["code"] == "NOT_FOUND"
    assert svc.approve(pid, "검토자", verification="대충")["error"]["code"] == "INVALID_INPUT"


def test_diff_ignores_retrieval_time_but_sees_quote_change(svc):
    old = svc.store.load_ruleset()
    new = copy.deepcopy(old)
    for r in new["rules"]:
        r["source"]["retrieved_at"] = "2030-01-01T00:00:00+09:00"
    assert diff_rulesets(old, new)["has_changes"] is False      # 수집 시각만 다르면 변경이 아니다
    new["rules"][0]["source"]["quote"] += " (문구 변경)"
    d = diff_rulesets(old, new)
    assert d["has_changes"] and d["changed"][0]["fields"] == ["source"]


def test_every_rule_traces_to_an_official_source(svc):
    for r in svc.store.load_ruleset()["rules"]:
        assert re.match(r"https://(impfood\.mfds|radsafe\.mfds|www\.law)\.go\.kr/", r["source"]["url"]), r["id"]
        assert r["source"]["location"] and r["source"]["quote"] and r["source"]["retrieved_at"]


def test_mark_reviewed_creates_expert_reviewed_version_with_same_rules(svc):
    before = svc.store.load_ruleset()
    res = svc.mark_reviewed("실무자", "원문 대조 완료")
    assert res["ok"] and res["version"] == 2 and res["verification"] == "expert_reviewed"
    after = svc.store.load_ruleset()
    assert diff_rulesets(before, after)["has_changes"] is False
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동")
    assert r["data_basis"]["verification"] == "expert_reviewed" and "실무자 검토 완료" in r["markdown"]


# ---------- 약정 고시·식약처 고시 변경, 조회 전 최신 확인 ----------

def test_agreement_notice_wording_change_blocks_rule_build(tmp_path):
    """결합표가 기대한 낱말이 고시 조문에 없으면(고시 개정) 후보를 만들지 않고 승인본을 유지한다."""
    svc = make_service(tmp_path)
    bootstrap(svc)
    b = copy.deepcopy(svc.cfg.bindings)
    next(x for x in b["hygiene_agreements"] if x["key"] == "CN")["cert_must_contain"] = ["전자증명서만 인정"]
    svc.cfg.bindings = b
    report = svc.check_regulation_updates(offline=True)
    assert report["rules_status"] == "BUILD_FAILED" and "중화인민공화국" in report["error"]
    assert svc.store.current_version() == 1 and not svc.store.list_pending()


def test_hypothetical_agreement_effective_date_change_is_detected(tmp_path):
    """가상 데이터: 보관된 정규화 데이터에서 페루 고시의 시행일을 바꾸면 검토 대기로 올라간다."""
    svc = make_service(tmp_path)
    bootstrap(svc)
    docn = svc.store.load_normalized("law_hygiene_agreements")
    next(r for r in docn["content"]["rules"] if r["key"] == "PE")["effective_date"] = "2027-01-01"
    svc.store.save_normalized("law_hygiene_agreements", docn)
    report = svc.check_regulation_updates(offline=True)
    assert report["rules_status"] == "CHANGE_DETECTED"
    ch = next(c for c in report["diff"]["changed"] if c["id"] == "HYG-PE")
    assert ch["detail"]["effective_from"] == {"before": "2024-01-13", "after": "2027-01-01"}
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동", refresh=False)
    assert doc(r, "HYG-PE")["effective_from"] == "2024-01-13"   # 승인 전에는 기존 규칙
    assert any(w["code"] == "PENDING_CHANGES" for w in r["warnings"])


def test_advisory_board_change_does_not_create_pending(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    docn = svc.store.load_normalized("fsk_inspection_advisories")
    docn["content"]["posts"].insert(0, {"no": "9999999", "title": "가상: 중국산 냉동오징어 검사강화", "date": dt.date.today().isoformat()})
    svc.store.save_normalized("fsk_inspection_advisories", docn)
    assert svc.check_regulation_updates(offline=True)["rules_status"] == "NO_CHANGE"
    r = svc.get_required_documents(origin_country="중국", product_name="오징어", state="냉동", refresh=False)
    assert r["advisories"]["items"][0]["title"].startswith("가상:")


def test_query_checks_official_sources_when_last_check_is_old(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    calls = len(svc.fetcher.calls)
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동")
    assert r["freshness_check"]["checked"] is False and len(svc.fetcher.calls) == calls     # 방금 확인했으므로 접속 안 함
    svc.cfg.settings["freshness"]["auto_check_after_hours"] = 0                              # 매번 확인
    r = svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동")
    assert r["freshness_check"] == {"checked": True, "rules_status": "NO_CHANGE", "pending_id": None,
                                    "failed_sources": [], "message": "승인된 규칙과 내용이 같다."}
    assert len(svc.fetcher.calls) > calls and svc.store.current_version() == 1
    calls = len(svc.fetcher.calls)
    svc.get_required_documents(origin_country="페루", product_name="오징어", state="냉동", refresh=False)
    assert len(svc.fetcher.calls) == calls


def test_refresh_failure_still_answers_from_approved_rules(tmp_path):
    svc = make_service(tmp_path)
    bootstrap(svc)
    svc._fetcher = ReplayFetcher({"law.go.kr": FetchError("연결 실패(테스트)")})
    r = svc.get_required_documents(origin_country="중국", product_name="오징어", state="냉동", refresh=True)
    assert r["ok"] and doc(r, "HYG-CN")["status"] == "REQUIRED"
    assert "law_hygiene_agreements" in r["freshness_check"]["failed_sources"]
    assert any(w["code"] == "UPDATE_FAILED" for w in r["warnings"])
