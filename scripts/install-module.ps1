[CmdletBinding()]
param([string]$ModulesDirectory = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'))

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package'
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
if (-not (Test-Path -LiteralPath $plugin)) { throw 'Build the Release plug-in first.' }
New-Item -ItemType Directory -Force -Path $ModulesDirectory | Out-Null
Copy-Item -LiteralPath (Join-Path $packageRoot 'maya-mcp.mod') -Destination $ModulesDirectory -Force
$installedModule = Join-Path $ModulesDirectory 'maya-mcp'
New-Item -ItemType Directory -Force -Path $installedModule | Out-Null
Get-ChildItem -LiteralPath (Join-Path $packageRoot 'maya-mcp') | Copy-Item -Destination $installedModule -Recurse -Force
Write-Host "Installed maya-mcp in $ModulesDirectory"
