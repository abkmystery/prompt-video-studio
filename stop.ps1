$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
. (Join-Path $studioRoot 'scripts\windows_common.ps1')
try {
    $instance = Get-StudioInstance -Root $studioRoot
    if ($instance.State -eq 'foreign') { throw $instance.Message }
    if ($instance.State -eq 'stopped') { Write-Host $instance.Message; exit 0 }
    Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/shutdown' -Method Post -ContentType 'application/json' -Headers @{'X-Studio-Token'=$instance.Status.csrf_token} -Body '{}' -TimeoutSec 10 | Out-Null
    Write-Host 'Studio is stopping. Saved project files remain in the outputs folder.'
    Write-Host 'If a video was running, use Resume in the library after restarting.'
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
