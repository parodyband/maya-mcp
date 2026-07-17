[CmdletBinding()]
param(
    [string]$MayaLocation = 'C:\Program Files\Autodesk\Maya2027',
    [ValidateRange(30, 86400)]
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot 'build\maya2027-mcp-vs2022\package'
$maya = Join-Path $MayaLocation 'bin\maya.exe'
$plugin = Join-Path $packageRoot 'maya-mcp\plug-ins\maya_mcp.mll'
$testModule = Join-Path $repoRoot 'tests\interactive_viewport_test.py'
if (-not (Test-Path -LiteralPath $maya)) { throw "Maya was not found at $maya" }
if (-not (Test-Path -LiteralPath $plugin)) {
    throw 'Build the Release plug-in first with .\scripts\build.ps1 -Configuration Release.'
}
if (-not (Test-Path -LiteralPath $testModule)) { throw "Missing $testModule" }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runId = [Guid]::NewGuid().ToString('N')
$evidenceDir = Join-Path $repoRoot "build\viewport-validation\$stamp-$PID-$runId"
$mayaAppDir = Join-Path $evidenceDir 'maya-app'
$localAppData = Join-Path $evidenceDir 'local-app-data'
$resultPath = Join-Path $evidenceDir 'result.json'
New-Item -ItemType Directory -Path $mayaAppDir -Force | Out-Null
New-Item -ItemType Directory -Path $localAppData -Force | Out-Null

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $maya
$startInfo.WorkingDirectory = $repoRoot
$startInfo.UseShellExecute = $false
$startInfo.Arguments = "-noAutoloadPlugins -command `"python(\`"import interactive_viewport_test; interactive_viewport_test.install('$runId')\`")`""

# Set isolation variables on the child only. This script never mutates the
# environment or discovery files of a Maya process that is already running.
$testsPath = Join-Path $repoRoot 'tests'
$childEnvironment = $startInfo.EnvironmentVariables
$childEnvironment.Remove('MAYA_MCP_TOKEN')
$childEnvironment.Remove('MAYA_MCP_PORT')
$childEnvironment['MAYA_APP_DIR'] = $mayaAppDir
$childEnvironment['LOCALAPPDATA'] = $localAppData
$childEnvironment['MAYA_MODULE_PATH'] = $packageRoot
$childEnvironment['PYTHONPATH'] = $testsPath
$childEnvironment['MAYA_MCP_ALLOW_UNSAFE_CODE'] = '0'
$childEnvironment['MAYA_MCP_VIEWPORT_EVIDENCE_DIR'] = $evidenceDir
$childEnvironment['MAYA_MCP_VIEWPORT_RESULT'] = $resultPath
$childEnvironment['MAYA_MCP_VIEWPORT_TIMEOUT_SECONDS'] = [string]$TimeoutSeconds
$childEnvironment['MAYA_MCP_VIEWPORT_RUN_ID'] = $runId
$childEnvironment['MAYA_MCP_VIEWPORT_PLUGIN'] = $plugin
$childEnvironment['MAYA_DISABLE_CIP'] = '1'
$childEnvironment['MAYA_DISABLE_CER'] = '1'
$childEnvironment['MAYA_SKIP_USERSETUP_PY'] = '1'

$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
$exitCode = $null
$started = $false
try {
    Write-Host "Launching an isolated Maya viewport test. Evidence: $evidenceDir"
    if (-not $process.Start()) { throw 'Maya did not start.' }
    $started = $true
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        # Kill only the Process object started above. Never enumerate or stop
        # Maya by executable name, window title, or process ID.
        if (-not $process.HasExited) {
            $process.Kill()
            $process.WaitForExit()
        }
        throw "Interactive viewport validation timed out after $TimeoutSeconds seconds."
    }
    $exitCode = $process.ExitCode
} finally {
    try {
        if ($started -and -not $process.HasExited) {
            # An unexpected launcher error still cleans up only this child.
            $process.Kill()
            $process.WaitForExit()
        }
    } finally {
        $process.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $resultPath)) {
    $reportedExitCode = if ($null -ne $exitCode) { $exitCode } else { 'unknown' }
    throw "Maya exited with code $reportedExitCode without writing $resultPath"
}
$result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
if (-not $result.passed) {
    $result | ConvertTo-Json -Depth 20
    throw "Interactive viewport validation failed. See $resultPath"
}
if ($exitCode -ne 0) {
    throw "Maya wrote a passing result but exited with code $exitCode. See $resultPath"
}
$result | ConvertTo-Json -Depth 20
Write-Host "Interactive viewport validation passed. Evidence: $resultPath"
