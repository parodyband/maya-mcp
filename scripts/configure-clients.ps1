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
# Python hosts can inherit a PowerShell 7 PSModulePath while launching Windows
# PowerShell 5.1. Resolve its own utility module instead of an incompatible one.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
}
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
    $SkillSource = Join-Path $PSScriptRoot 'skills'
    if (-not (Test-Path -LiteralPath $SkillSource)) {
        $SkillSource = Join-Path (Split-Path -Parent $PSScriptRoot) 'skills'
    }
}

function Assert-SkillNotRedirected([string]$Path) {
    $candidate = [IO.Path]::GetFullPath($Path)
    while ($candidate) {
        if ((Test-Path -LiteralPath $candidate) -and ((Get-Item -LiteralPath $candidate -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Redirected skill path: $candidate"
        }
        $candidate = [IO.Path]::GetDirectoryName($candidate)
    }
}

function Get-SkillChild([string]$Directory, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.{1,2}([\\/]|$)' -or $Relative -match ':') {
        throw 'Invalid managed skill path'
    }
    $prefix = [IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
    $resolved = [IO.Path]::GetFullPath((Join-Path $Directory $Relative))
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Skill path escaped its directory' }
    Assert-SkillNotRedirected $resolved
    return $resolved
}

function Read-SkillReceipt([string]$Directory) {
    $receipt = Get-SkillChild $Directory '.maya-mcp-install.json'
    $previous = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($previous.owner -ne 'maya-mcp') { throw 'Unowned skill' }
    $files = @{}
    if ($previous.schema_version -eq 1) {
        $files['SKILL.md'] = [string]$previous.sha256
    } elseif ($previous.schema_version -eq 2) {
        foreach ($property in $previous.files.PSObject.Properties) {
            [void](Get-SkillChild $Directory $property.Name)
            $files[$property.Name] = [string]$property.Value
        }
    } else { throw 'Unsupported skill receipt' }
    if (-not $files.ContainsKey('SKILL.md')) { throw 'Incomplete skill receipt' }
    foreach ($digest in $files.Values) {
        if ($digest -notmatch '^[0-9A-Fa-f]{64}$') { throw 'Invalid skill digest' }
    }
    return $files
}

function Install-MayaSkillFolder([string]$Root, [IO.DirectoryInfo]$SourceFolder) {
    $destination = Join-Path $Root $SourceFolder.Name
    $previousFiles = @{}
    $sourceFiles = @{}
    Assert-SkillNotRedirected $SourceFolder.FullName
    foreach ($entry in Get-ChildItem -LiteralPath $SourceFolder.FullName -Recurse -Force) {
        Assert-SkillNotRedirected $entry.FullName
        if ($entry.PSIsContainer) { continue }
        $relative = $entry.FullName.Substring($SourceFolder.FullName.Length + 1).Replace('\', '/')
        if ($relative -eq '.maya-mcp-install.json') { throw 'Source must not include an installation receipt' }
        $sourceFiles[$relative] = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash
    }
    if (-not $sourceFiles.ContainsKey('SKILL.md')) { throw 'Skill source is missing SKILL.md' }
    try {
        Assert-SkillNotRedirected $destination
        foreach ($relative in $sourceFiles.Keys) { [void](Get-SkillChild $destination $relative) }
        if (Test-Path -LiteralPath $destination) {
            $previousFiles = Read-SkillReceipt $destination
            foreach ($relative in $previousFiles.Keys) {
                $target = Get-SkillChild $destination $relative
                if ((Test-Path -LiteralPath $target) -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $previousFiles[$relative]) {
                    throw "Locally modified skill file: $relative"
                }
            }
            foreach ($relative in $sourceFiles.Keys) {
                if ((Test-Path -LiteralPath (Get-SkillChild $destination $relative)) -and -not $previousFiles.ContainsKey($relative)) {
                    throw "Unmanaged file conflicts with bundled skill: $relative"
                }
            }
        }
    } catch {
        Write-Warning "Preserving skill at $destination. $($_.Exception.Message)"; return
    }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($relative in $sourceFiles.Keys) {
        $target = Get-SkillChild $destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceFolder.FullName $relative) -Destination $target -Force
    }
    foreach ($relative in $previousFiles.Keys) {
        if (-not $sourceFiles.ContainsKey($relative)) {
            $target = Get-SkillChild $destination $relative
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
        }
    }
    [ordered]@{ owner = 'maya-mcp'; schema_version = 2; files = $sourceFiles } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Get-SkillChild $destination '.maya-mcp-install.json') -Encoding UTF8
    Write-Host "Installed Maya skill: $(Join-Path $destination 'SKILL.md')"
}

function Install-MayaSkill([string]$Root) {
    if ($SkipSkills -or $env:MAYA_MCP_DISABLE_SKILL_SYNC -match '^(1|true|yes|on)$') { return }
    if (Test-Path -LiteralPath (Join-Path $SkillSource 'SKILL.md') -PathType Leaf) {
        # Retain compatibility with callers supplying a single skill directory.
        $folders = @(Get-Item -LiteralPath $SkillSource)
    } else {
        $folders = @(Get-ChildItem -LiteralPath $SkillSource -Directory | Where-Object {
            Test-Path -LiteralPath (Join-Path $_.FullName 'SKILL.md') -PathType Leaf
        })
    }
    if (-not $folders.Count) { throw "No bundled Maya skills found at $SkillSource" }
    if ($ExistingSkillsOnly) {
        # Receipts and existing MCP registrations cover customers who predate
        # companion skills. No registration or unrelated client config is changed.
        $enrolled = $false
        foreach ($name in @('maya-mcp') + @($folders | ForEach-Object Name)) {
            try { [void](Read-SkillReceipt (Join-Path $Root $name)); $enrolled = $true; break } catch { }
        }
        if (-not $enrolled) { $enrolled = Test-MayaClientRegistration $Root }
        if (-not $enrolled) { return }
    }
    foreach ($folder in $folders) {
        if ($folder.Name -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "Invalid bundled skill name: $($folder.Name)" }
        Install-MayaSkillFolder $Root $folder
    }
}

$codexSkills = Join-Path $AgentHome '.agents\skills'
$claudeSkills = if ($env:CLAUDE_CONFIG_DIR) { Join-Path $env:CLAUDE_CONFIG_DIR 'skills' } else { Join-Path $AgentHome '.claude\skills' }
$codexConfigRoot = if ($env:CODEX_HOME -and -not $PSBoundParameters.ContainsKey('AgentHome')) { $env:CODEX_HOME } else { Join-Path $AgentHome '.codex' }

function Test-MayaClientRegistration([string]$Root) {
    $bridgePattern = '(?i)(Start-MayaMcpBridge\.ps1|maya-mcp-bridge(?:\.exe)?)'
    if ($Root -eq $codexSkills) {
        $config = Join-Path $codexConfigRoot 'config.toml'
        if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $false }
        $text = Get-Content -LiteralPath $config -Raw
        # Only top-level MCP server tables, never a mention inside other config.
        foreach ($section in [regex]::Matches($text, '(?ms)^[ \t]*\[mcp_servers\.([^\]\r\n]+)\][ \t]*(?:\r?\n|\z)(.*?)(?=^[ \t]*\[|\z)')) {
            $name = $section.Groups[1].Value.Trim('"', "'")
            if ($name -eq 'maya-mcp' -or $section.Groups[2].Value -match $bridgePattern) { return $true }
        }
        return $false
    }
    if ($Root -eq $claudeSkills) {
        $configs = @((Join-Path $AgentHome '.claude.json'))
        if ($env:CLAUDE_CONFIG_DIR) { $configs += Join-Path $env:CLAUDE_CONFIG_DIR '.claude.json' }
        foreach ($config in $configs) {
            if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { continue }
            try {
                $settings = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
                foreach ($server in $settings.mcpServers.PSObject.Properties) {
                    if ($server.Name -eq 'maya-mcp' -or ($server.Value | ConvertTo-Json -Depth 12 -Compress) -match $bridgePattern) { return $true }
                }
            } catch { Write-Warning "Could not inspect existing Maya MCP registration at $config" }
        }
    }
    return $false
}

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
