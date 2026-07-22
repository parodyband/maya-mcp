[CmdletBinding()]
param(
    [ValidateSet('2026.3', '2027')]
    [string]$MayaVersion = '2027',
    [string]$ModulesDirectory = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules')
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$packageRoot = Get-MayaMcpPackageDirectory -MayaVersion $MayaVersion
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
if (-not (Test-Path -LiteralPath $plugin)) { throw 'Build the Release plug-in first.' }
$packagedModulePath = Join-Path $packageRoot 'maya-mcp.mod'
$packagedModule = Get-Content -LiteralPath $packagedModulePath -Raw
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'package-manifest.json') -Raw | ConvertFrom-Json
$version = [string]$manifest.version
if (-not $version -or [string]$manifest.maya_target -ne $MayaVersion) {
    throw "Package metadata does not match Maya $MayaVersion."
}
$majorVersion = Get-MayaMcpMajorVersion -MayaVersion $MayaVersion
$moduleFolderName = "maya-mcp-$version-maya$MayaVersion"
New-Item -ItemType Directory -Force -Path $ModulesDirectory | Out-Null
$installedModule = Join-Path $ModulesDirectory $moduleFolderName
New-Item -ItemType Directory -Force -Path $installedModule | Out-Null
Get-ChildItem -LiteralPath (Join-Path $packageRoot 'maya-mcp') | Copy-Item -Destination $installedModule -Recurse -Force
$installedModuleFile = Join-Path $ModulesDirectory "maya-mcp-$majorVersion.mod"
$installedModuleText = $packagedModule.Replace('./maya-mcp', "./$moduleFolderName")
[System.IO.File]::WriteAllText(
    $installedModuleFile,
    $installedModuleText,
    [System.Text.UTF8Encoding]::new($false)
)
$legacyModuleFile = Join-Path $ModulesDirectory 'maya-mcp.mod'
if (Test-Path -LiteralPath $legacyModuleFile) {
    $legacyText = Get-Content -LiteralPath $legacyModuleFile -Raw
    if ($legacyText -match '(?m)^\+\s+maya-mcp\s+') {
        Remove-Item -LiteralPath $legacyModuleFile -Force
    }
}
Write-Host "Installed Maya MCP $version for Maya $MayaVersion in $installedModule"
