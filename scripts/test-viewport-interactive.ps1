[CmdletBinding()]
param(
    [ValidateSet('2026.3', '2027')]
    [string]$MayaVersion = '2027',
    [string]$MayaLocation = '',
    [ValidateRange(30, 86400)]
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $MayaLocation) {
    $MayaLocation = "C:\Program Files\Autodesk\Maya$(Get-MayaMcpMajorVersion -MayaVersion $MayaVersion)"
}
$packageRoot = Get-MayaMcpPackageDirectory -MayaVersion $MayaVersion
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
$processCleanup = 'normal'
try {
    Write-Host "Launching an isolated Maya viewport test. Evidence: $evidenceDir"
    if (-not $process.Start()) { throw 'Maya did not start.' }
    $started = $true
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $passingResultSeenAt = $null
    while (-not $process.HasExited -and [DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $resultPath) {
            try {
                $liveResult = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
                if ($liveResult.passed -and $liveResult.plugin_unload -eq 'passed') {
                    if ($null -eq $passingResultSeenAt) {
                        $passingResultSeenAt = [DateTime]::UtcNow
                    } elseif (([DateTime]::UtcNow - $passingResultSeenAt).TotalSeconds -ge 10) {
                        # Maya has completed the gate and unloaded our DLL, but
                        # an Autodesk-owned shutdown service may still keep this
                        # isolated process alive. Clean up only the child this
                        # launcher owns after a bounded graceful-exit window.
                        $process.Kill()
                        $process.WaitForExit()
                        $processCleanup = 'forced-after-passing-plugin-unload'
                        break
                    }
                }
            } catch {
                # The Maya process may still be atomically replacing the result.
            }
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $process.HasExited) {
        # Kill only the Process object started above. Never enumerate or stop
        # Maya by executable name, window title, or process ID.
        $process.Kill()
        $process.WaitForExit()
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
$result | Add-Member -NotePropertyName process_cleanup -NotePropertyValue $processCleanup -Force
if (-not $result.passed) {
    $result | ConvertTo-Json -Depth 20
    throw "Interactive viewport validation failed. See $resultPath"
}
if ($processCleanup -eq 'normal' -and $exitCode -ne 0) {
    throw "Maya wrote a passing result but exited with code $exitCode. See $resultPath"
}
$result | Add-Member -NotePropertyName process_exit_code -NotePropertyValue $exitCode -Force
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $resultPath -Encoding utf8
$result | ConvertTo-Json -Depth 20
if ($processCleanup -ne 'normal') {
    Write-Warning 'Maya passed and unloaded Maya MCP but required isolated-process cleanup after the exit grace period.'
}
Write-Host "Interactive viewport validation passed. Evidence: $resultPath"
