[CmdletBinding()]
param(
    [string]$LauncherPath = '',
    [switch]$SkipCodex,
    [switch]$SkipClaudeCode,
    [switch]$SkipSkills,
    [switch]$SkillsOnly,
    [switch]$ExistingSkillsOnly,
    [string]$SkillSource = '',
    [string]$AgentHome = ([Environment]::GetFolderPath('UserProfile'))
)

$ErrorActionPreference = 'Stop'
if (-not $LauncherPath) {
    $base = if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA 'MayaMCP'
    } else {
        Join-Path ([System.IO.Path]::GetTempPath()) 'MayaMCP'
    }
    $LauncherPath = Join-Path $base 'client\Start-MayaMcpBridge.ps1'
}
$LauncherPath = [System.IO.Path]::GetFullPath($LauncherPath)
if (-not $SkillsOnly -and -not (Test-Path -LiteralPath $LauncherPath -PathType Leaf)) {
    throw "Maya MCP client launcher not found: $LauncherPath"
}

$serverName = 'maya-mcp'
$powerShellPath = Join-Path $PSHOME 'powershell.exe'
$bridgeCommand = @(
    $powerShellPath,
    '-NoLogo',
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    $LauncherPath
)
$configured = @()
$unavailable = @()

if (-not $SkillSource) {
    $SkillSource = Join-Path $PSScriptRoot 'skills\maya-mcp'
    if (-not (Test-Path -LiteralPath $SkillSource)) {
        $SkillSource = Join-Path (Split-Path -Parent $PSScriptRoot) 'skills\maya-mcp'
    }
}

function Install-MayaSkill([string]$Root) {
    if ($SkipSkills) { return }
    $destination = Join-Path $Root 'maya-mcp'
    $target = Join-Path $destination 'SKILL.md'
    $receipt = Join-Path $destination '.maya-mcp-install.json'
    if ($ExistingSkillsOnly -and -not (Test-Path -LiteralPath $receipt -PathType Leaf)) { return }
    $source = Join-Path $SkillSource 'SKILL.md'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Maya skill source missing: $source" }
    # Never follow a redirected skill directory/file or replace an unowned skill.
    foreach ($path in @($destination, $target, $receipt)) {
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Warning "Preserving redirected skill path: $path"; return
        }
    }
    if (Test-Path -LiteralPath $destination) {
        try {
            $previous = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
            if ($previous.owner -ne 'maya-mcp' -or $previous.schema_version -ne 1) { throw 'Unowned skill' }
            if ((Test-Path -LiteralPath $target) -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $previous.sha256) {
                throw 'Locally modified skill'
            }
        } catch {
            Write-Warning "Preserving existing skill at $destination. Move your custom copy before repairing this managed skill."; return
        }
    }
    $digest = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
    [ordered]@{ owner = 'maya-mcp'; schema_version = 1; sha256 = $digest } |
        ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
    Write-Host "Installed Maya skill: $target"
}

$codexSkills = Join-Path $AgentHome '.agents\skills'
$claudeSkills = if ($env:CLAUDE_CONFIG_DIR) { Join-Path $env:CLAUDE_CONFIG_DIR 'skills' } else { Join-Path $AgentHome '.claude\skills' }
if ($SkillsOnly) {
    if (-not $SkipCodex) { Install-MayaSkill $codexSkills }
    if (-not $SkipClaudeCode) { Install-MayaSkill $claudeSkills }
    return
}

function Invoke-McpClientCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$Label,
        [switch]$AllowFailure
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $details = ($output | ForEach-Object { [string]$_ }) -join ' '
        if ($details.Length -gt 500) { $details = $details.Substring(0, 500) }
        throw "$Label failed with exit code $exitCode. $details"
    }
    return $exitCode
}

if (-not $SkipCodex) {
    $codex = Get-Command codex -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($codex) {
        $entryExists = (Invoke-McpClientCommand -Command $codex.Source -Arguments @(
            'mcp', 'get', $serverName, '--json'
        ) -Label 'Codex MCP probe' -AllowFailure) -eq 0
        if ($entryExists) {
            [void](Invoke-McpClientCommand -Command $codex.Source -Arguments @(
                'mcp', 'remove', $serverName
            ) -Label 'Codex MCP replacement')
        }
        [void](Invoke-McpClientCommand -Command $codex.Source -Arguments (@(
            'mcp', 'add', $serverName, '--'
        ) + $bridgeCommand) -Label 'Codex MCP registration')
        $configured += 'Codex'
        Install-MayaSkill $codexSkills
    } else {
        $unavailable += 'Codex'
    }
}

if (-not $SkipClaudeCode) {
    $claude = Get-Command claude -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($claude) {
        [void](Invoke-McpClientCommand -Command $claude.Source -Arguments @(
            'mcp', 'remove', '--scope', 'user', $serverName
        ) -Label 'Claude Code MCP replacement' -AllowFailure)
        [void](Invoke-McpClientCommand -Command $claude.Source -Arguments (@(
            'mcp', 'add', '--transport', 'stdio', '--scope', 'user', $serverName, '--'
        ) + $bridgeCommand) -Label 'Claude Code MCP registration')
        $configured += 'Claude Code'
        Install-MayaSkill $claudeSkills
    } else {
        $unavailable += 'Claude Code'
    }
}

if ($configured.Count) {
    Write-Host "Configured Maya MCP for: $($configured -join ', ')." -ForegroundColor Green
}
if ($unavailable.Count) {
    Write-Host "Not installed or not on PATH: $($unavailable -join ', ')."
}
if (-not $configured.Count) {
    Write-Host 'No supported MCP command-line clients were detected. You can configure them later from Maya MCP > Configure AI Clients.'
}
Write-Output "MAYA_MCP_CLIENT_CONFIG_RESULT configured=$($configured.Count) unavailable=$($unavailable.Count)"
