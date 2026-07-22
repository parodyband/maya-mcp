[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Configuration Release -MayaVersion All
}

$projectText = Get-Content -LiteralPath (Join-Path $repoRoot 'CMakeLists.txt') -Raw
if ($projectText -notmatch 'project\(maya_mcp VERSION ([0-9]+\.[0-9]+\.[0-9]+)') {
    throw 'Could not read the Maya MCP version from CMakeLists.txt.'
}
$version = $Matches[1]
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot "dist\v$version" }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$releaseAssets = @()
foreach ($target in @('2026.3', '2027')) {
    $packageRoot = Get-MayaMcpPackageDirectory -MayaVersion $target
    $packageManifestPath = Join-Path $packageRoot 'package-manifest.json'
    $pluginPath = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
    if (-not (Test-Path -LiteralPath $pluginPath)) { throw "Missing Maya $target Release build." }
    $packageManifest = Get-Content -LiteralPath $packageManifestPath -Raw | ConvertFrom-Json
    if ([string]$packageManifest.version -ne $version -or [string]$packageManifest.maya_target -ne $target) {
        throw "Maya $target package metadata is stale."
    }

    $assetName = "maya-mcp-v$version-maya$target-windows-x64.zip"
    $assetPath = Join-Path $OutputDirectory $assetName
    if (Test-Path -LiteralPath $assetPath) { Remove-Item -LiteralPath $assetPath -Force }
    $folderName = "maya-mcp-$version-maya$target"
    $moduleFileName = "maya-mcp-$($packageManifest.maya_major_version).mod"
    $staging = Join-Path $OutputDirectory ".staging-$target-$PID"
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Copy-Item -LiteralPath (Join-Path $packageRoot 'maya-mcp') -Destination (Join-Path $staging $folderName) -Recurse
    $moduleText = (Get-Content -LiteralPath (Join-Path $packageRoot 'maya-mcp.mod') -Raw).Replace('./maya-mcp', "./$folderName")
    [System.IO.File]::WriteAllText(
        (Join-Path $staging $moduleFileName),
        $moduleText,
        [System.Text.UTF8Encoding]::new($false)
    )
    Copy-Item -LiteralPath $packageManifestPath -Destination (Join-Path $staging 'package-manifest.json')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install-release.ps1') -Destination (Join-Path $staging 'Install-MayaMcp.ps1')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install-release.cmd') -Destination (Join-Path $staging 'Install-MayaMcp.cmd')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'configure-autoload.py') -Destination (Join-Path $staging 'configure-autoload.py')
    Get-ChildItem -LiteralPath $staging -Directory -Filter '__pycache__' -Recurse |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $staging -File -Recurse |
        Where-Object Extension -In @('.pyc', '.pyo') |
        Remove-Item -Force
    Compress-Archive -LiteralPath (
        (Join-Path $staging $moduleFileName),
        (Join-Path $staging 'package-manifest.json'),
        (Join-Path $staging $folderName),
        (Join-Path $staging 'Install-MayaMcp.cmd'),
        (Join-Path $staging 'Install-MayaMcp.ps1'),
        (Join-Path $staging 'configure-autoload.py')
    ) -DestinationPath $assetPath -CompressionLevel Optimal
    Remove-Item -LiteralPath $staging -Recurse -Force
    $asset = Get-Item -LiteralPath $assetPath
    $releaseAssets += [ordered]@{
        maya_target = $target
        maya_major_version = [string]$packageManifest.maya_major_version
        maya_api_version = [int]$packageManifest.maya_api_version
        platform = 'windows-x64'
        name = $asset.Name
        size = [long]$asset.Length
        sha256 = (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$releaseManifest = [ordered]@{
    schema_version = 1
    name = 'maya-mcp'
    version = $version
    repository = 'parodyband/maya-mcp'
    assets = $releaseAssets
}
$releaseManifestPath = Join-Path $OutputDirectory 'release-manifest.json'
[System.IO.File]::WriteAllText(
    $releaseManifestPath,
    ($releaseManifest | ConvertTo-Json -Depth 8) + "`n",
    [System.Text.UTF8Encoding]::new($false)
)
Get-ChildItem -LiteralPath $OutputDirectory -File | Select-Object Name,Length
Write-Host "Release bundle ready at $OutputDirectory" -ForegroundColor Green
