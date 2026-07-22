[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [ValidateSet('2026.3', '2027')]
    [string]$MayaVersion = '2027',
    [string]$MayaLocation = '',
    [string]$ModulesDirectory = (
        Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'
    ),
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $MayaLocation) {
    $majorVersion = Get-MayaMcpMajorVersion -MayaVersion $MayaVersion
    $MayaLocation = "C:\Program Files\Autodesk\Maya$majorVersion"
}
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
if (-not (Test-Path -LiteralPath $mayapy)) {
    throw "Maya $MayaVersion mayapy was not found at $mayapy"
}

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Configuration $Configuration -MayaVersion $MayaVersion
}

& (Join-Path $PSScriptRoot 'install-module.ps1') -MayaVersion $MayaVersion -ModulesDirectory $ModulesDirectory

$packageRoot = Get-MayaMcpPackageDirectory -MayaVersion $MayaVersion
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'package-manifest.json') -Raw | ConvertFrom-Json
$version = [string]$manifest.version
$installedPlugin = Join-Path $ModulesDirectory (
    "maya-mcp-$version-maya$MayaVersion\plug-ins\maya_mcp.mll"
)
$previousModulePath = $env:MAYA_MODULE_PATH
try {
    $env:MAYA_MODULE_PATH = if ($previousModulePath) {
        "$ModulesDirectory;$previousModulePath"
    } else {
        $ModulesDirectory
    }
    & $mayapy (Join-Path $PSScriptRoot 'configure-autoload.py') $installedPlugin ([string]$manifest.maya_api_version)
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure Maya MCP autoload.' }
}
finally {
    $env:MAYA_MODULE_PATH = $previousModulePath
}

Write-Host ''
Write-Host 'Maya MCP setup is complete.' -ForegroundColor Green
Write-Host "Open Maya $MayaVersion; the plug-in and local MCP server will start automatically."
Write-Host 'Use the Maya MCP menu for status and per-session Python/MEL approval.'
