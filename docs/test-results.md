# 테스트 결과

실행일: 2026-11-10 (Python 3.12.3, pytest 8, mcp 2.3.0, pypdf). 버전 0.3.0 기준

| 구분 | 명령 | 결과 |
|---|---|---|
| 오프라인 | `.venv/bin/python -m pytest -q` | 154 passed, 1 deselected |
| 실제 사이트 접속 | `.venv/bin/python -m pytest -q -m live` | 1 passed |
| 규칙 검증 | `scripts/validate_rules` | 오류 0, 경고 0 (규칙 89개, v6) |
| MCP stdio | JSON-RPC `initialize` → `tools/call` 직접 전송 | `get_required_documents`(베트남 과자, 원재료 돼지고기, `refresh: false`) 응답 확인 |

오프라인 테스트는 2026-11-10 에 공식 사이트에서 녹화한 응답 87건(`tests/fixtures/http/`)을 재생한다. 수집기와 같은 요청만 녹화했고 응답 원문을 그대로 저장했다.

## 파일별 내용

| 파일 | 수 | 다루는 것 |
|---|---|---|
| `tests/test_normalization_and_parsers.py` | 59 | 국가·지역 별칭, 통칭 품목, 예시 문장 해석, 엑셀·일본 조치·조문·검사명령·공지·고시·PDF 별표·게시판 목록 파서, 구조 변경 시 실패 |
| `tests/test_engine.py` | 63 | 국가·지역별, 제품 유형별 판정, 약정 고시 12개국, 활수산물 제외, 신고 전 확인사항, 검사강화 게시물, 날짜, 근거 추적, 충돌, 오류 입력, 보조 도구. 축산물, 일본산 농산물·축산물, 구비서류 목록 전체 연번, 원재료 조건, 식품 유형 미입력 |
| `tests/test_update_and_rules.py` | 26 | 업데이트 절차, 실패 시 기존 규칙 유지, 변경 감지·승인·되돌리기, 수동 반영, 규칙 검증·비교, 조회 전 최신 확인 |
| `tests/test_interfaces.py` | 6 (+live 1) | MCP 도구 목록·스키마·호출, CLI 출력과 종료 코드 |

## 요청서의 시나리오와 테스트

국가·지역:

| 시나리오 | 테스트 |
|---|---|
| 일본 수입금지 지역 수산물 | `test_japan_banned_prefecture_is_prohibited`, `test_japan_all_eight_banned_prefectures` |
| 일본 방사능 검사증명서 제출 대상 지역 | `test_japan_radiation_certificate_region` |
| 일본 생산지증명서 제출 대상 지역 | `test_japan_origin_certificate_only_region` |
| 일본 생산지역 정보 누락 | `test_japan_region_missing_is_unknown_not_allowed` |
| 위생약정 체결 국가의 적용 대상 수산물 | `test_hygiene_agreement_country`, `test_hygiene_agreement_peru_generic_seafood`, `test_all_twelve_agreement_countries_are_built_from_notices`, `test_china_frozen_squid_requires_certificate_and_mentions_wild_notice`, `test_russia_pollock_and_processed_product` |
| 약정 고시가 적용대상에서 뺀 상태, 가공식품 | `test_china_live_fish_is_outside_notice_scope`, `test_processed_food_from_agreement_country_quotes_scope` |
| 위생약정 미확인 국가의 일반 수산물 | `test_country_without_confirmed_agreement_is_unknown_not_not_required` |
| 국가별 특별 제출서류가 있는 품목 | `test_country_specific_item_document`, `test_vietnam_shrimp_inspection_order_and_expiry`, `test_vietnam_shrimp_has_certificate_and_inspection_order`, `test_raw_pufferfish_origin_certificate_from_annex` |

수산물 외 식품 유형(0.3.0):

| 시나리오 | 테스트 |
|---|---|
| 축산물 수출 위생증명서, 수입 가능 여부 미확인 | `test_livestock_requires_export_certificate_and_importability_is_unknown` |
| 일본산 육류 금지, 유가공품 증명서 | `test_japan_meat_is_prohibited_but_cheese_needs_certificates`, `test_japan_livestock_generic_cannot_rule_out_ban` |
| 일본산 농산물 현별 금지 품목 | `test_japan_agricultural_bans_by_prefecture_and_item`, `test_japan_agricultural_without_region_is_unknown` |
| 품목 사전에 없는 품목 | `test_unlisted_product_with_category_uses_its_name`, `test_unknown_product_without_category_asks_for_food_class` |
| 국가·품목별 행(다진마늘, 꿀, 천일염, 죽염) | `test_country_specific_agricultural_rows`, `test_product_specific_processed_rows` |
| 원재료 조건(ASF, 젤라틴, BSE) | `test_ingredient_rows_depend_on_ingredient_input`, `test_gelatin_health_functional_food` |
| 기구·용기, 원물에는 원재료 행을 붙이지 않음 | `test_apparatus_and_raw_products_skip_ingredient_rows` |
| 수산물이 아니면 수산물 위생증명서를 붙이지 않음 | `test_non_seafood_never_gets_seafood_hygiene_certificate` |
| 구비서류 목록 전체 연번 연결, 국가 각주 | `test_all_required_docs_rows_are_bound` |

