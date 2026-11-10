"""실제 공식 사이트 응답을 녹화해 두고 테스트에서 재생한다.

녹화: `python tests/replay.py record` (공식 사이트에 접속한다. 수집기와 같은 요청만 보낸다.)
녹화본은 tests/fixtures/http/ 에 응답 원문 그대로 저장된다(manifest.json 이 요청과 파일을 잇는다).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "http"


def _key(url: str, data: dict[str, str] | None) -> str:
    return hashlib.sha1((url + "|" + json.dumps(data or {}, sort_keys=True)).encode()).hexdigest()[:16]


class ReplayFetcher:
    """녹화된 응답만 돌려준다. overrides 로 특정 주소의 응답을 바꾸거나 실패시킬 수 있다."""

    def __init__(self, overrides: dict[str, bytes | Exception] | None = None):
        self.manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.overrides = overrides or {}
        self.calls: list[str] = []

    def get(self, url: str, data: dict[str, str] | None = None) -> bytes:
        self.calls.append(url)
        for needle, value in self.overrides.items():
            if needle in url:
                if isinstance(value, Exception):
                    raise value
                return value
        entry = self.manifest.get(_key(url, data))
        if entry is None:
            from food_import.collectors import FetchError
            raise FetchError(f"녹화되지 않은 요청: {url}")
        return (FIXTURES / entry["file"]).read_bytes()

    def recorded(self, needle: str) -> bytes:
        """주소에 needle 이 든 녹화 응답 원문."""
        for entry in self.manifest.values():
            if needle in entry["url"]:
                return (FIXTURES / entry["file"]).read_bytes()
        raise KeyError(needle)


def record() -> None:
    from food_import.collectors import COLLECTORS, HttpFetcher
    from food_import.config import load_config

    cfg = load_config()
    http = HttpFetcher(cfg.settings["http"]["user_agent"], cfg.settings["http"]["min_interval_seconds"])
    manifest: dict[str, dict[str, str]] = {}

    class Recorder:
        def get(self, url, data=None):
            content = http.get(url, data)
            name = f"{len(manifest):02d}_{_key(url, data)}.bin"
            (FIXTURES / name).write_bytes(content)
            manifest[_key(url, data)] = {"url": url, "data": data or {}, "file": name}
            return content

    FIXTURES.mkdir(parents=True, exist_ok=True)
    for old in FIXTURES.glob("*.bin"):
        old.unlink()
    rec = Recorder()
    for s in cfg.sources:
        if s.get("enabled"):
            COLLECTORS[s["collector"]](rec, s.get("params", {}))
    import datetime
    (FIXTURES / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (FIXTURES / "RECORDED_AT.txt").write_text(datetime.datetime.now().astimezone().isoformat(timespec="seconds") + "\n")
    print(f"{len(manifest)}개 응답 녹화")


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        record()
