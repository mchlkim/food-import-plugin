"""규칙 형식, 검증, 비교.

규칙 하나의 형식은 docs/rule-schema.md 에 있다. 요약:

  id               고유 ID (버전은 규칙 묶음 단위로 붙는다)
  topic            general | hygiene | jp_seafood | jp_radiation_processed | item_specific | inspection_order | precheck
  priority_class   1 수입금지·중단, 2 특별 관리, 3 국가별 협약·증명서, 4 품목별, 5 일반
  effect           PROHIBIT | REQUIRE | CONDITIONAL | NOTICE(서류가 아닌 신고 전 확인사항)
  when             적용 조건(origin_country, export_country, any_country, region_in, product_type, product_tags_any, state_in)
  skip_when_unknown  선택 서류. 조건을 판단할 정보가 부족하면 UNKNOWN 으로 싣지 않고 뺀다
  excluded_states  근거 고시가 적용대상에서 뺀 상태(예: 활수생동물). 해당하면 UNKNOWN 으로 답한다
  exclusive_group  같은 묶음의 규칙이 둘 이상 동시에 해당하면 충돌로 본다
  documents        [{name, original_or_copy, issuer, category}]
  effective_from / effective_to / date_basis(declaration | shipment)
  legal_basis      [문자열]
  source           {source_id, title, url, publication_date, retrieved_at, location, quote}
  condition_note   조건부 제출의 조건 설명
  verification     source_quoted(원문 인용 위치 확인) | expert_reviewed(실무자 검토)
"""
from __future__ import annotations

import copy
from typing import Any

EFFECTS = {"PROHIBIT", "REQUIRE", "CONDITIONAL", "NOTICE"}
STATUSES = ["PROHIBITED", "REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "UNKNOWN"]
VERIFICATION_LEVELS = ["source_quoted", "expert_reviewed"]
WHEN_KEYS = {"origin_country", "export_country", "any_country", "region_in", "product_type", "product_tags_any", "state_in"}


def validate_ruleset(ruleset: dict[str, Any]) -> dict[str, list[str]]:
    """규칙 묶음의 형식 오류와 규칙 간 충돌을 찾는다. errors 가 있으면 승인할 수 없다."""
    errors: list[str] = []
    warnings: list[str] = []
    rules = ruleset.get("rules", [])
    if not rules:
        errors.append("규칙이 하나도 없다.")
    seen: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    for r in rules:
        rid = r.get("id", "(id 없음)")
        seen[rid] = seen.get(rid, 0) + 1
        if r.get("effect") not in EFFECTS:
            errors.append(f"{rid}: effect 값이 잘못됐다: {r.get('effect')!r}")
        unknown = set(r.get("when", {})) - WHEN_KEYS
        if unknown:
            errors.append(f"{rid}: 알 수 없는 조건 키 {sorted(unknown)}")
        if r.get("effect") in ("REQUIRE", "CONDITIONAL") and not r.get("documents"):
            errors.append(f"{rid}: 제출 규칙인데 서류가 없다.")
        src = r.get("source") or {}
        for key in ("source_id", "url", "location", "quote"):
            if not src.get(key):
                errors.append(f"{rid}: 출처 항목 누락({key}). 근거 없는 규칙은 승인할 수 없다.")
        if not r.get("legal_basis"):
            warnings.append(f"{rid}: 법적 근거 조문이 비어 있다.")
        ef, et = r.get("effective_from"), r.get("effective_to")
        if ef and et and ef > et:
            errors.append(f"{rid}: 시행 시작일({ef})이 종료일({et})보다 늦다.")
        if r.get("date_basis", "declaration") not in ("declaration", "shipment"):
            errors.append(f"{rid}: date_basis 값이 잘못됐다.")
        if r.get("effect") == "NOTICE" and not r.get("title"):
            errors.append(f"{rid}: 확인사항 규칙인데 제목(title)이 없다.")
        fp = repr((r.get("effect"), r.get("title"), sorted((k, repr(v)) for k, v in r.get("when", {}).items()),
                   [d.get("name") for d in r.get("documents", [])], ef, et))
        if fp in fingerprints:
            errors.append(f"{rid}: {fingerprints[fp]} 와 조건·서류가 같은 중복 규칙이다.")
        else:
            fingerprints[fp] = rid
    for rid, n in seen.items():
        if n > 1:
            errors.append(f"{rid}: 같은 ID 가 {n}번 나온다.")

    # 배타 묶음 안에서 지역이 겹치면 한 지역에 서로 다른 조치가 붙는다.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        if r.get("exclusive_group"):
            groups.setdefault(r["exclusive_group"], []).append(r)
    for g, members in groups.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                overlap = set(a.get("when", {}).get("region_in", [])) & set(b.get("when", {}).get("region_in", []))
                if overlap and _periods_overlap(a, b):
                    errors.append(f"충돌: {a['id']} 와 {b['id']} 에 같은 지역 {sorted(overlap)} 이 들어 있다({g}).")
    return {"errors": errors, "warnings": warnings}


def _periods_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a0, a1 = a.get("effective_from") or "0000", a.get("effective_to") or "9999"
    b0, b1 = b.get("effective_from") or "0000", b.get("effective_to") or "9999"
    return a0 <= b1 and b0 <= a1


def _material(rule: dict[str, Any]) -> dict[str, Any]:
    """비교에 쓰는 규칙 내용. 수집 시각과 검증 단계는 규정 내용이 아니므로 뺀다."""
    r = copy.deepcopy(rule)
    r.pop("verification", None)
    r.get("source", {}).pop("retrieved_at", None)
    return r


def diff_rulesets(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """승인된 규칙 묶음과 새 후보의 내용 차이. 파일 해시가 아니라 규칙 내용을 비교한다."""
    old_rules = {r["id"]: _material(r) for r in (old or {}).get("rules", [])}
    new_rules = {r["id"]: _material(r) for r in new.get("rules", [])}
    added = sorted(set(new_rules) - set(old_rules))
    removed = sorted(set(old_rules) - set(new_rules))
    changed = []
    for rid in sorted(set(old_rules) & set(new_rules)):
        a, b = old_rules[rid], new_rules[rid]
        if a != b:
            fields = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            detail = {}
            for k in fields:
                if k == "source":
                    sub = sorted(x for x in set(a.get(k, {})) | set(b.get(k, {}))
                                 if a.get(k, {}).get(x) != b.get(k, {}).get(x))
                    detail[k] = {x: {"before": a.get(k, {}).get(x), "after": b.get(k, {}).get(x)} for x in sub}
                else:
                    detail[k] = {"before": a.get(k), "after": b.get(k)}
            changed.append({"id": rid, "fields": fields, "detail": detail})
    old_cov, new_cov = (old or {}).get("coverage"), new.get("coverage")
    old_w, new_w = set((old or {}).get("warnings", [])), set(new.get("warnings", []))
    out = {
        "added": added, "removed": removed, "changed": changed,
        "coverage_changed": old is not None and old_cov != new_cov,
        "warnings_added": sorted(new_w - old_w), "warnings_removed": sorted(old_w - new_w),
    }
    out["has_changes"] = bool(old is None or added or removed or changed or out["coverage_changed"]
                              or out["warnings_added"] or out["warnings_removed"])
    out["summary"] = (
        "최초 규칙 묶음" if old is None else
        f"추가 {len(added)}, 삭제 {len(removed)}, 변경 {len(changed)}, "
        f"안내 문구 변동 {len(out['warnings_added']) + len(out['warnings_removed'])}"
    )
    return out
