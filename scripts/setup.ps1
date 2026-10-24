$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$installDirectory = Join-Path $projectRoot '.rp-install'
$statePath = Join-Path $installDirectory 'state.json'
$logPath = Join-Path $installDirectory 'install.log'
[System.IO.Directory]::CreateDirectory($installDirectory) | Out-Null
$phase = '准备安装'
$transcribing = $false
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

function Write-InstallState {
    param([bool]$Ready, [string]$Stage, [string]$Reason = '')
    $state = @{ ready = $Ready; stage = $Stage; reason = $Reason; updated_at = [DateTime]::UtcNow.ToString('o') }
    [System.IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    Write-Host "当前步骤：$Stage"
}

function Invoke-Required {
    param([string]$Program, [string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    try {
        # 原生程序输出显式交给 PowerShell，确保 Transcript 能记录下载和构建错误。
        # stderr 可以包含正常警告，是否失败以进程退出码为准。
        $ErrorActionPreference = 'Continue'
        & $Program @Arguments 2>&1 | ForEach-Object { Write-Host ([string]$_) }
        $commandExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($commandExit -ne 0) { throw "步骤 [$phase] 失败，退出码 $commandExit。请查看日志中的具体原因；已有文件保留。" }
}

function Find-Python {
    if ($env:RP_PYTHON) {
        if (-not (Test-Path -LiteralPath $env:RP_PYTHON -PathType Leaf)) { throw '指定的 Python 路径不存在，请检查 RP_PYTHON。' }
        return (Resolve-Path -LiteralPath $env:RP_PYTHON).Path
    }
    $candidates = @()
    $pathPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($pathPython) { $candidates += $pathPython.Source }
    $candidates += (Join-Path $env:USERPROFILE 'miniconda3\python.exe')
    $candidates += (Join-Path $env:USERPROFILE 'anaconda3\python.exe')
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -match '\\WindowsApps\\' -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'
        if ($LASTEXITCODE -eq 0) { return $candidate }
        Write-Host "跳过不满足 Python 3.13+ 要求的程序：$candidate"
    }
    throw '没有找到 Python 3.13+。请安装后重试，或用 RP_PYTHON 指定现有 Python 的完整路径。'
}

try {
    Start-Transcript -LiteralPath $logPath -Append | Out-Null
    $transcribing = $true
    Write-Host "安装日志：$logPath"
    Write-InstallState $false $phase
    $phase = '选择 Python 3.13+'
    Write-InstallState $false $phase
    $pythonCommand = Find-Python
    Write-Host "使用 Python：$pythonCommand"
    Invoke-Required $pythonCommand @('-c', 'import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 13) else 1)')
    $phase = '创建 RP Python 环境'
    Write-InstallState $false $phase
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Required $pythonCommand @('-m', 'venv', '.venv')
    }
    Invoke-Required $venvPython @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)')
    $phase = '下载并安装服务端依赖'
    Write-InstallState $false $phase
    Invoke-Required $venvPython @('-m', 'pip', 'install', '-e', 'backend')
    $phase = '查找 Node.js 与 npm'
    Write-InstallState $false $phase
    $npmInfo = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    $npmCommand = if ($npmInfo) { $npmInfo.Source } else { Join-Path $env:ProgramFiles 'nodejs\npm.cmd' }
    if (-not (Test-Path -LiteralPath $npmCommand -PathType Leaf)) { throw '未找到 npm.cmd，请安装 Node.js 20.19+ 后重试。' }
    $env:PATH = (Split-Path -Parent $npmCommand) + [System.IO.Path]::PathSeparator + $env:PATH
    $phase = '下载并安装管理端依赖'
    Write-InstallState $false $phase
    Invoke-Required $npmCommand @('--prefix', 'web', 'install')
    $phase = '构建管理端'
    Write-InstallState $false $phase
    Invoke-Required $npmCommand @('--prefix', 'web', 'run', 'build')
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) { throw '构建未生成管理端入口文件。' }
    Write-InstallState $true '安装完成'
    Write-Host '依赖安装和管理端构建已完成；这不代表功能验收通过。'
    Write-Host '首次使用请运行 初始化.bat；之后运行 启动.bat。'
} catch {
    Write-InstallState $false $phase $_.Exception.Message
    Write-Host "安装未完成：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "停在：$phase"
    Write-Host "详细日志：$logPath"
    exit 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
}
