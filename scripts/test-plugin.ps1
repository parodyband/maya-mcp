[CmdletBinding()]
param([string]$MayaLocation = 'C:\Program Files\Autodesk\Maya2027')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package'
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
if (-not (Test-Path -LiteralPath $mayapy)) { throw "mayapy was not found at $mayapy" }
if (-not (Test-Path -LiteralPath $plugin)) { throw 'Build the Release plug-in first.' }

$env:MAYA_MODULE_PATH = if ($env:MAYA_MODULE_PATH) { "$packageRoot;$env:MAYA_MODULE_PATH" } else { $packageRoot }
& $mayapy (Join-Path $repoRoot 'tests\smoke_test.py')
if ($LASTEXITCODE -ne 0) { throw 'Maya plug-in smoke test failed.' }
