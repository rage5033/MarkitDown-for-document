# PDF → MD + 이미지 모음 PDF 변환 개선 지침서

이 문서는 `markitdown_gui` 프로젝트의 PDF 변환 경로를 고치기 위한 작업 지침입니다.
VS Code의 코딩 에이전트가 이 문서와 함께 제공된 두 파일을 사용해 단계별로 작업합니다.

| 파일 | 역할 |
|---|---|
| `CONVERSION_FIX_SPEC.md` | 이 문서. 진단, 출력 형식, 작업 단계, 합격 기준 |
| `pdf_pipeline_reference.py` | 동작이 검증된 참고 구현. GUI와 독립된 모듈, 진입점은 `convert_pdf()` |
| `verify_conversion.py` | 원본 PDF와 변환 결과(MD + 이미지 모음 PDF)를 비교해 합격·불합격을 판정하는 검증 도구 |

---

## 0. 에이전트 작업 규칙

1. 4장의 T0부터 순서대로 진행한다. 단계를 건너뛰지 않는다.
2. 각 단계가 끝나면 `verify_conversion.py`를 실행하고, 출력 표를 그대로 사용자에게 보여준 뒤 다음 단계로 넘어간다.
3. 다음은 하지 않는다.
   - `verify_conversion.py`의 `THRESHOLDS`나 판정 로직을 고쳐서 합격시키기
   - 원본 PDF 수정, 원본 PDF를 git에 커밋하기(저작권 자료)
   - OCR 기본 활성화
   - PDF가 아닌 입력(DOCX, PPTX, XLSX 등)의 기존 MarkItDown 경로 변경
4. 다음 경우에는 먼저 사용자에게 묻는다.
   - 기존 코드를 삭제할 때(우회만 하는 것은 묻지 않아도 됨)
   - 3장의 출력 형식을 바꿔야 할 때
   - 합격 기준을 만족하지 못한 채 다음 단계로 가야 할 때
5. 단계마다 커밋한다. 커밋 메시지는 `T2: PDF 텍스트 경로를 pdf_pipeline으로 교체`처럼 단계 번호로 시작한다.

---

## 1. 배경: 현재 출력의 문제

원본 논문 9편(IEEE 4, Elsevier 3, MDPI 2, 총 170쪽)을 현재 프로그램으로 변환한 결과를 `verify_conversion.py`로 측정했다. 9편 모두 불합격이다(전체 결과는 부록 A).

| 지표 | 현재 출력 (9편) | 참고 구현 (같은 9편) |
|---|---|---|
| 본문 보존율 (coverage) | 81.5 – 96.5 % | 97.0 – 99.4 % |
| 줄 순서 유지율 (order, 2단 섞임 검출) | 8.2 – 86.8 % | 94.4 – 100 % |
| 공백 소실 단어 비율 (glued) | 최대 23.7 % | 최대 0.26 % |
| 쪽 구분 표시 | 0편 | 9편 모두 |
| 그림 (캡션 라벨 기준) | 48 / 142 | 142 / 142 |
| 표 | 0 / 34 | 34 / 34 |
| 용량 (원본 67.7 MB 대비) | 15.9 MB (23.5 %) | 10.7 MB (15.8 %) |

현재 출력의 그림 48개는 라벨만 맞은 개수다. 이 가운데 일부는 그래프의 래스터 부분만 잘려 있어(축·범례 없음), 사람이 확인했을 때 온전한 그림은 43개였다.

### 증상과 원인

