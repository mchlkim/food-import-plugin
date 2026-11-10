"""CLI, MCP 서버, 실제 사이트 접속(live) 테스트."""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import make_service

from seafood_import.cli import main as cli
from seafood_import.mcp.server import create_server

EXPECTED_TOOLS = {"get_required_documents", "check_import_restrictions", "get_hygiene_certificate_requirements",
                  "search_regulations", "check_regulation_updates", "get_source_status", "resolve_terms"}


def _payload(result) -> dict:
    """MCP call_tool 결과에서 도구가 돌려준 dict 를 꺼낸다."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured:
        return structured.get("result", structured)
    content = result.content if hasattr(result, "content") else result
    if isinstance(content, tuple):
        content = content[0]
    return json.loads(content[0].text)


def test_mcp_lists_tools_with_schemas(svc):
    server = create_server(svc)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    t = next(t for t in tools if t.name == "get_required_documents")
    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema")
    assert {"origin_country", "region", "product_name", "product_type", "declaration_date"} <= set(schema["properties"])
    assert "pattern" in json.dumps(schema["properties"]["declaration_date"])


def test_mcp_tool_call_returns_same_result_as_service(svc):
    server = create_server(svc)
    args = {"origin_country": "일본", "region": "홋카이도", "product_name": "가리비", "state": "냉동",
            "declaration_date": "2026-11-10"}
    via_mcp = _payload(asyncio.run(server.call_tool("get_required_documents", args)))
    direct = svc.get_required_documents(**args)
    assert via_mcp["importability"] == direct["importability"]
    assert [(d["name"], d["status"]) for d in via_mcp["documents"]] == [(d["name"], d["status"]) for d in direct["documents"]]


def test_mcp_error_is_structured(svc):
    server = create_server(svc)
    out = _payload(asyncio.run(server.call_tool("get_required_documents", {"origin_country": "아틀란티스", "product_name": "가리비"})))
    assert out["ok"] is False and out["error"]["code"] == "INVALID_INPUT"


def test_cli_docs_markdown_and_json(svc, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Service", lambda: svc)
    assert cli.main(["docs", "일본 후쿠시마산 수산물 수입 가능한가?"]) == 0
    out = capsys.readouterr().out
    assert "수입 가능 여부" in out and "수입금지" in out and "데이터 기준" in out
    assert cli.main(["--json", "docs", "--origin", "노르웨이", "--product", "연어", "--state", "냉동"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and any(d["rule_id"] == "HYG-NO" for d in data["documents"])
    assert cli.main(["docs", "--origin", "아틀란티스", "--product", "연어"]) == 1


def test_cli_status_versions_search(svc, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Service", lambda: svc)
    for argv in (["status"], ["versions"], ["validate"], ["search", "생산지증명서"], ["hygiene", "페루"],
                 ["resolve", "--region", "치바", "--product", "참치"], ["parse", "칠레산 냉동 홍합"]):
        assert cli.main(argv) == 0, argv
        assert json.loads(capsys.readouterr().out)["ok"]


def test_cli_update_exit_codes(tmp_path, monkeypatch, capsys):
    s = make_service(tmp_path)
    monkeypatch.setattr(cli, "Service", lambda: s)
    assert cli.main(["update"]) == 3                            # 변경 감지 → 검토 대기
    pid = json.loads(capsys.readouterr().out)["pending_id"]
    assert cli.main(["approve", pid, "--by", "테스트"]) == 0
    capsys.readouterr()
    assert cli.main(["update"]) == 0                            # 변경 없음
    capsys.readouterr()
    assert cli.main(["update", "--offline"]) == 0


@pytest.mark.live
def test_live_official_sites_collect_and_parse(tmp_path):
    """실제 공식 사이트에 접속한다. `pytest -m live` 로만 실행한다."""
    from seafood_import.config import load_config
    from seafood_import.services import Service
    from seafood_import.storage import Store

    s = Service(cfg=load_config(), store=Store(tmp_path / "data"))
    report = s.check_regulation_updates()
    failed = [x for x in report["sources"] if x["status"] in ("FETCH_FAILED", "PARSE_FAILED")]
    assert not failed, failed
    assert report["rules_status"] == "CHANGE_DETECTED" and report["validation"]["errors"] == []
