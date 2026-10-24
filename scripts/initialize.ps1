$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'installation.ps1')
$venvPython = Assert-RpInstallation $projectRoot
& $venvPython -m rp_server init
if ($LASTEXITCODE -ne 0) { throw '初始化未完成，请查看上方原因；已有账户和数据不会被覆盖。' }
