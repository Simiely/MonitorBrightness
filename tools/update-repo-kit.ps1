#Requires -Version 5.1
<#
.SYNOPSIS
    Install or verify the repo-kit copy used by this repository.

.DESCRIPTION
    Copies the kit-owned files into the fixed locations:
        tools/repo-kit/check_repo.py
        tools/repo-kit/generate.py        (all profiles except link-only)
        schema/frontmatter.schema.json
        schema/skills.schema.json         (skills profile, or if already present)
    and writes schema/kit.lock.json with the kit version, spec version, profile
    and SHA256 of every distributed file.

    The spec version is read from ../单项目规范/spec.json (sibling of the kit
    source), so the standard has a single definition point.

    Repo-owned files (schema/taxonomy.json, schema/sources.json) are never
    overwritten.

    -Check verifies the lock hashes only; it does not need the kit source.
    Run it in CI after checkout.

.EXAMPLE
    pwsh tools/update-repo-kit.ps1 -Source ..\repo-kit-v2 -Profile monorepo
.EXAMPLE
    pwsh tools/update-repo-kit.ps1 -Check
#>
param(
    [string]$Source = '',
    [ValidateSet('monorepo', 'skills', 'knowledge', 'link-only')]
    [string]$Profile = '',
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$lockPath = [System.IO.Path]::Combine($repo, 'schema', 'kit.lock.json')

function Get-Sha256([string]$Path) {
    # Hash text content with CRLF normalised to LF, so a Windows checkout and a
    # Linux CI checkout produce the same lock value.
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $ms = New-Object System.IO.MemoryStream
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        if ($bytes[$i] -eq 13 -and ($i + 1) -lt $bytes.Length -and $bytes[$i + 1] -eq 10) { continue }
        $ms.WriteByte($bytes[$i])
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($ms.ToArray())
    $ms.Dispose()
    $sha.Dispose()
    return 'sha256:' + ([BitConverter]::ToString($hash).Replace('-', '').ToLower())
}

function Read-Json([string]$Path) {
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

if ($Check) {
    if (-not (Test-Path -LiteralPath $lockPath)) {
        Write-Output 'KIT LOCK: MISSING (schema/kit.lock.json)'
        exit 1
    }
    $lock = Read-Json $lockPath
    $bad = New-Object System.Collections.ArrayList
    foreach ($p in $lock.files.PSObject.Properties) {
        $full = Join-Path $repo $p.Name
        if (-not (Test-Path -LiteralPath $full)) {
            [void]$bad.Add('MISSING ' + $p.Name)
            continue
        }
        if ((Get-Sha256 $full) -ne $p.Value) {
            [void]$bad.Add('DRIFT ' + $p.Name)
        }
    }
    if ($bad.Count -gt 0) {
        foreach ($b in $bad) { Write-Output $b }
        Write-Output ('KIT LOCK: FAIL (v' + $lock.version + ', spec=' + $lock.spec_version + ', profile=' + $lock.profile + ')')
        exit 1
    }
    Write-Output ('KIT LOCK: PASS (v' + $lock.version + ', spec=' + $lock.spec_version + ', profile=' + $lock.profile + ')')
    exit 0
}

if (-not $Profile) {
    if (Test-Path -LiteralPath $lockPath) {
        $Profile = (Read-Json $lockPath).profile
    }
    else {
        Write-Output 'ERROR: -Profile is required on first install (monorepo|skills|knowledge|link-only).'
        exit 2
    }
}

if (-not $Source) {
    $Source = Join-Path (Split-Path $repo -Parent) 'repo-kit-v2'
}
if (-not (Test-Path -LiteralPath ([System.IO.Path]::Combine($Source, 'scripts', 'check_repo.py')))) {
    Write-Output ('ERROR: kit source not found: ' + $Source)
    exit 2
}

$version = (Read-Json ([System.IO.Path]::Combine($Source, 'schema', 'taxonomy.json'))).version
# Spec source: sibling directory of the kit that contains spec.json.
# Resolved by search to keep this script ASCII-only (Windows PowerShell 5.1
# reads BOM-less .ps1 as ANSI and would corrupt non-ASCII path literals).
$specCandidates = @(
    Get-ChildItem -LiteralPath (Split-Path -Parent $Source) -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'spec.json' } |
        Where-Object { Test-Path -LiteralPath $_ }
)
if ($specCandidates.Count -eq 0) {
    Write-Output 'ERROR: spec source not found (sibling spec.json of kit)'
    exit 2
}
$specVersion = (Read-Json $specCandidates[0]).version
$toolDir = [System.IO.Path]::Combine($repo, 'tools', 'repo-kit')
New-Item -ItemType Directory -Path $toolDir -Force | Out-Null

$files = [ordered]@{}

function Install-File([string]$From, [string]$ToRel) {
    $to = Join-Path $repo $ToRel
    $parent = Split-Path -Parent $to
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $From -Destination $to -Force
    $script:files[$ToRel] = Get-Sha256 $to
    Write-Output ('INSTALLED ' + $ToRel)
}

Install-File ([System.IO.Path]::Combine($Source, 'scripts', 'check_repo.py')) 'tools/repo-kit/check_repo.py'
if ($Profile -ne 'link-only') {
    Install-File ([System.IO.Path]::Combine($Source, 'scripts', 'generate.py')) 'tools/repo-kit/generate.py'
}
Install-File ([System.IO.Path]::Combine($Source, 'schema', 'frontmatter.schema.json')) 'schema/frontmatter.schema.json'

$skillsSrc = [System.IO.Path]::Combine($Source, 'schema', 'skills.schema.json')
$skillsDst = [System.IO.Path]::Combine($repo, 'schema', 'skills.schema.json')
if ((Test-Path -LiteralPath $skillsSrc) -and (($Profile -eq 'skills') -or ($Profile -eq 'link-only') -or (Test-Path -LiteralPath $skillsDst))) {
    Install-File $skillsSrc 'schema/skills.schema.json'
}

# The updater distributes itself; hash it instead of copying onto the running file.
$files['tools/update-repo-kit.ps1'] = Get-Sha256 $PSCommandPath

$lock = [ordered]@{
    kit          = 'repo-kit'
    version      = $version
    spec_version = $specVersion
    profile      = $Profile
    installed    = (Get-Date -Format 'yyyy-MM-dd')
    files        = $files
}
Write-Utf8NoBom $lockPath (($lock | ConvertTo-Json -Depth 5) + "`n")
Write-Output ('KIT LOCK: WRITTEN (v' + $version + ', spec=' + $specVersion + ', profile=' + $Profile + ')')
