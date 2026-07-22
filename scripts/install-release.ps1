[CmdletBinding()]
param(
    [string]$ModulesDirectory = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'),
    [string]$MayaLocation = ''
)

$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $releaseRoot 'package-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Keep Install-MayaMcp.ps1 beside package-manifest.json and the Maya MCP folder.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$version = [string]$manifest.version
$target = [string]$manifest.maya_target
$major = [string]$manifest.maya_major_version
$folderName = "maya-mcp-$version-maya$target"
$moduleFileName = "maya-mcp-$major.mod"
$sourceFolder = Join-Path $releaseRoot $folderName
$sourceModule = Join-Path $releaseRoot $moduleFileName
if (-not (Test-Path -LiteralPath (Join-Path $sourceFolder 'plug-ins\maya_mcp.mll'))) {
    throw "The Maya $target plug-in package is incomplete."
}

New-Item -ItemType Directory -Force -Path $ModulesDirectory | Out-Null
$installedFolder = Join-Path $ModulesDirectory $folderName
if (-not ([System.IO.Path]::GetFullPath($sourceFolder).Equals(
    [System.IO.Path]::GetFullPath($installedFolder),
    [System.StringComparison]::OrdinalIgnoreCase
))) {
    New-Item -ItemType Directory -Force -Path $installedFolder | Out-Null
    Get-ChildItem -LiteralPath $sourceFolder | Copy-Item -Destination $installedFolder -Recurse -Force
}
$installedModule = Join-Path $ModulesDirectory $moduleFileName
if (-not ([System.IO.Path]::GetFullPath($sourceModule).Equals(
    [System.IO.Path]::GetFullPath($installedModule),
    [System.StringComparison]::OrdinalIgnoreCase
))) {
    Copy-Item -LiteralPath $sourceModule -Destination $installedModule -Force
}

$legacyModule = Join-Path $ModulesDirectory 'maya-mcp.mod'
if (Test-Path -LiteralPath $legacyModule) {
    $legacyText = Get-Content -LiteralPath $legacyModule -Raw
    if ($legacyText -match '(?m)^\+\s+maya-mcp\s+') {
        Remove-Item -LiteralPath $legacyModule -Force
    }
}

if (-not $MayaLocation) { $MayaLocation = "C:\Program Files\Autodesk\Maya$major" }
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
$installedPlugin = Join-Path $installedFolder 'plug-ins\maya_mcp.mll'
if (Test-Path -LiteralPath $mayapy) {
    $previousModulePath = $env:MAYA_MODULE_PATH
    try {
        $env:MAYA_MODULE_PATH = if ($previousModulePath) {
            "$ModulesDirectory;$previousModulePath"
        } else {
            $ModulesDirectory
        }
        & $mayapy (Join-Path $releaseRoot 'configure-autoload.py') $installedPlugin ([string]$manifest.maya_api_version)
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure Maya MCP autoload.' }
    } finally {
        $env:MAYA_MODULE_PATH = $previousModulePath
    }
} else {
    Write-Warning "Maya $target was not found at $MayaLocation. The module is installed; enable maya_mcp in Maya's Plug-in Manager once."
}

Write-Host "Maya MCP $version for Maya $target is installed." -ForegroundColor Green
Write-Host 'Close and reopen Maya. Future compatible releases are available from Maya MCP > Check for Updates.'
