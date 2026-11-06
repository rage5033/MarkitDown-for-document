# MarkItDown 다중 파일 → Markdown 변환기

Microsoft 오픈소스 [MarkItDown](https://github.com/microsoft/markitdown)을 사용해
PDF/Word/PowerPoint/Excel/이미지/오디오/HTML 등 여러 파일을 한 번에 Markdown(.md)으로
변환하는 Windows 데스크톱 GUI 도구입니다.

## 주요 기능
- 여러 파일을 한 번에 추가(버튼 선택 또는 드래그 앤 드롭) 후 일괄 변환
- 폴더 추가: 선택한 폴더(하위 폴더 포함)의 지원 파일을 한 번에 목록에 추가
- 변환 결과 미리보기(렌더링 / Markdown 원문 탭)
- 변환 취소 버튼 (처리 중인 파일이 끝나면 중단, 남은 항목은 "취소됨")
- 저장 폴더에 같은 이름의 파일이 있으면 덮어쓰기 / 건너뛰기 / 새 이름으로 저장 중 선택
- 완료 후 저장 폴더 자동 열기 (체크박스)
- PDF 변환 강화: 선이 있는 표 / 셀 배경색으로 그린 표를 Markdown 표로 복원
- 이미지 추출 (PDF·Word·PPT 등): 문서 속 그림을 파일로 저장하고 본문의 원래 위치에 링크, MD 끝에 "이미지 파일 목록" 표 추가
  - 저장 방식 "문서별 폴더 (AI 업로드용)": `저장폴더\문서명\문서명.md` + `문서명_p010_01.png`(PDF 쪽) / `문서명_s03_01.png`(PPT 슬라이드) / `문서명_img_01.png`(Word)
    → 폴더를 열고 Ctrl+A 후 ChatGPT·Claude 채팅창에 끌어놓기 (AI 서비스는 폴더 업로드 불가)
  - 이미지 파일명에 문서명이 붙어 여러 문서를 한 채팅에 올려도 구분됨, 같은 이미지(반복 로고 등)는 1개만 저장, 아이콘급 작은 이미지는 제외
  - EMF/WMF/BMP/TIFF 등 AI가 못 읽는 형식은 PNG로 자동 변환
- 이미지 모음 PDF (AI 업로드용, 기본 켜짐): 추출 이미지를 `문서명_images.pdf` 하나에 모음 (한 쪽에 1개, 상단에 파일명·원문 위치·설명)
  → 문서 폴더 맨 위에는 `문서명.md` + `문서명_images.pdf` 2개만 남고 개별 PNG는 `images\` 폴더에 보관
  → AI에는 이 2개 파일만 올리면 됨 (ChatGPT·Claude 모두 PDF 속 이미지를 시각적으로 분석, ZIP은 미지원)
  → 이미지가 90개를 넘으면 `_images_2.pdf`로 분할 (Claude는 100쪽 이하 PDF만 이미지 분석)
  → AI 업로드 용량 제한 준수: 모음 PDF가 28MB를 넘으면 `_images_2.pdf`, `_images_3.pdf`… 로 자동 분할
    (Claude 프로젝트 파일 30MB / Claude 채팅 500MB / ChatGPT 512MB 중 가장 엄격한 기준 + 여유분),
    개별 이미지 파일은 8000px·19MB 이하로 자동 축소(필요 시 JPEG), 그래도 28MB를 넘는 산출물이 있으면 로그에 경고
  → PDF 그림은 300 DPI 이상(작은 그림은 긴 변 약 1600px, 최대 600 DPI)으로 추출, 모음 PDF에는 고화질 JPEG(품질 92)로 넣어 용량 절감 (`images\`의 PNG 원본은 무손실)
- 긴 파일명 자동 단축: 저장 경로가 Windows 경로 길이 제한(260자)을 넘으면 탐색기·PDF 뷰어에서 파일이 열리지 않으므로,
  폴더/파일명을 줄이고 끝에 6자리 구분 코드를 붙임 (예: `..._Converter_644dee.md`, 로그에 표시)
- 이미지 글자 인식(OCR, 선택): PDF 안 그림·스캔 페이지의 글자를 Tesseract로 인식해 MD에 텍스트로 삽입 (Tesseract 설치 시에만 활성화)
- YouTube 링크를 입력해 제목/설명/자막을 Markdown으로 변환 (목록에 파일과 함께 추가되어 동일하게 일괄 변환됨)
- 출력 파일명은 기본적으로 원본 파일명(또는 YouTube 영상 제목) + `.md` 이며, 표에서 더블클릭으로 자유롭게 수정 가능
- "PDF로도 저장" 체크박스 선택 시 변환된 Markdown을 한글 지원 폰트로 PDF까지 함께 생성
- 저장 폴더를 지정해 PC의 원하는 위치에 저장 (마지막 사용 폴더 자동 기억)
- 변환은 백그라운드 스레드에서 처리되어 GUI가 멈추지 않음, 진행률/로그 표시

## 실행 방법

### 1) 개발/실행 (Python)
```powershell
cd D:\01_Project\vs_code\02.MarkitDown
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```
또는 `.venv`가 이미 있다면 `run.bat`을 더블클릭해서 실행합니다.

### 2) 배포용 단일 실행 파일(.exe) 빌드 (권장)
Python 설치 없이 다른 PC에서도 바로 실행할 수 있도록 PyInstaller로 패키징합니다.
```powershell
cd D:\01_Project\vs_code\02.MarkitDown
.\build_exe.ps1
```
빌드가 끝나면 `dist\MarkItDownGUI.exe` 파일이 생성됩니다. 이 파일 하나만 복사해서
다른 PC에서도 더블클릭으로 실행할 수 있습니다 (최초 실행 시 압축 해제로 몇 초 소요).

### 3) OCR(선택) - PC마다 Tesseract 설치
OCR을 쓸 PC에서 `install_tesseract.bat`을 더블클릭하고 관리자 승인(UAC)을 누르면
Tesseract OCR(Apache-2.0, 무료)과 한국어 인식 데이터가 설치됩니다. 배포 시 `MarkItDownGUI.exe`와
`install_tesseract.bat`, `install_tesseract.ps1`을 함께 전달하세요. 미설치 PC에서는 OCR 옵션만 비활성화되고
나머지 기능은 그대로 동작합니다.

## 참고
- 오디오 변환 시 ffmpeg 경고가 표시될 수 있으나, mp3/wav 변환 자체는 정상 동작합니다.
- PDF 저장은 Windows에 내장된 맑은 고딕(malgun.ttf) 폰트를 사용해 한글을 렌더링합니다.
- YouTube 변환은 자막이 없는 영상의 경우 본문(제목/설명)만 생성됩니다.
