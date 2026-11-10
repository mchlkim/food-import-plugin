# 구조

## 계층

```text
공식 사이트
   │  collectors/   원본 받기(HTTP). 해석하지 않는다
   ▼
data/raw/          원본 그대로 보존 + 메타데이터
   │  parsers/      구조 확인 후 정규화. 구조가 다르면 ParseError
   ▼
data/normalized/   정규화 문서(행·조문·표 단위, 원본 위치 포함)
   │  rules/builder  + config/bindings.yaml → 규칙 후보
   │  rules/         검증(validate), 승인본과 비교(diff)
   ▼
data/rules/pending/    검토 대기  ──(사람이 approve)──▶  data/rules/approved/vNNNN.json
                                                              │
                              rules/engine  ◀─────────────────┘  승인본만 읽어 판정
                                   ▲
                              services/Service   도구 함수(공개 인터페이스)
                              ▲        ▲        ▲
                            cli/     mcp/     다른 프로그램(직접 import)
```

데이터 수집(collectors, parsers), 규칙 판정(rules), 에이전트 연동(cli, mcp, SKILL.md)은 서로를 모른다. `services` 만 셋을 잇는다.

## 모듈

| 경로 | 역할 |
|---|---|
| `src/food_import/collectors/` | `HttpFetcher`(요청 간격·재시도), 게시판 목록·첨부 해석, 출처별 수집 함수 |
| `src/food_import/parsers/` | 엑셀, 일본 조치 페이지, 법령 조문, 검사명령 표, 위생약정 공지, 행정규칙(고시) 본문, PDF 별표, 게시판 목록(JSON) 파서 |
| `src/food_import/normalization/` | 국가·일본 지역·품목(식품 유형 포함)·원재료·상태 사전(`data/terms.yaml`), 자연어 간이 해석 |
| `src/food_import/rules/` | 규칙 형식·검증·비교(`__init__`), 후보 생성(`builder`), 판정(`engine`) |
| `src/food_import/storage/` | 파일 저장소: 원본, 정규화, 규칙 버전, 검토 대기, 이력, 출처 상태 |
| `src/food_import/services/` | `Service`(도구 함수, 조회 전 최신 확인 `ensure_fresh`, 검사강화 게시물 붙이기), `updater`(업데이트 절차), `render`(Markdown) |
| `src/food_import/cli/`, `mcp/` | 인자·스키마 변환만 한다. 판정 로직이 없다 |
| `config/` | 출처 등록부, 실행 설정, 결합표 |

## 판정 흐름

1. 입력 표기를 코드로 정규화한다. 해석하지 못한 국가·지역·날짜는 `INVALID_INPUT` 으로 돌려준다.
2. 기준일(신고 예정일, 선적일 기준 규칙은 선적일)에 시행 중인 규칙만 고른다.
3. 규칙마다 조건을 대조해 해당, 비해당, 정보 부족 중 하나로 본다.
4. 우선순위 순서로 결과를 만든다: 수입금지·중단 → 특별 관리 → 국가별 협약 → 품목별 → 일반. 앞 단계가 해당해도 뒤 단계는 따로 평가한다.
5. 정보 부족인 규칙의 서류는 `UNKNOWN` 으로 싣고, 필요한 정보를 `missing_info` 에 적는다.
6. 배타 묶음(`exclusive_group`)의 규칙이 둘 이상 해당하면 충돌로 보고 결론을 내지 않는다.
7. 위생증명서는 `coverage.hygiene.exhaustive` 에 따라 `UNKNOWN` 또는 `NOT_REQUIRED` 로 답한다.
8. 출처별 최종 확인일, 실패, 미적용 변경을 `warnings` 로 붙인다.

## 결합표(config/bindings.yaml)

원문 문장은 수집기가 가져오고, 결합표는 "이 문장이 어떤 조회 조건에서 적용되는가"만 정한다. 국가·지역 목록, 증명 내용, 시행일은 원문에서 읽는다. 결합표가 기대한 낱말(`must_contain`)이 원문에 없으면 규칙 후보 생성을 멈추고 승인된 규칙을 유지한다.

수산물 낱말이 있는데 결합표에 없는 행은 경고로 올라온다. 새 행을 규칙으로 만들려면 결합표에 항목을 추가하고 `update --offline` → 검토 → `approve` 를 거친다.

## 외부 입력 취급

웹페이지와 첨부파일의 글은 문자열 데이터로만 다룬다. 실행하거나 명령으로 해석하는 경로가 없다. 규칙의 `quote` 로 결과에 실리며, SKILL.md 와 MCP 서버 안내문이 이를 지시로 따르지 말라고 명시한다.
