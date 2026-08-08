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

function Command-Exists($Name) {
  $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Read-UpdateSource {
  if (!(Test-Path -LiteralPath $SourceConfig)) {
    if (Test-Path -LiteralPath $SourceExample) {
      Copy-Item -LiteralPath $SourceExample -Destination $SourceConfig -Force
    }
    throw "还没有配置更新源。请先编辑：$SourceConfig，把里面的 GitHub 仓库地址改成你的真实仓库。"
  }
  $config = Get-Content -LiteralPath $SourceConfig -Raw -Encoding UTF8 | ConvertFrom-Json
  $repoUrl = [string]($config.repository_url)
  $branch = [string]($config.branch)
  $zipUrl = [string]($config.zip_url)
  if ([string]::IsNullOrWhiteSpace($branch)) { $branch = "main" }
  if ([string]::IsNullOrWhiteSpace($zipUrl) -and ![string]::IsNullOrWhiteSpace($repoUrl)) {
    $base = $repoUrl.Trim()
    if ($base.EndsWith(".git")) { $base = $base.Substring(0, $base.Length - 4) }
    $zipUrl = "$base/archive/refs/heads/$branch.zip"
  }
  if ([string]::IsNullOrWhiteSpace($zipUrl)) {
    throw "更新源里没有 zip_url，也无法从 repository_url 推导下载地址。请检查：$SourceConfig"
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
    if (
      (Test-Path -LiteralPath (Join-Path $candidate.FullName "apps")) -and
      (Test-Path -LiteralPath (Join-Path $candidate.FullName "scripts"))
    ) {
      return $candidate.FullName
    }
  }
  throw "下载包里没有找到工作台源码目录。"
}

function Copy-DirectoryClean($Source, $Destination) {
  if (!(Test-Path -LiteralPath $Source)) { return }
  if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-FileIfExists($Source, $Destination) {
  if (Test-Path -LiteralPath $Source) {
    $parent = Split-Path -Parent $Destination
    if (!(Test-Path -LiteralPath $parent)) {
      New-Item -ItemType Directory -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
  }
}

function Update-FromPackage($PackageRoot) {
  Write-Step "覆盖程序文件，保留每位老师自己的配置和数据"

  foreach ($dir in @("apps", "scripts", "skills", "config")) {
    Copy-DirectoryClean (Join-Path $PackageRoot $dir) (Join-Path $Root $dir)
  }

  if (Test-Path -LiteralPath (Join-Path $PackageRoot "docs")) {
    if (!(Test-Path -LiteralPath (Join-Path $Root "docs"))) {
      New-Item -ItemType Directory -Path (Join-Path $Root "docs") | Out-Null
    }
    Get-ChildItem -LiteralPath (Join-Path $PackageRoot "docs") -File | ForEach-Object {
      if ($_.Name -ne "工作台持续改进记录.md") {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Root "docs" $_.Name) -Force
      }
    }
  }

  foreach ($file in @(
    "README.md",
    "install-runtime.ps1",
    "install-runtime.bat",
    "一键安装运行环境.bat",
    "launch-workbench.vbs",
    "启动教师工作台.vbs",
    "启动教师工作台.bat",
    "update-workbench.ps1",
    "update-workbench.bat",
    "一键更新工作台.bat"
  )) {
    Copy-FileIfExists (Join-Path $PackageRoot $file) (Join-Path $Root $file)
  }

  if (!(Test-Path -LiteralPath $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
  }
  foreach ($file in @(
    "crm-cookies.example.json",
    "fetch-new-class-student-list.mjs",
    "new-class-group-send-cancel-config.json",
    "workbench-update-source.example.json"
  )) {
    Copy-FileIfExists (Join-Path $PackageRoot "data\$file") (Join-Path $Root "data\$file")
  }
}

function Update-WithGitIfPossible($source) {
  $gitDir = Join-Path $Root ".git"
  if (!(Test-Path -LiteralPath $gitDir)) { return $false }
  if (!(Command-Exists "git")) { return $false }

  Write-Step "检测到 Git 仓库，尝试使用 Git 更新"
  Push-Location $Root
  try {
    if (![string]::IsNullOrWhiteSpace($source.RepositoryUrl)) {
      $remote = ""
      try { $remote = git remote get-url origin 2>$null } catch {}
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
  Write-Step "开始更新教师工作台"
  $source = Read-UpdateSource

  if (Update-WithGitIfPossible $source) {
    Write-Step "Git 更新完成"
  } else {
    Write-Step "未使用 Git，改为下载 GitHub 压缩包更新"
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
    Write-Step "压缩包更新完成"
  }

  Write-Host ""
  Write-Host "更新完成。请重新启动教师工作台。" -ForegroundColor Green
  Write-Host "已保留：data/teacher-workbench-config.json、CRM cookies、运行缓存、定时任务。"
} catch {
  Write-Host ""
  Write-Host "更新失败：" -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host ""
  Write-Host "如果这是第一次使用更新功能，请先配置 data/workbench-update-source.json。"
  exit 1
} finally {
  if (!$NoPause) {
    Write-Host ""
    Read-Host "按回车退出"
  }
}
