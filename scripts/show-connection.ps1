[CmdletBinding()]
param(
    [switch]$RevealToken,
    [switch]$AsJson
)

$ErrorActionPreference = 'Stop'
$discoveryPath = Join-Path $env:LOCALAPPDATA 'MayaMCP\current.json'
if (-not (Test-Path -LiteralPath $discoveryPath)) {
    throw "No running Maya MCP instance was found at $discoveryPath"
}

$connection = Get-Content -LiteralPath $discoveryPath -Raw | ConvertFrom-Json
if (-not $RevealToken) {
    $connection.token = $connection.token.Substring(0, [Math]::Min(8, $connection.token.Length)) + '...'
}

if ($AsJson) {
    $connection | ConvertTo-Json -Depth 5
} else {
    $connection | Format-List
    if (-not $RevealToken) {
        Write-Host 'Use -RevealToken when configuring a trusted MCP client.'
    }
}
