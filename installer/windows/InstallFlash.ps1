<#
    Install Flash: Flash's installer in a native window on Windows. No console, no browser.

    Started by "Install Flash.cmd" (double-click it). Runs install.ps1 (beside this file, or the one on GitHub)
    out of sight with SETUP_WINDOW_DIR set; install.ps1 writes status.json there as it goes, and this window shows
    each step turning from waiting to done, a percentage and a bar. At the end, Open starts Flash and Close quits.
#>
param([switch]$Dev)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Work = Join-Path ([IO.Path]::GetTempPath()) ("flash-setup-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force $Work | Out-Null
$Script = Join-Path $PSScriptRoot "install.ps1"
if (-not (Test-Path $Script)) { $Script = Join-Path $PSScriptRoot "..\..\install.ps1" }
if (-not (Test-Path $Script)) {
    $Script = Join-Path $Work "install.ps1"
    $branch = if ($Dev) { "dev" } else { "main" }
    try {
        Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Natuworkguy/Flash/$branch/install.ps1" -OutFile $Script
    } catch {
        [System.Windows.Forms.MessageBox]::Show("Couldn't download Flash's installer: $($_.Exception.Message)", "Install Flash") | Out-Null
        exit 1
    }
}

# ---- the window ----
$Ink = [Drawing.Color]::FromArgb(245, 245, 245)
$Dim = [Drawing.Color]::FromArgb(140, 140, 146)
$Accent = [Drawing.Color]::FromArgb(255, 138, 92)
$Bg = [Drawing.Color]::FromArgb(14, 14, 16)
$Card = [Drawing.Color]::FromArgb(26, 26, 30)

$form = New-Object Windows.Forms.Form
$form.Text = "Install Flash"
$form.ClientSize = New-Object Drawing.Size(760, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.BackColor = $Bg
$form.ForeColor = $Ink
$form.Font = New-Object Drawing.Font("Segoe UI", 10)

$title = New-Object Windows.Forms.Label
$title.Text = "Setting up Flash."
$title.Font = New-Object Drawing.Font("Segoe UI Semibold", 26)
$title.AutoSize = $true
$title.Location = New-Object Drawing.Point(36, 30)
$form.Controls.Add($title)

$note = New-Object Windows.Forms.Label
$note.Text = "This takes a few minutes. You can keep working."
$note.ForeColor = $Dim
$note.AutoSize = $true
$note.Location = New-Object Drawing.Point(40, 84)
$form.Controls.Add($note)

$pct = New-Object Windows.Forms.Label
$pct.Text = "0%"
$pct.Font = New-Object Drawing.Font("Segoe UI Light", 34)
$pct.ForeColor = $Ink
$pct.AutoSize = $false
$pct.TextAlign = "MiddleRight"
$pct.Size = New-Object Drawing.Size(200, 60)
$pct.Location = New-Object Drawing.Point(520, 26)
$form.Controls.Add($pct)

$bar = New-Object Windows.Forms.Panel
$bar.BackColor = [Drawing.Color]::FromArgb(40, 40, 46)
$bar.Location = New-Object Drawing.Point(40, 122)
$bar.Size = New-Object Drawing.Size(680, 4)
$fill = New-Object Windows.Forms.Panel
$fill.BackColor = $Accent
$fill.Size = New-Object Drawing.Size(0, 4)
$bar.Controls.Add($fill)
$form.Controls.Add($bar)

$list = New-Object Windows.Forms.Panel
$list.BackColor = $Card
$list.Location = New-Object Drawing.Point(40, 146)
$list.Size = New-Object Drawing.Size(680, 300)
$form.Controls.Add($list)

$open = New-Object Windows.Forms.Button
$open.Text = "Open Flash"
$open.FlatStyle = "Flat"
$open.FlatAppearance.BorderSize = 0
$open.BackColor = $Ink
$open.ForeColor = $Bg
$open.Size = New-Object Drawing.Size(130, 36)
$open.Location = New-Object Drawing.Point(590, 464)
$open.Visible = $false
$form.Controls.Add($open)

$close = New-Object Windows.Forms.Button
$close.Text = "Close"
$close.FlatStyle = "Flat"
$close.FlatAppearance.BorderColor = [Drawing.Color]::FromArgb(60, 60, 66)
$close.ForeColor = $Ink
$close.Size = New-Object Drawing.Size(100, 36)
$close.Location = New-Object Drawing.Point(478, 464)
$close.Visible = $false
$form.Controls.Add($close)

$rows = @()
$marks = @{ wait = [char]0x25CB; run = [char]0x25D4; ok = [char]0x2713; warn = "!"; ask = [char]0x2022; fail = [char]0x2715 }
$markColour = @{ wait = $Dim; run = $Accent; ok = $Ink; warn = [Drawing.Color]::FromArgb(255, 182, 72); ask = $Accent; fail = [Drawing.Color]::FromArgb(255, 93, 93) }

function Show-Status($s) {
    $steps = @($s.steps)
    while ($script:rows.Count -lt $steps.Count) {
        $i = $script:rows.Count
        $mark = New-Object Windows.Forms.Label
        $mark.Location = New-Object Drawing.Point(18, (14 + 34 * $i))
        $mark.Size = New-Object Drawing.Size(24, 24)
        $mark.Font = New-Object Drawing.Font("Segoe UI Symbol", 12)
        $name = New-Object Windows.Forms.Label
        $name.Location = New-Object Drawing.Point(50, (16 + 34 * $i))
        $name.AutoSize = $true
        $side = New-Object Windows.Forms.Label
        $side.Location = New-Object Drawing.Point(330, (16 + 34 * $i))
        $side.Size = New-Object Drawing.Size(330, 22)
        $side.TextAlign = "TopRight"
        $side.ForeColor = $Dim
        $side.AutoEllipsis = $true
        $list.Controls.AddRange(@($mark, $name, $side))
        $script:rows += ,@($mark, $name, $side)
    }
    $doneCount = 0
    for ($i = 0; $i -lt $steps.Count; $i++) {
        $st = [string]$steps[$i].state
        $row = $script:rows[$i]
        $row[0].Text = [string]$marks[$st]
        $row[0].ForeColor = $markColour[$st]
        $row[1].Text = [string]$steps[$i].title
        $row[1].ForeColor = if ($st -eq "wait") { $Dim } else { $Ink }
        $row[2].Text = if ($st -eq "ask") { "Asking for your password" } else { [string]$steps[$i].note }
        if ($st -in @("ok", "warn")) { $doneCount++ }
        if ($st -eq "run") { $doneCount += 0.5 }
    }
    $p = if ($steps.Count) { [int](100 * $doneCount / $steps.Count) } else { 0 }
    if ($s.done) { $p = 100 }
    $pct.Text = "$p%"
    $fill.Width = [int](680 * $p / 100)
    if ($s.failed) {
        $title.Text = "Flash didn't finish."
        $note.Text = [string]$s.failed
        $close.Visible = $true
    } elseif ($s.done) {
        $title.Text = "Flash is ready."
        $note.Text = [string]$s.doneNote
        $open.Visible = $true
        $close.Visible = $true
    }
}

# ---- the script, out of sight ----
$log = Join-Path $Work "install.log"
$env:SETUP_WINDOW_DIR = $Work
$psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Script`"")
if ($Dev) { $psArgs += "-Dev" }
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError (Join-Path $Work "errors.log")

$last = ""
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    $file = Join-Path $Work "status.json"
    if (Test-Path $file) {
        try {
            $text = [IO.File]::ReadAllText($file)
            if ($text -ne $script:last) { $script:last = $text; Show-Status ($text | ConvertFrom-Json) }
        } catch { }
    }
    if ($proc.HasExited -and -not $close.Visible) {
        $timer.Stop()
        if ($proc.ExitCode -ne 0) {
            $why = (Get-Content (Join-Path $Work "errors.log") -ErrorAction SilentlyContinue | Select-Object -Last 1)
            Show-Status ([pscustomobject]@{ steps = @(); failed = "The installer stopped. $why" })
        }
    }
})
$timer.Start()

$open.Add_Click({
    $file = Join-Path $Work "open.json"
    if (Test-Path $file) {
        $cmd = @((Get-Content $file -Raw | ConvertFrom-Json).cmd)
        if ($cmd.Count) { Start-Process -FilePath $cmd[0] -ArgumentList ($cmd | Select-Object -Skip 1) -WindowStyle Hidden }
    }
    $form.Close()
})
$close.Add_Click({ $form.Close() })
$form.Add_FormClosing({
    param($sender, $e)
    if (-not $proc.HasExited) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Flash is still being installed. Stopping now leaves it half set up.`n`nStop anyway?",
            "Install Flash", "YesNo", "Warning")
        if ($answer -ne "Yes") { $e.Cancel = $true; return }
        try { $proc.Kill() } catch { }
    }
})
[void]$form.ShowDialog()
