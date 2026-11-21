# The Windows installer, checked on Windows: it parses, and its reading of pip's output says what it should.
$ErrorActionPreference = "Stop"
$path = Join-Path $PSScriptRoot "..\..\install.ps1"
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $path), [ref]$tokens, [ref]$errors)
if ($errors.Count) { $errors | ForEach-Object { Write-Host "$($_.Extent.StartLineNumber): $($_.Message)" }; exit 1 }
# The setup window's helpers parse too.
$kit = Join-Path $PSScriptRoot "..\..\installer\window\window.ps1"
$null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $kit), [ref]$tokens, [ref]$errors)
if ($errors.Count) { $errors | ForEach-Object { Write-Host "window.ps1 $($_.Extent.StartLineNumber): $($_.Message)" }; exit 1 }
# Format-PipLine on its own, out of the installer, without running the install.
$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq "Format-PipLine" }, $true)[0]
. ([scriptblock]::Create($fn.Extent.Text))
$cases = @(
    @("DEBUG Selecting: httpie==3.2.4 [compatible] (httpie-3.2.4-py3-none-any.whl)", "found httpie 3.2.4"),
    @("DEBUG Adding transitive dependency for cryptography==50.0.2: cffi{platform_python_implementation != 'PyPy'}>=2.0.0", "cryptography needs cffi>=2.0.0"),
    @("DEBUG Adding transitive dependency for flash==0.5.12: colorama*", "flash needs colorama"),
    @("pipx >(_setup:1958): pipx version is 1.18.19", $null),
    @("creating virtual environment...", "making Flash a space of its own"),
    @("installing flash from spec 'C:\temp\flash'...", "installing flash"),
    @("Collecting argcomplete>=3.5 (from flash==0.5.12)", "looking at argcomplete>=3.5"),
    @("  Downloading playwright-1.65.2-py3-none-win_amd64.whl (40.1 MB)", "downloading playwright 1.65.2 (40.1 MB)"),
    @("  Downloading playwright-1.65.2-py3-none-manylinux1_x86_64.whl.metadata (3.3 kB)", $null),
    @("  Building wheel for flash (pyproject.toml): started", "building flash"),
    @("  Building wheel for flash (pyproject.toml): finished with status 'done'", $null),
    @("Successfully installed anyio-4.16.0 argcomplete-3.7.2 certifi-2026.11.20", "installed 3 packages"),
    @("Resolved 37 packages in 1.91s", "resolved 37 packages in 1.91s"),
    @("Downloading pydantic-core (2.2MiB)", "downloading pydantic-core (2.2MiB)"),
    @(" Downloaded lxml", "downloaded lxml"),
    @(" + rich==14.0.0", "added rich 14.0.0")
)
$bad = 0
foreach ($c in $cases) {
    $got = Format-PipLine $c[0]
    if ($got -ne $c[1]) { Write-Host "FAIL: '$($c[0])' gave '$got', not '$($c[1])'"; $bad++ }
}
if ($bad) { exit 1 }
Write-Host "install.ps1 parses, and reads pip's output right ($($cases.Count) lines)."
