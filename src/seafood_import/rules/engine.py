"""구비서류 판정 엔진.

입력은 구조화된 조회 조건(Query)과 승인된 규칙 묶음이다. 네트워크에 접속하지 않고,
규칙에 없는 내용을 추론해 채우지 않는다. 근거가 없으면 UNKNOWN 으로 돌려준다.

판정 순서(결과 표시 순서이기도 하다):
  1 수입금지·수입중단  2 특별 관리 규정  3 국가별 협약·증명서  4 품목별 요건  5 일반 수입신고 서류
앞 단계가 해당해도 뒤 단계 규칙은 독립적으로 평가한다. 수입금지에 해당하면 금지 사실을 먼저 싣는다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from ..normalization import (country_name, jp_region_name, match_product, state_is_processed, state_name)

SUBMIT_TO = "통관장소 관할 지방식품의약품안전청"
GENERIC_TAGS = {"seafood_generic", "fish_generic"}


@dataclass
class Query:
    origin_country: str | None = None       # 원산지(생산국) 코드
    export_country: str | None = None       # 수출국 코드. 없으면 원산지와 같다고 가정하고 그 사실을 결과에 적는다
    region: str | None = None               # 생산지역 코드(일본: JP-01 … JP-47)
    product_name: str | None = None
    product_tag: str | None = None
    product_category: str | None = None
    product_generic: bool = False
    product_candidates: list[str] = field(default_factory=list)
    scientific_name: str | None = None
    state: str | None = None                # frozen | chilled | live | dried | salted | boiled | seasoned | …
    product_type: str | None = None         # raw(수산물 원물) | processed(가공식품)
    declaration_date: str | None = None     # 수입신고 예정일(ISO)
    shipment_date: str | None = None        # 선적일(ISO)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate_tags(q: Query) -> set[str]:
    tags = set()
    for c in q.product_candidates:
        m = match_product(c.split("(")[0])
        if m:
            tags.add(m.tag)
    return tags


def _match(rule: dict[str, Any], q: Query, export: str | None) -> tuple[bool | None, list[str]]:
    """규칙 조건 대조. True 해당 / False 비해당 / None 정보 부족(부족한 항목 목록과 함께)."""
    missing: list[str] = []
    w = rule.get("when", {})
    if "origin_country" in w:
        if q.origin_country is None:
            missing.append("원산지 국가")
        elif q.origin_country not in w["origin_country"]:
            return False, []
    if "export_country" in w:
        if export is None:
            missing.append("수출국")
        elif export not in w["export_country"]:
            return False, []
    if "any_country" in w:
        known = [c for c in (q.origin_country, export) if c]
        if not known:
            missing.append("원산지 국가")
        elif not set(known) & set(w["any_country"]):
            return False, []
    if "product_type" in w:
        if q.product_type is None:
            missing.append("원물/가공식품 구분")
        elif q.product_type not in w["product_type"]:
            return False, []
    if "product_tags_any" in w:
        tags = set(w["product_tags_any"])
        if q.product_tag is None or q.product_tag in GENERIC_TAGS:
            missing.append("구체적인 품목명")
        elif q.product_tag in tags:
            pass
        elif q.product_generic and tags & _candidate_tags(q):
            missing.append("구체적인 어종(통칭으로 입력됨)")
        else:
            return False, []
    if "state_in" in w and q.state not in w["state_in"]:
        return False, []          # 상태가 입력되지 않았으면 해당하지 않는 것으로 본다(선택 서류라 미확인으로 늘어놓지 않는다)
    if "region_in" in w:
        if q.region is None:
            missing.append("생산지역(일본 도도부현)")
        elif q.region not in w["region_in"]:
            return False, []
    return (None, missing) if missing else (True, [])


def _in_force(rule: dict[str, Any], decl: str, ship: str | None) -> bool:
    basis = ship if rule.get("date_basis") == "shipment" and ship else decl
    if rule.get("effective_from") and basis < rule["effective_from"]:
        return False
    if rule.get("effective_to") and basis > rule["effective_to"]:
        return False
    return True


def _doc_entry(rule: dict[str, Any], doc: dict[str, Any], status: str, reason: str, condition: str = "") -> dict[str, Any]:
    src = rule["source"]
    return {
        "name": doc["name"], "status": status, "category": doc.get("category", ""),
        "original_or_copy": doc.get("original_or_copy", ""), "issuer": doc.get("issuer", ""),
        "submit_to": SUBMIT_TO, "condition": condition, "reason": reason,
        "legal_basis": rule.get("legal_basis", []),
        "effective_from": rule.get("effective_from"), "effective_to": rule.get("effective_to"),
        "date_basis": rule.get("date_basis", "declaration"),
        "source": {"title": src.get("title"), "url": src.get("url"), "publication_date": src.get("publication_date"),
                   "retrieved_at": src.get("retrieved_at"), "location": src.get("location"), "quote": src.get("quote")},
        "rule_id": rule["id"], "verification": rule.get("verification", "source_quoted"),
    }


def evaluate(q: Query, ruleset: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    today = today or dt.date.today().isoformat()
    assumptions: list[str] = []
    missing_info: list[str] = []
    confirm: list[str] = []
    conflicts: list[dict[str, Any]] = []

    decl = q.declaration_date or today
    if not q.declaration_date:
        assumptions.append(f"수입신고 예정일이 없어 오늘({today}) 기준으로 판정했다.")
    elif q.declaration_date > today:
        confirm.append(f"수입신고 예정일({q.declaration_date})이 조회일 이후다. 그 사이 규정이 바뀔 수 있으니 신고 전에 다시 조회한다.")
    export = q.export_country or q.origin_country
    if q.origin_country and not q.export_country:
        assumptions.append("수출국이 입력되지 않아 원산지와 같다고 가정했다.")
    if q.origin_country and q.export_country and q.origin_country != q.export_country:
        confirm.append(
            f"원산지({country_name(q.origin_country)})와 수출국({country_name(q.export_country)})이 다르다. "
            "생산지 기준 규정은 원산지로, 수출국 정부 발행 증명서 규정은 수출국으로 판정했다. "
            "제3국 경유·가공 건의 적용 기준은 관할 지방식약청에 확인한다.")
    if q.product_type is None:
        missing_info.append("원물(단순 절단·가열·숙성·건조·염장 수산물)인지 가공식품인지")
    if q.origin_country is None:
        missing_info.append("원산지 국가")
    if q.product_tag is None:
        missing_info.append("품목명")
    elif q.product_generic and q.product_tag not in GENERIC_TAGS:
        cand = ", ".join(q.product_candidates)
        confirm.append(f"'{q.product_name}'은(는) 여러 어종을 묶어 부르는 이름이다"
                       + (f"(예: {cand})" if cand else "") + ". 어종에 따라 달라지는 규정은 학명이나 정확한 품목명으로 다시 확인한다.")

    matched: list[dict[str, Any]] = []
    indeterminate: list[tuple[dict[str, Any], list[str]]] = []
    for rule in ruleset.get("rules", []):
        if not _in_force(rule, decl, q.shipment_date):
            basis = q.shipment_date if rule.get("date_basis") == "shipment" and q.shipment_date else decl
            if rule.get("effective_from") and basis < rule["effective_from"] and _match(rule, q, export)[0] is not False:
                note = (f"기준일({basis})이 수록된 규정의 시행일({rule['effective_from']}) 이전이다. "
                        f"그 시점의 규정은 수록되지 않아 판정하지 않았다: {rule['id']}")
                if note not in confirm:
                    confirm.append(note)
            continue
        res, miss = _match(rule, q, export)
        if res is True:
            matched.append(rule)
            if rule.get("date_basis") == "shipment" and not q.shipment_date and rule.get("effective_from"):
                confirm.append(f"{rule['id']}: 선적일 기준 규정({rule['effective_from']} 선적분부터)인데 선적일이 없어 "
                               "신고 예정일로 판정했다. 선적일을 확인한다.")
        elif res is None and not rule.get("skip_when_unknown"):
            indeterminate.append((rule, miss))        # 선택 서류(skip_when_unknown)는 정보가 부족하면 싣지 않는다

    # 배타 묶음 충돌: 한 화물에 서로 다른 지역 조치가 동시에 해당하면 자동 결론을 내지 않는다.
    by_group: dict[str, list[dict[str, Any]]] = {}
    for r in matched:
        if r.get("exclusive_group"):
            by_group.setdefault(r["exclusive_group"], []).append(r)
    conflicted_ids: set[str] = set()
    for g, rs in by_group.items():
        if len(rs) > 1:
            conflicts.append({"group": g, "rule_ids": [r["id"] for r in rs],
                              "message": "같은 조건에 서로 다른 조치가 동시에 해당한다. 원문을 확인하기 전에는 결론을 내지 않는다.",
                              "sources": [{"rule_id": r["id"], "url": r["source"].get("url"),
                                           "location": r["source"].get("location")} for r in rs]})
            conflicted_ids.update(r["id"] for r in rs)

    documents: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    prechecks: list[dict[str, Any]] = []
    for r in sorted(matched, key=lambda x: x.get("priority_class", 9)):
        if r["id"] in conflicted_ids:
            for d in r.get("documents") or [{"name": f"({r['id']} 조치)"}]:
                documents.append(_doc_entry(r, d, "UNKNOWN", "규칙 충돌로 판정을 보류했다."))
            continue
        if r["effect"] == "PROHIBIT":
            s = r["source"]
            prohibitions.append({
                "rule_id": r["id"], "reason": s.get("quote"), "scope_note": r.get("condition_note", ""),
                "legal_basis": r.get("legal_basis", []), "effective_from": r.get("effective_from"),
                "source": {"title": s.get("title"), "url": s.get("url"), "location": s.get("location"),
                           "retrieved_at": s.get("retrieved_at")},
                "verification": r.get("verification", "source_quoted")})
        elif r["effect"] == "NOTICE":
            s = r["source"]
            prechecks.append({"rule_id": r["id"], "title": r.get("title", ""), "quote": s.get("quote"),
                              "legal_basis": r.get("legal_basis", []),
                              "source": {"title": s.get("title"), "url": s.get("url"), "location": s.get("location"),
                                         "retrieved_at": s.get("retrieved_at")},
                              "verification": r.get("verification", "source_quoted")})
        elif q.state and q.state in r.get("excluded_states", []):
            for d in r["documents"]:
                documents.append(_doc_entry(
                    r, d, "UNKNOWN",
                    f"근거 고시가 이 상태({state_name(q.state)})를 적용대상에서 뺐다. 이 고시로는 제출 의무를 판정할 수 없다. "
                    "활수산물은 수산생물 검역(국립수산물품질관리원) 요건을 따로 확인한다.", r.get("condition_note", "")))
        elif r["effect"] == "REQUIRE":
            for d in r["documents"]:
                documents.append(_doc_entry(r, d, "REQUIRED", "조건에 해당한다.", r.get("condition_note", "")))
        else:
            for d in r["documents"]:
                documents.append(_doc_entry(r, d, "CONDITIONAL", "아래 조건에 해당하면 제출한다.", r.get("condition_note", "")))

    unknown_prohibition = False
    for r, miss in indeterminate:
        need = ", ".join(miss)
        if r["effect"] == "PROHIBIT":
            unknown_prohibition = True
            for m in miss:
                if m not in missing_info:
                    missing_info.append(m)
            continue
        for m in miss:
            if m.startswith("생산지역") and m not in missing_info:
                missing_info.append(m)
        for d in r["documents"]:
            if any(x["status"] == "UNKNOWN" and x["name"] == d["name"] for x in documents):
                continue  # 같은 서류가 여러 규칙에서 미확인으로 나오면 한 번만 싣는다
            documents.append(_doc_entry(r, d, "UNKNOWN", f"해당 여부를 판단하려면 {need} 정보가 필요하다.",
                                        r.get("condition_note", "")))

    cov = ruleset.get("coverage", {})
    # 위생증명서: 확인된 약정국 규칙이 해당하지 않으면, 목록이 한정 열거일 때만 NOT_REQUIRED 로 답한다.
    hyg_hit = any(r["topic"] == "hygiene" for r in matched) or any(r["topic"] == "hygiene" for r, _ in indeterminate)
    if export and not hyg_hit:
        h = cov.get("hygiene", {})
        basis_rule = next((r for r in ruleset.get("rules", []) if r["topic"] == "hygiene"), None)
        basis = basis_rule["legal_basis"] if basis_rule else []
        own = next((r for r in ruleset.get("rules", []) if r["topic"] == "hygiene"
                    and export in r["when"].get("export_country", []) and r.get("scope_quote")), None)
        if q.product_type == "processed" and own:
            reason = (f"{country_name(export)}은(는) 위생약정 고시가 있는 국가다. 고시의 적용대상은 다음과 같다. {own['scope_quote']} "
                      "식품첨가물이나 다른 원료를 쓴 가공식품이 여기에 드는지는 제품별로 확인한다.")
            status, basis = "UNKNOWN", own["legal_basis"]
        elif q.product_type == "processed":
            reason = "가공식품에 수산물 위생약정이 적용되는지 확인한 공식 자료가 규칙에 없다."
            status = "UNKNOWN"
        elif h.get("exhaustive"):
            reason = f"수록된 위생약정 체결국 목록(한정 열거)에 수출국({country_name(export)})이 없다."
            status = "NOT_REQUIRED"
        else:
            reason = (f"수출국({country_name(export)})과의 수산물 위생약정 체결 여부를 확인한 공식 자료가 규칙에 없다. "
                      f"확인된 약정국: {', '.join(country_name(c) for c in h.get('confirmed_countries', [])) or '없음'}. "
                      "목록에 없다고 약정이 없다는 뜻은 아니다.")
            status = "UNKNOWN"
        documents.append({
            "name": "위생증명서 또는 검사증명서(수출국 정부 발행)", "status": status, "category": "국가별 추가 구비서류",
            "original_or_copy": "", "issuer": "수출국 정부기관", "submit_to": SUBMIT_TO, "condition": "",
            "reason": reason, "legal_basis": basis, "effective_from": None, "effective_to": None,
            "date_basis": "declaration", "source": None, "rule_id": None, "verification": None})

    # 일본산 원물인데 어느 지역 규칙에도 해당하지 않는 경우
    if q.origin_country == "JP" and q.product_type == "raw" and q.region:
        jp_hit = any(r["topic"] == "jp_seafood" and "region_in" in r["when"] for r in matched)
        if not jp_hit:
            confirm.append(f"생산지역 {jp_region_name(q.region)}에 해당하는 일본산 수산물 규칙이 없다. 공식 자료를 직접 확인한다.")
    if q.origin_country == "JP" and q.product_type == "processed":
        confirm.append("일본산 가공식품은 제조·생산 지역 기준으로 판정했다. 원료 수산물의 생산지에 수입금지가 적용되는지는 "
                       "규칙에 수록되지 않았으니 관할 지방식약청에 확인한다.")

    if prohibitions and not any(p["rule_id"] in conflicted_ids for p in prohibitions):
        importability = "PROHIBITED"
        summary = "수입금지 대상이다."
    elif conflicts:
        importability = "UNKNOWN"
        summary = "규칙이 충돌해 수입 가능 여부를 판정하지 않았다."
    elif unknown_prohibition or q.origin_country is None:
        importability = "UNKNOWN"
        summary = "수입금지 해당 여부를 판단할 정보가 부족하다: " + ", ".join(missing_info)
    else:
        importability = "NO_PROHIBITION_FOUND"
        summary = "수록된 수입금지 규칙에 해당하지 않는다. 개별 제품·업체 단위 수입금지와 수입중단은 아래 확인사항을 본다."

    if importability == "PROHIBITED":
        confirm = [c for c in confirm if "선적일 기준 규정" not in c]
    order = {"REQUIRED": 0, "CONDITIONAL": 1, "UNKNOWN": 2, "NOT_REQUIRED": 3}
    documents.sort(key=lambda d: order.get(d["status"], 9))

    confirm += [
        "개별 제품·해외제조업소 단위의 수입금지·수입중단은 규칙에 수록되지 않았다. 수입식품정보마루의 "
        "수입금지제품(/CFCII03F01)과 수입중단업체(/CFCCC01F08)에서 확인한다.",
        "식약처 외 기관의 요건(관세청 통관 서류, 국립수산물품질관리원 수산생물 검역 등)은 이 도구가 판정하지 않는다. "
        "해외제조업소가 실제로 등록돼 있는지도 조회하지 않는다.",
    ]
    # 규칙 묶음의 안내 문구 중 수산물 관련이고, 다른 국가만 언급한 것이 아닌 문구를 싣는다.
    from ..normalization import countries_in_text
    mine = {c for c in (q.origin_country, export) if c}
    for w in ruleset.get("warnings", []):
        if not any(k in w for k in ("수산", "위생증명", "위생약정")):
            continue
        named = countries_in_text(w)
        if named and not (named & mine):
            continue
        confirm.append(w)

    return {
        "query": {**q.to_dict(), "origin_country_name": country_name(q.origin_country),
                  "export_country_name": country_name(export), "region_name": jp_region_name(q.region),
                  "state_name": state_name(q.state)},
        "applied_date": decl,
        "importability": {"status": importability, "summary": summary, "prohibitions": prohibitions},
        "documents": [] if importability == "PROHIBITED" else documents,
        "documents_if_not_prohibited": documents if importability == "PROHIBITED" else [],
        "prechecks": prechecks,
        "missing_info": missing_info, "assumptions": assumptions, "needs_confirmation": confirm,
        "conflicts": conflicts,
        "rule_version": ruleset.get("version"), "rule_verification": ruleset.get("verification"),
        "rules_approved_at": ruleset.get("approved_at"),
    }


def make_query(origin_country: str | None = None, export_country: str | None = None, region: str | None = None,
               product_name: str | None = None, state: str | None = None, product_type: str | None = None,
               scientific_name: str | None = None, declaration_date: str | None = None,
               shipment_date: str | None = None) -> tuple[Query, list[str]]:
    """표기를 정규화해 Query 를 만든다. 해석하지 못한 값은 errors 로 돌려준다(추측하지 않는다)."""
    from ..normalization import country_code, jp_region_code, state_code

    errors: list[str] = []
    q = Query(scientific_name=scientific_name or None)

    def _country(v: str | None, label: str) -> str | None:
        if not v:
            return None
        code = country_code(v)
        if not code:
            errors.append(f"{label} '{v}' 을(를) 국가 사전에서 찾지 못했다.")
        return code

    q.origin_country = _country(origin_country, "원산지")
    q.export_country = _country(export_country, "수출국")
    if region:
        code = region if str(region).startswith("JP-") and jp_region_name(region) != region else jp_region_code(region)
        if code:
            q.region = code
        elif (q.origin_country or "JP") == "JP":
            errors.append(f"생산지역 '{region}' 을(를) 일본 도도부현 사전에서 찾지 못했다.")
    if product_name:
        q.product_name = product_name
        m = match_product(product_name)
        if m:
            q.product_tag, q.product_category = m.tag, m.category
            q.product_generic, q.product_candidates = m.generic, m.candidates
            if m.processed and not product_type:
                product_type = "processed"
        else:
            errors.append(f"품목 '{product_name}' 을(를) 품목 사전에서 찾지 못했다. 품목 단위 규칙은 판정하지 못한다.")
            q.product_tag, q.product_generic = "seafood_generic", True
    if state:
        q.state = state_code(state)
        if not q.state:
            errors.append(f"상태 '{state}' 을(를) 해석하지 못했다.")
    if product_type:
        pt = {"raw": "raw", "원물": "raw", "수산물": "raw", "processed": "processed", "가공": "processed",
              "가공식품": "processed"}.get(str(product_type).strip().lower())
        if not pt:
            errors.append(f"제품 유형 '{product_type}' 은(는) raw(원물) 또는 processed(가공식품)여야 한다.")
        q.product_type = pt
    elif q.state:
        q.product_type = "processed" if state_is_processed(q.state) else "raw"
    for label, v in (("수입신고 예정일", declaration_date), ("선적일", shipment_date)):
        if v:
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                errors.append(f"{label} '{v}' 은(는) YYYY-MM-DD 형식이어야 한다.")
    q.declaration_date, q.shipment_date = declaration_date or None, shipment_date or None
    return q, errors
