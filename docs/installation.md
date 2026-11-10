# 설치

## 요구 사항

- Python 3.11 이상
- [uv](https://docs.astral.sh/uv/) (권장) 또는 pip

## 설치

```bash
cd food-import-plugin
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"        # MCP 서버와 테스트까지. 엔진과 CLI 만 쓰면 -e .
.venv/bin/python -m pytest -q     # 오프라인 테스트
```

pip 을 쓰면 `python -m venv .venv && .venv/bin/pip install -e ".[dev]"` 다.

## 초기 설정

저장소에는 2026-11-10 수집본으로 만든 규칙 v6 이 `data/` 에 들어 있어 바로 조회할 수 있다. 의존 패키지는 openpyxl, pyyaml, pypdf 다. 빈 데이터 폴더에서 시작한다면 다음 순서로 만든다.

```bash
scripts/food-import update                 # 공식 사이트 수집 → 검토 대기 등록(종료 코드 3)
scripts/food-import pending                # 검토 대기 ID 확인
scripts/food-import show-pending <ID>      # 규칙 후보와 경고 검토
scripts/food-import approve <ID> --by "<이름>" --verification source_quoted
```

## 실행 방법별 설정

### CLI

```bash
scripts/food-import docs "일본 홋카이도산 냉동 가리비 수입신고 서류 알려줘."
scripts/food-import --json docs --origin 노르웨이 --product 연어 --state 냉동 --date 2026-12-01
scripts/food-import --help
```

`scripts/food-import` 는 프로젝트의 `.venv` 를 찾아 실행한다. 가상환경을 활성화했다면 `food-import` 로 바로 부를 수 있다.

### MCP 서버

전송 방식은 stdio(기본), streamable-http, sse 다. MCP Python SDK 2.x 의 `MCPServer.run` 이 지원하는 범위다.

```bash
.venv/bin/food-import-mcp                              # stdio
.venv/bin/food-import-mcp --transport streamable-http
```

stdio 클라이언트 설정 예(형식은 클라이언트마다 다르다):

```json
{
  "mcpServers": {
    "food-import": {
      "command": "/절대/경로/food-import-plugin/.venv/bin/food-import-mcp",
      "env": {"FOOD_IMPORT_HOME": "/절대/경로/food-import-plugin"}
    }
  }
}
```

Claude Code 라면 `claude mcp add food-import -- /절대/경로/food-import-plugin/.venv/bin/food-import-mcp` 로 등록한다.

### Agent Skill

`SKILL.md` 는 프로젝트 루트에 있다. 에이전트의 스킬 폴더에 프로젝트를 심볼릭 링크로 건다. 스킬 폴더 위치는 에이전트마다 다르므로 인자로 준다.

```bash
scripts/install_skill ~/.claude/skills        # Claude Code 사용자 스킬
scripts/install_skill <프로젝트>/.claude/skills  # 특정 프로젝트에서만
```

같은 이름(`food-import-plugin`)이 이미 있으면 덮어쓰지 않고 멈춘다. 비슷한 설명의 스킬이 이미 있으면 에이전트가 둘 중 무엇을 쓸지 헷갈리므로 하나만 둔다.

### 다른 프로그램에서 직접 호출

```python
from food_import.services import Service

svc = Service()
result = svc.get_required_documents(origin_country="일본", region="홋카이도", product_name="가리비", state="냉동")
print(result["importability"]["status"], [(d["name"], d["status"]) for d in result["documents"]])
```

## 환경변수

| 이름 | 용도 |
|---|---|
| `FOOD_IMPORT_HOME` | 프로젝트 폴더. 패키지를 다른 위치에 설치했을 때 `config/` 를 찾는 데 쓴다 |
| `FOOD_IMPORT_DATA_DIR` | 데이터 폴더를 프로젝트 밖에 둘 때 |

API 키 같은 민감한 값은 소스와 `config/` 에 적지 않는다. `.env.example` 을 본다.
