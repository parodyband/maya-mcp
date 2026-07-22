$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "maya-mcp-client-config-test-$PID-$([guid]::NewGuid().ToString('N'))"
$shimRoot = Join-Path $testRoot 'bin'
$launcher = Join-Path $testRoot 'Start-MayaMcpBridge.ps1'
$log = Join-Path $testRoot 'commands.log'
$previousPath = $env:PATH
$previousLog = $env:MAYA_MCP_CLIENT_TEST_LOG
try {
    New-Item -ItemType Directory -Force -Path $shimRoot | Out-Null
    Set-Content -LiteralPath $launcher -Value '# test launcher' -Encoding ASCII
    $shim = @'
@echo off
echo %~n0 %*>>"%MAYA_MCP_CLIENT_TEST_LOG%"
if /I "%~n0"=="codex" if /I "%2"=="get" exit /b 1
exit /b 0
'@
    Set-Content -LiteralPath (Join-Path $shimRoot 'codex.cmd') -Value $shim -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $shimRoot 'claude.cmd') -Value $shim -Encoding ASCII
    $env:PATH = "$shimRoot;$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:MAYA_MCP_CLIENT_TEST_LOG = $log

    & (Join-Path $repoRoot 'scripts\configure-clients.ps1') -LauncherPath $launcher
    $commands = @(Get-Content -LiteralPath $log)
    if ($commands.Count -ne 4) { throw "Expected four client CLI commands, got $($commands.Count)." }
    if ($commands[0] -notmatch '^codex mcp get maya-mcp --json$') { throw "Unexpected Codex probe: $($commands[0])" }
    if ($commands[1] -notmatch '^codex mcp add maya-mcp -- .*powershell\.exe .*Start-MayaMcpBridge\.ps1$') {
        throw "Unexpected Codex registration: $($commands[1])"
    }
    if ($commands[2] -notmatch '^claude mcp (remove --scope user maya-mcp|add --transport stdio --scope user maya-mcp -- .*powershell\.exe .*)$') {
        throw "Unexpected Claude command: $($commands[2])"
    }
    if (-not ($commands | Where-Object {
        $_ -match '^claude mcp add --transport stdio --scope user maya-mcp -- .*powershell\.exe .*Start-MayaMcpBridge\.ps1$'
    })) {
        throw 'Claude Code registration command was not issued.'
    }
} finally {
    $env:PATH = $previousPath
    $env:MAYA_MCP_CLIENT_TEST_LOG = $previousLog
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

Write-Host 'MAYA_MCP_CLIENT_CONFIG_TEST_RESULT=passed codex=true claude_code=true' -ForegroundColor Green
