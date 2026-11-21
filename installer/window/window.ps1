# The setup window, for an installer in PowerShell: dot-sourced, then
#     Open-SetupWindow PRODUCT ACCENT PYTHON TITLES     opens it ($true when it did)
#     Set-SetupStep INDEX STATE [NOTE]                  a step, from 0: wait, run, ok, warn, ask or fail
#     Complete-SetupWindow NOTE COMMAND                 all done, and what its Open button starts (an array)
#     Stop-SetupWindow WHY                              it stopped
# The page (index.html beside this file) reads status.json, which these write; serve.py serves both on this
# computer only. It opens as a window of its own in Edge (every Windows has it) or Chrome, in app mode. Nothing
# here ever stops an install: when the window can't open, the installer carries on in the terminal.

$Script:Window = $null
$Script:WindowState = $null

function Write-SetupStatus {
    if (-not $Script:Window) { return }
    $json = $Script:WindowState | ConvertTo-Json -Depth 5 -Compress
    $temp = Join-Path $Script:Window "status.tmp"
    [IO.File]::WriteAllText($temp, $json, (New-Object Text.UTF8Encoding $false))
    Move-Item -Force $temp (Join-Path $Script:Window "status.json")
}

function Open-SetupWindow([string]$Product, [string]$Accent, [string]$Python, [string[]]$Titles) {
    try {
        if ($env:XN_NO_WINDOW -or [Console]::IsOutputRedirected) { return $false }
        $kit = $PSScriptRoot
        if (-not $kit -or -not (Test-Path (Join-Path $kit "index.html"))) { return $false }
        $browser = @(
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $browser) { return $false }

        $dir = Join-Path ([IO.Path]::GetTempPath()) ("setup-window-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force $dir | Out-Null
        foreach ($f in "index.html", "serve.py", "film.mp4") { Copy-Item (Join-Path $kit $f) $dir }
        $Script:Window = $dir
        $Script:WindowState = [ordered]@{
            product = $Product; accent = $Accent; done = $false; doneNote = ""
            steps = @($Titles | ForEach-Object { [ordered]@{ title = $_; state = "wait"; note = "" } })
        }
        Write-SetupStatus

        # The server outlives the installer, so the page can still say it's done, and start the app.
        $portFile = Join-Path $dir "port"
        Start-Process -FilePath $Python -ArgumentList "`"$(Join-Path $dir 'serve.py')`"", "`"$dir`"" `
            -WindowStyle Hidden -RedirectStandardOutput $portFile | Out-Null
        $port = $null
        for ($i = 0; $i -lt 40 -and -not $port; $i++) {
            Start-Sleep -Milliseconds 100
            if (Test-Path $portFile) { $port = (Get-Content $portFile -ErrorAction SilentlyContinue | Select-Object -First 1) }
        }
        if (-not $port) { $Script:Window = $null; return $false }
        $url = "http://127.0.0.1:$($port.Trim())/"
        Start-Process -FilePath $browser -ArgumentList "--app=$url", "--window-size=980,640", `
            "--user-data-dir=`"$(Join-Path $dir 'profile')`"", "--no-first-run", "--no-default-browser-check" | Out-Null
        return $true
    } catch {
        $Script:Window = $null
        return $false
    }
}

function Set-SetupStep([int]$Index, [string]$State, [string]$Note = "") {
    if (-not $Script:Window) { return }
    $Script:WindowState.steps[$Index].state = $State
    $Script:WindowState.steps[$Index].note = $Note
    Write-SetupStatus
}

function Complete-SetupWindow([string]$Note, [string[]]$Command) {
    if (-not $Script:Window) { return }
    $open = [ordered]@{ cmd = @($Command) } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $Script:Window "open.json"), $open, (New-Object Text.UTF8Encoding $false))
    $Script:WindowState.done = $true
    $Script:WindowState.doneNote = $Note
    Write-SetupStatus
}

function Stop-SetupWindow([string]$Why) {
    if (-not $Script:Window) { return }
    $Script:WindowState.failed = $Why
    Write-SetupStatus
}