| 증상 | 원인 | 해결 (참고 구현) |
|---|---|---|
| 2단 편집의 왼쪽·오른쪽 단 줄이 한 줄로 섞임 | MarkItDown 기본 PDF 변환은 단 구성을 분석하지 않고 줄 단위로 텍스트를 뽑음 | pymupdf4llm 레이아웃 분석으로 읽기 순서 복원 |
| 공백이 사라진 단어(`Reconfigurablebatteriescan…`) | 원문 텍스트층에 공백 문자가 없는 PDF를 그대로 추출 | 글자 간격 기반 공백 복원(pymupdf4llm) |
| 아래첨자가 다른 줄로 분리(`SIB` / `HE`) | 줄 단위 추출 | 레이아웃 분석 결과에서 같은 줄로 결합 |
| 벡터 그래프·회로도 누락 (88개) | PDF 안의 래스터 이미지 객체만 추출 | 캡션 기준 영역을 페이지 렌더링으로 잘라냄 |
| 래스터 그림 일부 누락 | 크기 등 조건 필터 | 영역 기반이라 필터 불필요 |
| 캡션 번호 오류 (5건) | 가장 가까운 캡션을 방향·차단 조건 없이 매칭 | 그림=아래 캡션, 표=위 캡션, 사이에 본문이 있으면 매칭 금지 |
| 표 값이 MD에도 이미지에도 없음 (7개 표) | 이미지로 들어간 표, 글자 윤곽선으로 그려진 표 | 표 영역 렌더링 + 가능한 경우 MD 텍스트 표 |
| 수식 판독 불가 (`v c1 ¼v c2 ¼0:5v E`) | 원문 폰트 인코딩 오류 | 수식 텍스트는 MD에 남기고, 깨진 수식은 이미지로도 저장 |
| 다운로드 스탬프·쪽 머리글이 쪽마다 반복 | 머리글·바닥글 미제거 | 레이아웃의 page-header/page-footer 제거 |
| 쪽 구분이 없어 쪽 번호로 인용 불가 | — | 쪽마다 `<!-- page: N -->` |
| 파일명이 128자에서 잘려 접미사 손상(`_ima`, 접미사 없음) | 접미사를 붙인 뒤 자름 | 본문 이름을 먼저 자르고 접미사를 붙임 |
| 이미지 모음 PDF 머리글의 캡션 텍스트에 공백 없음 | 원본 추출 텍스트 사용 | 레이아웃 텍스트 사용 |

---

## 2. 목표와 범위

- **대상:** PDF 입력만 새 경로(`convert_pdf`)로 바꾼다. 다른 형식은 기존 MarkItDown 경로를 유지한다.
- **유지:** GUI 동작, 파일·폴더 선택, 일괄 변환 흐름, 출력 폴더 구조.
- **출력:** MD와 이미지 모음 PDF를 원본과 같은 이름 체계로 만든다(3장). 현재 형식과 호환되도록 매핑표와 이미지 모음 PDF 머리글 구조를 유지하고, 필요한 항목만 추가했다.
- **완료 조건:** 테스트 세트 10편이 `verify_conversion.py`에서 모두 PASS (5장).

---

## 3. 출력 형식 명세

`verify_conversion.py`가 이 형식을 읽어 검사하므로, 아래 문구와 구조는 바꾸지 않는다.

### 3.1 파일 이름

| 파일 | 이름 |
|---|---|
| MD | `<stem>.md` |
| 이미지 모음 PDF | `<stem>_images.pdf` (접미사 고정) |
| 개별 이미지(선택) | `images/<stem 앞 40자>_p<쪽 3자리>_<순번 2자리>_<라벨>.jpg` |

- `stem = safe_stem(원본 파일명, max_len=90)`: Windows 금지 문자와 괄호를 `_`로 바꾸고 연속 `_`를 하나로 줄인 뒤 90자로 자른다. 접미사는 자른 **다음에** 붙인다.
- 같은 이름의 MD가 이미 있고 그 MD의 `source_sha256`이 다르면(다른 원본) `<stem 83자>_<sha256 앞 6자>`로 바꾼다. 같은 원본이면 덮어쓴다.

### 3.2 MD 구조

