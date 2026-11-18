@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_ROOT%\logs\minio-cleanup"
set "LOCK_DIR=%PROJECT_ROOT%\logs\.minio-cleanup.lock"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python virtual environment not found: %PYTHON_EXE%
  exit /b 2
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
  echo [ERROR] Unable to create log directory: %LOG_DIR%
  exit /b 3
)

2>nul mkdir "%LOCK_DIR%"
if errorlevel 1 (
  echo [SKIP] MinIO cleanup is already running.
  exit /b 10
)

for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\cleanup-%RUN_STAMP%.log"

pushd "%PROJECT_ROOT%"
>>"%LOG_FILE%" echo [%date% %time%] MinIO cleanup started. Arguments: %*
"%PYTHON_EXE%" -m scripts.cleanup_minio_records %*>>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
>>"%LOG_FILE%" echo [%date% %time%] MinIO cleanup finished. ExitCode=%EXIT_CODE%
popd

rmdir "%LOCK_DIR%" >nul 2>&1
echo Log: %LOG_FILE%
exit /b %EXIT_CODE%
