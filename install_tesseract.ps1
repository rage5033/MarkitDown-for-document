# Installs Tesseract OCR (Apache-2.0) machine-wide and adds Korean language data. Requires admin.
#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$tessDir = Join-Path $env:ProgramFiles "Tesseract-OCR"
$tessExe = Join-Path $tessDir "tesseract.exe"

if (-not (Test-Path $tessExe)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget이 없습니다. https://github.com/UB-Mannheim/tesseract/wiki 에서 설치 파일을 받아 설치하세요." -ForegroundColor Red
        exit 1
    }
    Write-Host "Tesseract OCR 설치 중..."
    winget install --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
    if (-not (Test-Path $tessExe)) {
        Write-Host "설치 경로에서 tesseract.exe를 찾지 못했습니다: $tessExe" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Tesseract가 이미 설치되어 있습니다: $tessExe"
}

$tessdata = Join-Path $tessDir "tessdata"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# tessdata_best models are noticeably more accurate for Korean than the default fast models.
foreach ($lang in "kor", "eng") {
    $marker = Join-Path $tessdata "$lang.best"
    if (-not (Test-Path $marker)) {
        Write-Host "고정밀 인식 데이터 다운로드 중: $lang ..."
        Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_best/raw/main/$lang.traineddata" -OutFile (Join-Path $tessdata "$lang.traineddata") -UseBasicParsing
        New-Item -ItemType File -Path $marker -Force | Out-Null
    }
}

Write-Host ""
Write-Host "설치된 인식 언어:" -ForegroundColor Green
& $tessExe --list-langs
Write-Host ""
Write-Host "완료. MarkItDown 변환기를 다시 실행하면 'OCR' 옵션이 활성화됩니다." -ForegroundColor Green