```markdown
---
source_file: "원본 파일명.pdf"
source_sha256: "…"
pages: 9
images_pdf: "<stem>_images.pdf"
images: 15
converter: "pdf_pipeline_reference / pymupdf 2.1 / pymupdf4llm 2.1"
converted_at: "2026-11-16T10:13:45"
---

<!-- page: 1 -->

# 논문 제목
본문 …

<!-- page: 2 -->

![Fig 1](images/<파일명>.jpg)

**Fig. 1.** 캡션 …

---

## 이미지 파일 목록

이 문서의 이미지는 이미지 모음 PDF('<stem>_images.pdf')에 한 쪽에 1개씩 들어 있습니다. 각 쪽 상단에 본문 이미지 링크와 같은 파일명이 표시되어 있습니다.

| 파일명 | 이미지 모음 PDF | 원문 위치 | 라벨 | 영역 판정 | 설명 |
|---|---|---|---|---|---|
| … | <stem>_images.pdf 1쪽 | 2쪽 | Fig 1 | layout | Fig. 1. … |
```

- 앞머리(front matter) 값은 JSON 문자열로 쓴다(따옴표·한글 안전).
- 쪽 구분은 모든 쪽에 넣는다(빈 쪽 포함). 쪽 머리글·바닥글은 넣지 않는다.
- 그림·표 링크는 해당 캡션 줄 바로 앞에 넣는다. 캡션을 찾지 못하면 그 쪽 끝에 넣는다.
- 라벨 형식: `Fig 3`, `Fig A.1`, `Table II`, `Table B.8`, 수식은 `Eq (12)`. 캡션 없는 이미지는 `-`.
- 영역 판정: `layout`(레이아웃 상자로 확정), `band`(대체 영역, 사람이 확인 필요).
- 파일은 UTF-8, 줄바꿈 LF.

### 3.3 이미지 모음 PDF

- A4 세로, 한 쪽에 이미지 1개.
- 머리글 4줄. 한글은 PyMuPDF 내장 CJK 폰트(`fontname='korea'`)로 쓴다.
  1. `[i/N] <파일명>`
  2. `원문 위치: <쪽>쪽`
  3. `라벨: <라벨> (영역 판정: <layout|band>)`
  4. `설명: <캡션 최대 400자>`
- 이미지: 150 dpi 렌더링, JPEG 품질 80, 사방 3 pt 여백 포함, 작은 그림은 1.6배 이상 확대하지 않음.
- 메타데이터: title `<stem>_images - 이미지 모음`, subject 원본 파일명, keywords 원본 sha256.

---

## 4. 작업 단계

### T0. 현재 코드 파악과 기준선 측정

1. 다음을 담당하는 코드를 찾아 `파일 : 함수 : 역할` 표로 정리해 사용자에게 보여준다.
   - PDF → MD 변환 호출(`MarkItDown(`, `convert(`)
   - 이미지 추출(`get_images`, `extract_image`, `pdfimages`, `PIL` 등), 크기·조건 필터
   - 그림–캡션 연결(가장 가까운 캡션 찾기)
   - 이미지 모음 PDF 생성(현재 파일의 Producer가 `ReportLab PDF Library`이므로 `reportlab` 검색)
   - 출력 파일명 생성과 128자 자르기
   - 개별 이미지 파일(`images/…png`)을 실제로 저장하는지 여부
2. 의존성 목록(`requirements.txt`, `pip freeze`)과 Python 버전을 확인한다.
3. 테스트 세트를 준비한다.
   - 사용자가 원본 10편을 `tests/fixtures/pdf/`에 넣는다. 이 폴더는 `.gitignore`에 추가한다.
   - 현재 프로그램으로 만든 출력이 있으면 `tests/baseline_out/`에 둔다(없으면 현재 프로그램으로 변환).
4. 기준선을 측정한다.

   ```powershell
   python verify_conversion.py --orig-dir tests/fixtures/pdf --out-dir tests/baseline_out --json reports/baseline.json
   ```

   **기대 결과:** 모두 FAIL. 수치가 부록 A와 크게 다르면 원인을 사용자에게 보고한다.

