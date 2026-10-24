$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repository = 'Rahul130603/EPDF-INVENTORY'
$headers = @{ 'User-Agent' = 'EPDF-Inventory-Portable'; 'Accept' = 'application/vnd.github+json' }
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repository/releases/latest" -Headers $headers
$asset = @($release.assets | Where-Object name -eq 'BookScannerPDFExport.exe')
$checksums = @($release.assets | Where-Object name -eq 'SHA256SUMS.txt')
if ($asset.Count -ne 1 -or $checksums.Count -ne 1) {
    throw 'The latest release does not contain the portable EXE and its checksum. Download them from GitHub Releases.'
}
$checksumText = (Invoke-WebRequest -Uri $checksums[0].browser_download_url -Headers $headers -UseBasicParsing).Content
$checksumLine = @($checksumText -split '\r?\n' | Where-Object { $_ -match '^([a-fA-F0-9]{64})\s+\*?BookScannerPDFExport\.exe$' })
if ($checksumLine.Count -ne 1) { throw 'The release checksum is invalid.' }
$expectedHash = ($checksumLine[0] -split '\s+')[0]
$destinationFolder = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force -Path $destinationFolder | Out-Null
$destination = Join-Path $destinationFolder 'BookScannerPDFExport.exe'
$needsDownload = -not (Test-Path -LiteralPath $destination)
if (-not $needsDownload) {
    $needsDownload = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $expectedHash
}
if ($needsDownload) {
    Write-Host "Downloading EPDF Inventory $($release.tag_name)..."
    $temporary = Join-Path $destinationFolder 'BookScannerPDFExport.download'
    Invoke-WebRequest -Uri $asset[0].browser_download_url -Headers $headers -OutFile $temporary -UseBasicParsing
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $expectedHash) {
        Remove-Item -LiteralPath $temporary -Force
        throw 'Download verification failed. Please try again.'
    }
    Move-Item -LiteralPath $temporary -Destination $destination -Force
}
Write-Host 'Opening EPDF Inventory at http://localhost:4510. Keep this window open.'
& $destination
if ($LASTEXITCODE -ne 0) { throw "EPDF Inventory exited with code $LASTEXITCODE" }
