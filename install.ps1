#Requires -Version 5.1
<#
    Flash's installer for Windows (PowerShell):
        irm https://raw.githubusercontent.com/Natuworkguy/Flash/main/install.ps1 | iex
    Mirrors install.sh: clones the repo into a temp dir, installs it with
    pipx, and cleans up after itself.
#>

param(
    [switch]$Uninstall,
    # With -Uninstall, also delete your chats, memories and settings.
    [switch]$Purge,
    # The dev branch, where changes land before a release.
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# --- How it looks -----------------------------------------------------------------
# Steps that settle into a tick (or a "!" and why), the noisy output of each kept in
# a log, shown only if something goes wrong.

$Log = Join-Path ([System.IO.Path]::GetTempPath()) ("flash-install-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8) + ".log")
$Script:Step = 0
$Script:Steps = 8
$Script:Current = ""
$Script:Notes = @()
$Script:KeepLog = $false

function Show-Banner {
    Write-Host ""
    Write-Host "  Flash" -ForegroundColor DarkYellow -NoNewline
    Write-Host "  installer" -ForegroundColor DarkGray
    Write-Host "  --------------------" -ForegroundColor DarkGray
    Write-Host ""
}

function Start-Step([string]$Title) {
    $Script:Step++
    $Script:Current = $Title
    Write-Host "  .    " -ForegroundColor DarkGray -NoNewline
    Write-Host $Title -NoNewline
    Write-Host ("  {0}/{1}" -f $Script:Step, $Script:Steps) -ForegroundColor DarkGray -NoNewline
    "`n== $Title" | Out-File -FilePath $Log -Append -Encoding utf8
}

function Complete-Step([string]$Said = "") {
    Write-Host ("`r" + (" " * 78) + "`r") -NoNewline
    Write-Host "  $([char]0x221A)    " -ForegroundColor Green -NoNewline
    Write-Host $Script:Current -NoNewline
    if ($Said) { Write-Host "  $Said" -ForegroundColor DarkGray } else { Write-Host "" }
}

function Warn-Step([string]$Why) {
    Write-Host ("`r" + (" " * 78) + "`r") -NoNewline
    Write-Host "  !    " -ForegroundColor Yellow -NoNewline
    Write-Host $Script:Current -NoNewline
    Write-Host "  $Why" -ForegroundColor DarkGray
    $Script:Notes += $Why
}

function Stop-Install([string]$What, [string]$Hint = "") {
    Write-Host ("`r" + (" " * 78) + "`r") -NoNewline
    Write-Host "  x    " -ForegroundColor Red -NoNewline
    Write-Host $Script:Current
    Write-Host ""
    Write-Host "  $What" -ForegroundColor Red
    if ($Hint) { Write-Host "  $Hint" }
    if (Test-Path $Log) {
        Write-Host ""
        Write-Host "  The last of what happened:" -ForegroundColor DarkGray
        Get-Content $Log -Tail 12 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Write-Host ""
        Write-Host "  All of it: $Log" -ForegroundColor DarkGray
    }
    $Script:KeepLog = $true
    exit 1
}

# Runs a command with its output in the log; true when it worked.
function Invoke-Quietly([scriptblock]$Command) {
    # A tool's progress on stderr (git's, pip's) is not a failure: only its exit code says.
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    try {
        & $Command 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
        return ($LASTEXITCODE -eq 0)
    } catch {
        $_.ToString() | Out-File -FilePath $Log -Append -Encoding utf8
        return $false
    }
}

# Fancy: a real console, where the live lines and the bar can be drawn; plain lines anywhere else (a log, CI).
$Script:Fancy = $false
try { $Script:Fancy = (-not [Console]::IsOutputRedirected) -and (-not $env:NO_COLOR) -and ([Console]::WindowWidth -gt 30) } catch { }
$Script:Installed = 0

# What pip, or uv under pipx, is up to, as a line of its own; $null for its noise.
function Format-PipLine([string]$Line) {
    $l = $Line.TrimEnd()
    if ($l -match '^DEBUG Selecting: (\S+)==(\S+)') { return "found $($Matches[1]) $($Matches[2])" }
    if ($l -match '^DEBUG Adding transitive dependency for ([^=]+)==[^:]*: (.+)$') {
        $needs = ($Matches[2] -replace '\{[^}]*\}', '') -replace '\*$', ''
        return "$($Matches[1]) needs $needs"
    }
    if ($l -match '^(DEBUG|pipx >|warning:)' -or $l -match '\.metadata') { return $null }
    if ($l -match '^creating virtual environment') { return 'making Flash a space of its own' }
    if ($l -match '^installing (\S+) from spec') { return "installing $($Matches[1])" }
    if ($l -match '^\s*Installing build dependencies: started') { return 'getting what it takes to build Flash' }
    if ($l -match '^\s*Preparing metadata.*started') { return 'reading what Flash needs' }
    if ($l -match '^Collecting (\S+)') { return "looking at $($Matches[1])" }
    if ($l -match '^\s*Downloading (\S+?\.(whl|tar\.gz|zip))\s*(.*)$') {
        $part = $Matches[1] -split '-'
        return ("downloading {0} {1} {2}" -f $part[0], $part[1], $Matches[3]).Trim()
    }
    if ($l -match '^\s*Using cached (\S+\.whl)') { $part = $Matches[1] -split '-'; return "have $($part[0]) $($part[1]) already" }
    if ($l -match '^\s*Building wheel for (\S+)' -and $l -notmatch 'finished') { return "building $($Matches[1])" }
    if ($l -match '^Installing collected packages: (.+)$') { return "installing $(($Matches[1] -split ',').Count) packages" }
    if ($l -match '^Successfully installed (.+)$') { return "installed $(($Matches[1] -split ' ').Count) packages" }
    if ($l -match '^(Resolved|Prepared|Installed) \d+ packages') { return $l.Substring(0, 1).ToLower() + $l.Substring(1) }
    if ($l -match '^\s*Building (\S+) @') { return "building $($Matches[1])" }
    if ($l -match '^\s*Built (\S+) @') { return "built $($Matches[1])" }
    if ($l -match '^Downloading (\S+) \((.+)\)') { return "downloading $($Matches[1]) ($($Matches[2]))" }
    if ($l -match '^ Downloaded (\S+)') { return "downloaded $($Matches[1])" }
    if ($l -match '^ \+ (\S+)==(\S+)') { return "added $($Matches[1]) $($Matches[2])" }
    return $null
}

# Rows of dim lines under the step's own line, drawn in place; the cursor goes back to the end of the step's line.
function Show-Live([int]$Top, [int]$Col, [string[]]$Lines, [int]$Rows, [string]$Mark) {
    $width = [Math]::Max(20, [Console]::WindowWidth - 1)
    for ($i = 0; $i -lt $Rows; $i++) {
        [Console]::SetCursorPosition(0, $Top + 1 + $i)
        $text = if ($i -lt $Lines.Count) { "       " + $Lines[$i] } else { "" }
        if ($text.Length -gt $width) { $text = $text.Substring(0, $width) }
        Write-Host $text.PadRight($width) -ForegroundColor DarkGray -NoNewline
    }
    if ($Mark) {
        [Console]::SetCursorPosition(2, $Top)
        Write-Host $Mark -ForegroundColor DarkYellow -NoNewline
    }
    [Console]::SetCursorPosition($Col, $Top)
}

# Like Invoke-Quietly, but what pip is doing shows live under the step, and goes once it's done.
function Invoke-Watching([scriptblock]$Command, [int]$Rows = 5) {
    if (-not $Script:Fancy) { return (Invoke-Quietly $Command) }
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    $col = [Console]::CursorLeft
    for ($i = 0; $i -lt $Rows; $i++) { [Console]::WriteLine() }
    $top = [Console]::CursorTop - $Rows
    $shown = New-Object System.Collections.Generic.List[string]
    $frames = @('|', '/', '-', '\')
    $n = 0
    $ok = $true
    try {
        & $Command 2>&1 | ForEach-Object {
            $line = "$_"
            $line | Out-File -FilePath $Log -Append -Encoding utf8
            if ($line -match '^Installed (\d+) packages') { $Script:Installed += [int]$Matches[1] }
            elseif ($line -match '^Successfully installed (.+)$') { $Script:Installed += ($Matches[1] -split ' ').Count }
            $said = Format-PipLine $line
            $n++
            if ($said) {
                $shown.Add($said)
                while ($shown.Count -gt $Rows) { $shown.RemoveAt(0) }
                Show-Live $top $col $shown.ToArray() $Rows ($frames[$n % 4])
            } elseif ($n % 6 -eq 0) {
                Show-Live $top $col $shown.ToArray() $Rows ($frames[$n % 4])
            }
        }
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        $_.ToString() | Out-File -FilePath $Log -Append -Encoding utf8
        $ok = $false
    }
    Show-Live $top $col @() $Rows ""
    return $ok
}

# The screenshot browser's download, as a bar: Playwright says which browser and how big; how much has come is
# the size of its file so far, in a temp folder of the installer's own.
function Invoke-BrowserDownload([string]$Python) {
    if (-not $Script:Fancy) { return (Invoke-Quietly { & $Python -m playwright install chromium }) }
    $tmp = Join-Path $TempDir "browser"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $out = Join-Path $TempDir "browser-out.log"
    $err = Join-Path $TempDir "browser-err.log"
    $was = @($env:TEMP, $env:TMP)
    $env:TEMP = $tmp
    $env:TMP = $tmp
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList @('-m', 'playwright', 'install', 'chromium') -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err
        $null = $proc.Handle  # so its exit code is kept
    } catch {
        $_.ToString() | Out-File -FilePath $Log -Append -Encoding utf8
        return $false
    } finally {
        $env:TEMP = $was[0]
        $env:TMP = $was[1]
    }
    $col = [Console]::CursorLeft
    [Console]::WriteLine(); [Console]::WriteLine()
    $top = [Console]::CursorTop - 2
    $frames = @('|', '/', '-', '\')
    $n = 0
    $full = [char]0x2588
    $empty = [char]0x2591
    while (-not $proc.HasExited) {
        $said = if (Test-Path $out) { Get-Content $out -Raw -ErrorAction SilentlyContinue } else { "" }
        $title = ""; $total = 0.0
        foreach ($m in [regex]::Matches("$said", 'Downloading (.+?) \(playwright')) { $title = $m.Groups[1].Value; $total = 0.0 }
        foreach ($m in [regex]::Matches("$said", '% of ([\d.]+) MiB')) { $total = [double]$m.Groups[1].Value }
        $lines = @()
        if ($title) {
            $file = Get-ChildItem -Path $tmp -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            $got = if ($file) { $file.Length / 1MB } else { 0 }
            if ($total -gt 0) {
                $got = [Math]::Min($got, $total)
                $cells = [Math]::Max(8, [Math]::Min(32, [Console]::WindowWidth - 40))
                $k = [int][Math]::Round($got / $total * $cells)
                $bar = ([string]$full * $k) + ([string]$empty * ($cells - $k))
                $lines = @($title, $(if ($got -ge $total) { "$bar  unpacking" } else { "{0}  {1,3}%  {2:N1} of {3:N1} MB" -f $bar, [int]($got / $total * 100), $got, $total }))
            } else {
                $lines = @($title, ("{0:N1} MB" -f $got))
            }
        } else {
            $lines = @("finding the browser")
        }
        $n++
        Show-Live $top $col $lines 2 ($frames[$n % 4])
        Start-Sleep -Milliseconds 150
    }
    $proc.WaitForExit()
    foreach ($f in @($out, $err)) { if (Test-Path $f) { Get-Content $f | Out-File -FilePath $Log -Append -Encoding utf8 } }
    Show-Live $top $col @() 2 ""
    return ($proc.ExitCode -eq 0)
}

function Invoke-FlashScheme {
    param(
        [string]$FlashExe,
        [string]$Flag,
        [string]$SkipMessage
    )

    try {
        & $FlashExe $Flag
        if ($LASTEXITCODE -ne 0) {
            Write-Host $SkipMessage
        }
    } catch {
        Write-Host $SkipMessage
    }
}

function Uninstall-Flash {
    Show-Banner
    Write-Host "  Uninstalling Flash..."
    # flash uninstall turns off its login tasks, removes the flash://
    # handler and shortcut, then the package.
    if (Get-Command flash -ErrorAction SilentlyContinue) {
        $flashArgs = @("uninstall", "--yes")
        if ($Purge) { $flashArgs += "--purge" }
        try {
            & flash @flashArgs
            if ($LASTEXITCODE -eq 0) { return }
        } catch { }
    }
    if (Get-Command flash -ErrorAction SilentlyContinue) {
        Write-Host "  Removing the flash:// URL handler..."
        Invoke-FlashScheme -FlashExe "flash" -Flag "--unregister-url-scheme" `
            -SkipMessage "  Could not remove the flash:// URL handler."
        Write-Host "  Removing the Start menu shortcut..."
        try { & flash --shortcut remove } catch {
            Write-Host "  Could not remove the Start menu shortcut."
        }
    }
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Host "  Uninstalling flash via pipx..."
        try {
            pipx uninstall flash
        } catch {
            Write-Host "  flash is not installed via pipx."
        }
    } else {
        Write-Host "  pipx is not installed. Cannot uninstall flash via pipx."
    }
    if ($Purge) {
        Remove-Item -Recurse -Force (Join-Path $HOME ".flash") `
            -ErrorAction SilentlyContinue
    }
}

if ($Uninstall) {
    Uninstall-Flash
    exit 0
}

$RepoUrl = "https://github.com/Natuworkguy/Flash"
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("flash-install-" + [System.Guid]::NewGuid().ToString("N"))
$RepoDir = Join-Path $TempDir "flash"

New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

try {
    Show-Banner
    $Branch = if ($Dev) { "dev" } else { "main" }
    if ($Dev) {
        Write-Host "  The dev version" -ForegroundColor Yellow -NoNewline
        Write-Host ": changes land here before a release."
        Write-Host ""
    }

    # --- 1. Flash's code ----------------------------------------------------------
    Start-Step "Getting Flash"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-Install "git isn't installed." "Install it from https://git-scm.com/download/win and run this again."
    }
    if (-not (Invoke-Quietly { git clone --depth 1 --branch $Branch $RepoUrl $RepoDir })) {
        Stop-Install "Couldn't download Flash." "Check your internet connection and try again."
    }
    if (-not (Test-Path $RepoDir)) { Stop-Install "Flash's download is missing after cloning." }
    Push-Location $RepoDir
    Complete-Step $Branch

    # --- 2. Python ----------------------------------------------------------------------
    Start-Step "Finding Python"
    $Python = $null
    foreach ($candidate in @("python", "python3", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { $Python = $candidate; break }
    }
    if (-not $Python) {
        Stop-Install "Python 3 isn't installed." "Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again."
    }
    Complete-Step ((& $Python --version 2>&1) | Out-String).Trim()

    # --- 3. Ollama ----------------------------------------------------------------------
    # Install Ollama only when it is missing. Reinstalling over an existing
    # install wipes the models and data already on the machine, so an existing
    # ollama on PATH is always left exactly as it is. Flash can also use a remote
    # Ollama server or hosted models, so a missing one never stops the install.
    Start-Step "Ollama, for models on this computer"
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Complete-Step "already here"
    } elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn-Step "Not installed: get it from https://ollama.com/download, or use hosted models."
    } elseif (Invoke-Quietly { winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements }) {
        Complete-Step "installed"
    } else {
        Warn-Step "Not installed: get it from https://ollama.com/download, or use hosted models."
    }

    # --- 4. pipx ------------------------------------------------------------------------
    Start-Step "pipx, to keep Flash in its own space"
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        $PipxCmd = { & pipx @args }
        $said = "already here"
    } else {
        if (-not (Invoke-Quietly { & $Python -m pip --version })) {
            Stop-Install "Couldn't install pipx: pip isn't available for $Python." "See https://pipx.pypa.io/latest/how-to/install-pipx.html"
        }
        if (-not (Invoke-Quietly { & $Python -m pip install --user pipx })) {
            Stop-Install "Couldn't install pipx with pip."
        }
        Invoke-Quietly { & $Python -m pipx ensurepath } | Out-Null
        # pipx was just installed into the user's Python scripts dir, which may
        # not be on PATH in this session yet: `python -m pipx` until it is.
        $PipxCmd = if (Get-Command pipx -ErrorAction SilentlyContinue) { { & pipx @args } } else { { & $Python -m pipx @args } }
        $said = "installed"
    }
    Invoke-Quietly { & $PipxCmd ensurepath --force } | Out-Null
    Complete-Step $said

    # --- 5. Flash itself ----------------------------------------------------------------
    Start-Step "Installing Flash"
    # Install (or reinstall, if already present) flash from this repo
    $Script:Installed = 0
    if (-not (Invoke-Watching { & $PipxCmd install --verbose --force $RepoDir })) {
        Stop-Install "Couldn't install Flash." "The details are below."
    }
    $PipxBinDir = & $PipxCmd environment --value PIPX_BIN_DIR 2>$null
    if (-not $PipxBinDir) { $PipxBinDir = Join-Path $env:USERPROFILE ".local\bin" }
    $PipxVenvs = & $PipxCmd environment --value PIPX_LOCAL_VENVS 2>$null
    $VenvPython = Join-Path $PipxVenvs "flash\Scripts\python.exe"
    $FlashExe = Join-Path $PipxBinDir "flash.exe"
    if (-not (Test-Path $FlashExe)) { $FlashExe = "flash" }
    $FlashVersion = try { ((& $FlashExe --version) | Out-String).Trim() } catch { "Flash" }
    if (-not $FlashVersion) { $FlashVersion = "Flash" }
    # Which branch and commit this is, so `flash --update` keeps to the same branch.
    try {
        $FlashHome = Join-Path $HOME ".flash"
        New-Item -ItemType Directory -Force -Path $FlashHome | Out-Null
        $Commit = (git -C $RepoDir rev-parse HEAD 2>$null | Out-String).Trim()
        @{ branch = $Branch; commit = $Commit } | ConvertTo-Json -Compress |
            Set-Content -Path (Join-Path $FlashHome "install.json") -Encoding UTF8
    } catch { }
    $FlashPackages = $Script:Installed
    Complete-Step $(if ($FlashPackages) { "$FlashVersion  " + [char]0x00B7 + "  $FlashPackages packages" } else { $FlashVersion })

    # --- 6. The browser for screenshots ----------------------------------------------------
    Start-Step "A browser for page screenshots"
    if ((Test-Path $VenvPython) -and (Invoke-BrowserDownload $VenvPython)) {
        Complete-Step
    } else {
        Warn-Step "Screenshots stay off until it downloads (run: flash --update)."
    }

    # --- 7. Voice ---------------------------------------------------------------------------
    # A failure here leaves the rest of Flash working, so it never stops the install.
    # `inject` leaves a package that is already in the venv at whatever version it
    # was, so the forced reinstall behind it is what actually moves one. `runpip`
    # hands its arguments straight to pip, so the subcommand is `install`, not
    # `pip install`.
    Start-Step "Voice mode"
    $NeededVoicePackages = @("sounddevice", "vosk", "piper-tts")
    if ((Invoke-Watching { & $PipxCmd inject --verbose flash @NeededVoicePackages }) -and
        (Invoke-Watching { & $PipxCmd runpip flash install --upgrade --force-reinstall @NeededVoicePackages })) {
        Complete-Step "speech models download the first time you use it"
    } else {
        Warn-Step "Voice mode stays off until its packages install."
    }

    # --- 8. Links and the Start menu --------------------------------------------------------
    # The flash:// URL handler, and a Start menu shortcut, "Flash": the web UI in
    # the browser, started out of sight if it is not running. Neither ever fails
    # the install.
    Start-Step "flash:// links and the Start menu"
    $linksOk = (Invoke-Quietly { & $FlashExe --register-url-scheme })
    $linksOk = (Invoke-Quietly { & $FlashExe --shortcut }) -and $linksOk
    if ($linksOk) { Complete-Step } else { Warn-Step "Not all set up; Flash works without them." }

    # --- Done ---------------------------------------------------------------------------------
    Write-Host ""
    Write-Host "  $FlashVersion is installed." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Start it        " -NoNewline; Write-Host "flash            " -NoNewline; Write-Host "in a terminal" -ForegroundColor DarkGray
    Write-Host "                  flash --web      " -NoNewline; Write-Host "in your browser, or Flash in the Start menu" -ForegroundColor DarkGray
    Write-Host "  Talk to it      " -NoNewline; Write-Host "/voice on        " -NoNewline; Write-Host "inside Flash" -ForegroundColor DarkGray

    $PathEntries = $env:PATH -split ";"
    if ($PathEntries -notcontains $PipxBinDir) {
        Write-Host ""
        Write-Host "  Open a new terminal first" -ForegroundColor Yellow -NoNewline
        Write-Host ", or run this to use it in this one:"
        Write-Host "      `$env:PATH = `"$PipxBinDir;`$env:PATH`""
    }
    if ($Script:Notes.Count -gt 0) {
        Write-Host ""
        Write-Host "  Worth knowing:" -ForegroundColor DarkGray
        foreach ($note in $Script:Notes) { Write-Host "  - $note" }
        Write-Host "  Details: $Log" -ForegroundColor DarkGray
        $Script:KeepLog = $true
    }
    Write-Host ""
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
    if (-not $Script:KeepLog) { Remove-Item -Force $Log -ErrorAction SilentlyContinue }
}