### T1. 의존성 설치와 참고 모듈 단독 실행

1. 설치하고 버전을 고정한다(검증 환경과 동일).

   ```powershell
   .venv\Scripts\python.exe -m pip install pymupdf==2.1 pymupdf4llm==2.1
   ```

   `pymupdf_layout`, `onnxruntime`이 함께 설치된다. `requirements.txt`에 `pymupdf==2.1`, `pymupdf4llm==2.1`, `pymupdf_layout==2.1`을 추가한다. 설치가 실패하면(Python 버전 등) 오류 내용을 사용자에게 보고하고 멈춘다.
2. `pdf_pipeline_reference.py`를 `markitdown_gui/pdf_pipeline.py`로 복사한다. 이 단계에서는 내용을 고치지 않는다.
3. GUI 없이 1편을 변환하고 검증한다.

   ```powershell
   .venv\Scripts\python.exe -m markitdown_gui.pdf_pipeline tests/out_t1 "tests/fixtures/pdf/Efficiency_Characteristic_….pdf"
   python verify_conversion.py --orig-dir tests/fixtures/pdf --out-dir tests/out_t1
   ```

   **합격:** 해당 1편 PASS, 이미지 모음 PDF 머리글의 한글이 정상 표시.

### T2. PDF 텍스트 경로 교체

1. 변환 분기에서 확장자가 `.pdf`이면 `pdf_pipeline.convert_pdf(src, out_dir)`를 호출하고, 그 외는 기존 MarkItDown을 호출한다.
2. 기존 PDF 후처리(공백 보정, 캡션 매칭, MD 재작성 등)가 새 MD를 다시 건드리지 않게 한다.
3. **확인:** 10편 변환 후 `coverage`, `order`, `glued`, `pages` 4개 기준 합격.

### T3. 그림 경로 교체

1. PDF 경로에서는 기존 이미지 객체 추출, 크기 필터, 최근접 캡션 연결 코드가 호출되지 않게 한다. 삭제는 사용자 확인 후에 한다.
2. 이미지 모음 PDF는 `convert_pdf`가 만든 것을 그대로 쓴다. 기존 ReportLab 생성기를 계속 쓰려면 3.3절 머리글 형식을 정확히 맞춘다.
3. T0에서 현재 프로그램이 `images/` 폴더에 개별 이미지를 저장하고 있었다면 `convert_pdf(..., save_image_files=True)`로 같은 동작을 유지한다.
4. **확인:** `figures`, `tables`, `labels`, `formulas` 기준 합격.

### T4. 파일명과 덮어쓰기

1. 기존 128자 자르기를 제거하고 `safe_stem()`과 3.1절 규칙을 쓴다.
2. 출력 폴더에 같은 이름이 있을 때의 동작(같은 원본 덮어쓰기, 다른 원본은 해시 접미사)을 확인한다.
3. 긴 경로 대비: 출력 경로 전체가 240자를 넘으면 사용자에게 경고를 표시한다.

### T5. GUI 연결

1. 변환을 작업 스레드(`QThread` 또는 `QThreadPool` + `QRunnable`)에서 실행한다. 벡터 그림이 많은 문서는 오래 걸린다(검증 세트에서 24쪽 논문 1편이 약 160초).
2. `convert_pdf(..., progress=callback)`의 `(stage, done, total)`을 진행 표시줄·상태 문구로 연결한다. 콜백에서 UI를 직접 건드리지 말고 시그널로 전달한다.
3. 파일 단위로 예외를 잡아 오류 파일만 표시하고 나머지 변환은 계속한다. 취소는 파일 사이에서 처리한다.
4. 파일마다 `report`의 요약을 로그에 남긴다: 그림·표·수식 개수, `band_regions`(사람이 확인할 그림), `captions_without_region`, 용량.
5. (선택) 설정 화면: dpi(기본 150), JPEG 품질(80), 캡션 없는 이미지 포함(기본 끔), 수식 이미지(`garbled` 기본, `all`, `none`), 개별 이미지 저장.

