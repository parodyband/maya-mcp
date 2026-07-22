[CmdletBinding()]
param([string]$DistributionDirectory = '')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DistributionDirectory) {
    $projectText = Get-Content -LiteralPath (Join-Path $repoRoot 'CMakeLists.txt') -Raw
    if ($projectText -notmatch 'project\(maya_mcp VERSION ([0-9]+\.[0-9]+\.[0-9]+)') {
        throw 'Could not read the Maya MCP version from CMakeLists.txt.'
    }
    $DistributionDirectory = Join-Path $repoRoot "dist\v$($Matches[1])"
}

$archives = @(Get-ChildItem -LiteralPath $DistributionDirectory -Filter '*.zip' -File)
if ($archives.Count -ne 2) { throw "Expected two release ZIPs in $DistributionDirectory." }

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "maya-mcp-installer-test-$PID-$([guid]::NewGuid().ToString('N'))"
$previousNoPause = $env:MAYA_MCP_INSTALLER_NO_PAUSE
$previousArchive = $env:MAYA_MCP_INSTALLER_ARCHIVE
$previousSha256 = $env:MAYA_MCP_INSTALLER_SHA256
$previousModules = $env:MAYA_MCP_INSTALLER_MODULES_DIRECTORY
$previousMayaLocation = $env:MAYA_MCP_INSTALLER_MAYA_LOCATION
$previousAllowRunning = $env:MAYA_MCP_INSTALLER_ALLOW_RUNNING
$previousSkipClients = $env:MAYA_MCP_INSTALLER_SKIP_CLIENT_CONFIGURATION
try {
    $env:MAYA_MCP_INSTALLER_NO_PAUSE = '1'
    $env:MAYA_MCP_INSTALLER_SKIP_CLIENT_CONFIGURATION = '1'
    foreach ($archive in $archives) {
        $caseRoot = Join-Path $testRoot $archive.BaseName
        $extracted = Join-Path $caseRoot 'release'
        $modules = Join-Path $caseRoot 'modules'
        $missingMaya = Join-Path $caseRoot 'maya-not-installed'
        New-Item -ItemType Directory -Force -Path $extracted | Out-Null
        Expand-Archive -LiteralPath $archive.FullName -DestinationPath $extracted

        $manifest = Get-Content -LiteralPath (Join-Path $extracted 'package-manifest.json') -Raw | ConvertFrom-Json
        $installer = Join-Path $extracted 'Install-MayaMcp.cmd'
        if (-not (Test-Path -LiteralPath $installer)) { throw "$($archive.Name) has no double-click installer." }
        if (Get-ChildItem -LiteralPath $extracted -Directory -Filter '__pycache__' -Recurse) {
            throw "$($archive.Name) contains a Python cache directory."
        }
        if (Get-ChildItem -LiteralPath $extracted -File -Recurse |
            Where-Object Extension -In @('.pyc', '.pyo')) {
            throw "$($archive.Name) contains generated Python bytecode."
        }

        $command = "`"$installer`" -ModulesDirectory `"$modules`" -MayaLocation `"$missingMaya`" -AllowMayaRunning"
        & $env:ComSpec /d /c $command
        if ($LASTEXITCODE -ne 0) { throw "Installer failed for $($archive.Name)." }

        $folder = "maya-mcp-$($manifest.version)-maya$($manifest.maya_target)"
        $plugin = Join-Path $modules "$folder\plug-ins\maya_mcp.mll"
        $descriptor = Join-Path $modules "maya-mcp-$($manifest.maya_major_version).mod"
        if (-not (Test-Path -LiteralPath $plugin)) { throw "Installer did not copy $plugin." }
        if (-not (Test-Path -LiteralPath $descriptor)) { throw "Installer did not copy $descriptor." }
        if ((Get-Content -LiteralPath $descriptor -Raw) -notmatch [regex]::Escape("./$folder")) {
            throw "Installed descriptor does not select $folder."
        }

        $directRoot = Join-Path $caseRoot 'direct-from-zip'
        $directModules = Join-Path $caseRoot 'direct-modules'
        New-Item -ItemType Directory -Force -Path $directRoot | Out-Null
        $directInstaller = Join-Path $directRoot 'Install-MayaMcp.cmd'
        Copy-Item -LiteralPath $installer -Destination $directInstaller
        $env:MAYA_MCP_INSTALLER_ARCHIVE = $archive.FullName
        $env:MAYA_MCP_INSTALLER_SHA256 = (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash
        $env:MAYA_MCP_INSTALLER_MODULES_DIRECTORY = $directModules
        $env:MAYA_MCP_INSTALLER_MAYA_LOCATION = $missingMaya
        $env:MAYA_MCP_INSTALLER_ALLOW_RUNNING = '1'
        & $env:ComSpec /d /c "`"$directInstaller`""
        if ($LASTEXITCODE -ne 0) { throw "Direct-from-ZIP bootstrap failed for $($archive.Name)." }

        $directPlugin = Join-Path $directModules "$folder\plug-ins\maya_mcp.mll"
        $directDescriptor = Join-Path $directModules "maya-mcp-$($manifest.maya_major_version).mod"
        if (-not (Test-Path -LiteralPath $directPlugin)) { throw "Bootstrap installer did not copy $directPlugin." }
        if (-not (Test-Path -LiteralPath $directDescriptor)) { throw "Bootstrap installer did not copy $directDescriptor." }
    }
} finally {
    $env:MAYA_MCP_INSTALLER_NO_PAUSE = $previousNoPause
    $env:MAYA_MCP_INSTALLER_ARCHIVE = $previousArchive
    $env:MAYA_MCP_INSTALLER_SHA256 = $previousSha256
    $env:MAYA_MCP_INSTALLER_MODULES_DIRECTORY = $previousModules
    $env:MAYA_MCP_INSTALLER_MAYA_LOCATION = $previousMayaLocation
    $env:MAYA_MCP_INSTALLER_ALLOW_RUNNING = $previousAllowRunning
    $env:MAYA_MCP_INSTALLER_SKIP_CLIENT_CONFIGURATION = $previousSkipClients
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

Write-Host 'MAYA_MCP_RELEASE_INSTALLER_TEST_RESULT=passed extracted=true direct_from_zip=true' -ForegroundColor Green
