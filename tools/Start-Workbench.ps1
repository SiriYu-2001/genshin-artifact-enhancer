param([string]$Python='python',[ValidateRange(1024,65535)][int]$Port=8766,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$runtimeDir=Join-Path $projectRoot 'runtime'
$null=New-Item -ItemType Directory -Path $runtimeDir -Force
$url="http://127.0.0.1:$Port"
$ready=$false
try {$reply=Invoke-RestMethod -Uri ($url+'/api/bootstrap') -TimeoutSec 2 -NoProxy; $ready=($null -ne $reply.catalog.profiles)} catch {}
if(-not $ready){
    $binary=(Get-Command $Python -ErrorAction Stop).Source
    $env:PYTHONIOENCODING='utf-8'
    $null=Start-Process -FilePath $binary -ArgumentList "-u -m enhancer ui --port $Port" -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'ui-server.stdout.log') -RedirectStandardError (Join-Path $runtimeDir 'ui-server.stderr.log') -PassThru
    for($attempt=0;$attempt -lt 30;$attempt++){
        Start-Sleep -Milliseconds 300
        try {$reply=Invoke-RestMethod -Uri ($url+'/api/bootstrap') -TimeoutSec 1 -NoProxy; $ready=($null -ne $reply.catalog.profiles); if($ready){break}} catch {}
    }
    if(-not $ready){throw '本地界面未能启动，请检查 runtime/ui-server.stderr.log 和 Python 依赖。'}
}
Write-Output $url
if(-not $NoBrowser){Start-Process $url}
