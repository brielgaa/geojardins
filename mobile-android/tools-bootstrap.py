"""Download build tools into an ignored workspace directory, never install globally."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
import time
import shutil

ROOT = Path(__file__).resolve().parents[1] / '.cache' / 'mobile-tools'
ROOT.mkdir(parents=True, exist_ok=True)

def download(name, url, checksum=None):
    target = ROOT / name
    if not target.exists() or not zipfile.is_zipfile(target):
        temporary = target.with_suffix('.download')
        for attempt in range(3):
            request = urllib.request.Request(url, headers={'User-Agent': 'GeoJardins-build/1', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=90) as src, temporary.open('wb') as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            if zipfile.is_zipfile(temporary):
                temporary.replace(target)
                break
            time.sleep(1)
        else:
            raise RuntimeError(f'Incomplete ZIP download: {name}')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if checksum and digest != checksum:
        raise RuntimeError(f'Checksum mismatch: {name}')
    print(name, digest, flush=True)
    return target

def gradle():
    if (ROOT / 'gradle-8.14.3/bin/gradle.bat').exists():
        return
    url = 'https://services.gradle.org/distributions/gradle-8.14.3-bin.zip'
    checksum = urllib.request.urlopen(url + '.sha256', timeout=60).read().decode().strip()
    path = download('gradle.zip', url, checksum)
    with zipfile.ZipFile(path) as z:
        z.extractall(ROOT)

def sdk():
    if (ROOT / 'sdk/cmdline-tools/latest/bin/sdkmanager.bat').exists():
        return
    path = download('android-tools.zip', 'https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip',
                    '98b565cb657b012dae6794cefc0f66ae1efb4690c699b78a614b4a6a3505b003')
    with zipfile.ZipFile(path) as z:
        z.extractall(ROOT / 'sdk' / 'cmdline-tools' / 'bootstrap')
    shutil.copytree(ROOT / 'sdk/cmdline-tools/bootstrap/cmdline-tools', ROOT / 'sdk/cmdline-tools/latest', dirs_exist_ok=True)

def python():
    if (ROOT / 'python310/tools/python.exe').exists():
        return
    path = download('python310.zip', 'https://api.nuget.org/v3-flatcontainer/python/3.10.11/python.3.10.11.nupkg')
    with zipfile.ZipFile(path) as z:
        z.extractall(ROOT / 'python310')

def jdk():
    req = 'https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse'
    assets = json.loads(urllib.request.urlopen(req, timeout=60).read())
    pkg = assets[0]['binary']['package']
    path = download('jdk17.zip', pkg['link'], pkg['checksum'])
    with zipfile.ZipFile(path) as z:
        z.extractall(ROOT / 'jdk')

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(f) for f in (gradle, sdk, python, jdk)]:
            try:
                future.result()
            except Exception as exc:
                print(type(exc).__name__, str(exc), flush=True)
    required = ('gradle-8.14.3/bin/gradle.bat', 'sdk/cmdline-tools/latest/bin/sdkmanager.bat', 'python310/tools/python.exe')
    missing = [name for name in required if not (ROOT / name).exists()]
    if missing:
        raise SystemExit('Missing build tools: ' + ', '.join(missing))
