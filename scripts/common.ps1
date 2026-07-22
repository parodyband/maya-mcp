function Get-MayaMcpPresetName {
    param(
        [ValidateSet('2026.3', '2027')]
        [string]$MayaVersion
    )
    if ($MayaVersion -eq '2026.3') { return 'maya2026-3-vs2022' }
    return 'maya2027-vs2022'
}

function Get-MayaMcpBuildDirectory {
    param(
        [ValidateSet('2026.3', '2027')]
        [string]$MayaVersion
    )
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is required.' }
    return Join-Path $env:LOCALAPPDATA "MayaMCP\build\maya$MayaVersion-mcp-vs2022"
}

function Get-MayaMcpPackageDirectory {
    param(
        [ValidateSet('2026.3', '2027')]
        [string]$MayaVersion
    )
    return Join-Path (Get-MayaMcpBuildDirectory -MayaVersion $MayaVersion) 'package'
}

function Get-MayaMcpMajorVersion {
    param(
        [ValidateSet('2026.3', '2027')]
        [string]$MayaVersion
    )
    if ($MayaVersion -eq '2026.3') { return '2026' }
    return '2027'
}
