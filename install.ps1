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
    if (-not (Invoke-Quietly { & $PipxCmd install --force $RepoDir })) {
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
    Complete-Step $FlashVersion

    # --- 6. The browser for screenshots ----------------------------------------------------
    Start-Step "A browser for page screenshots"
    if ((Test-Path $VenvPython) -and (Invoke-Quietly { & $VenvPython -m playwright install chromium })) {
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
    if ((Invoke-Quietly { & $PipxCmd inject flash @NeededVoicePackages }) -and
        (Invoke-Quietly { & $PipxCmd runpip flash install --upgrade --force-reinstall @NeededVoicePackages })) {
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
