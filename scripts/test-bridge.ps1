[CmdletBinding()]
param(
    [ValidateSet('2026.3', '2027')]
    [string]$MayaVersion = '2027'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')
$bridge = Join-Path (Get-MayaMcpPackageDirectory -MayaVersion $MayaVersion) 'maya-mcp\bin\maya-mcp-bridge.exe'
if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) {
    throw "Build the Maya $MayaVersion Release package before testing the bridge."
}

python (Join-Path $repoRoot 'tests\bridge_test.py') $bridge --launcher (Join-Path $repoRoot 'scripts\start-client-bridge.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Maya MCP stdio bridge tests failed.' }
