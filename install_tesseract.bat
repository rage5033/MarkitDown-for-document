@echo off
rem Double-click to install Tesseract OCR + Korean data (asks for administrator approval).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0install_tesseract.ps1\"'"
