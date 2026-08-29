$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Test-Command {
    param([string]$Command)
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Refresh-CurrentPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $windowsApps = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
    $env:Path = "$machinePath;$userPath;$windowsApps"
}

function Resolve-PythonRuntime {
    # Use a base Python >= 3.10. Ignore activated virtualenvs so packages are
    # not installed into another project's .venv by mistake.
    $checkCode = "import sys; raise SystemExit(0 if sys.version_info >= (3,10) and sys.prefix == sys.base_prefix else 1)"
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.14") },
        @{ Exe = "py"; Args = @("-3.13") },
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3.10") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        $exe = [string]$candidate.Exe
        if (-not (Test-Command $exe)) { continue }
        $baseArgs = @($candidate.Args)
        try {
            & $exe @baseArgs -c $checkCode 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Exe = $exe; Args = $baseArgs }
            }
        } catch { }
    }
    return $null
}

$script:PythonRuntime = $null

function Set-PythonRuntime {
    $script:PythonRuntime = Resolve-PythonRuntime
    return ($null -ne $script:PythonRuntime)
}

function Invoke-Python {
    param([string[]]$Arguments)
    if ($null -eq $script:PythonRuntime) {
        Set-PythonRuntime | Out-Null
    }
    if ($null -eq $script:PythonRuntime) {
        throw "Python 3.10+ was not found."
    }
    $exe = [string]$script:PythonRuntime.Exe
    $baseArgs = @($script:PythonRuntime.Args)
    & $exe @baseArgs @Arguments
}

function Get-PythonSummary {
    try {
        $version = Invoke-Python -Arguments @("--version") 2>&1
        $exe = Invoke-Python -Arguments @("-c", "import sys; print(sys.executable)") 2>&1
        return (($version -join "") + " / " + ($exe -join ""))
    } catch {
        return "Python 3.10+"
    }
}

function Test-NodeRuntime {
    if (-not (Test-Command "node")) { return $false }
    try {
        $major = & node -p "Number(process.versions.node.split('.')[0])" 2>&1
        return ($LASTEXITCODE -eq 0 -and [int]($major -join "") -ge 18)
    } catch {
        return $false
    }
}

