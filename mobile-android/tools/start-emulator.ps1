$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$env:ANDROID_HOME = Join-Path $repo '.cache/mobile-tools/sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:ANDROID_AVD_HOME = Join-Path $repo '.cache/mobile-tools/avd'
$env:ANDROID_USER_HOME = Join-Path $repo '.cache/mobile-android-user'
$emulator = Join-Path $env:ANDROID_HOME 'emulator/emulator.exe'
$log = Join-Path $repo '.cache/mobile-tools/emulator-run.log'
Start-Process -FilePath $emulator -ArgumentList @('-avd','GeoJardins35','-no-window','-no-audio','-no-boot-anim','-no-snapshot','-gpu','swiftshader','-memory','1536') -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError ($log + '.err') -PassThru | Select-Object Id
