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
try {
    $env:MAYA_MCP_INSTALLER_NO_PAUSE = '1'
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
    }
} finally {
    $env:MAYA_MCP_INSTALLER_NO_PAUSE = $previousNoPause
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

Write-Host 'MAYA_MCP_RELEASE_INSTALLER_TEST_RESULT=passed' -ForegroundColor Green
