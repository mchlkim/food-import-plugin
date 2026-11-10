"""국가·일본 지역·품목·상태 표기를 코드로 정규화하고, 자연어 문장에서 조회 조건을 뽑는다.

여기서 하는 일은 입력 해석이다. 서류 제출 의무는 rules 계층이 판정한다.
자연어 해석은 사전 대조 방식의 간이 파서다. 에이전트 환경에서는 에이전트가 문장을 해석해
구조화된 값을 넘기고, 이 모듈은 표기 정규화와 모호성 확인에 쓴다.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DATA = Path(__file__).parent / "data" / "terms.yaml"


@lru_cache(maxsize=1)
def _terms() -> dict[str, Any]:
    with _DATA.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def _norm(s: str) -> str:
    return re.sub(r"[\s·\-_.,()]+", "", s).lower()


# ---------- 국가 ----------

def country_code(name: str | None) -> str | None:
    if not name:
        return None
    key = _norm(name)
    for code, names in _terms()["countries"].items():
        if key == code.lower() or key in {_norm(n) for n in names}:
            return code
        if key.endswith("산") and key[:-1] in {_norm(n) for n in names}:
            return code
    return None


def countries_in_text(text: str) -> set[str]:
    """문장에 한글 국가명이 나오는 국가 코드 집합."""
    return {code for code, names in _terms()["countries"].items()
            if any(n in text for n in names if not n.isascii())}


def country_name(code: str | None) -> str | None:
    if not code:
        return None
    names = _terms()["countries"].get(code)
    return names[0] if names else code


# ---------- 일본 지역 ----------

def jp_region_code(name: str | None) -> str | None:
    if not name:
        return None
    full = _norm(name)
    variants = {full}
    no_san = full[:-1] if full.endswith("산") and len(full) > 2 else full
    variants.add(no_san)
    if len(no_san) > 2:
        variants.add(re.sub(r"(현|도|부)$", "", no_san))
    for code, names in _terms()["jp_regions"].items():
        if variants & {_norm(n) for n in names}:
            return code
    return None


def jp_region_name(code: str | None) -> str | None:
    if not code:
        return None
    names = _terms()["jp_regions"].get(code)
    return names[0] if names else code


def all_jp_region_codes() -> list[str]:
    return list(_terms()["jp_regions"].keys())


def jp_regions_in_text(text: str) -> list[str]:
    """공식 자료의 목록 문장에서 도도부현 이름을 찾아 코드 목록으로 돌려준다(나온 순서, 중복 제거).

    쉼표·가운뎃점으로 나뉜 낱말 단위로 대조하므로 다른 낱말의 일부와 겹치지 않는다.
    """
    found: list[str] = []
    for token in re.split(r"[,\s·ㆍ、()*:]+", text):
        token = token.strip()
        if not token:
            continue
        code = jp_region_code(token)
        if code and code not in found:
            found.append(code)
    return found


# ---------- 품목 ----------

@dataclass
class ProductMatch:
    tag: str
    matched_name: str
    category: str | None
    generic: bool = False
    candidates: list[str] = field(default_factory=list)
    processed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag, "matched_name": self.matched_name, "category": self.category,
            "generic": self.generic, "candidates": self.candidates, "processed": self.processed,
        }


def _product_index() -> list[tuple[str, dict[str, Any]]]:
    pairs = [(n, p) for p in _terms()["products"] for n in p["names"]]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)  # 긴 이름 먼저: '참다랑어'가 '다랑어'보다 우선
    return pairs


def _to_match(name: str, p: dict[str, Any]) -> ProductMatch:
    # 통칭 묶음 안의 구체적 이름(예: '흰다리새우')을 직접 쓴 경우는 통칭으로 보지 않는다.
    generic = bool(p.get("generic")) and (name == p["names"][0] or name in p.get("also_generic", []) or name.isascii())
    return ProductMatch(
        tag=p["tag"], matched_name=name, category=p.get("category"), generic=generic,
        candidates=list(p.get("candidates", [])) if generic else [],
        processed=bool(p.get("processed")),
    )


def match_product(name: str | None) -> ProductMatch | None:
    """품목명 하나를 사전과 대조한다(정확히 일치하거나 품목명이 입력에 포함된 경우)."""
    if not name:
        return None
    key = _norm(name)
    for n, p in _product_index():
        if key == _norm(n):
            return _to_match(n, p)
    singles = set(_terms()["single_char_products"])
    for n, p in _product_index():
        if n in singles:
            continue
        if _norm(n) in key:
            return _to_match(n, p)
    return None


def product_tags_in_text(text: str, keyword_map: dict[str, str] | None = None) -> list[str]:
    """공식 자료의 대상식품 문장에서 품목 태그를 찾는다."""
    tags: list[str] = []
    if keyword_map:
        for kw, tag in keyword_map.items():
            if kw in text and tag not in tags:
                tags.append(tag)
    return tags


# ---------- 상태·가공 여부 ----------

def state_code(name: str | None) -> str | None:
    if not name:
        return None
    key = _norm(name)
    for code, s in _terms()["states"].items():
        if key == code or key in {_norm(n) for n in s["names"]}:
            return code
    return None


def state_is_processed(code: str | None) -> bool | None:
    if not code:
        return None
    s = _terms()["states"].get(code)
    return bool(s["processed"]) if s else None


def state_name(code: str | None) -> str | None:
    if not code:
        return None
    s = _terms()["states"].get(code)
    return s["names"][0] if s else code


# ---------- 자연어 간이 해석 ----------

_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})"),
]


def _find_date(text: str) -> str | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                return None
    return None


def parse_query_text(text: str) -> dict[str, Any]:
    """문장에서 조회 조건을 뽑는다. 찾지 못한 값은 None 으로 둔다(추측해서 채우지 않는다)."""
    terms = _terms()
    out: dict[str, Any] = {
        "origin_country": None, "export_country": None, "region": None, "product_name": None,
        "state": None, "product_type": None, "declaration_date": None, "shipment_date": None,
        "scientific_name": None, "notes": [],
    }

    # 국가: 문장에 나온 순서대로. '수출국' 낱말 뒤에 나온 국가는 수출국으로 본다.
    hits: list[tuple[int, str]] = []
    for code, names in terms["countries"].items():
        for n in names:
            if re.fullmatch(r"[A-Za-z ]+", n):
                m = re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text, re.I)
                pos = m.start() if m else -1
            else:
                pos = text.find(n)
            if pos >= 0:
                hits.append((pos, code))
                break
    hits.sort()
    codes = [c for _, c in hits]
    export_pos = text.find("수출국")
    via = re.search(r"(경유|거쳐|통해|에서 수출|에서 가공)", text)
    if codes:
        out["origin_country"] = codes[0]
        if len(codes) > 1:
            out["export_country"] = codes[1]
            out["notes"].append("문장에 국가가 둘 이상 있어 첫 번째를 원산지, 두 번째를 수출국으로 읽었다. 맞는지 확인한다.")
        if export_pos >= 0 and len(codes) == 1 and hits[0][0] > export_pos and not via:
            out["notes"].append("국가가 수출국으로만 언급됐다. 원산지가 같은지 확인한다.")

    # 일본 지역
    if out["origin_country"] == "JP" or "JP" in codes:
        ambiguous = set(terms["jp_short_ambiguous"])
        best: tuple[int, str] | None = None
        for code, names in terms["jp_regions"].items():
            for n in names:
                if code in ambiguous and not re.search(rf"{re.escape(n)}\s*현", text):
                    continue
                pos = text.find(n)
                if pos >= 0 and (best is None or pos < best[0]):
                    best = (pos, code)
        if best:
            out["region"] = best[1]

    # 품목: 긴 이름부터, 한 글자 이름은 앞뒤가 한글 낱말의 일부가 아닐 때만
    singles = set(terms["single_char_products"])
    for n, p in _product_index():
        if p["tag"] in ("seafood_generic", "fish_generic"):
            continue
        if n in singles:
            if re.search(rf"(?<![가-힣]){n}(?![가-힣])|(냉동|냉장|활|건조|염장)\s*{n}(?![가-힣])", text):
                out["product_name"] = n
                break
            continue
        if re.fullmatch(r"[A-Za-z ]+", n):
            if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text, re.I):
                out["product_name"] = n
                break
        elif n in text:
            out["product_name"] = n
            break
    if not out["product_name"]:
        for generic in ("수산물", "해산물", "어류", "생선", "활어"):
            if generic in text:
                out["product_name"] = generic
                break

    # 상태
    single_states = set(terms["single_char_states"])
    for code, s in terms["states"].items():
        for n in sorted(s["names"], key=len, reverse=True):
            if n in single_states:
                prod = out["product_name"]
                if prod and re.search(rf"(?<![가-힣]){n}\s?{re.escape(prod)}", text):
                    out["state"] = code
            elif re.fullmatch(r"[A-Za-z]+", n):
                if re.search(rf"(?<![A-Za-z]){n}(?![A-Za-z])", text, re.I):
                    out["state"] = code
            elif n in text:
                out["state"] = code
            if out["state"] == code:
                break
        if out["state"]:
            break
    if not out["state"] and "활어" in text:
        out["state"] = "live"

    # 원물/가공
    pm = match_product(out["product_name"])
    if (pm and pm.processed) or state_is_processed(out["state"]):
        out["product_type"] = "processed"
    elif re.search(r"가공(식품|품)|원료로\s*(한|사용)", text):
        out["product_type"] = "processed"
    elif out["state"] and state_is_processed(out["state"]) is False:
        out["product_type"] = "raw"
    elif "원물" in text:
        out["product_type"] = "raw"

    # 날짜
    d = _find_date(text)
    if d:
        if re.search(r"선적", text):
            out["shipment_date"] = d
        else:
            out["declaration_date"] = d

    m = re.search(r"학명\s*[:：]?\s*([A-Z][a-z]+ [a-z]+)", text)
    if m:
        out["scientific_name"] = m.group(1)
    return out