제품 유형:

| 시나리오 | 테스트 |
|---|---|
| 냉동·냉장·활어 | `test_states_frozen_chilled_live_are_raw` |
| 패류, 해조류 | `test_shellfish_and_seaweed` |
| 조미·가공수산물 | `test_seasoned_processed_seafood_skips_raw_only_rules`, `test_japan_processed_food_is_judged_separately_from_raw`, `test_pufferfish_processed_requires_three_documents` |
| 품목명만 있고 학명이 없는 경우 | `test_product_without_scientific_name` |
| 같은 통칭에 여러 어종 | `test_common_name_covering_several_species` |

데이터 품질:

| 시나리오 | 테스트 |
|---|---|
| 공식 자료 업데이트 | `test_official_content_change_goes_to_pending_then_applies_on_approval` |
| 원본 파일 구조 변경 | `test_xlsx_structure_change_raises`, `test_source_structure_change_keeps_approved_rules`, `test_changed_source_text_blocks_rule_build`, `test_admin_rule_structure_change_raises`, `test_agreement_notice_wording_change_blocks_rule_build` |
| 데이터 다운로드 실패 | `test_download_failure_keeps_approved_rules`, `test_refresh_failure_still_answers_from_approved_rules` |
| 규칙 충돌 | `test_hypothetical_conflicting_rules_block_conclusion`, `test_validation_catches_duplicates_missing_source_and_overlap` |
| 시행일 변경 | `test_effective_date_change_is_detected`, `test_shipment_date_before_effective_date` |
| 중복 규칙, 출처 누락 | `test_validation_catches_duplicates_missing_source_and_overlap`, `test_invalid_pending_cannot_be_approved` |
| 오래된 자료 | `test_stale_data_warning`, `test_query_checks_official_sources_when_last_check_is_old` |
| 미확인 API | `test_unverified_api_is_not_collected_and_has_no_endpoint` |

## 가상 데이터를 쓰는 테스트

아래 테스트는 실제 규정이 아닌 값을 넣는다. 테스트 이름이나 설명에 "가상"이라고 적었다.

- `test_hypothetical_exhaustive_list_allows_not_required`: 약정국 목록을 한정 열거로 표시(실제는 아님)
- `test_hypothetical_conflicting_rules_block_conclusion`: 홋카이도를 금지 목록에도 추가
- `test_official_content_change_…`, `test_same_change_is_not_registered_twice`: 홋카이도를 생산지증명서 구분으로 옮긴 페이지
- `test_effective_date_change_is_detected`: 학명 자료 행의 적용일을 2027-01-01 로 바꾼 엑셀
- `test_unbound_seafood_row_is_reported`, `test_changed_source_text_blocks_rule_build`: 행을 추가하거나 바꾼 엑셀
- `test_validation_catches_…`, `test_invalid_pending_cannot_be_approved`: 오류가 든 규칙 묶음
- `test_hypothetical_agreement_effective_date_change_is_detected`: 페루 고시 시행일을 2027-01-01 로 바꾼 정규화 데이터
- `test_advisory_board_change_does_not_create_pending`: 가상의 검사강화 게시물 한 건

## 개발 중 테스트가 잡은 오류

- 첨부 링크 글자에 파일 크기가 붙어 엑셀 첨부를 찾지 못함 → 확장자까지만 파일명으로 쓰도록 수정
- YAML 에서 노르웨이 코드 `NO` 가 불리언으로 읽힘 → 따옴표 처리
- 통칭 "참치"가 통칭으로 인식되지 않아 참다랑어 검사명령이 누락됨 → 통칭 판정 수정
- 결합표 확인 낱말을 증명내용 문장에서도 찾아, 대상제품이 바뀐 행을 통과시킴 → 대상국가·대상제품 문장만 확인하도록 수정
- (0.2.0) 결합표의 서류명에 쉼표가 있어 YAML 이 중간에서 잘라 읽음 → 따옴표 처리
- (0.2.0) 품목을 모르는 조회에 선택 서류(복어 원산지증명서)가 미확인으로 나옴 → 정보가 부족하면 싣지 않도록 수정
- (0.2.0) 검사강화 게시물 낱말 "김"이 "김장", "김치"에 걸림 → 한 글자 낱말 제거
- (0.3.0) 국가 이름 "인도"가 "인도네시아"에, "가나"가 "가나가와"에 걸림 → 긴 이름부터 찾고 찾은 자리를 가리도록 수정
- (0.3.0) 품목 이름 "고기"가 "물고기"에, "크림"이 "아이스크림"에 걸림 → 사전에서 제거
- (0.3.0) 원물·기구에 원재료 조건 서류가 조건부로 붙음 → 원재료가 없는 유형으로 처리
- (0.2.0) 고시 본문의 낱말 중간 링크 때문에 인용문이 "해 양식 물"처럼 끊김 → 링크 태그를 먼저 없애도록 수정
