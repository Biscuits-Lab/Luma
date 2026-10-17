@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_tests.ps1" %*
set "TestResult=%errorlevel%"
echo.
if not "%TestResult%"=="0" echo Tests failed. See the worker logs above.
pause
exit /b %TestResult%
