@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_ROOT%\logs\pipeline-dispatcher"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python virtual environment not found: %PYTHON_EXE%
  exit /b 2
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
  echo [ERROR] Unable to create log directory: %LOG_DIR%
  exit /b 3
)

for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\dispatcher-%RUN_STAMP%.log"

pushd "%PROJECT_ROOT%"
>>"%LOG_FILE%" echo [%date% %time%] Pipeline dispatcher started. Arguments: %*
"%PYTHON_EXE%" -m scripts.run_pipeline_dispatcher %*>>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
>>"%LOG_FILE%" echo [%date% %time%] Pipeline dispatcher finished. ExitCode=%EXIT_CODE%
popd

echo Log: %LOG_FILE%
exit /b %EXIT_CODE%
