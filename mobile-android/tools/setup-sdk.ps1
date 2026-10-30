$ErrorActionPreference = 'Stop'
$mobile = Split-Path $PSScriptRoot -Parent
$repo = Split-Path $mobile -Parent
$toolsDir = Join-Path $repo '.cache/mobile-tools'
$env:ANDROID_HOME = Join-Path $toolsDir 'sdk'
$jdk = Get-ChildItem (Join-Path $toolsDir 'jdk') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
$manager = Join-Path $env:ANDROID_HOME 'cmdline-tools/latest/bin/sdkmanager.bat'
# Standard Android SDK licenses, accepted for the local development toolchain.
(1..20 | ForEach-Object { 'y' }) | & $manager "--sdk_root=$env:ANDROID_HOME" --licenses
& $manager "--sdk_root=$env:ANDROID_HOME" 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
if ($LASTEXITCODE -ne 0) { throw 'Android SDK setup failed.' }
