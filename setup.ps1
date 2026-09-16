[CmdletBinding()]
param([switch]$NonInteractive, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
. (Join-Path $studioRoot 'scripts\windows_common.ps1')
try {
    if ($studioRoot -match '(?i)\\OneDrive(?:[ -][^\\]*)?\\') {
        Write-Host 'Tip: extract this app into Downloads to keep videos outside OneDrive.' -ForegroundColor Yellow
    }
    $studioPython = Find-StudioPython -PythonPath $PythonPath
    if (-not $studioPython) {
        Write-Host 'Python 3.11 or newer is required. Python is free.'
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if ($winget -and -not $NonInteractive) {
            $answer = Read-Host 'Install Python 3.12 using Windows winget? [y/N]'
            if ($answer -match '^(y|yes)$') {
                & $winget.Source install --id Python.Python.3.12 --exact --source winget --scope user --accept-source-agreements --accept-package-agreements
                if ($LASTEXITCODE -ne 0) { throw 'Python installation did not complete. Install Python from https://www.python.org/downloads/windows/ and try again.' }
                $studioPython = Find-StudioPython
            }
        }
        if (-not $studioPython) { throw 'Install Python 3.11+ from https://www.python.org/downloads/windows/ (include the Python launcher), then open Start Studio.cmd again.' }
    }
    Write-Host 'Preparing the local Python environment...'
    & $studioPython -m venv (Join-Path $studioRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check free disk space and extract the app to a writable folder, such as Downloads.' }
    $venvPython = Join-Path $studioRoot '.venv\Scripts\python.exe'
    & $venvPython -c 'import sys; assert sys.version_info >= (3,11); import http.server, json, pathlib'
    if ($LASTEXITCODE -ne 0) { throw 'The local Python environment could not be verified. Run Setup.cmd again.' }
    # The core server uses the standard library; no pip packages or production models are installed here.
    Push-Location $studioRoot
    try {
        $codex = & $venvPython -c "from studio.codex_bridge import CodexBridge; print('available' if CodexBridge._find_command() else 'missing')"
        if ($LASTEXITCODE -ne 0) { throw 'Could not check the Codex installation. Confirm all ZIP files were extracted.' }
    } finally { Pop-Location }
    if ($codex -eq 'missing') {
        Write-Host ''
        Write-Host 'Codex is not installed yet. The studio will open, but AI production needs Codex.' -ForegroundColor Yellow
        Write-Host 'Install the official Codex CLI: https://developers.openai.com/codex/cli/'
        Write-Host 'If Node.js is installed, run: npm install -g @openai/codex'
        Write-Host 'Then close and restart the studio. Sign in with ChatGPT in its Connect panel.'
        if (-not $NonInteractive) {
            $answer = Read-Host 'Open the official Codex installation guide? [y/N]'
            if ($answer -match '^(y|yes)$') { Start-Process 'https://developers.openai.com/codex/cli/' }
        }
    }
    Write-Host ''
    Write-Host 'Setup complete. Open Start Studio.cmd.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