### T6. 회귀 검증과 보고

1. 테스트 세트 10편을 GUI로 변환한다.
2. 검증을 실행한다.

   ```powershell
   python verify_conversion.py --orig-dir tests/fixtures/pdf --out-dir <GUI 출력 폴더> --json reports/after.json
   ```

3. 사용자에게 보고한다: 기준선 대비 표(부록 A와 같은 열), 합격 여부, `band` 판정 그림 목록과 이미지 모음 PDF의 해당 쪽 번호.
4. **완료 조건:** 10편 모두 PASS이고, 지표가 부록 B와 같은 수준(각 지표 차이 1 %p 이내).

### T7. (선택) 확장

- 중국어 문서·특허: 캡션 정규식(`FIG_RE`, `TAB_RE`)에 `图`, `圖`, `表` 형식을 추가한다. 추가하면 해당 문서 2편 이상으로 그림 수를 수동 확인하고, `verify_conversion.py`에도 같은 정규식을 반영한다.
- 스캔 PDF: 텍스트층이 없으면(`page.get_text()`가 거의 비어 있음) 현재는 경고만 한다. OCR 지원은 별도 과제로 사용자와 상의한다.

---

## 5. 합격 기준

`verify_conversion.py` 기준이며, 하나라도 어기면 FAIL이다.

| 검사 | 기준 | 의미 |
|---|---|---|
| coverage | ≥ 97 % | 원문 본문(2줄 이상, 줄 평균 40자 이상 블록)을 12자 단위로 잘라 MD에 있는 비율 |
| order | ≥ 80 % | 원문에서 이어지는 두 줄이 MD에서도 이어지는 비율 |
| glued | ≤ 1 % | 22자 이상 붙어 버린 영단어 비율 |
| pages | = 쪽수 | `<!-- page: N -->` 개수 |
| formulas | 조건부 | 깨진 수식 문자(`¼`, `(cid:N)`, `�`, 사용자정의영역 문자)가 MD에 있으면 `Eq` 라벨 이미지가 1개 이상 |
| figures / tables | 누락 0 | 원문의 모든 캡션 라벨이 이미지 모음 PDF에 있음 |
| labels | 중복·쪽 불일치 0 | 같은 라벨 2회 이상 없음, 라벨의 원문 쪽이 캡션 쪽과 ±1쪽 이내 |

경고(판정 영향 없음): 반복 줄(머리글·스탬프), 캡션 없는 이미지, 작은·빈 이미지, `band` 판정 영역.

---

## 6. 참고 구현의 핵심 규칙 (수정할 때 지킬 것)

1. **레이아웃 분석은 문서당 1회.** `_parse_once()`가 분석 결과 하나로 영역 찾기(JSON)와 MD 생성을 모두 한다. 공개 API(`to_json`, `to_markdown`)를 따로 부르면 시간이 2배다.
2. **수식 텍스트 보존.** MD 생성 직전에 `formula` 상자를 `text`로 바꿔 본문에 남긴다. 그림 안 글자(`<!-- Start of picture text -->` 블록)는 축 눈금·범례라 삭제한다.
3. **내부 API 의존.** `pymupdf4llm.helpers.document_layout.parse_document`는 내부 API다. 실패하면 공개 API로 대체되는데, 이때는 수식 텍스트가 그림 텍스트 블록에 섞여 나오므로 그림 텍스트를 지우지 않는다(`report['text_api'] == 'public-fallback'`). 이 때문에 버전을 고정한다.
4. **영역 규칙.**
   - 그림은 캡션 위, 표는 캡션 아래에서 찾는다.
   - 시각 요소와 캡션의 수평 겹침이 작은 쪽 폭의 30 % 이상이어야 한다.
   - 둘 사이에 높이 22 pt를 넘는 본문 블록이나 다른 캡션이 있으면 연결하지 않는다.
   - 옆으로 붙은 조각(분할된 표 열, 좌우 부분그림)은 수평 간격 35 pt 이내, 수직 겹침 50 % 이상이면 합친다.
   - 표가 한 열만 잡히면 단 폭으로 넓힌다.
   - `formula`로 분류된 작은 도식은 그림 캡션 바로 위(20 pt 이내)일 때만 그림에 포함한다.
   - 위 규칙으로 못 찾으면 캡션과 가장 가까운 본문 사이 띠(`band`)에서 그림 요소가 있는 범위를 자른다. 이웃 그림 일부가 섞일 수 있어 사람이 확인해야 한다.
