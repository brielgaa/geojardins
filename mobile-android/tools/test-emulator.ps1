$ErrorActionPreference = 'Stop'
$mobile = Split-Path $PSScriptRoot -Parent
$repo = Split-Path $mobile -Parent
$adb = Join-Path $repo '.cache/mobile-tools/sdk/platform-tools/adb.exe'
$device = 'emulator-5554'
& $adb -s $device install -r (Join-Path $mobile 'app/build/outputs/apk/debug/app-debug.apk')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar o APK no emulador.' }
& $adb -s $device install -r (Join-Path $mobile 'app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar os testes no emulador.' }
$result = & $adb -s $device shell am instrument -w -r 'br.com.geojardins.mobile.debug.test/androidx.test.runner.AndroidJUnitRunner'
$result | Write-Output
$report = Join-Path $mobile 'build/reports/instrumentation.txt'
New-Item -ItemType Directory -Force (Split-Path $report -Parent) | Out-Null
$result | Set-Content -Encoding UTF8 -LiteralPath $report
$captureDir = Join-Path $mobile 'build/screenshots'
New-Item -ItemType Directory -Force $captureDir | Out-Null
# Copy only test-created PNGs. No credentials or unrelated device files are read.
foreach ($name in @('dashboard','coordinate','map','fiscais','settings','batch')) {
    $source = "files/screenshots/$name.png"
    $destination = Join-Path $captureDir ($name + '.png')
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $adb
    $process.StartInfo.Arguments = "-s $device exec-out run-as br.com.geojardins.mobile.debug cat $source"
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $null = $process.Start()
    $output = [System.IO.File]::Create($destination)
    try { $process.StandardOutput.BaseStream.CopyTo($output) } finally { $output.Dispose() }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { Remove-Item -LiteralPath $destination -ErrorAction SilentlyContinue }
}
if (($result -join "`n") -notmatch 'OK \(2 tests\)') { throw 'Testes Android falharam; veja build/reports/instrumentation.txt.' }
