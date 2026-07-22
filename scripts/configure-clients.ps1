[CmdletBinding()]
param(
    [string]$LauncherPath = '',
    [switch]$SkipCodex,
    [switch]$SkipClaudeCode
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
if (-not (Test-Path -LiteralPath $LauncherPath -PathType Leaf)) {
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
