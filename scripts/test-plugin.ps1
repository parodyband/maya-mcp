[CmdletBinding()]
param([string]$MayaLocation = 'C:\Program Files\Autodesk\Maya2027')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package'
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
if (-not (Test-Path -LiteralPath $mayapy)) { throw "mayapy was not found at $mayapy" }
if (-not (Test-Path -LiteralPath $plugin)) { throw 'Build the Release plug-in first.' }

$testRoot = Join-Path $repoRoot "build\test-runtime\$PID"
$mayaAppDir = Join-Path $testRoot 'maya-app'
$localAppData = Join-Path $testRoot 'local-app-data'
New-Item -ItemType Directory -Path $mayaAppDir, $localAppData -Force | Out-Null

$environmentNames = @(
    'MAYA_APP_DIR', 'LOCALAPPDATA', 'MAYA_MODULE_PATH', 'MAYA_MCP_TOKEN',
    'MAYA_MCP_ALLOW_UNSAFE_CODE', 'MAYA_DISABLE_CIP', 'MAYA_DISABLE_CER',
    'PYTHONPATH'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Invoke-MayaTest([string]$RelativePath, [string]$Label) {
    & $mayapy (Join-Path $repoRoot $RelativePath)
    if ($LASTEXITCODE -ne 0) { throw "$Label failed." }
}

try {
    $env:MAYA_APP_DIR = $mayaAppDir
    $env:LOCALAPPDATA = $localAppData
    $env:MAYA_MODULE_PATH = if ($env:MAYA_MODULE_PATH) {
        "$packageRoot;$env:MAYA_MODULE_PATH"
    } else { $packageRoot }
    Remove-Item Env:MAYA_MCP_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:MAYA_MCP_ALLOW_UNSAFE_CODE = '0'
    $env:MAYA_DISABLE_CIP = '1'
    $env:MAYA_DISABLE_CER = '1'

    Invoke-MayaTest 'tests\vp2_command_test.py' 'Native VP2 command test'
    Invoke-MayaTest 'tests\viewport_contract_test.py' 'Viewport contract test'
    Invoke-MayaTest 'tests\scene_map_test.py' 'Viewport scene-map test'
    Invoke-MayaTest 'tests\rig_preview_test.py' 'Rig-preview test'
    Invoke-MayaTest 'tests\rig_operations_test.py' 'Typed rig-operations test'
    Invoke-MayaTest 'tests\smoke_test.py' 'Maya plug-in smoke test'
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name, $previousEnvironment[$name], 'Process'
        )
    }
}
