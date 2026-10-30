param([ValidateSet('debug','release','check','connected')][string]$Mode='debug')
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$toolsDir = Join-Path $repo '.cache/mobile-tools'
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = Join-Path $toolsDir 'sdk' }
$env:ANDROID_USER_HOME = Join-Path $repo '.cache/mobile-android-user'
if (-not $env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME = Join-Path $env:TEMP 'GeoJardins-Android/gradle' }
$jdk = Get-ChildItem (Join-Path $toolsDir 'jdk') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
$python = Join-Path $toolsDir 'python310/tools/python.exe'
if ($env:GEOJARDINS_BUILD_PYTHON) { $python = $env:GEOJARDINS_BUILD_PYTHON }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python 3.10 necessário. Execute tools-bootstrap.py ou defina GEOJARDINS_BUILD_PYTHON.' }
$gradle = Join-Path $toolsDir 'gradle-8.14.3/bin/gradle.bat'
if (-not (Test-Path -LiteralPath $gradle)) { $gradle = Join-Path $PSScriptRoot 'gradlew.bat' }
$env:DEBUG = $null
# OneDrive marks generated directories read-only. Gradle's Java cleanup cannot
# remove them; clear only this attribute inside the Android-generated subtree.
$generatedBuild = Join-Path $PSScriptRoot 'app/build'
if (Test-Path -LiteralPath $generatedBuild) {
    Get-ChildItem -LiteralPath $generatedBuild -Directory -Recurse -Force | ForEach-Object {
        if ($_.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
            $_.Attributes = $_.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        }
    }
}
$tasks = switch ($Mode) {
    'debug' { @(':app:assembleDebug', 'wrapper') }
    'release' { @(':app:assembleRelease') }
    'check' { @(':app:assembleDebug', ':app:lintDebug', ':app:assembleDebugAndroidTest') }
    'connected' { @(':app:connectedDebugAndroidTest', 'wrapper') }
}
& $gradle -p $PSScriptRoot "-PbuildPython=$python" --no-daemon --console=plain @tasks
if ($LASTEXITCODE -ne 0) { throw 'Build Android falhou; consulte o log acima.' }
