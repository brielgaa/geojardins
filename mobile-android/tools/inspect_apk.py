"""Inspect package contents and actual ELF page alignment; no device claims."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

def inspect(path):
    libraries = []
    forbidden = []
    def scan(z, prefix=''):
        for name in z.namelist():
            full = prefix + name
            if ((name.endswith(('.sqlite', '.db', '.jks', '.keystore', '.p12')) and name != 'chaquopy/share/proj/proj.db')
                    or name.rsplit('/', 1)[-1] == '.env'):
                forbidden.append(full)
            if name.endswith(('.imy', '.zip')) and 'chaquopy/' in full:
                data = z.read(name)
                if zipfile.is_zipfile(io.BytesIO(data)):
                    with zipfile.ZipFile(io.BytesIO(data)) as child:
                        scan(child, full + '!/')
            if name.endswith('.so'):
                data = z.read(name)
                if not data.startswith(b'\x7fELF'):
                    continue
                endian = '<' if data[5] == 1 else '>'
                is64 = data[4] == 2
                phoff = struct.unpack_from(endian + ('Q' if is64 else 'I'), data, 32 if is64 else 28)[0]
                entry_size, count = struct.unpack_from(endian + 'HH', data, 54 if is64 else 42)
                alignment = []
                for i in range(count):
                    pos = phoff + i * entry_size
                    if struct.unpack_from(endian + 'I', data, pos)[0] == 1:
                        alignment.append(struct.unpack_from(endian + ('Q' if is64 else 'I'), data, pos + (48 if is64 else 28))[0])
                libraries.append(dict(path=full, load_alignment=alignment, supports_16kb=bool(alignment) and min(alignment) >= 16384))
    with zipfile.ZipFile(path) as apk:
        scan(apk)
    return dict(apk=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size,
        forbidden_files=forbidden, libraries=libraries, all_elf_16kb=all(x['supports_16kb'] for x in libraries))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('apk', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = inspect(args.apk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"{len(report['libraries'])} native libraries; all support 16 KB: {report['all_elf_16kb']}; forbidden files: {len(report['forbidden_files'])}")
    if report['forbidden_files']:
        raise SystemExit(1)
