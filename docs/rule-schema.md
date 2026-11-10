# 규칙 형식

## 규칙 묶음

`data/rules/approved/vNNNN.json`. 버전은 묶음 단위다. 승인할 때마다 새 파일이 생기고 이전 파일은 그대로 남는다.

```json
{
  "schema": 1,
  "version": 1,
  "built_at": "2026-11-10T10:13:45+09:00",
  "approved_at": "…", "approved_by": "…", "verification": "source_quoted", "note": "…",
  "rules": [ … ],
  "coverage": {
    "hygiene": {"exhaustive": false, "confirmed_countries": ["AR", "CL", "ES", "NO", "PE", "PH"], "note": "…"},
    "jp_seafood": {"exhaustive": true, "unlisted_regions": []},
    "required_docs_list": {"exhaustive": true, "note": "…"}
  },
  "warnings": ["규칙에 반영되지 않은 공지 …"],
  "sources": {"mfds_required_docs": {"title": "…", "url": "…", "publication_date": "…", "effective_date": "…", "retrieved_at": "…", "content_hash": "…"}}
}
```

## 규칙 하나

```json
{
  "id": "JP-SEAFOOD-RADCERT",
  "topic": "jp_seafood",
  "priority_class": 2,
  "effect": "REQUIRE",
  "exclusive_group": "jp_seafood_region",
  "when": {"origin_country": ["JP"], "region_in": ["JP-14", "JP-13", "JP-01", "…"], "product_type": ["raw"]},
  "documents": [
    {"name": "방사능 검사증명서(세슘·요오드)", "issuer": "일본 정부", "category": "국가별 추가 구비서류"},
    {"name": "생산지증명서", "issuer": "일본 정부", "category": "국가별 추가 구비서류"}
  ],
  "condition_note": "",
  "effective_from": null,
  "effective_to": null,
  "date_basis": "declaration",
  "legal_basis": ["식품의약품안전처 일본산 식품 수입제한조치(임시특별조치)"],
  "source": {
    "source_id": "radsafe_japan_measures",
    "title": "일본산 식품 수입제한조치 …",
    "url": "https://radsafe.mfds.go.kr/CFQCC02F01",
    "publication_date": null,
    "retrieved_at": "2026-11-10T10:13:22+09:00",
    "location": "원전사고 이후 대응조치 > 일본산 식품 수입제한조치 > 수산물 > 검사증명서+생산지 증명서",
    "quote": "수입신고시 방사성 세슘… / 가나가와, 도쿄도, 홋카이도, …"
  },
  "verification": "source_quoted"
}
```

| 필드 | 값 |
|---|---|
| `topic` | `general`, `hygiene`, `jp_seafood`, `jp_radiation_processed`, `item_specific`, `inspection_order` |
| `priority_class` | 1 수입금지·중단, 2 특별 관리, 3 국가별 협약·증명서, 4 품목별, 5 일반 |
| `effect` | `PROHIBIT`, `REQUIRE`, `CONDITIONAL`, `NOTICE`(서류가 아닌 신고 전 확인사항. `title` 이 있어야 하고 결과의 `prechecks` 로 나간다) |
| `when` | 아래 조건 키. 적은 키가 모두 맞아야 해당한다. 비어 있으면 모든 화물 |
| `exclusive_group` | 같은 묶음에서 둘 이상 해당하면 충돌 |
| `date_basis` | `declaration`(수입신고일) 또는 `shipment`(선적일) |
| `verification` | `source_quoted`, `expert_reviewed` |

조건 키:

| 키 | 대조 대상 | 입력이 없을 때 |
|---|---|---|
| `origin_country` | 원산지 | 정보 부족 |
| `export_country` | 수출국(없으면 원산지로 가정) | 정보 부족 |
| `any_country` | 원산지 또는 수출국 | 정보 부족 |
| `region_in` | 생산지역 코드 | 정보 부족 |
| `product_type` | `raw` / `processed` | 정보 부족 |
| `product_tags_any` | 품목 태그 | 품목이 없거나 통칭이면 정보 부족 |

### 0.2.0 에서 추가된 필드

| 필드 | 뜻 |
|---|---|
| `when.state_in` | 제품 상태(예: `live`). 상태가 입력되지 않으면 해당하지 않는 것으로 본다 |
| `excluded_states` | 근거 고시가 적용대상에서 뺀 상태. 해당하면 서류를 `UNKNOWN` 으로 답하고 사유를 적는다 |
| `skip_when_unknown` | 선택 서류. 조건을 판단할 정보가 부족하면 `UNKNOWN` 으로 싣지 않고 뺀다 |
| `scope_quote` | 약정 고시의 적용대상 조문 인용. 가공식품 조회의 사유 문구에도 쓴다 |
| `related_notices` | 같은 국가의 식약처 공지(제목, 게시일, 주소) |
| `title` | `NOTICE` 규칙의 제목 |

검사강화 게시물(`advisories`)은 규칙이 아니다. 정규화 데이터(`fsk_inspection_advisories`)에서 조회 시점에 읽어 붙인다. 그래서 게시물이 새로 올라와도 검토 대기가 생기지 않는다.

## 판정 상태

| 상태 | 뜻 |
|---|---|
| `PROHIBITED` | 수입금지 규칙에 해당(수입 가능 여부에 쓴다) |
| `REQUIRED` | `REQUIRE` 규칙에 해당 |
| `CONDITIONAL` | `CONDITIONAL` 규칙에 해당. 조건을 함께 싣는다 |
| `UNKNOWN` | 정보가 부족하거나, 근거 자료가 규칙에 없거나, 규칙이 충돌한다 |
| `NOT_REQUIRED` | 한정 열거된 목록에 대상이 없다는 것이 확인됐다 |

수입 가능 여부는 `PROHIBITED`, `UNKNOWN`, `NO_PROHIBITION_FOUND` 중 하나다. 마지막 값은 "수록된 금지 규칙에 해당하지 않는다"는 뜻이고, 수입이 허용된다는 확인이 아니다.

## 검증(validate)

다음은 오류이며, 오류가 있는 묶음은 승인할 수 없다.

- 같은 ID 가 둘 이상
- 조건·서류·기간이 같은 중복 규칙
- 출처 항목 누락(`source_id`, `url`, `location`, `quote`)
- 시행 시작일이 종료일보다 늦음
- 배타 묶음 안에서 지역이 겹침
- 알 수 없는 `effect`, 조건 키, `date_basis`
- 제출 규칙인데 서류가 없음

## 비교(diff)

규칙 내용을 ID 별로 비교한다. 수집 시각(`retrieved_at`)과 검증 단계는 비교에서 뺀다. 인용문, 지역 목록, 시행일, 서류가 하나라도 다르면 변경이다. `coverage` 와 `warnings` 의 변화도 변경으로 본다.
