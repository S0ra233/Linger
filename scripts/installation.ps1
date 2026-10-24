function Assert-RpInstallation {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $statePath = Join-Path $ProjectRoot '.rp-install\state.json'
    $logPath = Join-Path $ProjectRoot '.rp-install\install.log'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "RP 的 Python 环境尚未建立。请运行 安装依赖.bat；安装失败的具体步骤保存在 $logPath。"
    }
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw '已有 Python 环境，但没有完整安装记录。请重新运行 安装依赖.bat，等待“安装完成”。'
    }
    try { $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw '安装状态文件无法读取，请重新运行 安装依赖.bat。已有配置和数据保留。' }
    if ($state.ready -ne $true) {
        throw "上次安装尚未完成，停在 [$($state.stage)]。请重新运行 安装依赖.bat；详细日志：$logPath。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'web\dist\index.html'))) {
        throw '管理端构建文件缺失，请重新运行 安装依赖.bat。'
    }
    return $python
}
