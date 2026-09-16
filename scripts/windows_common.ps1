# Shared Windows launcher helpers. Importing this file does not start or stop anything.
function Test-StudioSamePath {
    param([string]$First, [string]$Second)
    if (-not $First -or -not $Second) { return $false }
    try {
        $a = [IO.Path]::GetFullPath($First).TrimEnd('\', '/')
        $b = [IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
        return [string]::Equals($a, $b, [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-StudioPort {
    param([int]$Port = 8765)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connection = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $connection.AsyncWaitHandle.WaitOne(750)) { return $false }
        $client.EndConnect($connection)
        return $true
    } catch { return $false }
    finally { $client.Close() }
}

function Get-StudioInstance {
    param([string]$Root, [int]$Port = 8765)
    $url = "http://127.0.0.1:$Port"
    try { $health = Invoke-RestMethod -Uri ($url + '/api/health') -TimeoutSec 3 }
    catch {
        if (Test-StudioPort -Port $Port) {
            return [pscustomobject]@{ State='foreign'; Message="Port $Port is already in use. Close the other application, or use its own launcher." }
        }
        return [pscustomobject]@{ State='stopped'; Message='Studio is not running.' }
    }
    if ($health.app -ne 'Prompt Video Studio') {
        return [pscustomobject]@{ State='foreign'; Message="Another application is using port $Port. It has been left running." }
    }
    try { $status = Invoke-RestMethod -Uri ($url + '/api/status') -TimeoutSec 15 }
    catch { return [pscustomobject]@{ State='foreign'; Message='Studio is responding, but its folder could not be verified. Wait for it to finish starting and try again.' } }
    if (-not (Test-StudioSamePath -First $status.output_dir -Second (Join-Path $Root 'outputs'))) {
        return [pscustomobject]@{ State='foreign'; Message="A different copy of Prompt Video Studio is running on port $Port. Use that copy's Start/Stop Studio.cmd, then start this copy. The running copy has been left untouched." }
    }
    return [pscustomobject]@{ State='current'; Status=$status; Message='This copy of Studio is running.' }
}

function Find-StudioPython {
    param([string]$PythonPath)
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    if ($PythonPath) { $candidates.Add($PythonPath) }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $discovered = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $discovered) { $candidates.Add([string]$discovered) }
        } catch { }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch '\\WindowsApps\\') { $candidates.Add($command.Source) }
    }
    foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), 'C:\')) {
        if (Test-Path -LiteralPath $base) {
            Get-ChildItem -LiteralPath $base -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
                $candidate = Join-Path $_.FullName 'python.exe'
                if (Test-Path -LiteralPath $candidate) { $candidates.Add($candidate) }
            }
        }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $version = & $candidate -c "import sys, venv; print('studio-python-ok' if sys.version_info >= (3,11) else 'too-old')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -eq 'studio-python-ok') { return $candidate }
        } catch { }
    }
    return $null
}
