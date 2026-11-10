# 운영

## 업데이트 절차

```bash
scripts/update_data          # = seafood-import update
```

이 명령이 하는 일:

1. `config/sources.yaml` 의 사용 중인 출처를 차례로 확인한다.
2. 최신 공지와 첨부를 찾아 받는다.
3. 원본을 `data/raw/` 에 그대로 저장한다. 같은 내용(sha256)이면 다시 쓰지 않는다.
4. 파싱해 `data/normalized/` 에 저장한다. 내용이 달라졌으면 이전 판을 `normalized/history/` 로 옮긴다.
5. 정규화 데이터와 결합표로 규칙 후보를 만든다.
6. 후보를 검증하고 승인된 규칙과 내용을 비교한다.
7. 다르면 변경 보고서와 함께 `data/rules/pending/` 에 등록한다. 같은 내용의 검토 대기가 이미 있으면 새로 만들지 않는다.
8. 실행 결과를 `data/history/updates.jsonl` 에 남긴다.

조회 명령(`docs`, `restrictions`, `hygiene`)도 마지막 확인이 `config/settings.yaml` 의 `freshness.auto_check_after_hours`(기본 24시간)보다 오래됐으면 이 절차를 먼저 실행한다. `--refresh` 는 항상, `--no-refresh` 는 생략이다. 스케줄러로 매일 `update_data` 를 돌리면 조회 때는 접속하지 않는다.

적용은 하지 않는다. 변경이 감지된 것과 적용된 것은 다르다. 검토 대기가 있는 동안 조회 결과에는 `PENDING_CHANGES` 경고가 붙고, 판정은 승인된 규칙으로 계속한다.

| 종료 코드 | 뜻 |
|---|---|
| 0 | 변경 없음 |
| 3 | 변경 감지. 검토 대기가 등록됐다 |
| 2 | 수집·파싱·규칙 생성 중 실패. 승인된 규칙은 그대로다 |

실패한 출처는 `status` 의 `update_failures` 와 조회 결과의 `UPDATE_FAILED` 경고로 드러난다.

## 검토와 승인

```bash
scripts/seafood-import pending
scripts/seafood-import show-pending <ID>      # 추가·삭제·변경된 규칙, 달라진 필드의 전후 값, 검증 결과, 경고
scripts/seafood-import approve <ID> --by "<이름>" --verification expert_reviewed --note "<검토 메모>"
scripts/seafood-import reject  <ID> --by "<이름>" --note "<사유>"
```

- `--verification source_quoted`: 원문 인용 위치만 확인했다.
- `--verification expert_reviewed`: 실무자가 내용을 검토했다.
- 검증 오류가 있는 후보는 승인되지 않는다(`VALIDATION_FAILED`).
- 최초 규칙 v1 은 `source_quoted` 로 승인돼 있다. 실무자가 규칙을 원문과 대조한 뒤 `scripts/seafood-import review --by "<이름>" --note "<검토 메모>"` 를 실행하면, 내용이 같은 새 버전이 `expert_reviewed` 로 만들어진다.

## 버전과 되돌리기

```bash
scripts/seafood-import versions
scripts/seafood-import rules --version 1      # 이전 규칙 보기
scripts/seafood-import rollback 1 --by "<이름>" --note "<사유>"
scripts/seafood-import history
```

승인본 파일은 덮어쓰지 않는다. 되돌리기는 현재 버전 포인터(`data/rules/current.json`)만 바꾼다.

## 스케줄링

특정 플랫폼에 묶이지 않는다. 어떤 스케줄러든 `scripts/update_data` 를 부르고 종료 코드를 본다.

```cron
# 매일 07:00 확인. 0 이 아니면 담당자에게 알린다(알림 수단은 환경에 맞게).
0 7 * * * /절대/경로/seafood-import-plugin/scripts/update_data >> /var/log/seafood-import-update.log 2>&1
```

CI 예시는 `.github/workflows/check-regulations.yml` 에 있다.

## 수동 반영

자동으로 받지 못하는 공식 파일은 담당자가 내려받아 넣는다. 원본은 `data/raw/` 에 그대로 보관되고 메타에 `manual: true` 가 붙는다.

```bash
# 구비서류 목록 엑셀을 직접 받은 경우
scripts/seafood-import manual-import mfds_required_docs "수입신고시 제출하여야 하는 구비서류 목록(2026.8.3.현재).xlsx" \
  --title "(공지) 구비서류 목록('26.8.4)" --publication-date 2026-08-04 \
  --url "https://impfood.mfds.go.kr/CFCII04F01"

# 위생약정 공지 페이지를 저장한 경우
scripts/seafood-import manual-import mfds_hygiene_agreement_notices notice.html --country CL --sn 259736
```

파싱에 성공하면 규칙 후보를 만들어 검토 대기로 올린다. 실패하면 `PARSE_FAILED` 를 돌려주고 승인된 규칙은 그대로 둔다.

### 수동 규칙 반영

파서가 없는 문서(약정 고시 PDF·HWP 등)는 `manual-import` 가 원본만 보관한다. 규칙으로 만들려면:

1. 위생약정국을 추가하는 경우: 국가법령정보센터에서 약정 고시명을 확인해 `config/sources.yaml` 의 `law_hygiene_agreements.params.rules` 에 추가하고, `config/bindings.yaml` 의 `hygiene_agreements` 에 적용대상 조문 제목, 증명서 조문 제목, 확인 낱말, 서류명을 적는다. 식약처 공지가 있으면 `mfds_hygiene_agreement_notices.params.notices` 에도 추가한다.
2. 구비서류 목록의 새 행을 연결하는 경우: `config/bindings.yaml` 의 `required_docs_rows` 에 연번, 확인 낱말, 조건을 추가한다.
3. `scripts/seafood-import update`(또는 `--offline`)로 후보를 만들고, 검토 후 승인한다.

`coverage.hygiene.exhaustive` 는 코드(`rules/builder.py`)에서 `False` 로 고정돼 있다. 체결국 전체를 한정 열거한 공식 문서를 확보해 수집·파싱 경로를 만든 뒤에만 바꾼다.

## 새 품목·국가 표기 추가

`src/seafood_import/normalization/data/terms.yaml` 에 이름을 추가한다. 표기 사전은 법적 요건이 아니므로 승인 절차를 거치지 않는다. 다만 품목 태그는 규칙 조건에 쓰이므로 기존 태그 이름은 바꾸지 않는다.

## 점검 명령

```bash
scripts/seafood-import status        # 출처별 최종 확인, 최신 문서, 실패, 규칙 버전, 검토 대기
scripts/validate_rules               # 승인된 규칙 검증
.venv/bin/python -m pytest -q        # 오프라인 테스트
.venv/bin/python -m pytest -q -m live   # 실제 사이트 접속 테스트
```

사이트 구조가 바뀌어 테스트 녹화본을 새로 만들 때는 `.venv/bin/python tests/replay.py record` 를 실행한다. 녹화본이 바뀌면 그 내용에 맞춰 테스트의 기대값도 검토한다.
