"""정규화된 공식 문서 데이터 + 결합표(config/bindings.yaml) → 규칙 후보.

서류명 외의 사실 값(국가·지역 목록, 증명 내용, 시행일)은 모두 정규화 데이터에서 읽는다.
결합표가 기대한 낱말이 원문에 없으면 BuildError 를 내고, 호출한 쪽은 기존 승인 규칙을 유지한다.
"""
from __future__ import annotations

import re
from typing import Any

from ..normalization import all_jp_region_codes, country_code, jp_regions_in_text
from ..parsers import find_span, parse_kr_date
from ..storage import now_iso

SUBMIT_TO = "통관장소 관할 지방식품의약품안전청"
LAW_ART27 = "수입식품안전관리 특별법 시행규칙 제27조제1항"


class BuildError(Exception):
    """정규화 데이터로 규칙 후보를 만들 수 없다."""


def _src(doc: dict[str, Any], location: str, quote: str) -> dict[str, Any]:
    return {
        "source_id": doc["source_id"], "title": doc.get("title", ""), "url": doc.get("url", ""),
        "publication_date": doc.get("publication_date"), "retrieved_at": doc.get("retrieved_at"),
        "location": location, "quote": quote,
    }


def _need(normalized: dict[str, dict[str, Any]], source_id: str) -> dict[str, Any]:
    doc = normalized.get(source_id)
    if not doc or not doc.get("content"):
        raise BuildError(f"정규화 데이터가 없다: {source_id}. 먼저 수집하거나 manual-import 로 원본을 넣는다.")
    return doc


def _check_contains(text: str, words: list[str], where: str) -> None:
    missing = [w for w in words if str(w) not in text]
    if missing:
        raise BuildError(f"{where}: 원문에 기대한 낱말 {missing} 이 없다. 원문 구조나 내용이 바뀐 것으로 보인다.")


