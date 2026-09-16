[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
. (Join-Path $studioRoot 'scripts\windows_common.ps1')
try {
    $instance = Get-StudioInstance -Root $studioRoot
    if ($instance.State -eq 'foreign') { throw $instance.Message }
    if ($instance.State -eq 'current') {
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765/' }
        Write-Host 'Studio is already running.'
        exit 0
    }
    $studioPython = Join-Path $studioRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $studioPython)) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $studioRoot 'setup.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. Follow the instructions above, then try again.' }
    }
    $studioScript = Join-Path $studioRoot 'server.py'
    $studioProcess = Start-Process -FilePath $studioPython -ArgumentList @('"' + $studioScript + '"') -WorkingDirectory $studioRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $studioRoot 'server.log') -RedirectStandardError (Join-Path $studioRoot 'server-error.log') -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $studioProcess.Refresh()
        if ($studioProcess.HasExited) { throw 'Studio could not start. Open server-error.log in this folder for details, or run Setup.cmd to repair the Python environment.' }
        $instance = Get-StudioInstance -Root $studioRoot
        if ($instance.State -eq 'foreign') { throw $instance.Message }
        if ($instance.State -eq 'current') { $ready = $true; break }
    }
    if (-not $ready) { throw 'Studio is taking longer than expected to start. Check server-error.log, then try Start Studio.cmd again.' }
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765/' }
    Write-Host 'Studio is ready: http://127.0.0.1:8765/'
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
