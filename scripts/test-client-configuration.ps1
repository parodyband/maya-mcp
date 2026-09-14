$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "maya-mcp-client-config-test-$PID-$([guid]::NewGuid().ToString('N'))"
$shimRoot = Join-Path $testRoot 'bin'
$launcher = Join-Path $testRoot 'Start-MayaMcpBridge.ps1'
$log = Join-Path $testRoot 'commands.log'
$previousPath = $env:PATH
$previousLog = $env:MAYA_MCP_CLIENT_TEST_LOG
$previousClaudeConfig = $env:CLAUDE_CONFIG_DIR
$previousSkillSync = $env:MAYA_MCP_DISABLE_SKILL_SYNC
try {
    Remove-Item Env:MAYA_MCP_DISABLE_SKILL_SYNC -ErrorAction SilentlyContinue
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
        $metadata = Join-Path (Split-Path -Parent $skill) 'agents\openai.yaml'
        $sourceMetadata = Join-Path $repoRoot 'skills\maya-mcp\agents\openai.yaml'
        if ((Get-FileHash -LiteralPath $metadata).Hash -ne (Get-FileHash -LiteralPath $sourceMetadata).Hash) {
            throw 'Operational skill metadata was not installed exactly.'
        }
    }
    $animationSource = Join-Path $repoRoot 'skills\maya-animation-principles'
    $codexAnimation = Join-Path $testRoot '.agents\skills\maya-animation-principles'
    $claudeAnimation = Join-Path $env:CLAUDE_CONFIG_DIR 'skills\maya-animation-principles'
    foreach ($entry in Get-ChildItem -LiteralPath $animationSource -File -Recurse) {
        $relative = $entry.FullName.Substring($animationSource.Length + 1)
        foreach ($destination in @($codexAnimation, $claudeAnimation)) {
            if ((Get-FileHash -LiteralPath (Join-Path $destination $relative)).Hash -ne (Get-FileHash -LiteralPath $entry.FullName).Hash) {
                throw "Animation skill file not installed exactly: $relative"
            }
        }
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
    # Reference edits receive the same protection as an edited entrypoint.
    $customReference = Join-Path $codexAnimation 'references\body-mechanics.md'
    Add-Content -LiteralPath $customReference -Value 'Personal timing notes'
    $missingReference = Join-Path $claudeAnimation 'references\acting-and-review.md'
    Remove-Item -LiteralPath $missingReference
    & $configure -SkillsOnly -ExistingSkillsOnly -AgentHome $testRoot
    if ((Get-Content -LiteralPath $customReference -Raw) -notmatch 'Personal timing notes') { throw 'Custom reference was overwritten.' }
    if (-not (Test-Path -LiteralPath $missingReference)) { throw 'Managed reference was not repaired.' }

    # Migrate a legacy single-file receipt and add companions to enrolled clients.
    $legacyHome = Join-Path $testRoot 'legacy-user'
    $legacySkill = Join-Path $legacyHome '.agents\skills\maya-mcp'
    New-Item -ItemType Directory -Path $legacySkill -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $legacySkill 'SKILL.md')
    @{ owner='maya-mcp'; schema_version=1; sha256=(Get-FileHash -LiteralPath $source).Hash } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $legacySkill '.maya-mcp-install.json') -Encoding UTF8
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $legacyHome
    if ((Get-Content -LiteralPath (Join-Path $legacySkill '.maya-mcp-install.json') -Raw | ConvertFrom-Json).schema_version -ne 2) {
        throw 'Legacy skill receipt was not migrated.'
    }
    $legacyAnimation = Join-Path $legacyHome '.agents\skills\maya-animation-principles'
    if (-not (Test-Path -LiteralPath (Join-Path $legacyAnimation 'references\body-mechanics.md'))) {
        throw 'Bundle upgrade omitted the new animation skill.'
    }
    $personal = Join-Path $legacyAnimation 'personal.md'
    Set-Content -LiteralPath $personal -Value 'Keep this extra file'
    $obsoleteSource = Join-Path $package 'skills\maya-animation-principles\references\obsolete.md'
    Set-Content -LiteralPath $obsoleteSource -Value 'Old bundled reference'
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $legacyHome
    Remove-Item -LiteralPath $obsoleteSource
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $legacyHome
    if (Test-Path -LiteralPath (Join-Path $legacyAnimation 'references\obsolete.md')) { throw 'Obsolete managed reference remains.' }
    if ((Get-Content -LiteralPath $personal -Raw).Trim() -ne 'Keep this extra file') { throw 'Unrelated user file was changed.' }

    # A new bundled file must not overwrite an untracked file with the same name.
    $collision = Join-Path $legacyAnimation 'references\new.md'
    Set-Content -LiteralPath $collision -Value 'User-owned reference'
    Set-Content -LiteralPath (Join-Path $package 'skills\maya-animation-principles\references\new.md') -Value 'Bundled reference'
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $legacyHome
    if ((Get-Content -LiteralPath $collision -Raw).Trim() -ne 'User-owned reference') { throw 'Unmanaged file collision was overwritten.' }

    # Receipt-relative paths cannot escape the skill directory.
    $receiptPath = Join-Path $legacyAnimation '.maya-mcp-install.json'
    $receiptData = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $receiptData.files | Add-Member -NotePropertyName '../escape.md' -NotePropertyValue ('A' * 64)
    $receiptData | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    $beforeEntry = (Get-FileHash -LiteralPath (Join-Path $legacyAnimation 'SKILL.md')).Hash
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $legacyHome
    if ((Get-FileHash -LiteralPath (Join-Path $legacyAnimation 'SKILL.md')).Hash -ne $beforeEntry) { throw 'Invalid receipt caused writes.' }
    # Registered customers who predate skills must receive both companions.
    $registeredHome = Join-Path $testRoot 'registered-no-skills'
    New-Item -ItemType Directory -Path (Join-Path $registeredHome '.codex') -Force | Out-Null
    $registeredConfig = Join-Path $registeredHome '.codex\config.toml'
    Set-Content -LiteralPath $registeredConfig -Value "[mcp_servers.maya]`ncommand = 'powershell.exe'`nargs = [`n  'Start-MayaMcpBridge.ps1'`n]" -Encoding UTF8
    $configDigest = (Get-FileHash -LiteralPath $registeredConfig).Hash
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipClaudeCode -AgentHome $registeredHome
    foreach ($name in @('maya-mcp','maya-animation-principles')) {
        if (-not (Test-Path -LiteralPath (Join-Path $registeredHome ".agents\skills\$name\SKILL.md"))) { throw "Existing Codex customer missing $name" }
    }
    if ((Get-FileHash -LiteralPath $registeredConfig).Hash -ne $configDigest) { throw 'Skill migration changed MCP registration' }
    $currentClaudeConfig = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = Join-Path $registeredHome 'claude-config'
    New-Item -ItemType Directory -Path $env:CLAUDE_CONFIG_DIR -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $env:CLAUDE_CONFIG_DIR '.claude.json') -Value '{"mcpServers":{"maya-mcp":{"command":"powershell.exe"}}}' -Encoding UTF8
    & $configure -SkillsOnly -ExistingSkillsOnly -SkipCodex -AgentHome $registeredHome
    foreach ($name in @('maya-mcp','maya-animation-principles')) {
        if (-not (Test-Path -LiteralPath (Join-Path $env:CLAUDE_CONFIG_DIR "skills\$name\SKILL.md"))) { throw "Existing Claude customer missing $name" }
    }
    $env:CLAUDE_CONFIG_DIR = $currentClaudeConfig
    $env:MAYA_MCP_DISABLE_SKILL_SYNC = '1'
    $disabledHome = Join-Path $testRoot 'disabled-user'
    & $configure -SkillsOnly -SkipClaudeCode -AgentHome $disabledHome
    if (Test-Path -LiteralPath $disabledHome) { throw 'Explicit skill-sync opt-out ignored' }
    Remove-Item Env:MAYA_MCP_DISABLE_SKILL_SYNC
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
    $env:MAYA_MCP_DISABLE_SKILL_SYNC = $previousSkillSync
    $resolvedTest = [IO.Path]::GetFullPath($testRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTest.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid cleanup path' }
    if (Test-Path -LiteralPath $resolvedTest) { Remove-Item -LiteralPath $resolvedTest -Recurse -Force }
}

Write-Host 'MAYA_MCP_CLIENT_CONFIG_TEST_RESULT=passed codex=true claude_code=true' -ForegroundColor Green
