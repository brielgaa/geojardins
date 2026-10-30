"""Strict offline base transfer, shared by the desktop export tool and Android."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

FILES = ('config/settings.json', 'config/fiscais.json', 'data/config/address_aliases.json',
         'data/processed/areas_jardins_partition.geojson', 'data/processed/ugr_jardins_boundary_partition.geojson',
         'data/processed/ugr_streets.json', 'map/operational_map.png', 'map/operational_map_overlays.json')

def create(base, target):
    base, target = Path(base), Path(target)
    hashes = {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in FILES}
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(base / name, name)
        z.writestr('manifest.json', json.dumps({'schema': 1, 'sha256': hashes}))

def install(archive, destination):
    """Hashes protect integrity; operator must obtain the package from a trusted source."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from geojardins.settings import Settings, load_settings
    destination = Path(destination).resolve()
    pending = destination.with_name(destination.name + '.pending')
    backup = destination.with_name(destination.name + '.backup')
    for path in (pending, backup):
        if path.resolve().parent != destination.parent:
            raise ValueError('Diretório de atualização fora da pasta privada.')
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir()
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if len(names) != len(FILES) + 1 or set(names) != {*FILES, 'manifest.json'}:
                raise ValueError('Pacote deve conter apenas os arquivos canônicos permitidos, sem duplicatas.')
            if sum(i.file_size for i in z.infolist()) > 30 * 1024 * 1024:
                raise ValueError('Base expandida excede 30 MB.')
            manifest = json.loads(z.read('manifest.json'))
            if manifest.get('schema') != 1 or set(manifest.get('sha256', {})) != set(FILES):
                raise ValueError('Manifesto de base inválido.')
            for name in FILES:
                raw = z.read(name)
                if hashlib.sha256(raw).hexdigest() != manifest['sha256'][name]:
                    raise ValueError('Integridade da base inválida: ' + name)
                path = pending / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (pending / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        def read(name):
            return json.loads((pending / name).read_text(encoding='utf-8-sig'))
        areas = read(FILES[3])
        boundary = read(FILES[4])
        if areas.get('type') != 'FeatureCollection' or boundary.get('type') != 'FeatureCollection' or len(boundary.get('features', [])) != 1:
            raise ValueError('Coleções geográficas inválidas.')
        features = areas.get('features', [])
        names = [f['properties']['area'] for f in features]
        if not names or len(names) != len(set(names)):
            raise ValueError('Áreas ausentes ou duplicadas.')
        geometries = [shape(f['geometry']) for f in features]
        bound = shape(boundary['features'][0]['geometry'])
        for geometry in [*geometries, bound]:
            if not geometry.is_valid or geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                raise ValueError('Geometria inválida; valide a base no desktop.')
            x1, y1, x2, y2 = geometry.bounds
            if not (-180 <= x1 <= x2 <= 180 and -90 <= y1 <= y2 <= 90):
                raise ValueError('A base deve estar em coordenadas geográficas EPSG:4326.')
        for i, a in enumerate(geometries):
            if any(a.intersection(b).area > 1e-12 for b in geometries[i + 1:]):
                raise ValueError('Partição contém sobreposição não resolvida.')
        if unary_union(geometries).symmetric_difference(bound).area > 1e-10:
            raise ValueError('Boundary e partição não coincidem.')
        settings = read(FILES[0])
        if set(settings) - set(Settings.__dataclass_fields__):
            raise ValueError('Parâmetros desconhecidos na base.')
        load_settings(pending / FILES[0])
        fiscais = read(FILES[1])
        if set(fiscais) != set(names) or any(not isinstance(v, str) or len(v) > 160 for v in fiscais.values()):
            raise ValueError('Mapeamento de fiscais incompatível com as áreas.')
        overlays = read(FILES[7])
        if set(f['area'] for f in overlays['features']) != set(names):
            raise ValueError('Mapa de referência incompatível com as áreas.')
        if not (pending / FILES[6]).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Mapa deve ser PNG.')
        from geojardins.address_normalizer import load_alias_rules
        from geojardins.street_dictionary import StreetDictionary
        load_alias_rules(pending / FILES[2])
        StreetDictionary.from_path(pending / FILES[5])
        if backup.exists():
            shutil.rmtree(backup)
        destination.rename(backup)
        try:
            pending.rename(destination)
        except OSError:
            backup.rename(destination)
            raise
        shutil.rmtree(backup)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
