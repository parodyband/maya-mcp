$ErrorActionPreference = 'Stop'

try {
    $base = if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA 'MayaMCP'
    } else {
        Join-Path ([System.IO.Path]::GetTempPath()) 'MayaMCP'
    }
    $discoveryPath = if ($env:MAYA_MCP_DISCOVERY_FILE) {
        $env:MAYA_MCP_DISCOVERY_FILE
    } else {
        Join-Path $base 'current.json'
    }
    if (-not (Test-Path -LiteralPath $discoveryPath -PathType Leaf)) {
        throw 'No running Maya MCP server was found. Open Maya and load maya_mcp first.'
    }
    $discovery = Get-Content -LiteralPath $discoveryPath -Raw | ConvertFrom-Json
    $version = [string]$discovery.pluginVersion
    if ($version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw 'The active Maya MCP discovery file has no valid plug-in version.'
    }

    $registryPath = Join-Path $PSScriptRoot 'bridge-installations.json'
    if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) {
        throw 'The Maya MCP client bridge is not registered. Run the Maya MCP installer again.'
    }
    $registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
    if ([int]$registry.schema_version -ne 1) {
        throw 'The Maya MCP client bridge registry is not supported.'
    }
    $record = @($registry.installations | Where-Object { [string]$_.version -eq $version }) |
        Select-Object -Last 1
    if (-not $record) {
        throw "The client bridge for Maya MCP $version is not installed. Run the latest installer again."
    }
    $bridge = [System.IO.Path]::GetFullPath([string]$record.path)
    if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) {
        throw "The registered Maya MCP $version client bridge is missing. Run the installer again."
    }

    & $bridge
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine("maya-mcp-launcher: $($_.Exception.Message)")
    exit 1
}
