$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "maya-mcp-client-config-test-$PID-$([guid]::NewGuid().ToString('N'))"
$shimRoot = Join-Path $testRoot 'bin'
$launcher = Join-Path $testRoot 'Start-MayaMcpBridge.ps1'
$log = Join-Path $testRoot 'commands.log'
$previousPath = $env:PATH
$previousLog = $env:MAYA_MCP_CLIENT_TEST_LOG
$previousClaudeConfig = $env:CLAUDE_CONFIG_DIR
try {
    New-Item -ItemType Directory -Force -Path $shimRoot | Out-Null
    Set-Content -LiteralPath $launcher -Value '# test launcher' -Encoding ASCII
    $shim = @'
@echo off
echo %~n0 %*>>"%MAYA_MCP_CLIENT_TEST_LOG%"
if /I "%~n0"=="codex" if /I "%2"=="get" (
  1>&2 echo Error: No MCP server named maya-mcp found.
  exit /b 1
)
if /I "%~n0"=="claude" if /I "%2"=="remove" (
  1>&2 echo No MCP server named maya-mcp.
  exit /b 1
)
exit /b 0
'@
    Set-Content -LiteralPath (Join-Path $shimRoot 'codex.cmd') -Value $shim -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $shimRoot 'claude.cmd') -Value $shim -Encoding ASCII
    $env:PATH = "$shimRoot;$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:MAYA_MCP_CLIENT_TEST_LOG = $log
    $env:CLAUDE_CONFIG_DIR = Join-Path $testRoot 'custom-claude'

    & (Join-Path $repoRoot 'scripts\configure-clients.ps1') -LauncherPath $launcher -AgentHome $testRoot
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
    $configure = Join-Path $repoRoot 'scripts\configure-clients.ps1'
    $source = Join-Path $repoRoot 'skills\maya-mcp\SKILL.md'
    $codexSkill = Join-Path $testRoot '.agents\skills\maya-mcp\SKILL.md'
    $claudeSkill = Join-Path $env:CLAUDE_CONFIG_DIR 'skills\maya-mcp\SKILL.md'
    foreach ($skill in @($codexSkill, $claudeSkill)) {
        if ((Get-FileHash $skill).Hash -ne (Get-FileHash $source).Hash) { throw 'Companion skill not installed exactly.' }
    }
    # A packaged configurator locates its sibling skill, independent of cwd.
    $package = Join-Path $testRoot 'package'
    New-Item -ItemType Directory -Path $package | Out-Null
    Copy-Item $configure (Join-Path $package 'Configure-MayaMcpClients.ps1')
    Copy-Item (Join-Path $repoRoot 'skills') $package -Recurse
    $configure = Join-Path $package 'Configure-MayaMcpClients.ps1'
    Add-Content (Join-Path $package 'skills\maya-mcp\SKILL.md') "`nUpdated fixture"
    & $configure -SkillsOnly -ExistingSkillsOnly -AgentHome $testRoot
    if ((Get-Content $codexSkill -Raw) -notmatch 'Updated fixture') { throw 'Managed skill was not updated.' }
    Add-Content $codexSkill 'User customization'
    Remove-Item -LiteralPath $claudeSkill
    & $configure -SkillsOnly -ExistingSkillsOnly -AgentHome $testRoot
    if ((Get-Content $codexSkill -Raw) -notmatch 'User customization') { throw 'Custom edits overwritten.' }
    if (-not (Test-Path $claudeSkill)) { throw 'Missing managed file was not repaired.' }
    $fresh = Join-Path $testRoot 'fresh-user'
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $fresh
    if (Test-Path $fresh) { throw 'Update enrolled an unconfigured client.' }
    & $configure -SkillsOnly -SkipSkills -AgentHome $fresh
    if (Test-Path $fresh) { throw 'SkipSkills installed a skill.' }
    New-Item -ItemType Directory -Path (Join-Path $fresh '.agents\skills\maya-mcp') -Force | Out-Null
    $unowned = Join-Path $fresh '.agents\skills\maya-mcp\SKILL.md'
    Set-Content $unowned 'Unowned skill'
    & $configure -SkillsOnly -SkipClaudeCode -AgentHome $fresh
    if ((Get-Content $unowned -Raw).Trim() -ne 'Unowned skill') { throw 'Unowned skill overwritten.' }
    if (@(Get-Content $log).Count -ne 4) { throw 'Skills-only update called a client CLI.' }
} finally {
    $env:PATH = $previousPath
    $env:MAYA_MCP_CLIENT_TEST_LOG = $previousLog
    $env:CLAUDE_CONFIG_DIR = $previousClaudeConfig
    $resolvedTest = [IO.Path]::GetFullPath($testRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTest.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid cleanup path' }
    if (Test-Path -LiteralPath $resolvedTest) { Remove-Item -LiteralPath $resolvedTest -Recurse -Force }
}

Write-Host 'MAYA_MCP_CLIENT_CONFIG_TEST_RESULT=passed codex=true claude_code=true' -ForegroundColor Green