function Test-NpmRuntime {
    if (-not (Test-Command "npm")) { return $false }
    try {
        & npm --version 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-WingetCommand {
    $command = Get-Command "winget" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $windowsAppsWinget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (Test-Path -LiteralPath $windowsAppsWinget) { return $windowsAppsWinget }
    return ""
}

function Test-Winget {
    $winget = Get-WingetCommand
    if (-not $winget) { return $false }
    try {
        & $winget --version 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-Winget {
    Write-Step "Installing winget / App Installer"
    $tempDir = Join-Path $env:TEMP "codemao-workbench-runtime"
    $bundlePath = Join-Path $tempDir "Microsoft.DesktopAppInstaller.msixbundle"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    try {
        Invoke-WebRequest -Uri "https://aka.ms/getwinget" -OutFile $bundlePath -UseBasicParsing
        Add-AppxPackage -Path $bundlePath
        Refresh-CurrentPath
        return (Test-Winget)
    } catch {
        Write-Warn ("winget auto install failed: " + $_.Exception.Message)
        return $false
    }
}

function Install-WingetPackage {
    param([string]$Id, [string]$Name)
    $winget = Get-WingetCommand
    if (-not $winget) { return $false }
    Write-Step ("Installing " + $Name + " by winget")
    & $winget install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements
    return ($LASTEXITCODE -eq 0)
}

function Install-PythonDirect {
    Write-Step "Installing Python 3.10 by direct download"
    $tempDir = Join-Path $env:TEMP "codemao-workbench-runtime"
    $installer = Join-Path $tempDir "python-3.10.11-amd64.exe"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" -OutFile $installer -UseBasicParsing
    Start-Process -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1" -Wait
    Refresh-CurrentPath
}

function Install-NodeDirect {
    Write-Step "Installing Node.js 20 by direct download"
    $tempDir = Join-Path $env:TEMP "codemao-workbench-runtime"
    $installer = Join-Path $tempDir "node-v20.11.1-x64.msi"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    Invoke-WebRequest -Uri "https://nodejs.org/dist/v20.11.1/node-v20.11.1-x64.msi" -OutFile $installer -UseBasicParsing
    Start-Process -FilePath "msiexec.exe" -ArgumentList "/i", $installer, "/qn", "/norestart" -Wait
    Refresh-CurrentPath
}

function Test-PythonPackage {
    param([string]$Module)
    try {
        Invoke-Python -Arguments @("-c", "import $Module") 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-PythonPackage {
    param([string]$Module, [string]$Package, [string]$Label)
    Write-Step ("Checking Python package: " + $Label)
    if (Test-PythonPackage -Module $Module) {
        Write-Ok ($Label + " already installed")
        return $true
    }

    Write-Host ("Installing " + $Label + " into: " + (Get-PythonSummary))
    try {
        Invoke-Python -Arguments @("-m", "ensurepip", "--upgrade")
    } catch {
        Write-Warn ("ensurepip failed: " + $_.Exception.Message)
    }

    $installAttempts = @(
        @("-m", "pip", "install", "--user", "--disable-pip-version-check", "--timeout", "30", "--retries", "2", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn", $Package),
        @("-m", "pip", "install", "--user", "--disable-pip-version-check", "--timeout", "30", "--retries", "2", $Package)
    )
    foreach ($args in $installAttempts) {
        Invoke-Python -Arguments $args
        if ($LASTEXITCODE -eq 0 -and (Test-PythonPackage -Module $Module)) {
            Write-Ok ($Label + " installed")
            return $true
        }
        Write-Warn ($Label + " install attempt failed; trying next source.")
    }
    Write-Warn ($Label + " is still missing")
    return $false
}

Write-Host "CodeMao Teacher Workbench runtime installer" -ForegroundColor Cyan
Write-Host "Checks existing Python 3.10+, Node.js 18+, npm, and required Python packages first."

Refresh-CurrentPath

Write-Step "Checking Python 3.10+"
if (Set-PythonRuntime) {
    Write-Ok (Get-PythonSummary)
} else {
    Write-Warn "Python 3.10+ not found"
}

Write-Step "Checking Node.js 18+ and npm"
$nodeOk = Test-NodeRuntime
$npmOk = Test-NpmRuntime
if ($nodeOk) { Write-Ok ("node " + (& node --version)) } else { Write-Warn "Node.js 18+ not found" }
if ($npmOk) { Write-Ok ("npm " + (& npm --version)) } else { Write-Warn "npm not found" }

if ($null -eq $script:PythonRuntime -or -not $nodeOk -or -not $npmOk) {
    Write-Step "Checking installer source"
    $wingetOk = Test-Winget
    if (-not $wingetOk) {
        Write-Warn "winget not found; trying to install it"
        $wingetOk = Install-Winget
    }
    if ($wingetOk) {
        Write-Ok ("winget " + (& (Get-WingetCommand) --version))
    } else {
        Write-Warn "winget unavailable; using direct installers when needed"
    }

    if ($null -eq $script:PythonRuntime) {
        if ($wingetOk -and (Install-WingetPackage -Id "Python.Python.3.10" -Name "Python 3.10")) {
            Refresh-CurrentPath
        } else {
            Install-PythonDirect
        }
    }
    if (-not (Test-NodeRuntime) -or -not (Test-NpmRuntime)) {
        if ($wingetOk -and (Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -Name "Node.js LTS")) {
            Refresh-CurrentPath
        } else {
            Install-NodeDirect
        }
    }
}

Write-Step "Final runtime check"
Refresh-CurrentPath
$pythonOk = Set-PythonRuntime
$nodeOk = Test-NodeRuntime
$npmOk = Test-NpmRuntime
if ($pythonOk) { Write-Ok (Get-PythonSummary) } else { Write-Warn "Python 3.10+ still not detected" }
if ($nodeOk) { Write-Ok ("node " + (& node --version)) } else { Write-Warn "Node.js 18+ still not detected" }
if ($npmOk) { Write-Ok ("npm " + (& npm --version)) } else { Write-Warn "npm still not detected" }

$packagesOk = $true
if ($pythonOk) {
    $pythonPackages = @(
        @{ Module = "requests"; Package = "requests"; Label = "requests" },
        @{ Module = "fpdf"; Package = "fpdf2"; Label = "fpdf2" },
        @{ Module = "pptx"; Package = "python-pptx"; Label = "python-pptx" },
        @{ Module = "win32com.client"; Package = "pywin32"; Label = "pywin32" },
        @{ Module = "winpty"; Package = "pywinpty"; Label = "pywinpty" }
    )
    foreach ($item in $pythonPackages) {
        $ok = Install-PythonPackage -Module $item.Module -Package $item.Package -Label $item.Label
        if (-not $ok) { $packagesOk = $false }
    }
} else {
    $packagesOk = $false
}

if ($pythonOk -and $nodeOk -and $npmOk -and $packagesOk) {
    Write-Host ""
    Write-Host "Runtime is ready. You can now launch the dashboard with launch-workbench.vbs." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Warn "Runtime is not fully ready. Please close this window and run this installer again once, or install the missing item manually."
exit 2
