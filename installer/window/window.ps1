# The setup window, for an installer in PowerShell: dot-sourced, then
#     Open-SetupWindow PRODUCT ACCENT PYTHON TITLES     opens it ($true when it did)
#     Set-SetupStep INDEX STATE [NOTE]                  a step, from 0: wait, run, ok, warn, ask or fail
#     Complete-SetupWindow NOTE COMMAND                 all done, and what its Open button starts (an array)
#     Stop-SetupWindow WHY                              it stopped
# The installer app (installer\windows\InstallFlash.ps1, a native window) sets SETUP_WINDOW_DIR and reads the
# status.json these write there. Without it (run in a terminal), nothing here does anything.

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
    # Only for the installer app (installer\windows), which shows the steps in a native window and says where to
    # write them in SETUP_WINDOW_DIR. Run in a terminal, the installer shows its steps there.
    try {
        if (-not $env:SETUP_WINDOW_DIR -or $env:XN_NO_WINDOW) { return $false }
        New-Item -ItemType Directory -Force $env:SETUP_WINDOW_DIR | Out-Null
        $Script:Window = $env:SETUP_WINDOW_DIR
        $Script:WindowState = [ordered]@{
            product = $Product; accent = $Accent; done = $false; doneNote = ""
            steps = @($Titles | ForEach-Object { [ordered]@{ title = $_; state = "wait"; note = "" } })
        }
        Write-SetupStatus
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
