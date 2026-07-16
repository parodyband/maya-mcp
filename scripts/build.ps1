[CmdletBinding()]
param([ValidateSet('Debug', 'Release')][string]$Configuration = 'Release')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$preset = if ($Configuration -eq 'Debug') { 'maya2027-debug' } else { 'maya2027-release' }
Push-Location $repoRoot
try {
    cmake --preset maya2027-vs2022
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
    cmake --build --preset $preset
    if ($LASTEXITCODE -ne 0) { throw 'Maya plug-in build failed.' }
}
finally { Pop-Location }

