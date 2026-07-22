[CmdletBinding()]
param([switch]$MakePublic)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI is not authenticated.' }
    if (git status --porcelain) { throw 'Commit or stash source changes before publishing a release.' }
    $branch = git branch --show-current
    $remoteBranch = git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null
    if (-not $remoteBranch) { throw "Branch $branch does not track a remote branch." }
    if ((git rev-parse HEAD) -ne (git rev-parse $remoteBranch)) { throw 'Push the current commit before publishing.' }

    & (Join-Path $PSScriptRoot 'package-release.ps1')
    $projectText = Get-Content -LiteralPath (Join-Path $repoRoot 'CMakeLists.txt') -Raw
    if ($projectText -notmatch 'project\(maya_mcp VERSION ([0-9]+\.[0-9]+\.[0-9]+)') { throw 'Could not read release version.' }
    $version = $Matches[1]
    $tag = "v$version"
    $dist = Join-Path $repoRoot "dist\$tag"

    if ($MakePublic) {
        gh repo edit parodyband/maya-mcp --visibility public --accept-visibility-change-consequences
        if ($LASTEXITCODE -ne 0) { throw 'Could not make the repository public.' }
    }
    $head = git rev-parse HEAD
    $localTagCommit = git rev-list -n 1 $tag 2>$null
    if ($localTagCommit) {
        if ($localTagCommit -ne $head) { throw "$tag already points at a different commit." }
    } else {
        git tag -a $tag -m "Maya MCP $version" $head
        if ($LASTEXITCODE -ne 0) { throw "Could not create $tag." }
    }
    git ls-remote --exit-code --tags origin "refs/tags/$tag" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        git push origin $tag
        if ($LASTEXITCODE -ne 0) { throw "Could not push $tag over the authenticated Git remote." }
    }
    $assets = @(
        Get-ChildItem -LiteralPath $dist -Filter '*.zip' -File | ForEach-Object FullName
        Join-Path $dist 'release-manifest.json'
    )
    gh release create $tag @assets --repo parodyband/maya-mcp --verify-tag --title "Maya MCP $version" --generate-notes
    if ($LASTEXITCODE -ne 0) { throw "Could not publish $tag." }
    gh release view $tag --repo parodyband/maya-mcp --json url,tagName,name,isDraft,isPrerelease
} finally {
    Pop-Location
}
