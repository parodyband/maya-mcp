[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [ValidateSet('2026.3', '2027', 'All')]
    [string]$MayaVersion = 'All'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')
$targets = if ($MayaVersion -eq 'All') { @('2026.3', '2027') } else { @($MayaVersion) }
Push-Location $repoRoot
try {
    foreach ($target in $targets) {
        $configurePreset = Get-MayaMcpPresetName -MayaVersion $target
        $buildPreset = if ($target -eq '2026.3') { 'maya2026-3' } else { 'maya2027' }
        $buildPreset += if ($Configuration -eq 'Debug') { '-debug' } else { '-release' }
        Write-Host "Building Maya MCP for Maya $target ($Configuration)..." -ForegroundColor Cyan
        cmake --preset $configurePreset
        if ($LASTEXITCODE -ne 0) { throw "Maya $target CMake configuration failed." }
        cmake --build --preset $buildPreset
        if ($LASTEXITCODE -ne 0) { throw "Maya $target plug-in build failed." }
    }
}
finally { Pop-Location }
