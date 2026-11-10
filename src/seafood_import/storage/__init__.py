"""파일 기반 저장소.

data/
  raw/<source_id>/<sha12>__<파일명>     수집한 원본(내용 변경 없이 보존)
  raw/index.jsonl                       원본 메타데이터(주소, 게시일, 수집 시각, sha256)
  normalized/<source_id>.json           파싱해 정규화한 문서 데이터(최신본)
  normalized/history/…                  정규화 데이터의 이전 판
  rules/approved/v0001.json             승인된 규칙 묶음(버전별, 덮어쓰지 않음)
  rules/current.json                    현재 적용 버전 포인터
  rules/pending/<id>.json               검토 대기 규칙 묶음과 변경 보고서
  history/updates.jsonl                 업데이트 실행 이력
  history/queries.jsonl                 조회 이력
  state/sources.json                    출처별 마지막 확인 상태
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-가-힣()]+", "_", name).strip("_")[:120] or "file"


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        for sub in ("raw", "normalized/history", "rules/approved", "rules/pending", "history", "state"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

    # ----- 공통 -----
    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as fp:
            return json.load(fp)

    def _write_json(self, path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _append_jsonl(self, path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[Any]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fp:
            return [json.loads(line) for line in fp if line.strip()]

    # ----- 원본 -----
    def save_raw(self, source_id: str, name: str, content: bytes, meta: dict[str, Any]) -> dict[str, Any]:
        """원본을 그대로 저장한다. 같은 내용이 이미 있으면 다시 쓰지 않는다."""
        sha = hashlib.sha256(content).hexdigest()
        path = self.dir / "raw" / source_id / f"{sha[:12]}__{_safe_name(name)}"
        is_new = not path.exists()
        if is_new:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        record = {
            "source_id": source_id, "name": name, "sha256": sha,
            "path": str(path.relative_to(self.dir)), "retrieved_at": now_iso(), "is_new": is_new, **meta,
        }
        self._append_jsonl(self.dir / "raw" / "index.jsonl", record)
        return record

    def raw_index(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.dir / "raw" / "index.jsonl")

    def raw_path(self, record: dict[str, Any]) -> Path:
        return self.dir / record["path"]

    # ----- 정규화 데이터 -----
    def load_normalized(self, source_id: str) -> dict[str, Any] | None:
        return self._read_json(self.dir / "normalized" / f"{source_id}.json")

    def save_normalized(self, source_id: str, doc: dict[str, Any]) -> bool:
        """내용이 바뀌었으면 이전 판을 history 로 옮기고 True 를 돌려준다."""
        path = self.dir / "normalized" / f"{source_id}.json"
        prev = self._read_json(path)
        new_hash = canonical_hash(doc.get("content"))
        doc = {**doc, "content_hash": new_hash}
        changed = prev is None or prev.get("content_hash") != new_hash
        if prev is not None and changed:
            stamp = re.sub(r"[^\d]", "", prev.get("retrieved_at", now_iso()))[:14]
            self._write_json(self.dir / "normalized" / "history" / f"{source_id}__{stamp}.json", prev)
        self._write_json(path, doc)
        return changed

    def all_normalized(self) -> dict[str, dict[str, Any]]:
        out = {}
        for p in sorted((self.dir / "normalized").glob("*.json")):
            out[p.stem] = self._read_json(p)
        return out

    # ----- 규칙 -----
    def current_version(self) -> int | None:
        cur = self._read_json(self.dir / "rules" / "current.json")
        return cur["version"] if cur else None

    def load_ruleset(self, version: int | None = None) -> dict[str, Any] | None:
        version = version if version is not None else self.current_version()
        if version is None:
            return None
        return self._read_json(self.dir / "rules" / "approved" / f"v{version:04d}.json")

    def list_versions(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.dir / "rules" / "approved").glob("v*.json")):
            rs = self._read_json(p)
            out.append({
                "version": rs["version"], "approved_at": rs.get("approved_at"),
                "approved_by": rs.get("approved_by"), "rule_count": len(rs.get("rules", [])),
                "note": rs.get("note"), "current": rs["version"] == self.current_version(),
            })
        return out

    def save_pending(self, ruleset: dict[str, Any], report: dict[str, Any]) -> str:
        pid = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._write_json(self.dir / "rules" / "pending" / f"{pid}.json",
                         {"pending_id": pid, "created_at": now_iso(), "ruleset": ruleset, "report": report})
        return pid

    def list_pending(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.dir / "rules" / "pending").glob("*.json")):
            d = self._read_json(p)
            out.append({"pending_id": d["pending_id"], "created_at": d["created_at"],
                        "summary": d["report"].get("summary")})
        return out

    def load_pending(self, pending_id: str) -> dict[str, Any] | None:
        return self._read_json(self.dir / "rules" / "pending" / f"{pending_id}.json")

    def approve_pending(self, pending_id: str, approver: str, verification: str, note: str = "") -> int:
        pend = self.load_pending(pending_id)
        if pend is None:
            raise FileNotFoundError(f"검토 대기 항목이 없다: {pending_id}")
        versions = [v["version"] for v in self.list_versions()]
        version = (max(versions) + 1) if versions else 1
        ruleset = dict(pend["ruleset"])
        ruleset.update({"version": version, "approved_at": now_iso(), "approved_by": approver,
                        "verification": verification, "note": note, "from_pending": pending_id})
        for r in ruleset["rules"]:
            r["verification"] = verification
        self._write_json(self.dir / "rules" / "approved" / f"v{version:04d}.json", ruleset)
        self._write_json(self.dir / "rules" / "current.json", {"version": version, "set_at": now_iso()})
        (self.dir / "rules" / "pending" / f"{pending_id}.json").rename(
            self.dir / "rules" / "pending" / f"{pending_id}.approved"
        )
        self.log_update({"event": "approved", "pending_id": pending_id, "version": version,
                         "approver": approver, "verification": verification, "note": note})
        return version

    def reject_pending(self, pending_id: str, reviewer: str, note: str = "") -> None:
        p = self.dir / "rules" / "pending" / f"{pending_id}.json"
        if not p.exists():
            raise FileNotFoundError(f"검토 대기 항목이 없다: {pending_id}")
        p.rename(p.with_suffix(".rejected"))
        self.log_update({"event": "rejected", "pending_id": pending_id, "reviewer": reviewer, "note": note})

    def rollback(self, version: int, operator: str, note: str = "") -> None:
        if self.load_ruleset(version) is None:
            raise FileNotFoundError(f"규칙 버전이 없다: v{version}")
        prev = self.current_version()
        self._write_json(self.dir / "rules" / "current.json", {"version": version, "set_at": now_iso()})
        self.log_update({"event": "rollback", "from": prev, "to": version, "operator": operator, "note": note})

    # ----- 이력·상태 -----
    def log_update(self, entry: dict[str, Any]) -> None:
        self._append_jsonl(self.dir / "history" / "updates.jsonl", {"at": now_iso(), **entry})

    def update_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._read_jsonl(self.dir / "history" / "updates.jsonl")[-limit:]

    def log_query(self, entry: dict[str, Any]) -> None:
        self._append_jsonl(self.dir / "history" / "queries.jsonl", {"at": now_iso(), **entry})

    def source_state(self) -> dict[str, Any]:
        return self._read_json(self.dir / "state" / "sources.json", {}) or {}

    def set_source_state(self, source_id: str, state: dict[str, Any]) -> None:
        all_state = self.source_state()
        all_state[source_id] = {**all_state.get(source_id, {}), **state}
        self._write_json(self.dir / "state" / "sources.json", all_state)
