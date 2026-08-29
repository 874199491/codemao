$ErrorActionPreference = "Stop"

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

function Get-WingetCommand {
    $command = Get-Command "winget" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $windowsAppsWinget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (Test-Path -LiteralPath $windowsAppsWinget) {
        return $windowsAppsWinget
    }
    return ""
}

function Test-Winget {
    $winget = Get-WingetCommand
    if (-not $winget) {
        return $false
    }
    try {
        & $winget --version 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-Python310 {
    if (-not (Test-Command "py")) {
        return $false
    }
    try {
        $version = & py -3.10 --version 2>&1
        return ($LASTEXITCODE -eq 0 -and (($version -join "`n") -match "Python 3\.10\."))
    } catch {
        return $false
    }
}

function Test-Node {
    if (-not (Test-Command "node")) {
        return $false
    }
    try {
        $version = & node --version 2>&1
        return ($LASTEXITCODE -eq 0 -and (($version -join "`n") -match "^v\d+\."))
    } catch {
        return $false
    }
}

function Test-Npm {
    if (-not (Test-Command "npm")) {
        return $false
    }
    try {
        $version = & npm --version 2>&1
        return ($LASTEXITCODE -eq 0 -and (($version -join "`n") -match "^\d+\."))
    } catch {
        return $false
    }
}

function Test-PythonPackage {
    param([string]$PackageName)
    if (-not (Test-Python310)) {
        return $false
    }
    try {
        & py -3.10 -c "import $PackageName" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-WingetPackage {
    param(
        [string]$Id,
        [string]$Name
    )
    Write-Step "Installing $Name"
    $winget = Get-WingetCommand
    if (-not $winget) {
        throw "winget is not available. Cannot install $Name."
    }
    & $winget install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "$Name install failed. winget exit code: $LASTEXITCODE"
    }
}

function Install-Winget {
    Write-Step "Installing winget/App Installer"
    $downloadUrl = "https://aka.ms/getwinget"
    $tempDir = Join-Path $env:TEMP "codemao-workbench-runtime"
    $bundlePath = Join-Path $tempDir "Microsoft.DesktopAppInstaller.msixbundle"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $bundlePath -UseBasicParsing
        Add-AppxPackage -Path $bundlePath
    } catch {
        Write-Warn "Automatic winget install failed: $($_.Exception.Message)"
        Write-Host "Please install or update App Installer from Microsoft Store, then run this script again."
        Write-Host "Search Microsoft Store for: App Installer"
        exit 1
    }
    Refresh-CurrentPath
    if (-not (Test-Winget)) {
        Write-Warn "App Installer was installed, but winget is still not available in this window."
        Write-Host "Close this window and run install-runtime.bat again. If it still fails, restart the computer."
        exit 2
    }
    Write-Ok ("winget " + (& (Get-WingetCommand) --version))
}

Write-Host "CodeMao Teacher Workbench runtime installer" -ForegroundColor Cyan
Write-Host "This script checks or installs Python 3.10, Node.js LTS, and npm."

Write-Step "Checking winget"
Refresh-CurrentPath
if (-not (Test-Winget)) {
    Write-Warn "winget was not found on this computer."
    Install-Winget
}
Write-Ok ("winget " + (& (Get-WingetCommand) --version))

Write-Step "Checking Python 3.10"
if (Test-Python310) {
    Write-Ok (& py -3.10 --version)
} else {
    Install-WingetPackage -Id "Python.Python.3.10" -Name "Python 3.10"
}

Write-Step "Checking Node.js and npm"
if (Test-Node -and Test-Npm) {
    Write-Ok ("node " + (& node --version))
    Write-Ok ("npm " + (& npm --version))
} else {
    Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -Name "Node.js LTS"
}

Write-Step "Refreshing PATH and checking again"
Refresh-CurrentPath

$pythonOk = Test-Python310
$nodeOk = Test-Node
$npmOk = Test-Npm

if ($pythonOk) {
    Write-Ok (& py -3.10 --version)
} else {
    Write-Warn "Python 3.10 was not detected. If it was just installed, close this window and run this script again."
}

if ($nodeOk) {
    Write-Ok ("node " + (& node --version))
} else {
    Write-Warn "node was not detected. If it was just installed, close this window and run this script again."
}

if ($npmOk) {
    Write-Ok ("npm " + (& npm --version))
} else {
    Write-Warn "npm was not detected. If it was just installed, close this window and run this script again."
}

if ($pythonOk) {
    $pythonPackages = @(
        @{ Module = "requests"; Package = "requests"; Label = "requests" },
        @{ Module = "fpdf"; Package = "fpdf2"; Label = "fpdf2" },
        @{ Module = "pptx"; Package = "python-pptx"; Label = "python-pptx" },
        @{ Module = "win32com.client"; Package = "pywin32"; Label = "pywin32" },
        @{ Module = "winpty"; Package = "pywinpty"; Label = "pywinpty" }
    )
    # 国内访问 PyPI 慢，优先使用清华镜像加速安装
    $pipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
    foreach ($item in $pythonPackages) {
        Write-Step ("Checking Python package: " + $item.Label)
        if (Test-PythonPackage -PackageName $item.Module) {
            Write-Ok ("Python package " + $item.Label + " is available")
        } else {
            & py -3.10 -m pip install --user -i $pipIndex $item.Package
            if ($LASTEXITCODE -ne 0) {
                Write-Warn ("Tsinghua mirror failed for " + $item.Label + ", retrying with default PyPI")
                & py -3.10 -m pip install --user $item.Package
            }
            if ($LASTEXITCODE -ne 0) {
                Write-Warn ("Failed to install Python package " + $item.Label + ". Some tools may not work.")
            } elseif (Test-PythonPackage -PackageName $item.Module) {
                Write-Ok ("Python package " + $item.Label + " installed")
            } else {
                Write-Warn ("Python package " + $item.Label + " was installed but cannot be imported yet.")
            }
        }
    }
}

if ($pythonOk -and $nodeOk -and $npmOk) {
    Write-Host ""
    Write-Host "Runtime is ready. You can now launch the dashboard with launch-workbench.vbs." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Warn "Install may have completed, but this window has not picked up the new PATH yet."
Write-Warn "Close this window and run this script again, or restart the computer before launching the dashboard."
exit 2
