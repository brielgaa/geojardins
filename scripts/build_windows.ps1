[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Venv não encontrado em $python. Crie-o e instale as dependências antes do build."
}

Push-Location $root
try {
    & $python -c "import PyInstaller, PySide6, openpyxl, pyproj, shapely"
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível instalar as dependências de build.' }
    & $python '.\scripts\generate_operational_map.py'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar os assets offline do mapa operacional.' }
    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { throw 'Os testes falharam; o release não foi gerado.' }
    }

    foreach ($target in @('build', 'dist', 'release')) {
        $path = Join-Path $root $target
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }

    $dist = Join-Path $root 'dist'
    $build = Join-Path $root 'build'
    $env:GEOJARDINS_BUILD_MODE = 'onedir'
    & $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $dist 'onedir') --workpath (Join-Path $build 'onedir') '.\GeoJardins.spec'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar o build onedir.' }
    Move-Item -LiteralPath (Join-Path $dist 'onedir\GeoJardins') -Destination (Join-Path $dist 'GeoJardins')
    Remove-Item -LiteralPath (Join-Path $dist 'onedir') -Force

    $env:GEOJARDINS_BUILD_MODE = 'onefile'
    & $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $dist 'onefile') --workpath (Join-Path $build 'onefile') '.\GeoJardins.spec'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar o build onefile.' }
    Move-Item -LiteralPath (Join-Path $dist 'onefile\GeoJardins.exe') -Destination (Join-Path $dist 'GeoJardins.exe')
    Remove-Item -LiteralPath (Join-Path $dist 'onefile') -Force
    Remove-Item Env:GEOJARDINS_BUILD_MODE -ErrorAction SilentlyContinue

    $onedirExe = Join-Path $dist 'GeoJardins\GeoJardins.exe'
    $onefileExe = Join-Path $dist 'GeoJardins.exe'
    foreach ($artifact in @($onedirExe, $onefileExe)) {
        if (-not (Test-Path -LiteralPath $artifact)) { throw "Artefato ausente: $artifact" }
    }
    & $onedirExe --smoke-test
    if ($LASTEXITCODE -ne 0) { throw 'O smoke test do build onedir falhou.' }
    & $onefileExe --smoke-test
    if ($LASTEXITCODE -ne 0) { throw 'O smoke test do build onefile falhou.' }

    $release = Join-Path $root 'release'
    $portable = Join-Path $release 'GeoJardins-portable'
    New-Item -ItemType Directory -Path $portable -Force | Out-Null
    Copy-Item -Path (Join-Path $dist 'GeoJardins\*') -Destination $portable -Recurse -Force
    Copy-Item -LiteralPath $onefileExe -Destination (Join-Path $release 'GeoJardins-onefile.exe') -Force
    Copy-Item -LiteralPath (Join-Path $root 'scripts\LEIA-ME.txt') -Destination (Join-Path $portable 'LEIA-ME.txt') -Force
    Compress-Archive -LiteralPath $portable -DestinationPath (Join-Path $release 'GeoJardins-portable.zip') -Force
    $hashes = @(
        Get-FileHash -Algorithm SHA256 -LiteralPath $onedirExe
        Get-FileHash -Algorithm SHA256 -LiteralPath $onefileExe
    )
    $hashes | ForEach-Object { "{0}  {1}" -f $_.Hash, $_.Path } | Set-Content -LiteralPath (Join-Path $release 'CHECKSUMS-SHA256.txt') -Encoding ascii
    Write-Host "Release pronto: $release"
    Write-Host "Onedir: $onedirExe"
    Write-Host "Onefile: $onefileExe"
    $hashes | Format-Table Algorithm, Hash, Path -AutoSize
}
finally {
    Remove-Item Env:GEOJARDINS_BUILD_MODE -ErrorAction SilentlyContinue
    Pop-Location
}
