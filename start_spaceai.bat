@echo off
setlocal EnableExtensions

set "SPACEAI_HOME=D:\spaceai"
set "BACKEND_HOME=%SPACEAI_HOME%\back_project"
set "PYTHON_EXE=%BACKEND_HOME%\venv\Scripts\python.exe"
set "JAVA_HOME=D:\offline\installers\JDK\jdk-21.0.4+7"
set "CATALINA_HOME=%SPACEAI_HOME%\apache-tomcat"
set "DOCKER_EXE=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
set "DOCKER_DESKTOP=C:\Program Files\Docker\Docker\Docker Desktop.exe"
set "LOG_DIR=%SPACEAI_HOME%\logs"
set "PATH=%JAVA_HOME%\bin;%CATALINA_HOME%\bin;C:\Windows\System32;%PATH%"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ================================================== >> "%LOG_DIR%\startup.log"
echo [%date% %time%] SpaceAI startup begin >> "%LOG_DIR%\startup.log"

if not exist "%PYTHON_EXE%" (
    echo [%date% %time%] ERROR: Backend Python not found >> "%LOG_DIR%\startup.log"
    exit /b 1
)

if not exist "%JAVA_HOME%\bin\java.exe" (
    echo [%date% %time%] ERROR: Java not found >> "%LOG_DIR%\startup.log"
    exit /b 1
)

if not exist "%CATALINA_HOME%\bin\catalina.bat" (
    echo [%date% %time%] ERROR: Tomcat not found >> "%LOG_DIR%\startup.log"
    exit /b 1
)

if not exist "%DOCKER_EXE%" (
    echo [%date% %time%] ERROR: Docker CLI not found >> "%LOG_DIR%\startup.log"
    exit /b 1
)

REM Start Docker Desktop if the engine is not ready
"%DOCKER_EXE%" info >nul 2>&1
if errorlevel 1 (
    if not exist "%DOCKER_DESKTOP%" (
        echo [%date% %time%] ERROR: Docker Desktop not found >> "%LOG_DIR%\startup.log"
        exit /b 1
    )
    echo [%date% %time%] Starting Docker Desktop >> "%LOG_DIR%\startup.log"
    start "" "%DOCKER_DESKTOP%"
)

REM Wait up to 3 minutes for Docker Engine
for /L %%I in (1,1,90) do (
    "%DOCKER_EXE%" info >nul 2>&1
    if not errorlevel 1 goto docker_ready
    timeout /t 2 /nobreak >nul
)

echo [%date% %time%] ERROR: Docker Engine not ready >> "%LOG_DIR%\startup.log"
exit /b 1

:docker_ready
echo [%date% %time%] Docker Engine ready >> "%LOG_DIR%\startup.log"

REM Start MinIO only if stopped
"%DOCKER_EXE%" ps --filter "name=^/spaceai-minio$" --filter "status=running" --format "{{.Names}}" | findstr /x /c:"spaceai-minio" >nul
if errorlevel 1 (
    echo [%date% %time%] Starting MinIO >> "%LOG_DIR%\startup.log"
    "%DOCKER_EXE%" start spaceai-minio >> "%LOG_DIR%\startup.log" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] ERROR: MinIO container failed to start >> "%LOG_DIR%\startup.log"
        exit /b 1
    )
) else (
    echo [%date% %time%] MinIO already running >> "%LOG_DIR%\startup.log"
)

REM Wait for MinIO health
for /L %%I in (1,1,60) do (
    curl.exe -fsS http://127.0.0.1:9100/minio/health/live >nul 2>&1
    if not errorlevel 1 goto minio_ready
    timeout /t 2 /nobreak >nul
)

echo [%date% %time%] ERROR: MinIO not ready >> "%LOG_DIR%\startup.log"
exit /b 1

:minio_ready
echo [%date% %time%] MinIO ready >> "%LOG_DIR%\startup.log"

REM Wait for PostgreSQL at 127.0.0.1:6432
echo [%date% %time%] Waiting for PostgreSQL 127.0.0.1:6432 >> "%LOG_DIR%\startup.log"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "for ($i=0; $i -lt 60; $i++) { if (Test-NetConnection -ComputerName 127.0.0.1 -Port 6432 -InformationLevel Quiet) { exit 0 }; Start-Sleep -Seconds 2 }; exit 1"
if errorlevel 1 (
    echo [%date% %time%] ERROR: PostgreSQL port 6432 not ready >> "%LOG_DIR%\startup.log"
    exit /b 1
)
echo [%date% %time%] PostgreSQL port ready >> "%LOG_DIR%\startup.log"

REM Start backend only if port 8001 is not listening
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 8001 -InformationLevel Quiet) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] Starting backend >> "%LOG_DIR%\startup.log"
    start "SPACEAI_BACKEND" /min cmd.exe /d /c "cd /d %BACKEND_HOME% && %PYTHON_EXE% -m uvicorn app.server:app --host 0.0.0.0 --port 8001 1>>%LOG_DIR%\backend.log 2>&1"
) else (
    echo [%date% %time%] Backend port 8001 already in use >> "%LOG_DIR%\startup.log"
)

REM Wait for backend health
for /L %%I in (1,1,60) do (
    curl.exe -fsS http://127.0.0.1:8001/healthz >nul 2>&1
    if not errorlevel 1 goto backend_ready
    timeout /t 2 /nobreak >nul
)

echo [%date% %time%] ERROR: Backend health check failed >> "%LOG_DIR%\startup.log"
exit /b 1

:backend_ready
echo [%date% %time%] Backend ready >> "%LOG_DIR%\startup.log"

REM Start Dispatcher; its own single-instance lock prevents duplicates
if not exist "%BACKEND_HOME%\scripts\run_pipeline_dispatcher.bat" (
    echo [%date% %time%] ERROR: Dispatcher script not found >> "%LOG_DIR%\startup.log"
    exit /b 1
)
echo [%date% %time%] Starting Dispatcher >> "%LOG_DIR%\startup.log"
start "SPACEAI_DISPATCHER" /min /D "%BACKEND_HOME%" "%BACKEND_HOME%\scripts\run_pipeline_dispatcher.bat"

REM Start Tomcat
echo [%date% %time%] Starting Tomcat >> "%LOG_DIR%\startup.log"
cd /d "%CATALINA_HOME%\bin"
call catalina.bat start >> "%LOG_DIR%\tomcat_start.log" 2>&1

REM Start edge Nginx only if stopped
"%DOCKER_EXE%" ps --filter "name=^/spaceai-edge-nginx$" --filter "status=running" --format "{{.Names}}" | findstr /x /c:"spaceai-edge-nginx" >nul
if errorlevel 1 (
    echo [%date% %time%] Starting edge Nginx >> "%LOG_DIR%\startup.log"
    "%DOCKER_EXE%" start spaceai-edge-nginx >> "%LOG_DIR%\startup.log" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] ERROR: Edge Nginx container failed to start >> "%LOG_DIR%\startup.log"
        exit /b 1
    )
) else (
    echo [%date% %time%] Edge Nginx already running >> "%LOG_DIR%\startup.log"
)

echo [%date% %time%] SpaceAI startup end >> "%LOG_DIR%\startup.log"
exit /b 0