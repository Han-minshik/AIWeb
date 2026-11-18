@echo off
setlocal EnableExtensions
set "SPACEAI_HOME=D:\spaceai"
set "SPACEAI_STOP_FILE=%~f0"
set "SPACEAI_STOP_MODE=%~1"
if not "%~2"=="" (
    echo Usage: stop_spaceai.bat [maintenance^|--check]
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$text = [IO.File]::ReadAllText($env:SPACEAI_STOP_FILE); & ([scriptblock]::Create(($text -split '(?m)^# POWERSHELL_BODY\r?\n', 2)[1]))"
exit /b %ERRORLEVEL%
# POWERSHELL_BODY
$ErrorActionPreference = 'Stop'
$mode = [string]$env:SPACEAI_STOP_MODE
if ($mode -notin @('', 'maintenance', '--check')) {
    Write-Host 'Usage: stop_spaceai.bat [maintenance|--check]'
    exit 2
}
$checkOnly = $mode -eq '--check'
$root = [IO.Path]::GetFullPath($env:SPACEAI_HOME).TrimEnd('\')
$backend = $root + '\back_project'
$tomcat = $root + '\apache-tomcat'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$failed = $false

function Fail([string]$message) {
    $script:failed = $true
    Write-Host ('[ERROR] ' + $message) -ForegroundColor Red
}
function Get-Inventory {
    @(Get-CimInstance Win32_Process -ErrorAction Stop)
}
function Is-Python($process) {
    $process.Name -match '^python(w|[0-9.]*)?\.exe$'
}
function Is-BackendProcess($process, $inventory) {
    if (-not (Is-Python $process)) { return $false }
    $command = [string]$process.CommandLine
    if ($command -notmatch '(?i)(uvicorn\s+app\.server:app|scripts\.run_pipeline_dispatcher)') {
        return $false
    }
    if (([string]$process.ExecutablePath).StartsWith($backend + '\', [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    # A venv may launch system Python: require a matching ancestor command path.
    $current = $process
    $visited = @{}
    for ($depth = 0; $depth -lt 12; $depth++) {
        if ($visited.ContainsKey([int]$current.ProcessId)) { break }
        $visited[[int]$current.ProcessId] = $true
        if (([string]$current.CommandLine).IndexOf($backend + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ([string]$current.CommandLine).IndexOf($backend + ' ', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ([string]$current.CommandLine).IndexOf($backend + '"', [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
        $parentId = [int]$current.ParentProcessId
        $current = $inventory | Where-Object { $_.ProcessId -eq $parentId } | Select-Object -First 1
        if (-not $current) { break }
    }
    return $false
}
function Is-TomcatProcess($process) {
    $command = [string]$process.CommandLine
    $process.Name -match '^java(w)?\.exe$' -and
        $command -match 'org\.apache\.catalina\.startup\.Bootstrap' -and
        $command -match '\bstart\b' -and
        $command.IndexOf($tomcat, [StringComparison]::OrdinalIgnoreCase) -ge 0
}
function Get-ContainerState([string]$name) {
    $names = @(& $docker ps -a --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list Docker containers.' }
    if ($names -notcontains $name) { return 'missing' }
    $running = & $docker inspect --format '{{.State.Running}}' $name
    if ($LASTEXITCODE -ne 0) { throw ('Cannot inspect ' + $name) }
    return ([string]$running).Trim()
}
function Stop-Container([string]$name) {
    $state = Get-ContainerState $name
    if ($state -in @('missing', 'false')) {
        Write-Host ('[OK] ' + $name + ': already stopped or absent')
        return
    }
    & $docker stop --time 30 $name
    if ($LASTEXITCODE -ne 0) { throw ('Failed to stop ' + $name) }
    if ((Get-ContainerState $name) -eq 'true') { throw ($name + ' is still running') }
    Write-Host ('[OK] Stopped ' + $name)
}
function Stop-SelectedProcesses($processes) {
    foreach ($process in $processes) {
        $processId = [int]$process.ProcessId
        # Protect against PID reuse between inspection and termination.
        $live = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $processId)
        if (-not $live) { continue }
        if ($live.CreationDate -ne $process.CreationDate) {
            Fail ("PID changed before termination: " + $processId)
            continue
        }
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host ('[OK] Stopped PID ' + $processId)
        } catch {
            if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                Fail ("Cannot stop PID " + $processId + ': ' + $_.Exception.Message)
            }
        }
    }
}

try {
    Write-Host ('Target: ' + $root + '; mode: ' + $(if ($mode) { $mode } else { 'full' }))
    if (-not $checkOnly) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Run this batch from Command Prompt / PowerShell as administrator.'
        }
    }
    if (-not (Test-Path -LiteralPath $docker)) { throw 'Docker CLI was not found.' }
    & $docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker engine is inaccessible. Check Docker Desktop and the account running this batch.'
    }
    $inventory = Get-Inventory
    $backendProcesses = @($inventory | Where-Object { Is-BackendProcess $_ $inventory })
    $tomcatProcesses = @($inventory | Where-Object { Is-TomcatProcess $_ })
    Write-Host ('Backend/dispatcher PIDs: ' + (($backendProcesses | ForEach-Object { $_.ProcessId }) -join ', '))
    Write-Host ('Tomcat PIDs: ' + (($tomcatProcesses | ForEach-Object { $_.ProcessId }) -join ', '))
    if ($checkOnly) {
        foreach ($name in @('spaceai-edge-nginx', 'spaceai-minio')) {
            Write-Host ($name + ': ' + (Get-ContainerState $name))
        }
        Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue |
            Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table
        exit 0
    }

    # Block incoming traffic before stopping application processes.
    Stop-Container 'spaceai-edge-nginx'
    Stop-SelectedProcesses $backendProcesses

    if ($tomcatProcesses.Count -gt 0) {
        $shutdown = $tomcat + '\bin\shutdown.bat'
        if (Test-Path -LiteralPath $shutdown) {
            $env:CATALINA_HOME = $tomcat
            $env:CATALINA_BASE = $tomcat
            $startFile = Join-Path (Split-Path $env:SPACEAI_STOP_FILE) 'start_spaceai.bat'
            if (Test-Path -LiteralPath $startFile) {
                $javaSetting = [regex]::Match([IO.File]::ReadAllText($startFile), '(?im)^set "JAVA_HOME=([^"]+)"')
                if ($javaSetting.Success) { $env:JAVA_HOME = $javaSetting.Groups[1].Value }
            }
            Write-Host '[INFO] Requesting graceful Tomcat shutdown...'
            $shutdownProcess = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c ""' + $shutdown + '""') -WindowStyle Hidden -PassThru
            if (-not $shutdownProcess.WaitForExit(20000)) {
                Stop-Process -Id $shutdownProcess.Id -Force -ErrorAction SilentlyContinue
                Write-Host '[WARN] Tomcat shutdown command timed out.'
            }
            for ($attempt = 0; $attempt -lt 10; $attempt++) {
                if (@((Get-Inventory) | Where-Object { Is-TomcatProcess $_ }).Count -eq 0) { break }
                Start-Sleep -Seconds 1
            }
        }
        $remainingTomcat = @((Get-Inventory) | Where-Object { Is-TomcatProcess $_ })
        if ($remainingTomcat.Count -gt 0) {
            Write-Host '[WARN] Forcing termination of the identified Tomcat process.'
            Stop-SelectedProcesses $remainingTomcat
        }
    } else {
        Write-Host '[OK] No matching Tomcat process is running.'
    }

    $inventory = Get-Inventory
    $remaining = @($inventory | Where-Object { (Is-BackendProcess $_ $inventory) -or (Is-TomcatProcess $_) })
    if ($remaining.Count -gt 0) {
        Fail ('Application processes remain: ' + (($remaining | ForEach-Object { $_.ProcessId }) -join ', '))
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        Fail ('Backend port 8001 is still listening. Owner PIDs: ' + (($listeners | ForEach-Object { $_.OwningProcess } | Select-Object -Unique) -join ', '))
    }
    if ($failed) {
        Write-Host '[ERROR] Shutdown is incomplete. MinIO has been left running.'
        exit 1
    }
    if ($mode -eq 'maintenance') {
        Write-Host '[OK] Maintenance shutdown complete. MinIO and PostgreSQL are left unchanged.'
    } else {
        Stop-Container 'spaceai-minio'
        Write-Host '[OK] Full shutdown complete. PostgreSQL is left unchanged.'
    }
    exit 0
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '[ERROR] Shutdown is incomplete. Check service state before maintenance.'
    exit 1
}
