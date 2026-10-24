param([switch]$KeepBuildFiles)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run Setup.bat first to create the Python environment.'
}

$scratch = Join-Path $project '.tmp\exe-build'
$dist = Join-Path $project 'dist'
$exeName = 'BookScannerPDFExport'
$work = Join-Path $scratch 'work'
$spec = Join-Path $scratch 'spec'
New-Item -ItemType Directory -Force $scratch, $dist, $work, $spec | Out-Null
$env:TEMP = $scratch
$env:TMP = $scratch
$env:PYINSTALLER_CONFIG_DIR = Join-Path $scratch 'config'
$modelData = (Join-Path $project 'models') + ';models'
$assetData = (Join-Path $project 'assets') + ';assets'
$webData = (Join-Path $project 'web') + ';web'
$readmeData = (Join-Path $project 'README.md') + ';.'
$icon = Join-Path $project 'assets\app-icon.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    throw "EXE icon missing: $icon"
}

Push-Location $project
try {
    & $python -m PyInstaller `
        --noconfirm --clean --onefile --console `
        --name $exeName `
        --icon $icon `
        --distpath $dist --workpath $work --specpath $spec `
        --add-data $modelData `
        --add-data $assetData `
        --add-data $webData `
        --add-data $readmeData `
        --collect-all lingua `
        --collect-all pymupdf `
        --collect-all cv2 `
        web_app.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Write-Output (Join-Path $dist ($exeName + '.exe'))
}
finally {
    Pop-Location
    if (-not $KeepBuildFiles -and (Test-Path -LiteralPath $work)) {
        $resolved = [IO.Path]::GetFullPath($work)
        $workspace = [IO.Path]::GetFullPath($project).TrimEnd('\') + '\'
        if (-not $resolved.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove build directory outside workspace: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
