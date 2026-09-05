#Requires -Version 5.1
<#
    Flash CLI installer for Windows (PowerShell).
    Mirrors install.sh: clones the repo into a temp dir, installs it with
    pipx, and cleans up after itself.
#>

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

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

function Install-Ollama {
    # Install Ollama only when it is missing. Reinstalling over an existing
    # install wipes the models and data already on the machine, so an existing
    # ollama on PATH is always left exactly as it is.
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host "Ollama is already installed."
        return
    }

    Write-Host "Ollama is not installed. Installing Ollama..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget is not available, so Ollama cannot be installed automatically."
        Write-Host "Install it manually: https://ollama.com/download"
        return
    }

    try {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to install Ollama via winget."
            Write-Host "Install it manually: https://ollama.com/download"
        }
    } catch {
        Write-Host "Failed to install Ollama via winget."
        Write-Host "Install it manually: https://ollama.com/download"
    }
}

function Uninstall-Flash {
    Write-Host "Uninstalling flash..."
    if (Get-Command flash -ErrorAction SilentlyContinue) {
        Write-Host "Removing the flash:// URL handler..."
        Invoke-FlashScheme -FlashExe "flash" -Flag "--unregister-url-scheme" `
            -SkipMessage "Could not remove the flash:// URL handler."
    }
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Host "Uninstalling flash via pipx..."
        try {
            pipx uninstall flash
        } catch {
            Write-Host "flash is not installed via pipx."
        }
    } else {
        Write-Host "pipx is not installed. Cannot uninstall flash via pipx."
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
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "git is not installed. Please install git and try again."
        exit 1
    }

    git clone $RepoUrl $RepoDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to clone repository. Please check your internet connection and try again."
        exit 1
    }

    if (-not (Test-Path $RepoDir)) {
        Write-Host "Repository directory $RepoDir does not exist after cloning."
        exit 1
    }

    Push-Location $RepoDir

    function Get-PythonCommand {
        foreach ($candidate in @("python", "python3", "py")) {
            $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($cmd) {
                Write-Host "Detected python command: $candidate"
                return $candidate
            }
        }
        Write-Host "Python is not installed. Please install Python 3."
        exit 1
    }

    $Python = Get-PythonCommand

    # Flash can also talk to a remote Ollama server, so never fail the
    # install over a missing local one.
    Install-Ollama

    if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) {
        Write-Host "pipx is not installed. Installing pipx..."
        & $Python -m pip --version | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Could not install pipx automatically: pip is not available for $Python."
            Write-Host "Install it manually: https://pipx.pypa.io/latest/how-to/install-pipx.html"
            exit 1
        }

        & $Python -m pip install --user pipx
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to install pipx via pip."
            exit 1
        }

        & $Python -m pipx ensurepath
    }

    if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) {
        # pipx was just installed into the user's Python scripts dir, which may
        # not be on PATH in this session yet. Fall back to `python -m pipx`.
        $PipxCmd = { & $Python -m pipx @args }
    } else {
        $PipxCmd = { & pipx @args }
    }

    & $PipxCmd ensurepath --force

    # Install (or reinstall, if already present) flash from this repo
    & $PipxCmd install --force $RepoDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install flash via pipx."
        exit 1
    }

    $PipxBinDir = & $PipxCmd environment --value PIPX_BIN_DIR 2>$null
    if (-not $PipxBinDir) {
        $PipxBinDir = Join-Path $env:USERPROFILE ".local\bin"
    }

    $PipxVenvs = & $PipxCmd environment --value PIPX_LOCAL_VENVS 2>$null
    $VenvPython = Join-Path $PipxVenvs "flash\Scripts\python.exe"

    Write-Host ""
    Write-Host "Downloading the headless browser used for page screenshots..."
    if (Test-Path $VenvPython) {
        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Download failed. Screenshots stay unavailable until it succeeds."
        }
    } else {
        Write-Host "Could not find the flash environment. Screenshots need chromium."
    }

    # A failure here leaves the rest of Flash working, so it never
    # stops the install.
    Write-Host ""
    Write-Host "Installing the voice mode packages..."
    & $PipxCmd inject flash vosk piper-tts sounddevice
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Voice packages failed to install. Voice mode stays unavailable."
    }

    # Register the flash:// URL handler. Never fail the install over it.
    $FlashExe = Join-Path $PipxBinDir "flash.exe"
    if (-not (Test-Path $FlashExe)) {
        $FlashExe = "flash"
    }

    Write-Host ""
    Write-Host "Registering the flash:// URL handler..."
    Invoke-FlashScheme -FlashExe $FlashExe -Flag "--register-url-scheme" `
        -SkipMessage "Unable to install flash:// URL handler. Continuing."

    Write-Host ""
    Write-Host "=== flash installed via pipx. ==="

    Write-Host "Run /voice on inside Flash to talk to it (it downloads"
    Write-Host "the speech models the first time)."

    $PathEntries = $env:PATH -split ";"
    if ($PathEntries -notcontains $PipxBinDir) {
        Write-Host "$PipxBinDir is not on your PATH yet."
        Write-Host "Open a new terminal, or run this in your current session:"
        Write-Host "    `$env:PATH = `"$PipxBinDir;`$env:PATH`""
    }
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    Write-Host "Cleaning up..."
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
}