5. **표 캡션 두 줄 합치기.** Elsevier·IEEE는 `Table 2` 줄과 제목 줄이 다른 블록이다. 10 pt 이내의 바로 아래 블록을 캡션에 합친다.
6. **캡션 없는 이미지 제외.** 저자 사진, 출판사 로고, 아이콘 조각은 기본으로 제외한다(`include_orphans=False`).

---

## 7. 알려진 한계

- **band 판정 5개:** DC Fault Fig. 4(위쪽 Fig. 3 포함), Na-ion Fig. 12(Fig. 11 포함)와 Fig. 14(Fig. 13 캡션 일부 포함), 재구성형 HV 배터리 Fig. 19(Fig. 18 캡션 일부 포함). 재구성형 HV 배터리 Fig. 14도 band로 잘렸지만 확인 결과 정상이다. 내용은 빠지지 않지만 이웃 그림이 함께 들어갈 수 있다.
- **처리 시간:** 대부분 5–20초, 벡터 객체가 많은 Na-ion 논문(한 쪽에 벡터 객체 30만 개 이상)은 약 160초.
- **깨진 수식 텍스트:** 원문 폰트 인코딩이 깨진 논문(DC Fault: `¼` 등 101자)은 MD에도 그대로 남는다. 수식 이미지(15개)로 보완되므로 AI에 줄 때 이미지 모음 PDF를 함께 준다.
- **공백 없는 캡션:** 원문 텍스트층에 공백이 아예 없는 캡션은 MD에서도 붙어 나온다(검증 세트 이미지 목록 233개 항목 중 2개).
- **스캔 PDF 미지원:** OCR을 끈 상태다.
- **라이선스:** PyMuPDF, pymupdf4llm, pymupdf_layout은 AGPL-3.0이다. 내부 사용은 문제없지만 프로그램을 외부에 배포하려면 라이선스를 검토해야 한다.

---

## 부록 A. 기준선: 현재 출력 검증 결과

(`verify_conversion.py`, 원본 9편. Impedance 논문은 현재 출력이 없어 제외)

