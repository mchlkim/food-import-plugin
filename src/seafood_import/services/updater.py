"""공식 출처 확인 → 원본 보존 → 정규화 → 규칙 후보 생성 → 승인본과 비교 → 검토 대기 등록.

변경 감지와 적용은 다른 단계다. 이 모듈은 감지와 검토 대기 등록까지만 한다.
적용은 사람이 approve 로 한다(config/settings.yaml 의 update.auto_apply: never).
수집·파싱·규칙 생성 중 어느 단계가 실패해도 승인된 규칙은 그대로 둔다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..collectors import COLLECTORS, Fetcher, FetchError, RawDoc
from ..config import Config
from ..parsers import (ParseError, parse_admin_rule, parse_fsk_board, parse_hygiene_notice, parse_inspection_orders,
                       parse_kr_date, parse_law_article, parse_law_text, parse_pdf_text, parse_radsafe_measures,
                       parse_required_docs_xlsx)
from ..rules import diff_rulesets, validate_ruleset
from ..rules.builder import BuildError, build_candidate
from ..storage import Store, now_iso


def normalize(source: dict[str, Any], docs: list[RawDoc], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """수집한 원본 → 정규화 문서. 구조가 다르면 ParseError."""
    sid, kind = source["id"], source["collector"]
    base = {"source_id": sid, "title": source["title"], "retrieved_at": now_iso()}

    def text(d: RawDoc) -> str:
        return d.content.decode("utf-8", "replace")

    if kind == "impfood_required_docs":
        xl = next((d for d in docs if d.kind == "xlsx"), None)
        if xl is None:
            raise ParseError("엑셀 원본이 없다.")
        return {**base, "title": xl.title or source["title"], "url": xl.extra.get("post_url", xl.url),
                "publication_date": xl.publication_date, "effective_date": parse_kr_date(xl.name),
                "attachment_name": xl.name, "attachment_url": xl.url,
                "newer_posts": xl.extra.get("newer_posts", []),
                "content": parse_required_docs_xlsx(xl.content)}
    if kind == "radsafe_measures":
        return {**base, "url": docs[0].url, "publication_date": None, "content": parse_radsafe_measures(text(docs[0]))}
    if kind == "lawgo_article":
        c = parse_law_article(text(docs[0]), source["params"]["article"])
        return {**base, "title": f"{c['law_name']} {c['article']}", "url": docs[0].url,
                "publication_date": None, "effective_date": c["effective_date"], "content": c}
    if kind == "impfood_page":
        return {**base, "url": docs[0].url, "publication_date": None, "content": parse_inspection_orders(text(docs[0]))}
    if kind == "impfood_notices":
        notices = {n["sn"]: n for n in (previous or {}).get("content", {}).get("notices", [])} if previous else {}
        unregistered = (previous or {}).get("content", {}).get("unregistered_notices", [])
        for d in docs:
            n = parse_hygiene_notice(text(d))
            notices[d.extra["sn"]] = {"country": d.extra["country"], "sn": d.extra["sn"], "url": d.url, **n}
            if "unregistered_notices" in d.extra:
                unregistered = d.extra["unregistered_notices"]
        ordered = sorted(notices.values(), key=lambda n: (n["country"], n["sn"]))
        return {**base, "url": source["params"]["search_url"], "publication_date": None,
                "content": {"notices": ordered, "unregistered_notices": unregistered}}
    if kind == "lawgo_text":
        c = parse_law_text(text(docs[0]), source["params"]["article"])
        return {**base, "title": f"{c['law_name']} {c['article']}", "url": docs[0].url,
                "publication_date": None, "effective_date": c["effective_date"], "content": c}
    if kind == "lawgo_admin_rules":
        rules: list[dict[str, Any]] = []
        for d in docs:
            if d.extra.get("role") == "body":
                rules.append({"key": d.extra["key"], "country": d.extra.get("country"), "url": d.url,
                              "address": d.extra.get("address"), "admRulSeq": d.extra.get("admRulSeq"),
                              "annexes": {}, **parse_admin_rule(text(d))})
        for d in docs:
            if d.extra.get("role") == "annex":
                owner = next(r for r in rules if r["key"] == d.extra["key"])
                owner["annexes"][d.extra["annex"]] = {"url": d.url, **parse_pdf_text(d.content)}
        if not rules:
            raise ParseError("고시 본문이 없다.")
        single = rules[0] if len(rules) == 1 else None
        return {**base, "url": single["url"] if single else source["params"]["address_base"],
                "publication_date": None, "effective_date": single["effective_date"] if single else None,
                "content": {"rules": rules}}
    if kind == "fsk_board":
        return {**base, "url": docs[0].url, "publication_date": None, "advisory": True,
                "detail_url": source["params"].get("detail_url", ""), "content": parse_fsk_board(docs[0].content)}
    raise ParseError(f"이 출처({sid})에는 파서가 없다.")


def run_update(cfg: Config, store: Store, fetcher: Fetcher, only: list[str] | None = None,
               register_pending: bool = True) -> dict[str, Any]:
    report: dict[str, Any] = {"started_at": now_iso(), "sources": [], "pending_id": None}
    for source in cfg.sources:
        sid = source["id"]
        if only and sid not in only:
            continue
        if not source.get("enabled"):
            report["sources"].append({"source_id": sid, "status": "NOT_COLLECTED", "reason": source.get("reason", "")})
            continue
        entry: dict[str, Any] = {"source_id": sid}
        try:
            docs = COLLECTORS[source["collector"]](fetcher, source.get("params", {}))
            new_raw = 0
            for d in docs:
                rec = store.save_raw(sid, d.name, d.content, d.meta())
                new_raw += int(rec["is_new"])
            doc = normalize(source, docs, store.load_normalized(sid))
            changed = store.save_normalized(sid, doc)
            entry.update({"status": "OK", "new_raw_files": new_raw, "content_changed": changed,
                          "latest_document": doc.get("title"), "publication_date": doc.get("publication_date"),
                          "effective_date": doc.get("effective_date")})
            store.set_source_state(sid, {"status": "OK", "last_checked": now_iso(), "last_success": now_iso(),
                                         "error": None, "latest_document": doc.get("title"),
                                         "publication_date": doc.get("publication_date"),
                                         "effective_date": doc.get("effective_date"),
                                         **({"last_content_change": now_iso()} if changed else {})})
        except FetchError as e:
            entry.update({"status": "FETCH_FAILED", "error": str(e)})
            store.set_source_state(sid, {"status": "FETCH_FAILED", "last_checked": now_iso(), "error": str(e)})
        except ParseError as e:
            entry.update({"status": "PARSE_FAILED", "error": str(e)})
            store.set_source_state(sid, {"status": "PARSE_FAILED", "last_checked": now_iso(), "error": str(e)})
        report["sources"].append(entry)

    report.update(compare_with_approved(cfg, store, register_pending))
    failed = [s["source_id"] for s in report["sources"] if s["status"] in ("FETCH_FAILED", "PARSE_FAILED")]
    if failed:
        report["notice"] = (f"수집 또는 파싱에 실패한 출처: {', '.join(failed)}. 해당 출처는 이전 정규화 데이터로 비교했고, "
                            "승인된 규칙은 바꾸지 않았다.")
    report["finished_at"] = now_iso()
    store.log_update({"event": "update_run", "sources": [{k: s.get(k) for k in ("source_id", "status", "content_changed", "error")}
                                                          for s in report["sources"]],
                      "rules": report.get("rules_status"), "pending_id": report.get("pending_id"),
                      "diff_summary": (report.get("diff") or {}).get("summary")})
    return report


def compare_with_approved(cfg: Config, store: Store, register_pending: bool = True) -> dict[str, Any]:
    """보관된 정규화 데이터로 규칙 후보를 만들어 승인본과 비교한다."""
    out: dict[str, Any] = {"pending_id": None}
    try:
        candidate = build_candidate(store.all_normalized(), cfg.bindings)
    except BuildError as e:
        out.update({"rules_status": "BUILD_FAILED", "error": str(e),
                    "message": "규칙 후보를 만들지 못했다. 승인된 규칙을 유지한다."})
        return out
    validation = validate_ruleset(candidate)
    current = store.load_ruleset()
    diff = diff_rulesets(current, candidate)
    out.update({"diff": diff, "validation": validation})
    if not diff["has_changes"]:
        out["rules_status"] = "NO_CHANGE"
        out["message"] = "승인된 규칙과 내용이 같다."
        store.set_source_state("_rules", {"last_compared": now_iso(), "status": "NO_CHANGE"})
        return out
    out["rules_status"] = "CHANGE_DETECTED_VALIDATION_FAILED" if validation["errors"] else "CHANGE_DETECTED"
    out["message"] = ("공식 자료 기준 규칙 내용이 승인본과 다르다. 아직 적용되지 않았다. "
                      + ("검증 오류가 있어 승인할 수 없다." if validation["errors"] else "검토 후 approve 로 적용한다."))
    if register_pending:
        # 같은 내용의 검토 대기가 이미 있으면 새로 만들지 않는다.
        for p in store.list_pending():
            prev = store.load_pending(p["pending_id"])
            if prev and not diff_rulesets(prev["ruleset"], candidate)["has_changes"]:
                out["pending_id"] = p["pending_id"]
                out["message"] += " (같은 내용의 검토 대기 항목이 이미 있다.)"
                break
        else:
            out["pending_id"] = store.save_pending(candidate, {"summary": diff["summary"], "diff": diff,
                                                              "validation": validation})
    store.set_source_state("_rules", {"last_compared": now_iso(), "status": out["rules_status"]})
    return out


def manual_import(cfg: Config, store: Store, source_id: str, file_path: Path, *, title: str = "",
                  url: str = "", publication_date: str | None = None, country: str | None = None,
                  sn: str | None = None) -> dict[str, Any]:
    """자동으로 받지 못한 공식 파일을 운영자가 내려받아 넣는다. 원본은 그대로 보존한다."""
    source = cfg.source(source_id)
    content = Path(file_path).read_bytes()
    name = Path(file_path).name
    kind = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    extra: dict[str, Any] = {"manual": True, "post_title": title, "post_url": url}
    if country:
        extra.update({"country": country, "sn": sn or f"manual-{name}"})
    doc = RawDoc(name=name, content=content, url=url or f"manual:{name}", kind=kind, title=title,
                 publication_date=publication_date, extra=extra)
    rec = store.save_raw(source_id, name, content, doc.meta())
    result: dict[str, Any] = {"source_id": source_id, "raw": rec["path"], "sha256": rec["sha256"]}
    parser_source = dict(source)
    if source["collector"] in ("manual", "openapi_unverified"):
        result.update({"normalized": False,
                       "message": "원본을 보관했다. 이 출처에는 파서가 없어 규칙에 자동 반영되지 않는다. "
                                  "docs/operations.md 의 '수동 규칙 반영' 절차를 따른다."})
        store.set_source_state(source_id, {"status": "MANUAL_RAW_ONLY", "last_checked": now_iso(),
                                           "latest_document": title or name, "publication_date": publication_date})
        store.log_update({"event": "manual_import", **result})
        return result
    normalized = normalize(parser_source, [doc], store.load_normalized(source_id))  # 실패하면 ParseError
    if title:
        normalized["title"] = title
    changed = store.save_normalized(source_id, normalized)
    store.set_source_state(source_id, {"status": "OK", "last_checked": now_iso(), "last_success": now_iso(),
                                       "error": None, "latest_document": normalized.get("title"),
                                       "publication_date": publication_date, "manual": True})
    result.update({"normalized": True, "content_changed": changed})
    result.update(compare_with_approved(cfg, store))
    store.log_update({"event": "manual_import", "source_id": source_id, "raw": rec["path"],
                      "pending_id": result.get("pending_id")})
    return result
