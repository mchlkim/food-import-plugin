"""테스트 공통 준비.

실제 규정 테스트는 2026-11-10 에 공식 사이트에서 녹화한 응답(tests/fixtures/http)으로 만든 규칙을 쓴다.
가상의 규칙을 쓰는 테스트는 이름과 설명에 '가상'이라고 적는다. 가상 규칙은 실제 법적 요건이 아니다.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from replay import ReplayFetcher  # noqa: E402

from seafood_import.config import load_config  # noqa: E402
from seafood_import.services import Service  # noqa: E402
from seafood_import.storage import Store  # noqa: E402


def make_service(tmp_path: Path, overrides=None, bindings=None) -> Service:
    cfg = load_config()
    cfg = copy.deepcopy(cfg)
    if bindings is not None:
        cfg.bindings = bindings
    store = Store(tmp_path / "data")
    return Service(cfg=cfg, store=store, fetcher=ReplayFetcher(overrides))


def bootstrap(svc: Service) -> int:
    """녹화본으로 수집 → 검토 대기 → 승인해 규칙 v1 을 만든다."""
    report = svc.check_regulation_updates()
    assert report["rules_status"] == "CHANGE_DETECTED", report
    res = svc.approve(report["pending_id"], approver="테스트", verification="source_quoted")
    assert res["ok"], res
    return res["version"]


@pytest.fixture(scope="session")
def _bootstrapped(tmp_path_factory) -> Path:
    """녹화본으로 만든 규칙 v1 데이터 폴더. 한 번만 만들고 테스트마다 복사해 쓴다(PDF 파싱이 느리다)."""
    base = tmp_path_factory.mktemp("bootstrapped")
    bootstrap(make_service(base))
    return base / "data"


@pytest.fixture()
def svc(tmp_path, _bootstrapped) -> Service:
    import shutil
    shutil.copytree(_bootstrapped, tmp_path / "data")
    return make_service(tmp_path)


@pytest.fixture()
def empty_svc(tmp_path) -> Service:
    return make_service(tmp_path)


def docs_by_status(result: dict, status: str) -> list[str]:
    return [d["name"] for d in result["documents"] if d["status"] == status]


def doc(result: dict, rule_id: str) -> dict | None:
    return next((d for d in result["documents"] + result["documents_if_not_prohibited"]
                 if d.get("rule_id") == rule_id), None)
