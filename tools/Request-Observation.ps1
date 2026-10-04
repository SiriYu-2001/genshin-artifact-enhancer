param(
    [ValidateSet('observe','read-current','scan-one','scan-all')][string]$Kind='observe',
    [string]$Layout='capture'
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$session=Get-Content -LiteralPath (Join-Path $projectRoot 'runtime\session.json') -Raw|ConvertFrom-Json
if($session.state -ne 'running' -or $session.busy){throw 'Controller unavailable or busy.'}
$id=[Guid]::NewGuid().ToString('N')
$requestPath=Join-Path $session.directory ('requests\'+$id+'.json')
$responsePath=Join-Path $session.directory ('responses\'+$id+'.json')
[ordered]@{id=$id;kind=$Kind;layout=$Layout}|ConvertTo-Json|Set-Content -LiteralPath ($requestPath+'.tmp') -Encoding UTF8
Move-Item -LiteralPath ($requestPath+'.tmp') -Destination $requestPath
[ordered]@{id=$id;response=$responsePath}|ConvertTo-Json
