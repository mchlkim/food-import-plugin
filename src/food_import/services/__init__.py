"""공개 함수(도구). CLI, MCP 서버, 다른 프로그램이 모두 이 Service 를 부른다.

모든 함수는 dict 를 돌려준다. 성공하면 {"ok": True, …}, 실패하면
{"ok": False, "error": {"code": …, "message": …}} 이다.

오류 코드
  INVALID_INPUT      입력값을 해석하지 못했다
  NO_APPROVED_RULES  승인된 규칙 묶음이 없다(최초 update → approve 필요)
  FETCH_FAILED       공식 사이트에서 원본을 받지 못했다
  PARSE_FAILED       원본 구조가 예상과 달라 해석하지 못했다
  BUILD_FAILED       규칙 후보를 만들지 못했다
  VALIDATION_FAILED  규칙 검증 오류로 승인할 수 없다
  NOT_FOUND          대상(검토 대기 항목, 규칙 버전, 출처)이 없다

경고 코드(결과의 warnings 에 들어간다)
  STALE_DATA         출처를 마지막으로 확인한 지 오래됐다
  UPDATE_FAILED      마지막 업데이트에서 이 출처의 수집·파싱이 실패했다
  PENDING_CHANGES    공식 자료 변경이 감지됐지만 아직 승인·적용되지 않았다
  SOURCE_UNVERIFIED  확인하지 못해 수집하지 않는 출처가 있다
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from ..collectors import Fetcher, HttpFetcher
from ..config import Config, load_config
from ..normalization import (countries_in_text, country_code, country_name, jp_region_code, jp_region_name,
                             match_product, parse_query_text, state_code, state_name)
from ..parsers import ParseError
from ..rules import VERIFICATION_LEVELS, validate_ruleset
from ..rules.engine import Query, evaluate, make_query
from ..storage import Store
from . import updater
from .render import render_markdown


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


class Service:
    def __init__(self, cfg: Config | None = None, store: Store | None = None, fetcher: Fetcher | None = None):
        self.cfg = cfg or load_config()
        self.store = store or Store(self.cfg.data_dir)
        self._fetcher = fetcher

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            h = self.cfg.settings.get("http", {})
            self._fetcher = HttpFetcher(h.get("user_agent", "food-import-plugin"),
                                        float(h.get("min_interval_seconds", 1.5)),
                                        float(h.get("timeout_seconds", 60)), int(h.get("max_retries", 2)))
        return self._fetcher

    # ------------------------------------------------------------------ 입력 해석
    def parse_query(self, text: str) -> dict[str, Any]:
        """문장에서 조회 조건을 뽑는다(사전 대조 방식). 찾지 못한 값은 null 이다."""
        if not text or not text.strip():
            return _err("INVALID_INPUT", "질문 문장이 비어 있다.")
        p = parse_query_text(text)
        missing = []
        if not p["origin_country"]:
            missing.append("원산지 국가")
        if not p["product_name"]:
            missing.append("품목명")
        if p["origin_country"] == "JP" and not p["region"]:
            missing.append("생산지역(일본 도도부현)")
        return {"ok": True, "parsed": p, "missing": missing,
                "note": "사전 대조 방식의 간이 해석이다. 에이전트가 문장을 직접 해석해 구조화된 값을 넘겨도 된다."}

    def resolve_terms(self, country: str | None = None, region: str | None = None,
                      product: str | None = None, state: str | None = None) -> dict[str, Any]:
        """국가·일본 지역·품목·상태 표기를 코드로 정규화하고, 통칭 품목이면 후보 어종을 알려준다."""
        out: dict[str, Any] = {"ok": True}
        if country is not None:
            c = country_code(country)
            out["country"] = {"input": country, "code": c, "name": country_name(c)}
        if region is not None:
            r = jp_region_code(region)
            out["region"] = {"input": region, "code": r, "name": jp_region_name(r)}
        if product is not None:
            m = match_product(product)
            out["product"] = {"input": product, **(m.to_dict() if m else {"tag": None})}
        if state is not None:
            s = state_code(state)
            out["state"] = {"input": state, "code": s, "name": state_name(s)}
        return out

    # ------------------------------------------------------------------ 판정
    def _build_query(self, f: dict[str, Any]) -> tuple[Query | None, list[str], list[str], dict[str, Any] | None]:
        notes: list[str] = []
        if f.get("text"):
            parsed = parse_query_text(f["text"])
            notes += parsed.pop("notes", [])
            for k, v in parsed.items():
                if not f.get(k):
                    f[k] = v
        q, errors = make_query(
            origin_country=f.get("origin_country"), export_country=f.get("export_country"), region=f.get("region"),
            product_name=f.get("product_name"), state=f.get("state"), product_type=f.get("product_type"),
            scientific_name=f.get("scientific_name"), declaration_date=f.get("declaration_date"),
            shipment_date=f.get("shipment_date"), category=f.get("category"), ingredients=f.get("ingredients"))
        hard = [e for e in errors if "형식이어야" in e or "여야 한다" in e or "국가 사전" in e or "도도부현 사전" in e]
        if hard:
            return None, [], [], _err("INVALID_INPUT", " ".join(hard), details=errors)
        soft = [e for e in errors if e not in hard]
        if q.product_type is None and q.product_tag and q.product_class in ("seafood", "agricultural"):
            q.product_type = "raw"
            notes.append("가공 여부가 입력되지 않아 원물(단순 절단·가열·숙성·건조·염장 범위)로 가정했다. "
                         "조미·훈제·통조림 등 가공식품이면 product_type 을 processed 로 다시 조회한다.")
        return q, notes, soft, None

    def _ruleset(self) -> dict[str, Any] | None:
        return self.store.load_ruleset()

    def _freshness_warnings(self, ruleset: dict[str, Any]) -> list[dict[str, str]]:
        warns: list[dict[str, str]] = []
        state = self.store.source_state()
        now = dt.datetime.now().astimezone()
        limit = self.cfg.warn_after_days
        for sid in ruleset.get("sources", {}):
            st = state.get(sid, {})
            last = st.get("last_success")
            if st.get("status") in ("FETCH_FAILED", "PARSE_FAILED"):
                warns.append({"code": "UPDATE_FAILED",
                              "message": f"{sid}: 마지막 확인({st.get('last_checked')})에서 실패했다: {st.get('error')}"})
            if not last:
                warns.append({"code": "STALE_DATA", "message": f"{sid}: 이 환경에서 공식 출처를 확인한 기록이 없다."})
            else:
                age = (now - dt.datetime.fromisoformat(last)).days
                if age > limit:
                    warns.append({"code": "STALE_DATA",
                                  "message": f"{sid}: 마지막 확인이 {age}일 전({last[:10]})이다. update 로 최신 여부를 확인한다."})
        for p in self.store.list_pending():
            warns.append({"code": "PENDING_CHANGES",
                          "message": f"공식 자료 변경이 감지됐지만 아직 적용되지 않았다(검토 대기 {p['pending_id']}: {p['summary']}). "
                                     "이 결과는 변경 전 승인 규칙 기준이다."})
        unverified = [s["id"] for s in self.cfg.sources if not s.get("enabled")]
        if unverified:
            warns.append({"code": "SOURCE_UNVERIFIED",
                          "message": "수집하지 않는 출처: " + ", ".join(unverified) + ". 사유는 get_source_status 에서 본다."})
        return warns

    def ensure_fresh(self, refresh: bool | None = None) -> dict[str, Any]:
        """조회 전에 공식 출처를 확인한다. refresh=True 면 항상, None 이면 마지막 확인이 설정 시간보다 오래됐을 때만,
        False 면 하지 않는다. 변경이 있으면 검토 대기로 올릴 뿐 규칙에 적용하지 않는다."""
        hours = float(self.cfg.settings.get("freshness", {}).get("auto_check_after_hours", 24))
        if refresh is False or (refresh is None and hours < 0):
            return {"checked": False, "reason": "확인하지 않도록 지정됐다."}
        state = self.store.source_state()
        now = dt.datetime.now().astimezone()
        oldest: dt.datetime | None = None
        never = False
        for src in self.cfg.sources:
            if not src.get("enabled"):
                continue
            last = state.get(src["id"], {}).get("last_checked")
            if not last:
                never = True
                continue
            t = dt.datetime.fromisoformat(last)
            oldest = t if oldest is None or t < oldest else oldest
        age_h = (now - oldest).total_seconds() / 3600 if oldest else None
        if refresh is None and not never and age_h is not None and age_h <= hours:
            return {"checked": False, "reason": f"마지막 확인이 {age_h:.1f}시간 전이다(기준 {hours:g}시간)."}
        report = self.check_regulation_updates()
        failed = [x["source_id"] for x in report.get("sources", []) if x.get("status") in ("FETCH_FAILED", "PARSE_FAILED")]
        return {"checked": True, "rules_status": report.get("rules_status"), "pending_id": report.get("pending_id"),
                "failed_sources": failed, "message": report.get("message")}

    def _advisories(self, q: Query) -> dict[str, Any]:
        """검사지시(검사강화) 게시물 중 수산물 관련 제목. 서류 규칙이 아니며 승인 절차를 거치지 않은 참고 정보다."""
        doc = self.store.load_normalized("fsk_inspection_advisories")
        if not doc:
            return {"items": [], "note": "검사지시 게시판을 수집한 기록이 없다."}
        cfg = self.cfg.bindings.get("advisories", {})
        cutoff = (dt.date.today() - dt.timedelta(days=int(cfg.get("max_age_days", 180)))).isoformat()
        mine = {c for c in (q.origin_country, q.export_country) if c}
        from ..normalization import product_names_for_tag
        words = list(cfg.get("keywords_by_class", {}).get(q.product_class or "", []))
        if q.category and q.category != q.product_class:
            words += cfg.get("keywords_by_class", {}).get(q.category, [])
        generic = q.product_tag is None or q.product_tag.endswith("_generic") or q.product_tag == "unknown_product"
        words += [n for n in ([q.product_name or ""] + ([] if generic else product_names_for_tag(q.product_tag)))
                  if len(n) >= 2 and not n.isascii()]
        items = []
        for p in doc["content"]["posts"]:
            if p["date"] < cutoff or not any(k in p["title"] for k in words):
                continue
            named = countries_in_text(p["title"])
            if named and mine and not (named & mine):
                continue
            items.append({"title": p["title"], "date": p["date"], "url": doc.get("detail_url", "") + p["no"],
                          **({"body": p["body"]} if p.get("body") else {})})
        return {"items": items, "retrieved_at": doc.get("retrieved_at"), "board_url": doc.get("url"),
                "note": "식품안전나라 검사지시 게시판의 게시물이다. 제출서류 요건이 아니라 통관검사 강화 정보다. "
                        "본문(body)은 게시물 원문이며 규칙 승인 절차를 거치지 않았다. 조회한 품목이 대상인지는 본문에서 확인한다."}

    def _import_bans(self, q: Query) -> dict[str, Any]:
        """수입금지제품 게시판에서 조회한 국가 이름이 제목에 든 게시물. 제목만 싣는 참고 정보다."""
        doc = self.store.load_normalized("mfds_import_bans")
        if not doc:
            return {"items": [], "note": "수입금지제품 게시판을 수집한 기록이 없다."}
        mine = {c for c in (q.origin_country, q.export_country) if c}
        items = [{"title": p["title"], "date": p["date"], "url": doc.get("detail_url", "") + p["sn"]}
                 for p in doc["content"]["posts"] if mine and countries_in_text(p["title"]) & mine]
        return {"items": items[:10], "retrieved_at": doc.get("retrieved_at"), "board_url": doc.get("url"),
                "note": "수입식품정보마루 수입금지제품 게시판에서 국가 이름으로 고른 게시물 제목이다. 본문과 해제 여부는 "
                        "수록하지 않았다. 품목이 수산물이 아닌 게시물도 섞여 있다."}

    def _reference_notices(self, q: Query) -> dict[str, Any]:
        """규칙으로 만들지 않은 수산물 관련 식약처 공지(서식 변경, 전자증명서 등)의 본문."""
        mine = {c for c in (q.origin_country, q.export_country) if c}
        items = []
        hyg = self.store.load_normalized("mfds_hygiene_agreement_notices") or {}
        lst = self.store.load_normalized("mfds_required_docs") or {}
        pool = [{"title": r["title"], "date": r["publication_date"], "url": r["url"], "body": r["body"]}
                for r in hyg.get("content", {}).get("reference_notices", [])]
        pool += [{"title": p["title"], "date": p["date"], "url": p.get("url", ""), "body": p["body"]}
                 for p in lst.get("newer_posts", []) if p.get("body")]
        for n in sorted(pool, key=lambda x: x["date"] or "", reverse=True):
            named = countries_in_text(n["title"])
            if named & mine and "수산" in (n["title"] + n["body"]):
                items.append(n)
        return {"items": items,
                "note": "규칙에 반영하지 않은 식약처 공지의 원문이다(위생증명서 서식 변경, 전자증명서 등). 승인 절차를 거치지 않았다."}

    def get_required_documents(self, refresh: bool | None = None, **fields: Any) -> dict[str, Any]:
        """국가·생산지역·품목·제품 유형으로 구비서류를 판정한다. text 에 문장을 주면 간이 해석을 먼저 한다.

        refresh: 조회 전에 공식 출처를 확인할지(ensure_fresh 참고). 판정은 항상 승인된 규칙으로 한다.
        """
        raw_input = dict(fields)
        q, notes, soft, err = self._build_query(fields)
        if err:
            return err
        if self._ruleset() is None:
            return _err("NO_APPROVED_RULES", "승인된 규칙 묶음이 없다. `food-import update` 후 `approve` 를 실행한다.")
        freshness = self.ensure_fresh(refresh)
        ruleset = self._ruleset()
        result = evaluate(q, ruleset)
        result["advisories"] = self._advisories(q)
        result["import_bans"] = self._import_bans(q)
        result["reference_notices"] = self._reference_notices(q)
        result["freshness_check"] = freshness
        result["assumptions"] = notes + result["assumptions"]
        result["needs_confirmation"] = soft + result["needs_confirmation"]
        warnings = self._freshness_warnings(ruleset)
        state = self.store.source_state()
        checked = [s.get("last_success") for sid, s in state.items() if sid in ruleset.get("sources", {}) and s.get("last_success")]
        result["data_basis"] = {
            "rule_version": ruleset.get("version"), "rules_approved_at": ruleset.get("approved_at"),
            "rules_approved_by": ruleset.get("approved_by"), "verification": ruleset.get("verification"),
            "sources_last_checked": min(checked)[:19] if checked else None,
            "sources": {sid: {"title": s.get("title"), "url": s.get("url"), "publication_date": s.get("publication_date"),
                              "effective_date": s.get("effective_date"), "retrieved_at": s.get("retrieved_at")}
                        for sid, s in ruleset.get("sources", {}).items()},
        }
        out = {"ok": True, **result, "warnings": warnings}
        out["markdown"] = render_markdown(out)
        if self.cfg.settings.get("query_log", True):
            self.store.log_query({"input": raw_input, "query": q.to_dict(), "rule_version": ruleset.get("version"),
                                  "importability": result["importability"]["status"],
                                  "documents": [(d["name"], d["status"]) for d in result["documents"]]})
        return out

    def check_import_restrictions(self, **fields: Any) -> dict[str, Any]:
        """수입금지·제한 해당 여부만 돌려준다."""
        full = self.get_required_documents(**fields)
        if not full["ok"]:
            return full
        return {"ok": True, "query": full["query"], "applied_date": full["applied_date"],
                "importability": full["importability"], "advisories": full["advisories"], "import_bans": full["import_bans"],
                "missing_info": full["missing_info"],
                "conflicts": full["conflicts"],
                "needs_confirmation": [c for c in full["needs_confirmation"] if "수입금지" in c or "수입중단" in c or "원산지" in c],
                "data_basis": full["data_basis"], "warnings": full["warnings"]}

    def get_hygiene_certificate_requirements(self, country: str, product_name: str | None = None,
                                             product_type: str | None = None,
                                             declaration_date: str | None = None,
                                             shipment_date: str | None = None) -> dict[str, Any]:
        """수출국·품목별 위생증명서·검사증명서 제출 의무와, 규칙에 수록된 약정국 목록을 돌려준다."""
        full = self.get_required_documents(origin_country=country, export_country=country,
                                           product_name=product_name or "수산물", product_type=product_type,
                                           declaration_date=declaration_date, shipment_date=shipment_date)
        if not full["ok"]:
            return full
        ruleset = self._ruleset() or {}
        docs = [d for d in full["documents"] + full["documents_if_not_prohibited"]
                if "위생증명서" in d["name"] or "검사증명서" in d["name"] or (d.get("rule_id") or "").startswith("HYG-")]
        agreements = [{"rule_id": r["id"], "country": c, "country_name": country_name(c),
                       "effective_from": r.get("effective_from"),
                       "date_basis": r.get("date_basis"), "source_title": r["source"].get("title"),
                       "source_url": r["source"].get("url"), "publication_date": r["source"].get("publication_date"),
                       "scope": r.get("scope_quote"), "related_notices": r.get("related_notices", [])}
                      for r in ruleset.get("rules", []) if r["topic"] == "hygiene" for c in r["when"]["export_country"]]
        return {"ok": True, "query": full["query"], "applied_date": full["applied_date"], "certificates": docs,
                "prechecks": [p for p in full["prechecks"] if "위생약정" in (p.get("quote") or "")],
                "reference_notices": full["reference_notices"],
                "confirmed_agreements": agreements, "coverage": ruleset.get("coverage", {}).get("hygiene"),
                "needs_confirmation": [c for c in full["needs_confirmation"] if "위생" in c or "원산지" in c],
                "data_basis": full["data_basis"], "warnings": full["warnings"]}

    # ------------------------------------------------------------------ 검색
    def search_regulations(self, query: str, limit: int = 10) -> dict[str, Any]:
        """수집·정규화된 공식 문서(조문, 구비서류 목록 행, 방사능 조치, 검사명령, 약정 고시, 별표, 공지)에서 낱말로 찾는다."""
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        if not tokens:
            return _err("INVALID_INPUT", "검색어가 비어 있다.")
        entries: list[dict[str, Any]] = []
        for sid, doc in self.store.all_normalized().items():
            c, base = doc.get("content", {}), {"source_id": sid, "document_title": doc.get("title"),
                                              "url": doc.get("url"), "publication_date": doc.get("publication_date"),
                                              "effective_date": doc.get("effective_date"), "retrieved_at": doc.get("retrieved_at")}
            if sid == "mfds_required_docs":
                for i in c.get("items", []):
                    entries.append({**base, "location": i["location"],
                                    "text": f"[대상국가] {i['countries_text']} [대상제품] {i['product_text']} [증명내용] {i['certificate_text']} {i['note']}"})
            elif sid == "law_enforcement_rule_27":
                entries.append({**base, "location": f"{c['article']} 제1항 각 호 외의 부분", "text": c["paragraph1_intro"]})
                for k, v in c.get("items", {}).items():
                    entries.append({**base, "location": f"{c['article']} 제1항제{k}호", "text": v})
                for name, annex in c.get("annexes", {}).items():
                    for no, page in enumerate(annex["pages"], 1):
                        entries.append({**base, "url": annex["url"], "location": f"{name} PDF {no}쪽",
                                        "text": re.sub(r"\s+", " ", page)})
            elif sid == "radsafe_japan_measures":
                for key, label in (("seafood", "수산물"), ("processed", "가공식품·축산물"), ("agricultural", "농산물")):
                    sec = c.get(key) or {}
                    parts = [f"수입금지: {sec.get('prohibited_text', '')}"]
                    for sub, sl in (("test_and_origin", "검사증명서+생산지증명서"), ("origin_only", "생산지증명서")):
                        if sec.get(sub):
                            parts.append(f"{sl}: {sec[sub]['text']}")
                    entries.append({**base, "location": f"일본산 식품 수입제한조치 > {label}", "text": " / ".join(parts)})
                for key, label in (("seafood_definition", "수산물 정의"), ("additional_nuclide_text", "추가 핵종 검사증명서")):
                    if c.get(key):
                        entries.append({**base, "location": label, "text": c[key] + " " + c.get("seafood_definition_scope", "")
                                        if key == "seafood_definition" else c[key]})
            elif sid == "mfds_inspection_orders":
                for o in c.get("orders", []):
                    entries.append({**base, "location": o["location"],
                                    "text": f"검사명령 [시행일자] {o['period_text']} [대상식품] {o['product_text']} [국가] {o['country_text']} [검사항목] {o['test_items']}"})
            elif sid in ("law_hygiene_agreements", "law_import_inspection_notice"):
                for g in c.get("rules", []):
                    gb = {**base, "document_title": f"{g['name']} [{g['promulgation']}]", "url": g["url"],
                          "effective_date": g["effective_date"]}
                    for a in g["articles"]:
                        entries.append({**gb, "location": f"제{a['no']}조({a['title']})", "text": a["text"]})
                    if g.get("addenda"):
                        entries.append({**gb, "location": "부칙", "text": g["addenda"]})
                    for name, annex in g.get("annexes", {}).items():
                        for no, page in enumerate(annex["pages"], 1):
                            entries.append({**gb, "url": annex["url"], "location": f"{name} PDF {no}쪽",
                                            "text": re.sub(r"\s+", " ", page)})
            elif sid == "law_special_act":
                for art, paras in c.get("articles", {}).items():
                    for mark, v in paras.items():
                        entries.append({**base, "location": art if mark == "본문" else f"{art} {mark}항", "text": v})
            elif sid == "fsk_inspection_advisories":
                for post in c.get("posts", []):
                    entries.append({**base, "document_title": post["title"], "publication_date": post["date"],
                                    "url": doc.get("detail_url", "") + post["no"],
                                    "location": "검사지시 게시판" + ("(본문)" if post.get("body") else "(제목)"),
                                    "text": post["title"] + " " + post.get("body", "")})
            elif sid == "mfds_import_bans":
                for post in c.get("posts", []):
                    entries.append({**base, "document_title": post["title"], "publication_date": post["date"],
                                    "url": doc.get("detail_url", "") + post["sn"],
                                    "location": "수입금지제품 게시판(제목)", "text": post["title"]})
            elif sid == "mfds_hygiene_agreement_notices":
                for n in c.get("notices", []) + c.get("reference_notices", []):
                    entries.append({**base, "document_title": n["title"], "url": n["url"],
                                    "publication_date": n["publication_date"],
                                    "location": f"공지 본문(게시물 번호 {n['sn']})", "text": n["title"] + " " + n["body"]})
        scored = []
        for e in entries:
            hay = e["text"] + " " + (e["document_title"] or "") + " " + e["location"]
            score = sum(hay.count(t) for t in tokens)
            if all(t in hay for t in tokens):
                score += 100
            if score:
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        results = []
        for score, e in scored[:limit]:
            pos = min((e["text"].find(t) for t in tokens if t in e["text"]), default=0)
            results.append({**{k: v for k, v in e.items() if k != "text"},
                            "snippet": e["text"][max(0, pos - 60): pos + 240], "score": score})
        return {"ok": True, "query": query, "count": len(results), "results": results,
                "note": "검색 대상은 이 도구가 수집·정규화한 문서뿐이다. 결과가 없다고 규정이 없다는 뜻은 아니다."}

    # ------------------------------------------------------------------ 업데이트·상태
    def check_regulation_updates(self, register_pending: bool = True, only: list[str] | None = None,
                                 offline: bool = False) -> dict[str, Any]:
        """공식 출처를 지금 확인해 승인된 규칙과 비교한다. 변경이 있으면 검토 대기로 등록한다(적용은 하지 않는다).

        offline=True 면 사이트에 접속하지 않고 보관된 정규화 데이터로만 비교한다(결합표를 고친 뒤 쓴다).
        """
        if offline:
            report = {"sources": [], **updater.compare_with_approved(self.cfg, self.store, register_pending)}
        else:
            report = updater.run_update(self.cfg, self.store, self.fetcher, only=only, register_pending=register_pending)
        return {"ok": True, **report,
                "applied": False,
                "note": "변경 감지와 적용은 다르다. 감지된 변경은 approve 로 승인해야 판정에 반영된다."}

    def get_source_status(self) -> dict[str, Any]:
        state = self.store.source_state()
        ruleset = self._ruleset()
        sources = []
        for s in self.cfg.sources:
            st = state.get(s["id"], {})
            entry = {"source_id": s["id"], "title": s["title"], "enabled": bool(s.get("enabled")),
                     "collector": s["collector"]}
            if not s.get("enabled"):
                entry.update({"status": st.get("status", "NOT_COLLECTED"), "reason": s.get("reason", ""),
                              "code": "SOURCE_UNVERIFIED" if s["collector"] == "openapi_unverified" else "MANUAL_ONLY"})
                if s.get("env_key"):
                    entry["env_key"] = s["env_key"]
            else:
                entry.update({"status": st.get("status", "NEVER_CHECKED"), "last_checked": st.get("last_checked"),
                              "last_success": st.get("last_success"), "last_content_change": st.get("last_content_change"),
                              "latest_document": st.get("latest_document"), "publication_date": st.get("publication_date"),
                              "effective_date": st.get("effective_date"), "error": st.get("error")})
            sources.append(entry)
        return {
            "ok": True, "sources": sources,
            "rules": {"current_version": self.store.current_version(),
                      "approved_at": (ruleset or {}).get("approved_at"), "approved_by": (ruleset or {}).get("approved_by"),
                      "verification": (ruleset or {}).get("verification"),
                      "rule_count": len((ruleset or {}).get("rules", [])),
                      "last_compared": state.get("_rules", {}).get("last_compared"),
                      "coverage": (ruleset or {}).get("coverage")},
            "pending_reviews": self.store.list_pending(),
            "update_failures": [s["source_id"] for s in sources if s.get("status") in ("FETCH_FAILED", "PARSE_FAILED")],
            "warnings": self._freshness_warnings(ruleset) if ruleset else
            [{"code": "NO_APPROVED_RULES", "message": "승인된 규칙 묶음이 없다."}],
        }

    # ------------------------------------------------------------------ 규칙 관리(운영자)
    def list_pending(self) -> dict[str, Any]:
        return {"ok": True, "pending": self.store.list_pending()}

    def show_pending(self, pending_id: str) -> dict[str, Any]:
        p = self.store.load_pending(pending_id)
        if p is None:
            return _err("NOT_FOUND", f"검토 대기 항목이 없다: {pending_id}")
        return {"ok": True, "pending_id": pending_id, "created_at": p["created_at"], "report": p["report"],
                "rule_ids": [r["id"] for r in p["ruleset"]["rules"]], "warnings": p["ruleset"].get("warnings", [])}

    def approve(self, pending_id: str, approver: str, verification: str = "source_quoted", note: str = "") -> dict[str, Any]:
        if verification not in VERIFICATION_LEVELS:
            return _err("INVALID_INPUT", f"verification 은 {VERIFICATION_LEVELS} 중 하나여야 한다.")
        if not approver.strip():
            return _err("INVALID_INPUT", "승인자 이름이 필요하다.")
        p = self.store.load_pending(pending_id)
        if p is None:
            return _err("NOT_FOUND", f"검토 대기 항목이 없다: {pending_id}")
        v = validate_ruleset(p["ruleset"])
        if v["errors"]:
            return _err("VALIDATION_FAILED", "규칙 검증 오류가 있어 승인할 수 없다.", errors=v["errors"])
        version = self.store.approve_pending(pending_id, approver, verification, note)
        return {"ok": True, "version": version, "verification": verification, "validation_warnings": v["warnings"]}

    def mark_reviewed(self, reviewer: str, note: str = "") -> dict[str, Any]:
        """현재 규칙을 실무자가 원문과 대조해 검토했음을 기록한다. 내용이 같은 새 버전을 expert_reviewed 로 만든다."""
        current = self._ruleset()
        if current is None:
            return _err("NO_APPROVED_RULES", "승인된 규칙 묶음이 없다.")
        if not reviewer.strip():
            return _err("INVALID_INPUT", "검토자 이름이 필요하다.")
        pid = self.store.save_pending(current, {"summary": f"v{current['version']} 내용 그대로 실무자 검토 기록"})
        return self.approve(pid, reviewer, "expert_reviewed", note or f"v{current['version']} 검토 완료")

    def reject(self, pending_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        try:
            self.store.reject_pending(pending_id, reviewer, note)
        except FileNotFoundError as e:
            return _err("NOT_FOUND", str(e))
        return {"ok": True, "pending_id": pending_id, "status": "rejected"}

    def rollback(self, version: int, operator: str, note: str = "") -> dict[str, Any]:
        try:
            self.store.rollback(version, operator, note)
        except FileNotFoundError as e:
            return _err("NOT_FOUND", str(e))
        return {"ok": True, "current_version": version}

    def versions(self) -> dict[str, Any]:
        return {"ok": True, "versions": self.store.list_versions()}

    def show_rules(self, version: int | None = None) -> dict[str, Any]:
        rs = self.store.load_ruleset(version)
        if rs is None:
            return _err("NO_APPROVED_RULES" if version is None else "NOT_FOUND", "규칙 묶음이 없다.")
        return {"ok": True, "ruleset": rs}

    def validate_rules(self, version: int | None = None) -> dict[str, Any]:
        rs = self.store.load_ruleset(version)
        if rs is None:
            return _err("NO_APPROVED_RULES", "승인된 규칙 묶음이 없다.")
        v = validate_ruleset(rs)
        return {"ok": not v["errors"], "version": rs.get("version"), **v,
                **({"error": {"code": "VALIDATION_FAILED", "message": "규칙 검증 오류가 있다."}} if v["errors"] else {})}

    def history(self, limit: int = 30) -> dict[str, Any]:
        return {"ok": True, "history": self.store.update_history(limit)}

    def manual_import(self, source_id: str, file_path: str, title: str = "", url: str = "",
                      publication_date: str | None = None, country: str | None = None, sn: str | None = None) -> dict[str, Any]:
        try:
            self.cfg.source(source_id)
        except KeyError:
            return _err("NOT_FOUND", f"등록되지 않은 출처다: {source_id}. config/sources.yaml 을 본다.")
        if not Path(file_path).is_file():
            return _err("INVALID_INPUT", f"파일이 없다: {file_path}")
        try:
            return {"ok": True, **updater.manual_import(self.cfg, self.store, source_id, Path(file_path), title=title,
                                                        url=url, publication_date=publication_date, country=country, sn=sn)}
        except ParseError as e:
            return _err("PARSE_FAILED", f"원본은 보관했지만 해석하지 못했다: {e}. 승인된 규칙은 그대로다.")
