"""판정 결과(dict)를 사람이 읽는 Markdown 으로 바꾼다. 판정 내용은 바꾸지 않는다."""
from __future__ import annotations

from typing import Any

_STATUS_KO = {"REQUIRED": "필수", "CONDITIONAL": "조건부", "UNKNOWN": "미확인", "NOT_REQUIRED": "불필요",
              "PROHIBITED": "수입금지"}
_VERIF_KO = {"source_quoted": "원문 인용 확인(실무자 검토 전)", "expert_reviewed": "실무자 검토 완료"}
_IMPORT_KO = {"PROHIBITED": "수입금지", "UNKNOWN": "판정 불가(정보 부족 또는 규칙 충돌)",
              "NO_PROHIBITION_FOUND": "수록된 수입금지 규칙에 해당 없음"}


def _doc_lines(d: dict[str, Any]) -> list[str]:
    head = f"- **{d['name']}** [{_STATUS_KO.get(d['status'], d['status'])}]"
    if d.get("original_or_copy"):
        head += f" ({d['original_or_copy'].replace(chr(10), ' ')})"
    lines = [head]
    if d.get("issuer"):
        lines.append(f"  - 발급: {d['issuer']}")
    if d.get("condition"):
        lines.append(f"  - 조건: {d['condition']}")
    if d.get("reason") and d["status"] in ("UNKNOWN", "NOT_REQUIRED"):
        lines.append(f"  - 사유: {d['reason']}")
    if d.get("legal_basis"):
        lines.append(f"  - 근거: {'; '.join(d['legal_basis'])}")
    if d.get("effective_from") or d.get("effective_to"):
        basis = "선적일" if d.get("date_basis") == "shipment" else "신고일"
        lines.append(f"  - 적용 기간({basis} 기준): {d.get('effective_from') or '시작일 미상'} ~ {d.get('effective_to') or '현재'}")
    s = d.get("source")
    if s:
        pub = f", 게시일 {s['publication_date']}" if s.get("publication_date") else ""
        lines.append(f"  - 출처: {s.get('title') or ''} ({s.get('location')}{pub}) {s.get('url')}")
    return lines


def render_markdown(r: dict[str, Any]) -> str:
    q = r["query"]
    out = ["### 수산물 수입신고 구비서류 조회 결과", "", "**제품 정보**"]
    out.append(f"- 원산지: {q.get('origin_country_name') or '미입력'}")
    if q.get("export_country") and q.get("export_country") != q.get("origin_country"):
        out.append(f"- 수출국: {q.get('export_country_name')}")
    if q.get("region_name"):
        out.append(f"- 생산지역: {q['region_name']}")
    prod = " ".join(x for x in (q.get("state_name"), q.get("product_name")) if x) or "미입력"
    out.append(f"- 품목: {prod}" + (f" (학명 {q['scientific_name']})" if q.get("scientific_name") else ""))
    ptype = {"raw": "수산물 원물", "processed": "가공식품"}.get(q.get("product_type"), "미입력")
    out.append(f"- 제품 유형: {ptype}")
    out.append(f"- 적용 기준일: {r['applied_date']}")

    imp = r["importability"]
    out += ["", "**수입 가능 여부**", f"- {_IMPORT_KO.get(imp['status'], imp['status'])}: {imp['summary']}"]
    for p in imp["prohibitions"]:
        out.append(f"  - 근거 원문: {p['reason']}")
        if p.get("scope_note"):
            out.append(f"  - {p['scope_note']}")
        if p.get("effective_from"):
            out.append(f"  - 시행: {p['effective_from']}")
        out.append(f"  - 출처: {p['source'].get('title')} ({p['source'].get('location')}) {p['source'].get('url')}")

    docs = r["documents"]
    if imp["status"] == "PROHIBITED":
        out += ["", "수입금지 대상이므로 구비서류 목록은 싣지 않는다."]
    else:
        groups = [("식약처 제출서류 (필수)", "REQUIRED"), ("조건부 제출서류", "CONDITIONAL"),
                  ("미확인 (근거 자료가 없거나 정보가 부족한 서류)", "UNKNOWN"), ("제출 불필요로 확인된 서류", "NOT_REQUIRED")]
        for title, status in groups:
            sel = [d for d in docs if d["status"] == status]
            if not sel:
                continue
            out += ["", f"**{title}**"]
            cats: dict[str, list[dict[str, Any]]] = {}
            for d in sel:
                cats.setdefault(d.get("category") or "기타", []).append(d)
            for cat, ds in cats.items():
                if status == "REQUIRED":
                    out.append(f"*{cat}*")
                for d in ds:
                    out += _doc_lines(d)

    if r.get("prechecks"):
        out += ["", "**신고 전 확인사항 (제출서류는 아니다)**"]
        for c in r["prechecks"]:
            out.append(f"- **{c['title']}**: {c['quote']}")
            out.append(f"  - 근거: {'; '.join(c['legal_basis'])} {c['source'].get('url')}")

    adv = r.get("advisories") or {}
    if adv.get("items"):
        out += ["", "**검사강화 게시물 (참고, 제목만)**"]
        for a in adv["items"][:10]:
            out.append(f"- {a['date']} {a['title']} {a['url']}")
        out.append(f"  - {adv['note']}")

    if r.get("conflicts"):
        out += ["", "**규칙 충돌**"]
        for c in r["conflicts"]:
            out.append(f"- {c['message']} ({', '.join(c['rule_ids'])})")

    extra = []
    if r.get("missing_info"):
        extra.append("- 누락된 제품 정보: " + ", ".join(r["missing_info"]))
    extra += [f"- 가정: {a}" for a in r.get("assumptions", [])]
    extra += [f"- {c}" for c in r.get("needs_confirmation", [])]
    if extra:
        out += ["", "**추가 확인사항**"] + extra

    b = r.get("data_basis", {})
    out += ["", "**데이터 기준**",
            f"- 판정 규칙 버전: v{b.get('rule_version')} (승인 {str(b.get('rules_approved_at'))[:10]}, "
            f"{_VERIF_KO.get(b.get('verification'), b.get('verification'))})",
            f"- 공식 출처 최종 확인일: {str(b.get('sources_last_checked'))[:10] if b.get('sources_last_checked') else '기록 없음'}"]
    fc = r.get("freshness_check") or {}
    if fc.get("checked"):
        out.append(f"- 이번 조회에서 공식 출처를 확인했다: {fc.get('rules_status')}"
                   + (f" (수집 실패: {', '.join(fc['failed_sources'])})" if fc.get("failed_sources") else ""))
    for w in r.get("warnings", []):
        out.append(f"- 경고[{w['code']}]: {w['message']}")
    out += ["", "이 결과는 수록된 공식 자료와 승인된 규칙에 따른 것이다. 미확인 항목과 최종 제출 서류는 "
                "관할 지방식품의약품안전청에 확인한다."]
    return "\n".join(out)