def build_candidate(normalized: dict[str, dict[str, Any]], bindings: dict[str, Any]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    warnings: list[str] = []

    law = _need(normalized, "law_enforcement_rule_27")
    rad = _need(normalized, "radsafe_japan_measures")
    lst = _need(normalized, "mfds_required_docs")
    orders = _need(normalized, "mfds_inspection_orders")
    hyg = _need(normalized, "mfds_hygiene_agreement_notices")
    agreements = _need(normalized, "law_hygiene_agreements")
    notice = _need(normalized, "law_import_inspection_notice")
    act = _need(normalized, "law_special_act")

    hygiene_basis = _hygiene_basis(law, bindings)
    original = _hygiene_original(notice, bindings)
    rules += _general_rules(law, bindings, original)
    hygiene_rules, hyg_warnings = _hygiene_rules(agreements, hyg, hygiene_basis, original, bindings)
    rules += hygiene_rules
    warnings += hyg_warnings
    rules += _jp_seafood_rules(rad)
    rules += _jp_agri_and_livestock_rules(rad, bindings)
    list_rules, list_warnings = _list_rules(lst, bindings)
    rules += list_rules
    warnings += list_warnings
    order_rules, order_warnings = _inspection_order_rules(orders, bindings)
    rules += order_rules
    warnings += order_warnings
    hygiene_countries = sorted({c for r in hygiene_rules for c in r["when"]["export_country"]})
    rules += _inspection_notice_rules(notice, bindings, hygiene_countries)
    rules += _act_rules(act, bindings)

    unlisted = rad["content"].get("seafood_unlisted_regions", [])
    coverage = {
        "hygiene": {
            "exhaustive": False,
            "confirmed_countries": hygiene_countries,
            "note": "국가법령정보센터에서 약정 고시 본문을 확인한 국가만 수록했다. 수록되지 않은 국가는 약정이 없다는 뜻이 아니다.",
        },
        "jp_seafood": {"exhaustive": not unlisted, "unlisted_regions": unlisted},
        "required_docs_list": {
            "exhaustive": True,
            "note": "식약처 게재 구비서류 목록의 수산물 관련 행을 결합표로 연결했다. 결합되지 않은 수산물 관련 행은 경고로 표시한다.",
        },
    }
    sources = {
        sid: {"title": d.get("title", ""), "url": d.get("url", ""), "publication_date": d.get("publication_date"),
              "effective_date": d.get("effective_date"), "retrieved_at": d.get("retrieved_at"),
              "content_hash": d.get("content_hash")}
        for sid, d in normalized.items() if not d.get("advisory")
    }
    return {"schema": 1, "built_at": now_iso(), "rules": rules, "coverage": coverage,
            "warnings": warnings, "sources": sources}


# ---------- 일반 구비서류 (시행규칙 제27조제1항) ----------

def _general_rules(law: dict[str, Any], bindings: dict[str, Any], original: dict[str, Any]) -> list[dict[str, Any]]:
    c = law["content"]
    intro = c["paragraph1_intro"]
    _check_contains(intro, ["수입신고서", "지방식품의약품안전청장"], "제27조제1항 본문")
    base = {"topic": "general", "priority_class": 5, "when": {}, "effective_from": c["effective_date"],
            "effective_to": None, "date_basis": "declaration"}
    form = re.search(r"별지 제\d+호서식", intro)
    out = [{
        **base, "id": "GEN-ART27-DECLARATION", "effect": "REQUIRE",
        "documents": [{"name": f"수입식품등의 수입신고서({form.group(0) if form else '별지 서식'})",
                       "category": "식약처 수입신고 기본 구비서류"}],
        "legal_basis": [f"{LAW_ART27} 각 호 외의 부분"],
        "source": _src(law, f"{c['article']} 제1항 각 호 외의 부분", intro),
    }]
    for b in bindings.get("law_article_27_items", []):
        text = c["items"].get(b["key"])
        if text is None:
            raise BuildError(f"제27조제1항제{b['key']}호가 조문에 없다.")
        _check_contains(text, b.get("must_contain", []), f"제27조제1항제{b['key']}호")
        if b.get("use_as"):
            continue
        # 조건부 서류의 조건은 요약하지 않고 조문 원문을 쓴다. 조문이 가리키는 별표 문장을 뒤에 붙인다.
        condition, basis = "", [f"{LAW_ART27}제{b['key']}호"]
        if b["effect"] == "CONDITIONAL" or b.get("verbatim_condition"):
            parts = [f"제{b['key']}호 원문: {text}"]
            for aq in b.get("annex_quotes", []):
                annex = c.get("annexes", {}).get(aq["annex"])
                if not annex:
                    raise BuildError(f"시행규칙 {aq['annex']} 정규화 데이터가 없다.")
                found = find_span(annex["pages"], aq["start"], aq["end"])
                if not found:
                    raise BuildError(f"시행규칙 {aq['annex']} 에서 '{aq['start']}' 로 시작하는 문장을 찾지 못했다.")
                _check_contains(re.sub(r"\s+", "", found[0]), [re.sub(r"\s+", "", w) for w in aq.get("must_contain", [])],
                                f"시행규칙 {aq['label']}")
                parts.append(f"{aq['label']} 원문(PDF {found[1]}쪽): {found[0]}")
            if b.get("annex_quotes"):
                basis.append(f"{law['content']['law_name']} {b['annex_quotes'][0]['annex']}")
            if b.get("extra_note"):
                parts.append("참고(이 프로젝트 작성): " + b["extra_note"])
            condition = " / ".join(parts)
        doc = {"name": b["document_name"], "category": b.get("category") or (
            "식약처 수입신고 기본 구비서류" if b["effect"] == "REQUIRE" else "조건부 제출서류")}
        if b.get("original_from_notice"):
            doc["original_or_copy"] = original["original_or_copy"]
            doc["issuer"] = "수출국 정부"
            basis.append(original["basis"])
        out.append({
            **base, "id": b["rule_id"], "effect": b["effect"], "when": dict(b.get("when", {})),
            **({"topic": "livestock", "priority_class": 3} if b["rule_id"].startswith("LIV-") else {}),
            "documents": [doc],
            "condition_note": condition, "condition_verbatim": True,
            "legal_basis": basis,
            "source": _src(law, f"{c['article']} 제1항제{b['key']}호", text),
        })
    return out


def _hygiene_basis(law: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    for b in bindings.get("law_article_27_items", []):
        if b.get("use_as") == "hygiene_legal_basis":
            text = law["content"]["items"].get(b["key"])
            if text is None:
                raise BuildError("제27조제1항제7호(위생증명서)가 조문에 없다.")
            _check_contains(text, b.get("must_contain", []), f"제27조제1항제{b['key']}호")
            return {"basis": f"{LAW_ART27}제{b['key']}호", "text": text}
    raise BuildError("결합표에 위생증명서 법적 근거(use_as: hygiene_legal_basis)가 없다.")


# ---------- 위생약정 ----------

NOTICE_NAME = "수입식품등 신고 및 검사에 관한 규정"


def _article(rule: dict[str, Any], title: str, where: str) -> dict[str, Any]:
    a = next((x for x in rule["articles"] if x["title"] == title), None)
    if a is None:
        raise BuildError(f"{where}: '{title}' 조문이 없다. 고시가 개정된 것으로 보인다.")
    return a


def _article_no(rule: dict[str, Any], no: str, where: str) -> dict[str, Any]:
    a = next((x for x in rule["articles"] if x["no"] == str(no)), None)
    if a is None:
        raise BuildError(f"{where}: 제{no}조가 없다. 고시가 개정된 것으로 보인다.")
    return a


def _admrul_src(doc: dict[str, Any], rule: dict[str, Any], location: str, quote: str) -> dict[str, Any]:
    pub = parse_kr_date(rule["promulgation"].split(",", 1)[-1]) if "," in rule["promulgation"] else None
    return {"source_id": doc["source_id"], "title": f"{rule['name']} [{rule['promulgation']}]", "url": rule["url"],
            "publication_date": pub, "retrieved_at": doc.get("retrieved_at"), "location": location, "quote": quote}


def _hygiene_original(notice: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    """식약처 고시 제9조: 위생증명서의 원본 요건."""
    b = bindings.get("inspection_notice", {}).get("hygiene_original")
    if not b:
        raise BuildError("결합표에 inspection_notice.hygiene_original 이 없다.")
    rule = notice["content"]["rules"][0]
    a = _article_no(rule, b["article"], NOTICE_NAME)
    _check_contains(a["text"], b.get("must_contain", []), f"{NOTICE_NAME} 제{a['no']}조")
    m = re.search(r"원본\([^)]*\)", a["text"])
    return {"basis": f"{NOTICE_NAME}(식약처 고시) 제{a['no']}조", "text": a["text"],
            "original_or_copy": "수출국 정부 발행 " + (m.group(0) if m else "원본")}


def _hygiene_rules(agreements: dict[str, Any], hyg: dict[str, Any], basis: dict[str, Any], original: dict[str, Any],
                   bindings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rules, warnings = [], []
    by_key = {r["key"]: r for r in agreements["content"]["rules"]}
    bound = set()
    for b in bindings.get("hygiene_agreements", []):
        g = by_key.get(b["key"])
        if g is None:
            raise BuildError(f"약정 고시({b['key']}) 정규화 데이터가 없다.")
        bound.add(b["key"])
        cc, where = g["country"], g["name"]
        scope = _article(g, b["scope_article"], where)
        cert = _article(g, b["cert_article"], where)
        _check_contains(scope["text"], b.get("scope_must_contain", []), f"{where} 제{scope['no']}조")
        _check_contains(cert["text"], b.get("cert_must_contain", []), f"{where} 제{cert['no']}조")
        if b.get("excluded_states"):
            _check_contains(scope["text"], b.get("excluded_must_contain", []), f"{where} 제{scope['no']}조(제외 대상)")
        scope_quote = f"제{scope['no']}조({scope['title']}): {scope['text']}"
        note = (b.get("condition_note_prefix", "") + " " if b.get("condition_note_prefix") else "") + \
            f"고시 적용대상에 드는 수산물이어야 한다. {scope_quote}"
        shipment = "선적일 기준" in g["addenda"]
        base = {
            "topic": "hygiene", "priority_class": 3,
            "documents": [{"name": f"{b['document_name']}(수출국 정부기관 발행)", "issuer": "수출국 정부기관(약정 고시의 검사기관)",
                           "original_or_copy": original["original_or_copy"], "category": "국가별 추가 구비서류"}],
            "condition_note": note, "scope_quote": scope_quote,
            "effective_from": g["effective_date"], "effective_to": None,
            "date_basis": "shipment" if shipment else "declaration",
            "legal_basis": [basis["basis"], f"{where} 제{cert['no']}조", original["basis"]],
            "source": _admrul_src(agreements, g, f"제{cert['no']}조({cert['title']})", cert["text"][:500]),
            "related_notices": [{"title": n["title"], "publication_date": n["publication_date"], "url": n["url"]}
                                for n in hyg["content"]["notices"] if n["country"] == cc],
        }
        if b.get("excluded_states"):
            base["excluded_states"] = list(b["excluded_states"])
        rules.append({**base, "id": b["rule_id"], "effect": b["effect"],
                      "when": {"export_country": [cc], "product_class_in": ["seafood"], "product_type": ["raw"]}})
        if b.get("processed_effect"):
            rules.append({**base, "id": b["rule_id"] + "-PROCESSED", "effect": b["processed_effect"],
                          "when": {"export_country": [cc], "product_class_in": ["seafood"], "product_type": ["processed"]},
                          "condition_note": f"고시 적용대상인 수산가공품에 해당할 때 제출한다. {scope_quote}"})
    for key, g in by_key.items():
        if key not in bound:
            warnings.append(f"수집한 약정 고시가 규칙에 연결되지 않았다: {g['name']}")
    for u in hyg["content"].get("unregistered_notices", []):
        if "수산" not in u["title"]:
            continue          # 검색어(위생증명서)에 함께 걸린 축산물 공지는 싣지 않는다
        warnings.append(f"규칙에 반영되지 않은 위생약정·위생증명서 관련 공지: {u['title']} (게시일 {u['date']}, 게시물 번호 {u['sn']})")
    return rules, warnings


# ---------- 식약처 고시 「수입식품등 신고 및 검사에 관한 규정」 ----------

def _inspection_notice_rules(notice: dict[str, Any], bindings: dict[str, Any],
                             hygiene_countries: list[str]) -> list[dict[str, Any]]:
    from ..parsers import paragraph_of

    cfg = bindings.get("inspection_notice", {})
    rule = notice["content"]["rules"][0]
    eff = rule["effective_date"]
    out: list[dict[str, Any]] = []
    for b in cfg.get("articles", []):
        a = _article_no(rule, b["article"], NOTICE_NAME)
        text = paragraph_of(a["text"], b["paragraph"]) if b.get("paragraph") else a["text"]
        loc = f"제{a['no']}조" + (f"제{'①②③④⑤⑥⑦⑧⑨'.index(b['paragraph']) + 1}항" if b.get("paragraph") else "")
        if not text:
            raise BuildError(f"{NOTICE_NAME} {loc} 을 찾지 못했다.")
        _check_contains(text, b.get("must_contain", []), f"{NOTICE_NAME} {loc}")
        r: dict[str, Any] = {
            "id": b["rule_id"], "topic": "general", "priority_class": 5, "effect": b["effect"], "when": {},
            "effective_from": eff, "effective_to": None, "date_basis": "declaration",
            "legal_basis": [f"{NOTICE_NAME}(식약처 고시) {loc}"],
            "source": _admrul_src(notice, rule, f"{loc}({a['title']})", text),
        }
        if b["effect"] == "NOTICE":
            r.update({"topic": "precheck", "documents": [], "title": b["title"]})
            if b.get("when_hygiene_countries"):
                r["when"] = {"export_country": hygiene_countries, **b.get("when", {})}
        else:
            r.update({"documents": [{"name": b["document_name"], "category": "조건부 제출서류"}],
                      "condition_note": b.get("condition_note", "")})
        out.append(r)
    annex = rule.get("annexes", {}).get("별표 2")
    if cfg.get("annex2") and not annex:
        raise BuildError(f"{NOTICE_NAME} 별표 2 정규화 데이터가 없다.")
    title = next((t for t in rule.get("annex_titles", []) if t.startswith("[별표 2]")), "[별표 2]")
    for b in cfg.get("annex2", []):
        found = find_span(annex["pages"], b["start"], b["end"])
        if not found:
            raise BuildError(f"{NOTICE_NAME} 별표 2 에서 '{b['start']}' 로 시작하는 문장을 찾지 못했다.")
        quote, page = found
        _check_contains(re.sub(r"\s+", "", quote), [re.sub(r"\s+", "", w) for w in b.get("must_contain", [])],
                        f"{NOTICE_NAME} 별표 2 ({b['rule_id']})")
        src = _admrul_src(notice, rule, f"{title} PDF {page}쪽", quote)
        src["url"] = annex["url"]
        out.append({
            "id": b["rule_id"], "topic": "item_specific", "priority_class": 4, "effect": b["effect"],
            "when": dict(b.get("when", {})),
            "documents": [{"name": b["document_name"], "category": "품목별 추가 구비서류"}],
            "condition_note": b.get("condition_note", ""), "effective_from": eff, "effective_to": None,
            "date_basis": "declaration", "legal_basis": [f"{NOTICE_NAME}(식약처 고시) 제10조제1항, 별표 2"],
            "source": src, **({"skip_when_unknown": True} if b.get("skip_when_unknown") else {}),
        })
    return out


# ---------- 수입식품안전관리 특별법 조문 ----------

def _act_rules(act: dict[str, Any], bindings: dict[str, Any]) -> list[dict[str, Any]]:
    c = act["content"]
    out = []
    for b in bindings.get("special_act", []):
        paras = c["articles"].get(b["article"])
        if paras is None:
            raise BuildError(f"{c['law_name']} {b['article']} 정규화 데이터가 없다.")
        parts = []
        for mark in b["paragraphs"]:
            if mark not in paras:
                raise BuildError(f"{c['law_name']} {b['article']} 에 {mark} 항이 없다.")
            text = paras[mark]
            if b.get("excerpt_start"):
                i = text.find(b["excerpt_start"])
                j = text.find(b["excerpt_end"], i + 1) if i >= 0 else -1
                if i < 0 or j < 0:
                    raise BuildError(f"{c['law_name']} {b['article']} 에서 '{b['excerpt_start']}' 구간을 찾지 못했다.")
                text = text[i:j].strip()
            parts.append(text if mark == "본문" else f"{mark} {text}")
        quote = " ".join(parts)
        _check_contains(quote, b.get("must_contain", []), f"{c['law_name']} {b['article']}")
        loc = b["article"] + ("" if b["paragraphs"] == ["본문"] else " " + "·".join(b["paragraphs"]) + "항")
        out.append({
            "id": b["rule_id"], "topic": "precheck", "priority_class": 5, "effect": "NOTICE",
            "when": dict(b.get("when", {})),
            "documents": [], "title": b["title"], "effective_from": c["effective_date"], "effective_to": None,
            "date_basis": "declaration",
            "legal_basis": [f"{c['law_name']} {b['article'].split('(')[0]}"],
            "source": _src(act, loc, quote),
            **({"importability_unknown": True} if b.get("importability_unknown") else {}),
        })
    return out


# ---------- 일본산 수산물 ----------

def _jp_seafood_rules(rad: dict[str, Any]) -> list[dict[str, Any]]:
    c = rad["content"]
    sea = c["seafood"]
    definition = (c["seafood_definition"] + " " + c["seafood_definition_scope"]).strip()
    base = {"topic": "jp_seafood", "effective_to": None, "date_basis": "declaration",
            "legal_basis": ["식품의약품안전처 일본산 식품 수입제한조치(임시특별조치)"]}
    where = "원전사고 이후 대응조치 > 일본산 식품 수입제한조치 > 수산물"
    cert_quote = f"{c['certificate_text']} / "
    rules = [
        {**base, "id": "JP-SEAFOOD-BAN", "priority_class": 1, "effect": "PROHIBIT",
         "exclusive_group": "jp_seafood_region",
         "when": {"origin_country": ["JP"], "region_in": sea["banned"]["regions"], "product_class_in": ["seafood"], "product_type": ["raw"]},
         "documents": [], "effective_from": c.get("seafood_ban_effective_from"),
         "condition_note": f"대상 수산물의 범위: {definition}",
         "source": _src(rad, where + " > 수입금지품목", sea["banned"]["text"])},
        {**base, "id": "JP-SEAFOOD-RADCERT", "priority_class": 2, "effect": "REQUIRE",
         "exclusive_group": "jp_seafood_region",
         "when": {"origin_country": ["JP"], "region_in": sea["test_and_origin"]["regions"], "product_class_in": ["seafood"], "product_type": ["raw"]},
         "documents": [
             {"name": "방사능 검사증명서(세슘·요오드)", "issuer": "일본 정부", "category": "국가별 추가 구비서류"},
             {"name": "생산지증명서", "issuer": "일본 정부", "category": "국가별 추가 구비서류"}],
         "effective_from": None,
         "source": _src(rad, where + " > 검사증명서+생산지 증명서", cert_quote + sea["test_and_origin"]["text"])},
        {**base, "id": "JP-SEAFOOD-ORIGINCERT", "priority_class": 2, "effect": "REQUIRE",
         "exclusive_group": "jp_seafood_region",
         "when": {"origin_country": ["JP"], "region_in": sea["origin_only"]["regions"], "product_class_in": ["seafood"], "product_type": ["raw"]},
         "documents": [{"name": "생산지증명서", "issuer": "일본 정부", "category": "국가별 추가 구비서류"}],
         "effective_from": None,
         "source": _src(rad, where + " > 생산지증명서", cert_quote + sea["origin_only"]["text"])},
    ]
    if c.get("additional_nuclide_text"):
        rules.append(
            {**base, "id": "JP-ADDITIONAL-NUCLIDE", "priority_class": 2, "effect": "CONDITIONAL",
             "when": {"origin_country": ["JP"]},
             "documents": [{"name": "추가 핵종 검사증명서", "issuer": "일본 정부 또는 일본 정부가 인정하는 기관",
                            "category": "조건부 제출서류"}],
             "condition_note": "통관단계 방사능 검사에서 세슘 또는 요오드가 검출된 경우 수입자에게 요구된다.",
             "effective_from": None,
             "source": _src(rad, "원전사고 이후 대응조치 > 통관단계 방사능 검사", c["additional_nuclide_text"])})
    return rules


# ---------- 일본산 농산물·축산물 ----------

def _jp_agri_and_livestock_rules(rad: dict[str, Any], bindings: dict[str, Any]) -> list[dict[str, Any]]:
    c = rad["content"]
    base = {"effective_to": None, "date_basis": "declaration", "effective_from": None,
            "legal_basis": ["식품의약품안전처 일본산 식품 수입제한조치"]}
    out: list[dict[str, Any]] = []
    # 농산물: 현별 수입금지 품목. 품목 낱말은 원문 그대로 두고 엔진이 조회 품목과 대조한다.
    for ban in c.get("agricultural_bans", []):
        out.append({**base, "id": f"JP-AGRI-BAN-{ban['region']}", "topic": "jp_agricultural", "priority_class": 1,
                    "effect": "PROHIBIT",
                    "when": {"origin_country": ["JP"], "region_in": [ban["region"]], "category_in": ["agricultural"],
                             "product_name_any": ban["items"]},
                    "documents": [],
                    "condition_note": "품목 이름은 원문 낱말(예: 버섯류, 엽채류)과 조회한 품목명을 대조해 판정했다. "
                                      "원문의 품목 분류에 드는지 애매하면 원문을 확인한다.",
                    "source": _src(rad, f"원전사고 이후 대응조치 > 일본 15개현 27개 품목 농산물 > {ban['region_text']}",
                                   f"{ban['region_text']} 수입금지품목: {ban['text']}")})
    # 축산물: '가공식품,축산물' 구간
    liv = c.get("processed")
    cfg = bindings.get("jp_livestock", {})
    if liv and cfg:
        _check_contains(liv["prohibited_text"], cfg.get("ban_must_contain", []), "일본산 가공식품·축산물 수입금지품목")
        where = "원전사고 이후 대응조치 > 일본산 식품 수입제한조치 > 가공식품,축산물"
        out.append({**base, "id": "JP-MEAT-MILK-BAN", "topic": "jp_livestock", "priority_class": 1, "effect": "PROHIBIT",
                    "when": {"origin_country": ["JP"], "category_in": ["livestock"], "product_tags_any": cfg["ban_tags"]},
                    "documents": [], "condition_note": "",
                    "source": _src(rad, where + " > 수입금지품목", liv["prohibited_text"])})
        quote = f"{c['certificate_text']} / "
        for key, rid, docs in (
                ("test_and_origin", "JP-LIVESTOCK-RADCERT",
                 [{"name": "방사능 검사증명서(세슘·요오드)", "issuer": "일본 정부", "category": "국가별 추가 구비서류"},
                  {"name": "생산지증명서", "issuer": "일본 정부", "category": "국가별 추가 구비서류"}]),
                ("origin_only", "JP-LIVESTOCK-ORIGINCERT",
                 [{"name": "생산지증명서", "issuer": "일본 정부", "category": "국가별 추가 구비서류"}])):
            if not liv.get(key):
                raise BuildError("일본산 가공식품·축산물 구간의 지역 목록을 읽지 못했다.")
            out.append({**base, "id": rid, "topic": "jp_livestock", "priority_class": 2, "effect": "REQUIRE",
                        "exclusive_group": "jp_livestock_region",
                        "when": {"origin_country": ["JP"], "region_in": liv[key]["regions"], "category_in": ["livestock"]},
                        "documents": docs,
                        "condition_note": "수입금지품목(육류 및 원유)이 아닌 축산물에 해당한다.",
                        "source": _src(rad, where + (" > 검사증명서+생산지 증명서" if key == "test_and_origin" else " > 생산지증명서"),
                                       quote + liv[key]["text"])})
    return out


# ---------- 식약처 게재 구비서류 목록 ----------

def _list_rules(lst: dict[str, Any], bindings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    c = lst["content"]
    items = c["items"]
    rules: list[dict[str, Any]] = []
    warnings: list[str] = []
    bound: set[tuple[int, int]] = set()
    prev_regions: list[str] = []
    for b in bindings.get("required_docs_rows", []):
        subs = b["sub"]
        rows = [i for i in items if i["seq"] == b["seq"] and (
            subs == "all" or i["sub"] == subs or (isinstance(subs, list) and i["sub"] in subs))]
        if not rows:
            raise BuildError(f"구비서류 목록에 연번 {b['seq']}(순서 {b['sub']}) 행이 없다.")
        # 대상국가·대상제품 문장으로 행을 확인한다(증명내용에만 낱말이 남아 있는 경우를 걸러낸다).
        joined = " ".join(r["countries_text"] + " " + r["product_text"] for r in rows)
        _check_contains(joined, b.get("must_contain", []), f"구비서류 목록 연번 {b['seq']} 대상국가·대상제품")
        bound.update((r["seq"], r["sub"]) for r in rows)
        country_codes, cw = _row_countries(b, rows[0], c.get("footnotes", []))
        warnings += cw
        when = dict(b.get("when", {}))
        mode = b.get("regions_from_text")
        if mode == "listed":
            m = re.search(r"\(([^()]*)\)", rows[0]["product_text"])
            regions = jp_regions_in_text(m.group(1)) if m else []
            prev_regions = regions
        elif mode == "complement_of_previous":
            if not prev_regions:
                raise BuildError(f"연번 {b['seq']}: 앞 행의 지역 목록이 없어 나머지 지역을 계산할 수 없다.")
            regions = [r for r in all_jp_region_codes() if r not in prev_regions]
        else:
            regions = []
        if mode:
            if len(regions) != b["expected_region_count"]:
                raise BuildError(f"연번 {b['seq']}: 지역 수 불일치(기대 {b['expected_region_count']}, 인식 {len(regions)}).")
            when["region_in"] = regions
        if country_codes:
            when[b.get("country_key", "origin_country")] = country_codes
        effective_from, date_basis = None, b.get("date_basis", "declaration")
        if b.get("effective_from_note"):
            effective_from = parse_kr_date(rows[0]["note"])
            if not effective_from:
                raise BuildError(f"연번 {b['seq']}: '기타' 열에서 적용 시작일을 읽지 못했다: {rows[0]['note']!r}")
        quote = "\n".join(
            f"[대상국가] {r['countries_text']} [대상제품] {r['product_text']} [증명내용] {r['certificate_text']}"
            + (f" [기타] {r['note']}" if r["note"] else "") for r in rows)
        rule = {
            "id": b["rule_id"], "topic": b["topic"],
            "priority_class": 2 if b["topic"].startswith("jp_") else 4, "effect": b["effect"], "when": when,
            "documents": [{"name": b["document_name"], "original_or_copy": rows[0]["original_or_copy"],
                           "category": "국가별 추가 구비서류" if (country_codes or b["topic"].startswith("jp_")
                                                              or "export_country" in when) else "품목별 추가 구비서류"}],
            "condition_note": b.get("condition_note", ""),
            "effective_from": effective_from, "effective_to": None, "date_basis": date_basis,
            "legal_basis": [f"{LAW_ART27}제10호다목"],
            "source": _src(lst, ", ".join(r["location"] for r in rows), quote),
        }
        if b["topic"] == "jp_radiation_processed":
            rule["exclusive_group"] = "jp_processed_region"
        rules.append(rule)

    for i in items:
        if (i["seq"], i["sub"]) not in bound:
            warnings.append(f"구비서류 목록 {i['location']}이 규칙에 연결되지 않았다: {i['product_text'][:60]}")
    for p in lst.get("newer_posts", []):
        warnings.append(f"구비서류 목록 공지 이후 증명서제출 게시판에 올라온 게시물: {p['title']} (게시일 {p['date']})")
    return rules, warnings


def _row_countries(b: dict[str, Any], row: dict[str, Any], footnotes: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """결합표의 countries 설정에 따라 대상국가를 국가 코드로 읽는다. 읽지 못한 이름은 경고로 돌려준다."""
    mode = b.get("countries")
    if not mode:
        return [], []
    declared = None
    if mode == "column":
        names = [x.strip() for x in re.split(r"[,，]", row["countries_text"]) if x.strip()]
        where = f"구비서류 목록 연번 {b['seq']} 대상국가"
    elif mode == "footnote":
        note = next((f for f in footnotes if f["seq"] == b["seq"] and ":" in f["text"]), None)
        if note is None:
            raise BuildError(f"구비서류 목록 연번 {b['seq']} 의 국가 목록 각주를 찾지 못했다.")
        names = [x.strip() for x in note["text"].split(":", 1)[1].split(",") if x.strip()]
        where = f"구비서류 목록 연번 {b['seq']} 각주({note['location']})"
        declared = re.search(r"(\d+)개국", note["text"])
    else:
        raise BuildError(f"결합표 연번 {b['seq']}: countries 값이 잘못됐다: {mode}")
    codes, unresolved = [], []
    for n in names:
        code = country_code(re.sub(r"\([^)]*\)", "", n).strip())
        if code and code not in codes:
            codes.append(code)
        elif not code:
            unresolved.append(n)
    if not codes:
        raise BuildError(f"{where}: 국가를 하나도 읽지 못했다: {row['countries_text'][:60]}")
    warns = []
    if unresolved:
        warns.append(f"{where}에서 국가 사전에 없는 이름이 있어 규칙에서 빠졌다: {', '.join(unresolved)}")
    if declared and int(declared.group(1)) != len(names):
        warns.append(f"{where}: 표기된 국가 수({declared.group(1)}개국)와 열거된 이름 수({len(names)}개)가 다르다.")
    return codes, warns


# ---------- 검사명령 ----------

def _inspection_order_rules(orders: dict[str, Any], bindings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    cfg = bindings.get("inspection_orders", {})
    kw_map: dict[str, str] = cfg.get("product_keywords", {})
    rules, warnings = [], []
    c = orders["content"]
    for o in c["orders"]:
        text = o["product_text"]
        tags = [tag for kw, tag in kw_map.items() if kw in text]
        countries = []
        for token in re.split(r"[,\s]+", re.sub(r"\([^)]*\)", " ", o["country_text"])):
            cc = country_code(token)
            if cc and cc not in countries:
                countries.append(cc)
        if not tags or not countries:
            warnings.append(f"검사명령 {o['location']}의 품목 또는 국가를 연결하지 못해 규칙에서 빠졌다: "
                            f"{text} / {o['country_text']}")
            continue
        key = "export_country" if "수출국" in o["country_text"] else "any_country"
        rules.append({
            "id": f"ORDER-{o['seq']:02d}", "topic": "inspection_order", "priority_class": 2,
            "effect": "CONDITIONAL", "when": {key: countries, "product_tags_any": tags},
            "documents": [{"name": f"검사명령 이행 시험·검사성적서(검사항목: {o['test_items']})",
                           "issuer": "식약처 지정 시험·검사기관 또는 국외 공인검사기관", "category": "조건부 제출서류"}],
            "condition_note": f"검사명령 대상({o['product_text']}, {o['country_text']})에 해당할 때 제출한다. "
                              f"세부 대상(제조업소, 용도 등)은 검사명령서에서 확인한다.",
            "effective_from": o["effective_from"], "effective_to": o["effective_to"], "date_basis": "declaration",
            "legal_basis": ["수입식품안전관리 특별법에 따른 검사명령(식약처 검사명령제 안내)"],
            "source": _src(orders, o["location"],
                           f"[시행일자] {o['period_text']} [대상식품] {o['product_text']} [국가] {o['country_text']} "
                           f"[검사항목] {o['test_items']} / {c.get('compliance_text', '')}"),
        })
    return rules, warnings
