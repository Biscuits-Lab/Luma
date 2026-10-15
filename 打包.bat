@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows.ps1" %*
if errorlevel 1 (
    echo.
    echo The command failed. Please keep this window's error details.
    pause
    exit /b 1
)
echo.
echo Output: %~dp0dist\Luma\Luma.exe
pause
