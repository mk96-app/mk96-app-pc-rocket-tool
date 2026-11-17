# Builds the Windows installer: checks (lint, types, tests), PyInstaller bundle, Inno Setup.
# Result: versions\<version>\mk96-app-pc-rocket-tool-setup-<version>.exe (version from pyproject.toml).
# Needs uv and Inno Setup 6 (winget install astral-sh.uv / JRSoftware.InnoSetup).
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Find-Tool([string]$Name, [string[]]$Candidates) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "$Name not found. Install it and try again."
}

function Invoke-Step([string]$Title, [scriptblock]$Command) {
    Write-Host "==> $Title"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Title failed (exit code $LASTEXITCODE)" }
}

$uv = Find-Tool 'uv' @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")
$iscc = Find-Tool 'ISCC' @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe")

# pyproject uses dots (26.10.1); releases use the YY-M-R scheme with dashes (26-10-1).
$match = Select-String -Path 'pyproject.toml' -Pattern '^version = "([0-9.]+)"' | Select-Object -First 1
if (-not $match) { throw 'version not found in pyproject.toml' }
$version = $match.Matches[0].Groups[1].Value -replace '\.', '-'
$outputDir = Join-Path $PSScriptRoot "versions\$version"

Invoke-Step 'Sync environment' { & $uv sync --locked }
Invoke-Step 'Lint' { & $uv run ruff check src tests }
Invoke-Step 'Type check' { & $uv run mypy }
Invoke-Step 'Tests' { & $uv run pytest -q }
Invoke-Step 'Icon' { & $uv run python packaging\make_icon.py packaging\generated\app.ico }
Invoke-Step 'PyInstaller' {
    & $uv run pyinstaller packaging\mk96-app-pc-rocket-tool.spec --noconfirm --clean `
        --distpath build\dist --workpath build\work
}
Invoke-Step 'Installer' {
    & $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$PSScriptRoot\build\dist\mk96-app-pc-rocket-tool" `
        "/DOutputDir=$outputDir" packaging\installer.iss
}
Write-Host "Done: $outputDir\mk96-app-pc-rocket-tool-setup-$version.exe"
