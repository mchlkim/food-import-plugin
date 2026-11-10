# 공식 출처

2026-11-10 에 실제로 접속해 확인한 내용이다. 등록부는 [config/sources.yaml](../config/sources.yaml) 이다.

## 자동 수집하는 출처

| ID | 자료 | 주소 | 제공 형식 | 수집 방법 |
|---|---|---|---|---|
| `mfds_required_docs` | 수입신고 시 제출하여야 하는 구비서류 목록 | `https://impfood.mfds.go.kr/CFCII04F01` (증명서제출 게시판) | 게시물 + xlsx 첨부 | 목록에서 제목에 "구비서류 목록"이 든 가장 최근 게시물을 찾고, 상세 페이지의 xlsx 첨부를 받는다 |
| `radsafe_japan_measures` | 일본산 식품 수입제한조치 | `https://radsafe.mfds.go.kr/CFQCC02F01` | HTML | 페이지 본문 |
| `law_enforcement_rule_27` | 수입식품안전관리 특별법 시행규칙 제27조 | `https://www.law.go.kr/법령/수입식품안전관리특별법시행규칙` | HTML | 한글주소 페이지에서 `lsiSeq`, `efYd` 를 읽고 `LSW/lsInfoR.do` 로 본문을 받는다 |
| `mfds_inspection_orders` | 검사명령 대상 식품 현황 | `https://impfood.mfds.go.kr/CFBEE01F01` | HTML 표 | 페이지 본문 |
| `mfds_hygiene_agreement_notices` | 수산물 위생약정·위생증명서 공지 | `https://impfood.mfds.go.kr/CFBAA01F02/getCntntsDetail?cntntsSn=…` | HTML | 등록된 게시물 번호의 본문. "위생약정", "위생증명서" 검색 결과에서 등록되지 않은 수산물 공지를 찾아 경고로 올린다. 규칙의 `related_notices` 에 붙는다 |
| `law_hygiene_agreements` | 국가별 수산물 위생약정 고시 13건(12개국) | `https://www.law.go.kr/행정규칙/<고시명>` | HTML | 한글주소 페이지에서 현행 `admRulSeq` 를 읽고 `LSW/admRulLsInfoR.do` 로 본문을 받는다 |
| `law_import_inspection_notice` | 식약처 고시 「수입식품등 신고 및 검사에 관한 규정」 | 위와 같다 | HTML + PDF | 본문과, 본문 페이지의 별표 2 PDF 링크(`LSW/flDownload.do?flSeq=…`) |
| `law_special_act` | 수입식품안전관리 특별법 제2조·제5조·제11조·제12조 | `https://www.law.go.kr/법령/수입식품안전관리특별법` | HTML | 시행규칙과 같은 방법 |
| `mfds_import_bans` | 수입금지제품 게시물 목록 | `https://impfood.mfds.go.kr/CFCII03F01` | HTML | 목록의 제목·게시일만 쓴다. 규칙 묶음에는 넣지 않는다 |
| `fsk_inspection_advisories` | 검사지시(검사강화) 게시물 목록과 본문 | `https://www.foodsafetykorea.go.kr/portal/board/boardList.do` (bbs031, 분류 577) | JSON | 게시판 페이지가 보내는 것과 같은 목록 요청. 최근 90일 게시물(최대 40건)은 `boardDetail.do` 로 본문도 받는다. 규칙 묶음에는 넣지 않는다 |

시행규칙 출처(`law_enforcement_rule_27`)는 본문 페이지의 링크로 별표 9 PDF 도 받는다. 구비서류 목록 출처는 목록 공지 뒤에 올라온 게시물 본문(최대 5건)을, 위생약정 공지 출처는 등록되지 않은 수산물 공지 본문(최대 15건)을 함께 받는다.

수집 시점의 최신 문서:

