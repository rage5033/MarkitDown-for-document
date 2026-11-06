# PyInstaller로 단일 실행 파일(.exe)을 생성한다. Python 설치 없이 배포/실행 가능.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --onefile `
    --name MarkItDownGUI `
    --collect-all markitdown `
    --collect-all magika `
    --collect-data pdfminer `
    --collect-all xhtml2pdf `
    --collect-all reportlab `
    --collect-all svglib `
    --collect-all arabic_reshaper `
    --collect-all youtube_transcript_api `
    --collect-all pdfplumber `
    --collect-all pypdfium2 `
    --collect-all pypdfium2_raw `
    main.py

Write-Host "빌드 완료: dist\MarkItDownGUI.exe"