```text
원본                                  cover  order  glued    page  garb  Eq  rep      그림     표   size  판정
-------------------------------------------------------------------------------------------------------
A_Hybrid_Modulation_Approach_for_S  85.1%  18.9%  9.57%   0/10      4   0    1  13/14   0/2   16.7%  FAIL: coverage,order,glued,pages,formulas,figures,tables,labels
    └ 그림 누락 Fig 9
    └ 표 누락 Table I, Table II
    └ 라벨 중복 Fig 7
    └ 캡션 없는 이미지 6
    └ 반복 줄 예: [다운로드 스탬프 - 기관명 삭제]
A_Partial-Power_Converter_for_Batt  82.5%  21.6%  6.68%   0/9     100   0    1   3/10   0/2   81.6%  FAIL: coverage,order,glued,pages,formulas,figures,tables
    └ 그림 누락 Fig 1, Fig 2, Fig 3, Fig 4, Fig 5, Fig 6, Fig 7
    └ 표 누락 Table I, Table II
    └ 캡션 없는 이미지 5
    └ 반복 줄 예: [다운로드 스탬프 - 기관명 삭제]
Characterisation_and_performance_e  85.7%  12.8%  0.04%   0/24      0   0    2   8/25   0/4    8.3%  FAIL: coverage,order,pages,figures,tables
    └ 그림 누락 Fig 2, Fig 3, Fig 5, Fig 6, Fig 7, Fig 8, Fig 10, Fig 11, Fig 16, Fig 17, Fig 18, Fig 19, Fig 20, Fig 21, Fig 22, Fig 23, Fig 25
    └ 표 누락 Table 1, Table 2, Table 3, Table 4
    └ 캡션 없는 이미지 5
    └ 반복 줄 예: Applied Energy 412 (2026) 127687
Comparing_the_electrical_performan  81.7%   8.2%  0.02%   0/16      0   0    1   1/13   0/3    4.2%  FAIL: coverage,order,pages,figures,tables
    └ 그림 누락 Fig 1, Fig 2, Fig 3, Fig 4, Fig 5, Fig 6, Fig 7, Fig A.1, Fig A.2, Fig A.3, Fig A.4, Fig A.5
    └ 표 누락 Table 1, Table 2, Table 3
    └ 캡션 없는 이미지 3
    └ 반복 줄 예: M. Rehm et al. Journal of Power Sources 633 (2025) 236290
Design_Considerations_of_Bidirecti  90.8%  69.3% 15.12%   0/26      0   0    1   3/21   0/3   54.4%  FAIL: coverage,order,glued,pages,figures,tables,labels
    └ 그림 누락 Fig 1, Fig 2, Fig 3, Fig 4, Fig 5, Fig 6, Fig 7, Fig 8, Fig 9, Fig 10, Fig 11, Fig 12, Fig 13, Fig 15, Fig 18, Fig 19, Fig 20, Fig 21
    └ 표 누락 Table 1, Table 2, Table 3
    └ 라벨 중복 Fig 16, Fig 17
    └ 캡션 없는 이미지 9
    └ 반복 줄 예: https://doi.org/10.3390/en19020432
Design_and_Implementation_of_an_In  96.5%  86.8% 23.68%   0/43      4   0    0   6/22   0/7   53.0%  FAIL: coverage,glued,pages,formulas,figures,tables
    └ 그림 누락 Fig 3, Fig 4, Fig 5, Fig 6, Fig 7, Fig 8, Fig 10, Fig 11, Fig 14, Fig 16, Fig 17, Fig 18, Fig 19, Fig 20, Fig 21, Fig 22
    └ 표 누락 Table 1, Table 2, Table 3, Table 4, Table 5, Table 6, Table 7
    └ 캡션 없는 이미지 5
Efficiency_Characteristic_and_Oper  84.6%  19.8%  9.31%   0/9       6   0    1   1/11   0/2    8.7%  FAIL: coverage,order,glued,pages,formulas,figures,tables,labels
    └ 그림 누락 Fig 1, Fig 2, Fig 4, Fig 5, Fig 6, Fig 7, Fig 8, Fig 9, Fig 10, Fig 11
    └ 표 누락 Table I, Table II
    └ 라벨 중복 Fig 3
    └ 캡션 없는 이미지 1
    └ 반복 줄 예: [다운로드 스탬프 - 기관명 삭제]
Energy_efficiency_evaluation_of_a_  85.5%  12.5% 13.41%   0/19      5   0    1   4/15   0/8   36.8%  FAIL: coverage,order,glued,pages,formulas,figures,tables
    └ 그림 누락 Fig 1, Fig 2, Fig 3, Fig 4, Fig 5, Fig 6, Fig 8, Fig 9, Fig 11, Fig 12, Fig 13
    └ 표 누락 Table 1, Table 2, Table 3, Table 4, Table 5, Table 6, Table A.7, Table B.8
    └ 캡션 없는 이미지 3
    └ 반복 줄 예: M.Schimpeetal. Applied Energy 210 (2018) 211–229
High-Efficiency_Partial_Power_Conv  81.5%  12.5%  6.96%   0/14     17   0    1   9/11   0/3   48.5%  FAIL: coverage,order,glued,pages,formulas,figures,tables,labels
    └ 그림 누락 Fig 2, Fig 8
    └ 표 누락 Table 1, Table 2, Table 3
    └ 라벨 중복 Fig 9
    └ 캡션 없는 이미지 5
    └ 반복 줄 예: HASSANPOURETAL.:HIGH-EFFICIENCYPARTIALPOWERCONVERTERFORINTEGRATIONOFSECOND-LIFEB

합계 9편: 합격 0편 | 그림 48/142 | 표 0/34 | 용량 67.7 MB → 15.9 MB (23.5%)
```

