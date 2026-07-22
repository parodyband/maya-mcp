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

if (-not $SkipCodex) {
    $codex = Get-Command codex -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($codex) {
        & $codex.Source mcp get $serverName --json *> $null
        if ($LASTEXITCODE -eq 0) {
            & $codex.Source mcp remove $serverName *> $null
            if ($LASTEXITCODE -ne 0) { throw 'Could not replace the existing Codex Maya MCP entry.' }
        }
        & $codex.Source mcp add $serverName -- @bridgeCommand
        if ($LASTEXITCODE -ne 0) { throw 'Could not add Maya MCP to Codex.' }
        $configured += 'Codex'
    } else {
        $unavailable += 'Codex'
    }
}

if (-not $SkipClaudeCode) {
    $claude = Get-Command claude -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($claude) {
        & $claude.Source mcp remove --scope user $serverName *> $null
        & $claude.Source mcp add --transport stdio --scope user $serverName -- @bridgeCommand
        if ($LASTEXITCODE -ne 0) { throw 'Could not add Maya MCP to Claude Code.' }
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
