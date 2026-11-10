"""프로젝트 루트, 설정 파일, 데이터 폴더 위치를 찾는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    """SEAFOOD_IMPORT_HOME 이 있으면 그 경로, 없으면 이 파일 기준 프로젝트 루트."""
    env = os.environ.get("SEAFOOD_IMPORT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "sources.yaml").exists():
            return parent
    raise RuntimeError(
        "config/sources.yaml 을 찾지 못했다. SEAFOOD_IMPORT_HOME 환경변수에 프로젝트 폴더를 지정한다."
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


@dataclass
class Config:
    root: Path
    settings: dict[str, Any]
    sources: list[dict[str, Any]]
    bindings: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        env = os.environ.get("SEAFOOD_IMPORT_DATA_DIR")
        if env:
            return Path(env).expanduser().resolve()
        return (self.root / self.settings.get("data_dir", "data")).resolve()

    def source(self, source_id: str) -> dict[str, Any]:
        for s in self.sources:
            if s["id"] == source_id:
                return s
        raise KeyError(source_id)

    @property
    def warn_after_days(self) -> int:
        return int(self.settings.get("freshness", {}).get("warn_after_days", 7))


def load_config(root: Path | None = None) -> Config:
    root = root or project_root()
    return Config(
        root=root,
        settings=_load_yaml(root / "config" / "settings.yaml"),
        sources=_load_yaml(root / "config" / "sources.yaml").get("sources", []),
        bindings=_load_yaml(root / "config" / "bindings.yaml"),
    )
