[CmdletBinding()]
param(
    [string]$Maya2026Archive = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\Autodesk_Maya_2026_3_Update_DEVKIT_Windows.zip'
    ),
    [string]$Maya2027Archive = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\Autodesk_Maya_2027_1_Update_DEVKIT_Windows.zip'
    ),
    [string]$DevkitsDirectory = (Join-Path $env:LOCALAPPDATA 'MayaMCP\devkits')
)

$ErrorActionPreference = 'Stop'

function Test-Devkit([string]$Path) {
    return (
        (Test-Path -LiteralPath (Join-Path $Path 'include\maya\MFnPlugin.h')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'include\maya\MTypes.h')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'lib\OpenMaya.lib')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'lib\OpenMayaUI.lib')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'lib\OpenMayaRender.lib')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'lib\Foundation.lib'))
    )
}

function Install-Devkit(
    [string]$Target,
    [string]$Archive,
    [int]$ExpectedApiVersion
) {
    $destination = Join-Path $DevkitsDirectory "maya$Target\devkitBase"
    if (Test-Devkit $destination) {
        Write-Host "Maya $Target devkit is already ready at $destination" -ForegroundColor Green
        return
    }
    if (-not $Archive -or -not (Test-Path -LiteralPath $Archive)) {
        throw "Maya $Target devkit archive was not found: $Archive"
    }

    $targetRoot = Split-Path -Parent $destination
    $stagingRoot = Join-Path $DevkitsDirectory ".staging-maya$Target-$PID-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
    try {
        Write-Host "Extracting the lean Maya $Target C++ devkit..." -ForegroundColor Cyan
        tar -xf $Archive -C $stagingRoot 'devkitBase/include/maya' 'devkitBase/lib'
        if ($LASTEXITCODE -ne 0) { throw "Could not extract the Maya $Target devkit." }
        $staged = Join-Path $stagingRoot 'devkitBase'
        if (-not (Test-Devkit $staged)) { throw "The Maya $Target archive is missing required C++ SDK files." }
        $apiLine = Select-String -LiteralPath (Join-Path $staged 'include\maya\MTypes.h') -Pattern '^#define MAYA_API_VERSION ([0-9]+)$'
        if (-not $apiLine -or [int]$apiLine.Matches[0].Groups[1].Value -ne $ExpectedApiVersion) {
            throw "The Maya $Target archive does not contain API $ExpectedApiVersion."
        }
        if (Test-Path -LiteralPath $targetRoot) { Remove-Item -LiteralPath $targetRoot -Recurse -Force }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetRoot) | Out-Null
        Move-Item -LiteralPath $stagingRoot -Destination $targetRoot
        Write-Host "Installed Maya $Target devkit at $destination" -ForegroundColor Green
    } finally {
        if (Test-Path -LiteralPath $stagingRoot) { Remove-Item -LiteralPath $stagingRoot -Recurse -Force }
    }
}

New-Item -ItemType Directory -Force -Path $DevkitsDirectory | Out-Null
Install-Devkit -Target '2026.3' -Archive $Maya2026Archive -ExpectedApiVersion 20260300
if (-not (Test-Devkit (Join-Path $DevkitsDirectory 'maya2027\devkitBase'))) {
    Install-Devkit -Target '2027' -Archive $Maya2027Archive -ExpectedApiVersion 20270100
} else {
    Write-Host "Maya 2027 devkit is already ready at $(Join-Path $DevkitsDirectory 'maya2027\devkitBase')" -ForegroundColor Green
}
