$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$toolsDir = Join-Path $repo '.cache/mobile-tools'
$env:ANDROID_HOME = Join-Path $toolsDir 'sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:ANDROID_USER_HOME = Join-Path $repo '.cache/mobile-android-user'
$env:ANDROID_AVD_HOME = Join-Path $toolsDir 'avd'
$env:DEBUG = $null
$env:JAVA_TOOL_OPTIONS = '--enable-native-access=ALL-UNNAMED'
$bin = Join-Path $env:ANDROID_HOME 'cmdline-tools/latest/bin'
$ErrorActionPreference = 'Continue'
& (Join-Path $bin 'sdkmanager.bat') "--sdk_root=$env:ANDROID_HOME" 'emulator' 'system-images;android-35;default;x86_64'
if ($LASTEXITCODE -ne 0) { throw 'Não foi possível instalar o emulador.' }
New-Item -ItemType Directory -Force $env:ANDROID_AVD_HOME | Out-Null
if (-not (Test-Path (Join-Path $env:ANDROID_AVD_HOME 'GeoJardins35.ini'))) {
    'no' | & (Join-Path $bin 'avdmanager.bat') create avd -n GeoJardins35 -k 'system-images;android-35;default;x86_64'
}
& (Join-Path $env:ANDROID_HOME 'emulator/emulator.exe') -accel-check
