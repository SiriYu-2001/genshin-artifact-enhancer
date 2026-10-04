$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$env:RUSTUP_HOME=Join-Path $projectRoot '.tools\rustup'
$env:CARGO_HOME=Join-Path $projectRoot '.tools\cargo'
$env:PATH=(Join-Path $env:CARGO_HOME 'bin')+';'+$env:PATH
$env:CARGO_BUILD_JOBS='4'
$proxy=[Net.WebRequest]::GetSystemWebProxy().GetProxy([Uri]'https://crates.io')
if($proxy.Host -ne 'crates.io'){$env:HTTPS_PROXY=$proxy.AbsoluteUri;$env:HTTP_PROXY=$proxy.AbsoluteUri}
Push-Location -LiteralPath (Join-Path $projectRoot 'vendor\yas')
try {
    cargo build --release -p yas-application --bin yas_artifact *> (Join-Path $projectRoot '.tools\build.log')
    if($LASTEXITCODE -ne 0){throw 'yas build failed; see .tools/build.log'}
    cargo build --release -p yas_scanner_genshin --bin yas_readonly *> (Join-Path $projectRoot '.tools\readonly-build.log')
    if($LASTEXITCODE -ne 0){throw 'read-only build failed; see .tools/readonly-build.log'}
    Get-FileHash -LiteralPath '.\target\release\yas_artifact.exe','.\target\release\yas_readonly.exe' -Algorithm SHA256
} finally {Pop-Location}