## 부록 B. 참고 구현 검증 결과

(검증 환경: Python 3.13, pymupdf 2.1, pymupdf4llm 2.1, pymupdf_layout 2.1, 원본 10편)

```text
원본                                  cover  order  glued    page  garb  Eq  rep      그림     표   size  판정
-------------------------------------------------------------------------------------------------------
A_Hybrid_Modulation_Approach_for_S  97.9%  99.8%  0.20%  10/10      2   1    0  14/14   2/2    5.8%  PASS
A_Partial-Power_Converter_for_Batt  97.0%  94.4%  0.06%   9/9     101  15    0  10/10   2/2   42.7%  PASS
    └ 확인 필요(band 영역) Fig 4
    └ 작은 이미지 1 / 빈 이미지 0
Characterisation_and_performance_e  99.1%  99.0%  0.03%  24/24      0   0    0  25/25   4/4    9.9%  PASS
    └ 확인 필요(band 영역) Fig 12, Fig 14
Comparing_the_electrical_performan  99.3%  99.9%  0.00%  16/16      0   0    0  13/13   3/3   19.0%  PASS
Design_Considerations_of_Bidirecti  98.4% 100.0%  0.00%  26/26      0   0    0  21/21   3/3   55.9%  PASS
Design_and_Implementation_of_an_In  99.2%  99.1%  0.01%  43/43      1   1    0  22/22   7/7   22.6%  PASS
    └ 확인 필요(band 영역) Fig 14, Fig 19
Efficiency_Characteristic_and_Oper  98.0%  99.8%  0.26%   9/9       3   2    0  11/11   2/2   12.5%  PASS
Energy_efficiency_evaluation_of_a_  98.6%  99.0%  0.12%  19/19      0   0    0  15/15   8/8   59.1%  PASS
High-Efficiency_Partial_Power_Conv  99.4%  99.5%  0.00%  14/14     17   5    0  11/11   3/3   16.8%  PASS
Impedance_Reshaping_Strategy_for_B  98.0%  95.8%  0.01%  20/20     47  11    0  20/20   2/2   12.5%  PASS

합계 10편: 합격 10편 | 그림 162/162 | 표 36/36 | 용량 75.2 MB → 11.7 MB (15.5%)
```

| 논문 | 변환 시간 |
|---|---|
| Hybrid Modulation PPC (10쪽) | 8 s |
| DC Fault Blocking PPC (9쪽) | 5 s |
| Na-ion 초기 상용셀 (24쪽) | 158 s |
| SIB vs LFP (16쪽) | 20 s |
| HB Push-Pull (26쪽) | 10 s |
| 재구성형 HV 배터리 (43쪽) | 17 s |
| 재구성 배터리 효율 (9쪽) | 6 s |
| 컨테이너 ESS 효율 (19쪽) | 11 s |
| 2nd-life PPC (14쪽) | 8 s |
| Impedance Reshaping (20쪽) | 8 s |
