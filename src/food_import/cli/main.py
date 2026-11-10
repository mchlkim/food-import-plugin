"""명령줄 인터페이스. 판정 로직은 services.Service 에 있고 여기서는 인자 해석과 출력만 한다."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..services import Service


def _print(result: dict[str, Any], as_json: bool, markdown_key: str | None = None) -> int:
    if as_json or not markdown_key or markdown_key not in result:
        printable = {k: v for k, v in result.items() if k != "markdown"} if as_json else result
        print(json.dumps(printable, ensure_ascii=False, indent=2))
    else:
        print(result[markdown_key])
    return 0 if result.get("ok") else 1


def _query_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("text", nargs="?", help="자연어 질문(간이 해석). 아래 옵션이 우선한다")
    p.add_argument("--origin", dest="origin_country", help="원산지 국가(한글·영문·ISO 코드)")
    p.add_argument("--export", dest="export_country", help="수출국(원산지와 다를 때)")
    p.add_argument("--region", help="생산지역(일본 도도부현)")
    p.add_argument("--product", dest="product_name", help="품목명")
    p.add_argument("--state", help="상태: 냉동, 냉장, 활, 건조, 염장, 조미, 훈제, 통조림 …")
    p.add_argument("--type", dest="product_type", choices=["raw", "processed"], help="raw=원물, processed=가공식품")
    p.add_argument("--category", help="식품 유형: 농산물, 축산물, 수산물, 가공식품, 건강기능식품, 식품첨가물, 기구용기포장 "
                                      "(품목 사전에 없는 품목일 때 준다)")
    p.add_argument("--ingredient", dest="ingredients", action="append",
                   help="원재료(여러 번 줄 수 있다). 예: --ingredient 돼지고기 --ingredient 젤라틴. 원재료가 없으면 --ingredient 없음")
    p.add_argument("--scientific-name", dest="scientific_name")
    p.add_argument("--date", dest="declaration_date", help="수입신고 예정일 YYYY-MM-DD")
    p.add_argument("--shipment-date", dest="shipment_date", help="선적일 YYYY-MM-DD")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--refresh", dest="refresh", action="store_true", default=None,
                   help="조회 전에 공식 출처를 지금 확인한다(기본: 마지막 확인이 24시간보다 오래됐을 때만)")
    g.add_argument("--no-refresh", dest="refresh", action="store_false", help="공식 출처를 확인하지 않고 조회한다")


def _fields(a: argparse.Namespace) -> dict[str, Any]:
    keys = ["text", "origin_country", "export_country", "region", "product_name", "state", "product_type",
            "scientific_name", "declaration_date", "shipment_date", "category", "ingredients"]
    out = {k: getattr(a, k) for k in keys if getattr(a, k, None)}
    if out.get("ingredients") in (["없음"], ["none"]):
        out["ingredients"] = []          # 해당 원재료가 없다고 밝힌 경우
    if getattr(a, "refresh", None) is not None:
        out["refresh"] = a.refresh
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="food-import", description="수입식품 수입신고 구비서류 조회 (식약처 제출분)")
    p.add_argument("--json", action="store_true", help="JSON 으로 출력")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("docs", help="구비서류 판정 (get_required_documents)")
    _query_args(s)
    s = sub.add_parser("restrictions", help="수입금지·제한 여부 (check_import_restrictions)")
    _query_args(s)
    s = sub.add_parser("hygiene", help="위생증명서·검사증명서 의무 (get_hygiene_certificate_requirements)")
    s.add_argument("country")
    s.add_argument("--product", dest="product_name")
    s.add_argument("--type", dest="product_type", choices=["raw", "processed"])
    s.add_argument("--date", dest="declaration_date")
    s.add_argument("--shipment-date", dest="shipment_date")
    s = sub.add_parser("search", help="수집된 공식 문서 검색 (search_regulations)")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("parse", help="자연어 질문 간이 해석")
    s.add_argument("text")
    s = sub.add_parser("resolve", help="표기 정규화")
    s.add_argument("--country")
    s.add_argument("--region")
    s.add_argument("--product")
    s.add_argument("--state")

    s = sub.add_parser("update", help="공식 출처 확인 → 변경 시 검토 대기 등록 (check_regulation_updates)")
    s.add_argument("--only", nargs="*", help="특정 출처만")
    s.add_argument("--no-pending", action="store_true", help="비교만 하고 검토 대기를 만들지 않는다")
    s.add_argument("--offline", action="store_true", help="사이트에 접속하지 않고 보관된 정규화 데이터로만 비교한다")
    sub.add_parser("status", help="출처·규칙 상태 (get_source_status)")
    sub.add_parser("pending", help="검토 대기 목록")
    s = sub.add_parser("show-pending", help="검토 대기 항목의 변경 보고서")
    s.add_argument("pending_id")
    s = sub.add_parser("approve", help="검토 대기 항목을 승인해 새 규칙 버전으로 적용")
    s.add_argument("pending_id")
    s.add_argument("--by", required=True, help="승인자")
    s.add_argument("--verification", default="source_quoted", choices=["source_quoted", "expert_reviewed"])
    s.add_argument("--note", default="")
    s = sub.add_parser("review", help="현재 규칙을 실무자 검토 완료(expert_reviewed)로 기록")
    s.add_argument("--by", required=True, help="검토자")
    s.add_argument("--note", default="")
    s = sub.add_parser("reject", help="검토 대기 항목 반려")
    s.add_argument("pending_id")
    s.add_argument("--by", required=True)
    s.add_argument("--note", default="")
    sub.add_parser("versions", help="규칙 버전 목록")
    s = sub.add_parser("rollback", help="이전 규칙 버전으로 되돌리기")
    s.add_argument("version", type=int)
    s.add_argument("--by", required=True)
    s.add_argument("--note", default="")
    s = sub.add_parser("rules", help="규칙 묶음 보기")
    s.add_argument("--version", type=int)
    s = sub.add_parser("validate", help="승인된 규칙 검증")
    s.add_argument("--version", type=int)
    s = sub.add_parser("history", help="업데이트 이력")
    s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("manual-import", help="직접 내려받은 공식 파일 반영")
    s.add_argument("source_id")
    s.add_argument("file")
    s.add_argument("--title", default="")
    s.add_argument("--url", default="")
    s.add_argument("--publication-date")
    s.add_argument("--country", help="위생약정 공지일 때 국가 코드")
    s.add_argument("--sn", help="위생약정 공지일 때 게시물 번호")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    svc = Service()
    j = a.json
    if a.cmd == "docs":
        return _print(svc.get_required_documents(**_fields(a)), j, "markdown")
    if a.cmd == "restrictions":
        return _print(svc.check_import_restrictions(**_fields(a)), True)
    if a.cmd == "hygiene":
        return _print(svc.get_hygiene_certificate_requirements(
            a.country, a.product_name, a.product_type, a.declaration_date, a.shipment_date), True)
    if a.cmd == "search":
        return _print(svc.search_regulations(a.query, a.limit), True)
    if a.cmd == "parse":
        return _print(svc.parse_query(a.text), True)
    if a.cmd == "resolve":
        return _print(svc.resolve_terms(a.country, a.region, a.product, a.state), True)
    if a.cmd == "update":
        r = svc.check_regulation_updates(register_pending=not a.no_pending, only=a.only, offline=a.offline)
        code = _print(r, True)
        failed = [s for s in r["sources"] if s["status"] in ("FETCH_FAILED", "PARSE_FAILED")]
        return 2 if failed or r.get("rules_status") == "BUILD_FAILED" else (3 if r.get("pending_id") else code)
    if a.cmd == "status":
        return _print(svc.get_source_status(), True)
    if a.cmd == "pending":
        return _print(svc.list_pending(), True)
    if a.cmd == "show-pending":
        return _print(svc.show_pending(a.pending_id), True)
    if a.cmd == "approve":
        return _print(svc.approve(a.pending_id, a.by, a.verification, a.note), True)
    if a.cmd == "review":
        return _print(svc.mark_reviewed(a.by, a.note), True)
    if a.cmd == "reject":
        return _print(svc.reject(a.pending_id, a.by, a.note), True)
    if a.cmd == "versions":
        return _print(svc.versions(), True)
    if a.cmd == "rollback":
        return _print(svc.rollback(a.version, a.by, a.note), True)
    if a.cmd == "rules":
        return _print(svc.show_rules(a.version), True)
    if a.cmd == "validate":
        return _print(svc.validate_rules(a.version), True)
    if a.cmd == "history":
        return _print(svc.history(a.limit), True)
    if a.cmd == "manual-import":
        return _print(svc.manual_import(a.source_id, a.file, a.title, a.url, a.publication_date, a.country, a.sn), True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
