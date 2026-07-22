[CmdletBinding()]
param(
    [string]$ModulesDirectory = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya\modules'),
    [string]$MayaLocation = '',
    [switch]$AllowMayaRunning,
    [switch]$SkipClientConfiguration
)

$ErrorActionPreference = 'Stop'
$runningMaya = @(Get-Process -Name 'maya' -ErrorAction SilentlyContinue)
if ($runningMaya.Count -and -not $AllowMayaRunning) {
    throw 'Close all Autodesk Maya windows, then double-click Install-MayaMcp.cmd again.'
}

$releaseRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $releaseRoot 'package-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Keep Install-MayaMcp.ps1 beside package-manifest.json and the Maya MCP folder.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$version = [string]$manifest.version
$target = [string]$manifest.maya_target
$major = [string]$manifest.maya_major_version
$folderName = "maya-mcp-$version-maya$target"
$moduleFileName = "maya-mcp-$major.mod"
$sourceFolder = Join-Path $releaseRoot $folderName
$sourceModule = Join-Path $releaseRoot $moduleFileName
if (-not (Test-Path -LiteralPath (Join-Path $sourceFolder 'plug-ins\maya_mcp.mll'))) {
    throw "The Maya $target plug-in package is incomplete."
}
if (-not (Test-Path -LiteralPath $sourceModule)) {
    throw "The Maya $target module descriptor is missing. Extract the entire ZIP, then try again."
}

Write-Host "Installing Maya MCP $version for Maya $target..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $ModulesDirectory | Out-Null
$installedFolder = Join-Path $ModulesDirectory $folderName
if (-not ([System.IO.Path]::GetFullPath($sourceFolder).Equals(
    [System.IO.Path]::GetFullPath($installedFolder),
    [System.StringComparison]::OrdinalIgnoreCase
))) {
    New-Item -ItemType Directory -Force -Path $installedFolder | Out-Null
    Get-ChildItem -LiteralPath $sourceFolder | Copy-Item -Destination $installedFolder -Recurse -Force
}
$installedModule = Join-Path $ModulesDirectory $moduleFileName
if (-not ([System.IO.Path]::GetFullPath($sourceModule).Equals(
    [System.IO.Path]::GetFullPath($installedModule),
    [System.StringComparison]::OrdinalIgnoreCase
))) {
    Copy-Item -LiteralPath $sourceModule -Destination $installedModule -Force
}

$legacyModule = Join-Path $ModulesDirectory 'maya-mcp.mod'
if (Test-Path -LiteralPath $legacyModule) {
    $legacyText = Get-Content -LiteralPath $legacyModule -Raw
    if ($legacyText -match '(?m)^\+\s+maya-mcp\s+') {
        Remove-Item -LiteralPath $legacyModule -Force
    }
}

if (-not $MayaLocation) { $MayaLocation = "C:\Program Files\Autodesk\Maya$major" }
$mayapy = Join-Path $MayaLocation 'bin\mayapy.exe'
$installedPlugin = Join-Path $installedFolder 'plug-ins\maya_mcp.mll'
$installedBridge = Join-Path $installedFolder 'bin\maya-mcp-bridge.exe'
$installedLauncher = Join-Path $installedFolder 'client\Start-MayaMcpBridge.ps1'
$installedConfigurator = Join-Path $installedFolder 'client\Configure-MayaMcpClients.ps1'
foreach ($required in @($installedBridge, $installedLauncher, $installedConfigurator)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "The installed Maya MCP package is missing $required."
    }
}

$clientRoot = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'MayaMCP\client'
} else {
    Join-Path ([System.IO.Path]::GetTempPath()) 'MayaMCP\client'
}
$bridgeVersionRoot = Join-Path $clientRoot "versions\$version"
New-Item -ItemType Directory -Force -Path $bridgeVersionRoot | Out-Null
$bridgeDigest = (Get-FileHash -LiteralPath $installedBridge -Algorithm SHA256).Hash.ToLowerInvariant()
$registeredBridge = Join-Path $bridgeVersionRoot "maya-mcp-bridge-$($bridgeDigest.Substring(0, 16)).exe"
if (-not (Test-Path -LiteralPath $registeredBridge -PathType Leaf)) {
    Copy-Item -LiteralPath $installedBridge -Destination $registeredBridge
}
$stableLauncher = Join-Path $clientRoot 'Start-MayaMcpBridge.ps1'
Copy-Item -LiteralPath $installedLauncher -Destination $stableLauncher -Force
Copy-Item -LiteralPath $installedConfigurator -Destination (Join-Path $clientRoot 'Configure-MayaMcpClients.ps1') -Force

$registryPath = Join-Path $clientRoot 'bridge-installations.json'
$records = @()
if (Test-Path -LiteralPath $registryPath -PathType Leaf) {
    try {
        $existingRegistry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
        if ([int]$existingRegistry.schema_version -eq 1) {
            $records = @($existingRegistry.installations | Where-Object { [string]$_.version -ne $version })
        }
    } catch {
        Write-Warning 'Replacing an unreadable Maya MCP client bridge registry.'
    }
}
$records += [ordered]@{
    version = $version
    path = $registeredBridge
    installed_at = [DateTime]::UtcNow.ToString('o')
}
$registry = [ordered]@{ schema_version = 1; installations = $records }
$temporaryRegistry = Join-Path $clientRoot ".bridge-installations.json.tmp-$PID"
[System.IO.File]::WriteAllText(
    $temporaryRegistry,
    ($registry | ConvertTo-Json -Depth 6) + "`n",
    [System.Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $temporaryRegistry -Destination $registryPath -Force
if (Test-Path -LiteralPath $mayapy) {
    $previousModulePath = $env:MAYA_MODULE_PATH
    try {
        $env:MAYA_MODULE_PATH = if ($previousModulePath) {
            "$ModulesDirectory;$previousModulePath"
        } else {
            $ModulesDirectory
        }
        & $mayapy (Join-Path $releaseRoot 'configure-autoload.py') $installedPlugin ([string]$manifest.maya_api_version)
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure Maya MCP autoload.' }
    } finally {
        $env:MAYA_MODULE_PATH = $previousModulePath
    }
} else {
    Write-Warning "Maya $target was not found at $MayaLocation. The module is installed; enable maya_mcp in Maya's Plug-in Manager once."
}

Write-Host "Maya MCP $version for Maya $target is installed." -ForegroundColor Green
Write-Host "Installed for this Windows user at $installedFolder"
if (-not $SkipClientConfiguration -and $env:MAYA_MCP_INSTALLER_SKIP_CLIENT_CONFIGURATION -ne '1') {
    try {
        & $installedConfigurator -LauncherPath $stableLauncher
    } catch {
        Write-Warning "The Maya plug-in is installed, but automatic AI client configuration failed: $($_.Exception.Message)"
        Write-Warning 'Open Maya MCP > Configure AI Clients after checking that Codex or Claude Code is installed.'
    }
}
$claudeDesktopBundle = Join-Path $releaseRoot 'Install-MayaMcp-Claude-Desktop.mcpb'
if (Test-Path -LiteralPath $claudeDesktopBundle -PathType Leaf) {
    Write-Host 'Claude Desktop: double-click Install-MayaMcp-Claude-Desktop.mcpb in this package, then approve the extension.'
}
Write-Host 'Open Maya. Future compatible releases are available from Maya MCP > Check for Updates.'
