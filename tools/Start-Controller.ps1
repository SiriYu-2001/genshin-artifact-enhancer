param([Parameter(Mandatory=$true)][string]$OrtDll,[ValidateRange(15,180)][int]$DurationMinutes=120)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$runtime=Join-Path $projectRoot 'runtime'
$null=New-Item -ItemType Directory -Force -Path $runtime
$sessionPath=Join-Path $runtime 'session.json'
$stopPath=Join-Path $runtime 'stop.signal'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator controller required.'}
$yas=Join-Path $projectRoot 'vendor\yas\target\release\yas_artifact.exe'
$yasHash=(Get-FileHash -LiteralPath $yas -Algorithm SHA256).Hash
$env:ORT_DYLIB_PATH=(Resolve-Path -LiteralPath $OrtDll).Path
$env:RAYON_NUM_THREADS='4'
$frostflake=Join-Path $projectRoot 'bin\cocogoat-control.exe'
if(-not(Test-Path -LiteralPath $frostflake)){$frostflake='C:\Program Files\cocogoat-control\cocogoat-control.exe'}
if(-not(Test-Path -LiteralPath $frostflake)){throw 'Frostflake executable is missing.'}
if(Get-Process -Name 'cocogoat-control' -ErrorAction SilentlyContinue){throw 'Existing bridge is already running.'}
if(Test-Path -LiteralPath $stopPath){Remove-Item -LiteralPath $stopPath}
$runDir=Join-Path $runtime ('controller-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
$requestDir=Join-Path $runDir 'requests'
$responseDir=Join-Path $runDir 'responses'
$progressPaths=@((Join-Path $runtime 'active-batch.json'),(Join-Path $runtime 'active-equip.json'))
$null=New-Item -ItemType Directory -Path $requestDir,$responseDir
$bytes=New-Object byte[] 32
$rng=[Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token=[BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant()
$session=[ordered]@{state='starting';busy=$false;endpoint='http://127.0.0.1:32333';token=$token;directory=$runDir;expires=(Get-Date).AddMinutes($DurationMinutes).ToString('o');yasHash=$yasHash}
$bridge=$null
$scanner=$null
$deadline=(Get-Date).AddMinutes($DurationMinutes)
$lastLeaseCheck=[datetime]::MinValue
$exitReason='deadline or stop signal'
$bridgeRestarts=0
function Save-Session {
    $session|ConvertTo-Json|Set-Content -LiteralPath ($sessionPath+'.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($sessionPath+'.tmp') -Destination $sessionPath -Force
}
try {
    $bridge=Start-Process -FilePath $frostflake -ArgumentList ('--local-auth='+$token+' --hide-window=true --stay') -WindowStyle Hidden -PassThru
    $session.state='running'
    $session['pid']=$bridge.Id
    Save-Session
    while(-not (Test-Path -LiteralPath $stopPath)){
        $now=Get-Date
        if(($now-$lastLeaseCheck).TotalSeconds -ge 10){
            $lastLeaseCheck=$now
            # Enhancement and equipment share the idle lease. A stale manifest
            # cannot keep an abandoned controller alive indefinitely.
            foreach($progressPath in $progressPaths){
              if(Test-Path -LiteralPath $progressPath){
                try {
                    $batchFile=Get-Item -LiteralPath $progressPath
                    if(($now-$batchFile.LastWriteTime).TotalMinutes -lt 10){
                        $batch=Get-Content -LiteralPath $progressPath -Raw|ConvertFrom-Json
                        if($batch.status -eq 'running'){
                            $deadline=$now.AddMinutes($DurationMinutes)
                            $session.expires=$deadline.ToString('o')
                            Save-Session
                            break
                        }
                    }
                } catch {
                    # An atomic manifest replacement can race this read. Retry next check.
                }
              }
            }
        }
        if($now -ge $deadline){break}
        if($bridge.HasExited){
            $bridgeRestarts++
            if($bridgeRestarts -gt 3){throw 'Frostflake repeatedly exited.'}
            $bridge=Start-Process -FilePath $frostflake -ArgumentList ('--local-auth='+$token+' --hide-window=true --stay') -WindowStyle Hidden -PassThru
            $session.pid=$bridge.Id
            Save-Session
        }
        foreach($file in @(Get-ChildItem -LiteralPath $requestDir -Filter '*.json' -File|Sort-Object Name)){
            if($file.BaseName -notmatch '^[0-9a-f]{32}$'){continue}
            $responsePath=Join-Path $responseDir $file.Name
            if(Test-Path -LiteralPath $responsePath){continue}
            $result=[ordered]@{id=$file.BaseName;state='starting';started=(Get-Date).ToString('o')}
            try {
                $request=Get-Content -LiteralPath $file.FullName -Raw|ConvertFrom-Json
                if($request.id -ne $file.BaseName){throw 'Mismatching request ID.'}
                if((Get-FileHash -LiteralPath $yas -Algorithm SHA256).Hash -ne $yasHash){throw 'yas changed during session.'}
                $jobDir=Join-Path $runDir ('job-'+$file.BaseName)
                $null=New-Item -ItemType Directory -Path $jobDir
                $arguments='--min-star 1 --min-level 0 --format good --delay 150 --max-wait-switch-item 100 --output-dir "'+$jobDir+'"'
                $timeout=60000
                switch($request.kind){
                    'observe' {
                        $layoutName=[string]$request.layout
                        if($layoutName -notmatch '^[a-z0-9-]+$'){throw 'Invalid layout name.'}
                        $layout=Join-Path $projectRoot ('layouts\'+$layoutName+'.json')
                        if(-not(Test-Path -LiteralPath $layout)){throw 'Unknown layout.'}
                        $arguments+=' --observe-layout "'+$layout+'"'
                    }
                    'read-current' {$arguments+=' --read-current --save-images'}
                    'scan-one' {$arguments+=' --number 1 --max-row 1 --save-images'}
                    'scan-all' {$arguments+=' --max-row 10000';$timeout=2400000}
                    default {throw 'Unsupported action. No consuming actions are exposed.'}
                }
                $session.busy=$true
                Save-Session
                $result['directory']=$jobDir
                $result.state='running'
                $result|ConvertTo-Json|Set-Content -LiteralPath $responsePath -Encoding UTF8
                $info=[Diagnostics.ProcessStartInfo]::new()
                $info.FileName=$yas
                $info.WorkingDirectory=$jobDir
                $info.Arguments=$arguments
                $info.UseShellExecute=$false
                $info.CreateNoWindow=$true
                $info.RedirectStandardInput=$true
                $info.RedirectStandardOutput=$true
                $info.RedirectStandardError=$true
                $info.StandardOutputEncoding=[Text.Encoding]::UTF8
                $info.StandardErrorEncoding=[Text.Encoding]::UTF8
                $scanner=[Diagnostics.Process]::new()
                $scanner.StartInfo=$info
                if(-not $scanner.Start()){throw 'yas failed to start.'}
                $stdout=$scanner.StandardOutput.ReadToEndAsync()
                $stderr=$scanner.StandardError.ReadToEndAsync()
                $scanner.StandardInput.WriteLine()
                $scanner.StandardInput.Close()
                # Poll so an explicit stop also interrupts a long scan.
                $jobDeadline=(Get-Date).AddMilliseconds($timeout)
                while(-not $scanner.WaitForExit(200)){
                    if((Get-Date) -ge $jobDeadline -or (Test-Path -LiteralPath $stopPath)){$scanner.Kill();$scanner.WaitForExit();throw 'Scanner interrupted or timed out.'}
                }
                $result.state='exited'
                $result['exitCode']=$scanner.ExitCode
            } catch {$result.state='failed';$result['error']=$_.Exception.Message}
            finally {
                if($scanner){
                    if(-not $scanner.HasExited){$scanner.Kill();$scanner.WaitForExit()}
                    $stdout.Result|Set-Content -LiteralPath (Join-Path $jobDir 'stdout.txt') -Encoding UTF8
                    $stderr.Result|Set-Content -LiteralPath (Join-Path $jobDir 'stderr.txt') -Encoding UTF8
                    $scanner.Dispose();$scanner=$null
                }
                $session.busy=$false
                Save-Session
                $result['finished']=(Get-Date).ToString('o')
                $result|ConvertTo-Json|Set-Content -LiteralPath $responsePath -Encoding UTF8
            }
        }
        Start-Sleep -Milliseconds 200
    }
} catch {
    $exitReason=$_.Exception.Message
    $_|Out-String|Set-Content -LiteralPath (Join-Path $runDir 'controller-error.txt') -Encoding UTF8
} finally {
    if($scanner -and -not $scanner.HasExited){$scanner.Kill();$scanner.WaitForExit()}
    if($bridge -and -not $bridge.HasExited){$bridge.Kill();$bridge.WaitForExit()}
    [ordered]@{state='stopped';busy=$false;directory=$runDir;finished=(Get-Date).ToString('o');reason=$exitReason;bridgeRestarts=$bridgeRestarts}|ConvertTo-Json|Set-Content -LiteralPath $sessionPath -Encoding UTF8
}
