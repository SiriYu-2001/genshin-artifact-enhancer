param([Parameter(Mandatory=$true)][string]$OrtDll,[int]$DurationMinutes=120,[Parameter(Mandatory=$true)][string]$SessionId)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$runtime=Join-Path $projectRoot 'runtime'
$null=New-Item -ItemType Directory -Force -Path $runtime
try {
    & (Join-Path $PSScriptRoot 'Start-Controller.ps1') -OrtDll $OrtDll -DurationMinutes $DurationMinutes -SessionId $SessionId
} catch {
    # Even preflight failures must reach the web worker; never log a session token.
    $reason=$_.Exception.Message
    $reason|Set-Content -LiteralPath (Join-Path $runtime 'controller-startup-error.txt') -Encoding UTF8
    $failure=[ordered]@{state='failed';busy=$false;session_id=$SessionId;reason=$reason;finished=(Get-Date).ToString('o')}
    $temp=Join-Path $runtime ('session-'+$SessionId+'.tmp')
    $failure|ConvertTo-Json|Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination (Join-Path $runtime 'session.json') -Force
    exit 1
}
