param(
  [switch]$NoPause
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$SourceConfig = Join-Path $DataDir "workbench-update-source.json"
$SourceExample = Join-Path $DataDir "workbench-update-source.example.json"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn($Message) {
  Write-Host "!! $Message" -ForegroundColor Yellow
}

function Wait-ForExitKey {
  Write-Host ""
  Write-Host "Press any key to close this window..."
  try {
    [void]$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
  } catch {
    [void](Read-Host "Press Enter to close this window")
  }
}

function Command-Exists($Name) {
  $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Assert-UnderRoot($Path) {
  $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
  $pathFull = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
  if ($pathFull -ne $rootFull -and !$pathFull.StartsWith($rootFull + '\')) {
    throw "Refusing to update path outside this workbench folder: $pathFull"
  }
}

function Read-UpdateSource {
  if (!(Test-Path -LiteralPath $SourceConfig)) {
    if (!(Test-Path -LiteralPath $DataDir)) {
      New-Item -ItemType Directory -Path $DataDir | Out-Null
    }
    if (Test-Path -LiteralPath $SourceExample) {
      Copy-Item -LiteralPath $SourceExample -Destination $SourceConfig -Force
    } else {
      @{
        repository_url = "https://github.com/874199491/codemao.git"
        branch = "main"
        zip_url = "https://github.com/874199491/codemao/archive/refs/heads/main.zip"
      } | ConvertTo-Json | Set-Content -LiteralPath $SourceConfig -Encoding UTF8
    }
    Write-Warn "Update source config was missing. Created default config: $SourceConfig"
  }

  $config = Get-Content -LiteralPath $SourceConfig -Raw -Encoding UTF8 | ConvertFrom-Json
  $repoUrl = [string]($config.repository_url)
  $branch = [string]($config.branch)
  $zipUrl = [string]($config.zip_url)

  if ([string]::IsNullOrWhiteSpace($branch)) {
    $branch = "main"
  }

  if ([string]::IsNullOrWhiteSpace($zipUrl) -and ![string]::IsNullOrWhiteSpace($repoUrl)) {
    $base = $repoUrl.Trim()
    if ($base.EndsWith(".git")) {
      $base = $base.Substring(0, $base.Length - 4)
    }
    $zipUrl = "$base/archive/refs/heads/$branch.zip"
  }

  if ([string]::IsNullOrWhiteSpace($zipUrl)) {
    throw "No zip_url was found, and repository_url cannot be converted to a download URL. Check: $SourceConfig"
  }

  [PSCustomObject]@{
    RepositoryUrl = $repoUrl
    Branch = $branch
    ZipUrl = $zipUrl
  }
}

function Find-PackageRoot($ExpandedDir) {
  $candidates = @($ExpandedDir) + @(Get-ChildItem -LiteralPath $ExpandedDir -Directory -Recurse -Depth 2)
  foreach ($candidate in $candidates) {
    $candidatePath = if ($candidate -is [string]) { $candidate } else { $candidate.FullName }
    if (
      (Test-Path -LiteralPath (Join-Path $candidatePath "apps")) -and
      (Test-Path -LiteralPath (Join-Path $candidatePath "scripts"))
    ) {
      return $candidatePath
    }
  }
  throw "The downloaded package does not contain a workbench source folder."
}

function Copy-DirectoryClean($Source, $Destination) {
  if (!(Test-Path -LiteralPath $Source)) {
    return
  }

  Assert-UnderRoot $Destination
  if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-FileIfExists($Source, $Destination) {
  if (!(Test-Path -LiteralPath $Source)) {
    return
  }

  Assert-UnderRoot $Destination
  $parent = Split-Path -Parent $Destination
  if (!(Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
  }
  Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Update-FromPackage($PackageRoot) {
  Write-Step "Updating program files while keeping local teacher config and runtime data"

  foreach ($dir in @("apps", "scripts", "skills", "config")) {
    Copy-DirectoryClean (Join-Path $PackageRoot $dir) (Join-Path $Root $dir)
  }

  $packageDocs = Join-Path $PackageRoot "docs"
  if (Test-Path -LiteralPath $packageDocs) {
    $targetDocs = Join-Path $Root "docs"
    if (!(Test-Path -LiteralPath $targetDocs)) {
      New-Item -ItemType Directory -Path $targetDocs | Out-Null
    }
    Get-ChildItem -LiteralPath $packageDocs -File | ForEach-Object {
      Copy-FileIfExists $_.FullName (Join-Path $targetDocs $_.Name)
    }
  }

  Get-ChildItem -LiteralPath $PackageRoot -File | Where-Object {
    $_.Extension -in @(".md", ".ps1", ".bat", ".vbs")
  } | ForEach-Object {
    Copy-FileIfExists $_.FullName (Join-Path $Root $_.Name)
  }

  if (!(Test-Path -LiteralPath $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
  }

  $packageData = Join-Path $PackageRoot "data"
  if (Test-Path -LiteralPath $packageData) {
    Get-ChildItem -LiteralPath $packageData -File | Where-Object {
      $_.Name -notin @("teacher-workbench-config.json", "crm-cookies.json")
    } | ForEach-Object {
      Copy-FileIfExists $_.FullName (Join-Path $DataDir $_.Name)
    }
  }
}

function Update-WithGitIfPossible($source) {
  $gitDir = Join-Path $Root ".git"
  if (!(Test-Path -LiteralPath $gitDir)) {
    return $false
  }
  if (!(Command-Exists "git")) {
    return $false
  }

  Write-Step "Git repository detected. Trying git update first"
  Push-Location $Root
  try {
    if (![string]::IsNullOrWhiteSpace($source.RepositoryUrl)) {
      $remote = ""
      try {
        $remote = git remote get-url origin 2>$null
      } catch {
        $remote = ""
      }
      if ([string]::IsNullOrWhiteSpace($remote)) {
        git remote add origin $source.RepositoryUrl
      }
    }
    git pull --ff-only origin $source.Branch
    return $true
  } finally {
    Pop-Location
  }
}

try {
  Write-Step "Starting teacher workbench update"
  $source = Read-UpdateSource

  if (Update-WithGitIfPossible $source) {
    Write-Step "Git update completed"
  } else {
    Write-Step "Git is not available here. Downloading GitHub zip package instead"
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("codemao-workbench-update-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "source.zip"
    $expanded = Join-Path $tempRoot "expanded"
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    New-Item -ItemType Directory -Path $expanded | Out-Null
    Invoke-WebRequest -Uri $source.ZipUrl -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $expanded -Force
    $packageRoot = Find-PackageRoot $expanded
    Update-FromPackage $packageRoot
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
    Write-Step "Zip package update completed"
  }

  Write-Host ""
  Write-Host "Update completed. Please restart the teacher workbench." -ForegroundColor Green
  Write-Host "Kept local files: data/teacher-workbench-config.json, CRM cookies, runtime cache, schedules."
} catch {
  Write-Host ""
  Write-Host "Update failed:" -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host ""
  Write-Host "If this is the first update, configure data/workbench-update-source.json first."
  exit 1
} finally {
  if (!$NoPause) {
    Wait-ForExitKey
  }
}
