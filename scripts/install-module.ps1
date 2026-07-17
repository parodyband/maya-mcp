[CmdletBinding()]
param([string]$ModulesDirectory = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'))

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package'
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
if (-not (Test-Path -LiteralPath $plugin)) { throw 'Build the Release plug-in first.' }
$packagedModulePath = Join-Path $packageRoot 'maya-mcp.mod'
$packagedModule = Get-Content -LiteralPath $packagedModulePath -Raw
if ($packagedModule -notmatch '(?m)^\+ maya-mcp ([^\s]+) ') {
    throw 'Could not read the Maya MCP version from the packaged module file.'
}
$version = $Matches[1]
$moduleFolderName = "maya-mcp-$version"
New-Item -ItemType Directory -Force -Path $ModulesDirectory | Out-Null
$installedModule = Join-Path $ModulesDirectory $moduleFolderName
New-Item -ItemType Directory -Force -Path $installedModule | Out-Null
Get-ChildItem -LiteralPath (Join-Path $packageRoot 'maya-mcp') | Copy-Item -Destination $installedModule -Recurse -Force
$installedModuleFile = Join-Path $ModulesDirectory 'maya-mcp.mod'
$installedModuleText = $packagedModule.Replace('./maya-mcp', "./$moduleFolderName")
[System.IO.File]::WriteAllText(
    $installedModuleFile,
    $installedModuleText,
    [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Installed Maya MCP $version in $installedModule"
