[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [string]$MayaLocation = 'C:\Program Files\Autodesk\Maya2027',
    [string]$ModulesDirectory = (
        Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'
    ),
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
if (-not (Test-Path -LiteralPath $mayapy)) {
    throw "Maya 2027 mayapy was not found at $mayapy"
}

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Configuration $Configuration
}

& (Join-Path $PSScriptRoot 'install-module.ps1') -ModulesDirectory $ModulesDirectory

$packagedModule = Get-Content -LiteralPath (
    Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package\maya-mcp.mod'
) -Raw
if ($packagedModule -notmatch '(?m)^\+ maya-mcp ([^\s]+) ') {
    throw 'Could not read the installed Maya MCP version.'
}
$version = $Matches[1]
$installedPlugin = Join-Path $ModulesDirectory (
    "maya-mcp-$version\plug-ins\maya_mcp.mll"
)
$previousModulePath = $env:MAYA_MODULE_PATH
try {
    $env:MAYA_MODULE_PATH = if ($previousModulePath) {
        "$ModulesDirectory;$previousModulePath"
    } else {
        $ModulesDirectory
    }
    & $mayapy (Join-Path $PSScriptRoot 'configure-autoload.py') $installedPlugin
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure Maya MCP autoload.' }
}
finally {
    $env:MAYA_MODULE_PATH = $previousModulePath
}

Write-Host ''
Write-Host 'Maya MCP setup is complete.' -ForegroundColor Green
Write-Host 'Open Maya; the plug-in and local MCP server will start automatically.'
Write-Host 'Use the Maya MCP menu for status and per-session Python/MEL approval.'
