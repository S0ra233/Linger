$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'installation.ps1')
$venvPython = Assert-RpInstallation $projectRoot
Write-Host 'RP 管理台：http://127.0.0.1:18765'
Write-Host '使用 Ctrl+C 停止服务。运行与资料设置在管理台中维护。'
& $venvPython -m rp_server serve
if ($LASTEXITCODE -ne 0) { throw "服务退出，代码 $LASTEXITCODE；请查看上方原因。" }
