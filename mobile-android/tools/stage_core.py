"""Allowlisted snapshot: preserves desktop and excludes private/user data."""
from pathlib import Path
import hashlib
import json
import os
import shutil

MOBILE = Path(__file__).resolve().parents[1]
REPO = MOBILE.parent
MODULES = ('__init__', 'address_lookup', 'address_normalizer', 'area_lookup',
           'batch_lookup', 'candidate_compatibility', 'classification', 'confidence',
           'fiscais', 'geoapify_precision', 'maps_links', 'settings', 'street_dictionary')
PROVIDERS = ('base', 'cache', 'fallback', 'geoapify', 'locationiq', 'models')
DATA = ('config/settings.json', 'config/fiscais.json', 'data/config/address_aliases.json',
        'data/processed/areas_jardins_partition.geojson',
        'data/processed/ugr_jardins_boundary_partition.geojson', 'data/processed/ugr_streets.json')
MAP = ('operational_map.png', 'operational_map_overlays.json')

def stage():
    root = MOBILE / 'app/build/generated/core'
    paths = [f'src/geojardins/{m}.py' for m in MODULES]
    paths += [f'src/geojardins/geocoding/{m}.py' for m in PROVIDERS]
    allowed = {root / 'python' / name.removeprefix('src/') for name in paths}
    allowed.update(root / 'assets/base' / name for name in DATA)
    allowed.update(root / 'assets/base/map' / name for name in MAP)
    allowed.update((root / 'python/geojardins/runtime.py',
                    root / 'python/geojardins/geocoding/__init__.py',
                    root / 'assets/base/manifest.json'))
    # OneDrive may hold directory handles: prune unexpected files without removing
    # generated directories. Every retained file is overwritten from the allowlist.
    assert root.resolve().is_relative_to((MOBILE / 'app/build').resolve())
    for item in root.rglob('*'):
        if not item.resolve().is_relative_to(root.resolve()):
            raise ValueError('Generated source contains a link outside its root.')
        if item.is_file() and item not in allowed:
            item.unlink()
    hashes = {}
    for name in paths:
        dest = root / 'python' / name.removeprefix('src/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, dest)
        hashes[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    shutil.copyfile(MOBILE / 'platform_runtime.py', root / 'python/geojardins/runtime.py')
    (root / 'python/geojardins/geocoding/__init__.py').write_text('"""Android providers: test mocks are intentionally not packaged."""\n', encoding='utf-8')
    for name in DATA:
        dest = root / 'assets/base' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name == 'config/fiscais.json' and os.environ.get('GEOJARDINS_INCLUDE_PRIVATE_DATA') != '1':
            areas = json.loads((REPO / 'data/processed/areas_jardins_partition.geojson').read_text(encoding='utf-8'))
            assignments = {
                feature['properties']['area']: 'NÃO CONFIGURADO'
                for feature in areas.get('features', [])
                if feature.get('properties', {}).get('area')
            }
            dest.write_text(json.dumps(assignments, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        else:
            shutil.copyfile(REPO / name, dest)
        hashes[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    for name in MAP:
        dest = root / 'assets/base/map' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / 'src/geojardins/gui/assets' / name, dest)
    (root / 'assets/base/manifest.json').write_text(json.dumps({'schema': 1, 'sha256': hashes}, indent=2), encoding='utf-8')
    print(f'Staged {len(paths)} unmodified Python modules; {len(DATA)} data files.')

if __name__ == '__main__':
    stage()