- 구비서류 목록: 「(공지)수입식품 수입신고 시 제출해야 하는 구비서류 목록('26.8.4)」, 게시일 2026-08-04, 첨부 `수입신고시 제출하여야 하는 구비서류 목록(2026.8.3.현재).xlsx`. 공지 본문이 밝힌 근거는 시행규칙 제27조제1항제10호다목이다.
- 시행규칙: 시행 2026-09-30, 총리령 제2158호.
- 약정 고시(시행일): 식약처 고시 6건 — 칠레 2020-03-27, 노르웨이 2021-09-23, 필리핀 2022-06-20, 페루 2024-01-13, 아르헨티나 2024-08-10, 스페인 2025-06-24(모두 부칙 적용례가 선적일 기준). 해양수산부·국립수산물품질관리원 고시 6건 — 에콰도르 2019-09-12, 베트남 2022-06-21, 중국·러시아·태국 2023-02-28, 인도네시아 2024-12-10(일부개정 고시는 현행본 시행일). 해양수산부·식약처 공동 고시 — 중국 야생 수산물 2026-07-05.
- 식약처 고시 「수입식품등 신고 및 검사에 관한 규정」: 제2025-59호, 시행 2025-09-01.
- 수입식품안전관리 특별법: 법률 제21525호, 시행 2026-10-08.
- 검사지시 게시판 최신 게시물: 2026-11-05 「겨울철 수입수산물 검사강화」.

첨부 내려받기 주소는 사이트의 공개 스크립트가 만드는 것과 같다.

- `gfnFileDownload(fileSeq, servFileName)` → `https://impfood.mfds.go.kr/file/downloadFile?fileSeq=…&servFileName=…`
- `fnAtchFileDown(url, …, fileName)` → `https://www.foodsafetykorea.go.kr/common/downloadAttchdFile.do?orgFileName=…&ecm_file_no=…`

## 자동 수집하지 않는 출처

| ID | 사유 | 대체 경로 |
|---|---|---|
| `data_go_kr_openapi` | 인증키와 활용신청이 필요해 엔드포인트와 응답 형식을 확인하지 못했다. 확인하지 않은 주소는 코드에 넣지 않았다 | 키 발급 후 형식을 확인해 수집기 추가 |
| `foodsafetykorea_openapi` | 위와 같다 | 위와 같다 |
| `mfds_hygiene_agreement_full_list` | 체결국 전체를 한정 열거한 공식 문서를 찾지 못했다. 국가법령정보센터의 행정규칙 검색은 화면 스크립트의 내부 요청으로만 동작하고 그 형식을 확인하지 못해 수집 경로로 쓰지 않았다 | 새 고시명을 `law_hygiene_agreements` 에 등록 |
| `livestock_import_requirements` | 법 제11조에 따른 축산물·동물성 식품의 국가별 수입위생요건 고시. 고시명과 목록 위치를 확인하지 못했다(추정한 고시명으로 한글주소를 조회했으나 없었다) | 고시명을 확인해 출처로 등록 |
| `bse_country_list` | 구비서류 목록이 'BSE관련 36개국'을 열거하지 않는다 | 목록이 실린 공식 문서를 확인해 출처로 등록 |
| `mfds_photo_exemption_notice` | 사진 제출 제외 대상 공지를 수입식품정보마루 공지 검색에서 찾지 못했다 | 공지 주소를 확인해 출처로 등록 |
| `mfds_import_suspension` | 수입금지제품(`/CFCII03F01`)·수입중단업체(`/CFCCC01F08`)는 업체·제품 단위의 자유 서술이다 | 조회 결과에 직접 확인 항목으로 표시 |

## 접근 제약 기록

- `impfood.mfds.go.kr`, `radsafe.mfds.go.kr` 는 `robots.txt` 를 제공하지 않는다(요청하면 오류 페이지를 돌려준다). 요청 간격 1.5초를 둔다.
- 법제처 Open API(`www.law.go.kr/DRF/…`)는 인증값 없이는 "필수입력요소 검증 실패"를 돌려준다. 그래서 공개 웹페이지 본문을 쓴다. 제3자 중계 서버는 쓰지 않는다.
- `www.law.go.kr/robots.txt` 는 전체 허용(`Allow: /`)이다.
- 한 번 확인하는 데 요청 89건, 3분쯤 걸린다.
- 국가 사전은 수입식품정보마루 수입중단업체 조회 화면(`/CFCCC01F08`)의 국가 선택 목록(코드와 한글명 249개)을 2026-11-10 에 한 번 읽어 `terms.yaml` 에 옮긴 것이다. 자동으로 갱신하지 않는다.
- 로그인, CAPTCHA, 접근 제한 우회는 하지 않는다.
- 수집한 원본은 `data/raw/<출처>/` 에 받은 그대로 저장하고, 주소·게시일·수집 시각·sha256 을 `data/raw/index.jsonl` 에 남긴다.

## 날짜의 구분

| 이름 | 뜻 | 위치 |
|---|---|---|
| 게시일 `publication_date` | 공지가 게시판에 올라온 날 | 원본 메타, 정규화 문서, 규칙의 `source` |
| 시행일 `effective_date` / `effective_from` | 법령 시행일, 약정 시행일, 목록의 "현재" 기준일 | 정규화 문서, 규칙 |
| 수집일 `retrieved_at` | 이 도구가 원본을 받은 시각 | 원본 메타, 정규화 문서, 규칙의 `source` |
| 확인일 `last_checked` / `last_success` | 출처를 마지막으로 확인한 시각 | `data/state/sources.json`, `status` 명령 |
