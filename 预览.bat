@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\preview.ps1" %*
if errorlevel 1 (
    echo.
    echo The command failed. Please keep this window's error details.
    pause
    exit /b 1
)
