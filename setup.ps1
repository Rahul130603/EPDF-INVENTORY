$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force '.tmp' | Out-Null
$env:TEMP = Join-Path $PSScriptRoot '.tmp'
$env:TMP = $env:TEMP
$venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$rebuildVenv = -not (Test-Path -LiteralPath $venvPython)
if (-not $rebuildVenv) {
    try {
        & $venvPython --version 2>$null | Out-Null
        $rebuildVenv = $LASTEXITCODE -ne 0
    } catch {
        $rebuildVenv = $true
    }
}
if ($rebuildVenv) {
    # A copied virtual environment can still contain the old computer's Python path.
    $venvPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.venv'))
    if ($venvPath -ne (Join-Path $PSScriptRoot '.venv')) {
        throw 'Virtual environment path is outside this project.'
    }
    $launcher = 'python'
    $launcherArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $hasPython313 = $false
        try {
            & py -3.13 --version 2>$null | Out-Null
            $hasPython313 = $LASTEXITCODE -eq 0
        } catch {}
        if ($hasPython313) {
            $launcher = 'py'
            $launcherArgs = @('-3.13')
        }
    }
    & $launcher @launcherArgs -m venv --clear $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required. Install Python and run Setup.bat again.' }
}
& '.venv/Scripts/python.exe' -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip.' }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency download failed. Check internet access and rerun Setup.bat.' }
& '.venv/Scripts/python.exe' setup_ocr.py
if ($LASTEXITCODE -ne 0) { throw 'OCR model download failed. Check internet access and rerun Setup.bat.' }
Write-Host 'Setup complete. Double-click Start.bat.' -ForegroundColor Green
