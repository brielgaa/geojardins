"""Deterministic offline comparison corpus: real canonical geometry, synthetic provider fixtures."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

MOBILE = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--core', choices=('desktop', 'mobile'), required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if args.core == 'mobile':
    os.environ['GEOJARDINS_ANDROID_BASE'] = str(MOBILE / 'app/build/generated/core/assets/base')
    os.environ['GEOJARDINS_ANDROID_FILES'] = tempfile.mkdtemp(prefix='geojardins-parity-')
sys.path.insert(0, str(MOBILE.parent / 'src' if args.core == 'desktop' else MOBILE / 'app/build/generated/core/python'))

from dataclasses import asdict, replace
from geojardins.classification import GeographicClassifier
from geojardins.address_lookup import AddressLookupService
from geojardins.geocoding.models import GeocodeCandidate
from geojardins.fiscais import FiscalResolver
from geojardins.settings import Settings
from shapely.geometry import shape

classifier = GeographicClassifier.from_paths()
bound = shape(classifier.boundary_collection['features'][0]['geometry'])
x1, y1, x2, y2 = bound.bounds
coordinates = [(x1+(x2-x1)*x/8, y1+(y2-y1)*y/8) for x in range(9) for y in range(9)]
for feature in classifier.areas_collection['features']:
    polygon = shape(feature['geometry'])
    point = polygon.representative_point()
    coordinates.append((point.x, point.y))
    parts = [polygon] if polygon.geom_type == 'Polygon' else polygon.geoms
    for part in list(parts)[:2]:
        coordinates.extend(list(part.exterior.coords)[:3])
coordinate_results = [asdict(classifier.classify(lat, lon)) for lon, lat in coordinates]
fixtures = []
class FixtureGeocoder:
    def __init__(self, values): self.values = values
    def geocode(self, query): return self.values
for feature in classifier.areas_collection['features']:
    p = shape(feature['geometry']).representative_point()
    candidate = GeocodeCandidate(latitude=p.y, longitude=p.x, provider='geoapify',
        display_name='Rua Oscar Freire, 1250, São Paulo', house_number='1250', road='Rua Oscar Freire',
        city='São Paulo', state='SP', country_code='br', result_type='building', confidence=1.0,
        confidence_building_level=1.0, confidence_street_level=1.0)
    variants = [candidate, replace(candidate, house_number='1400'), replace(candidate, road='Rua Outra'),
        replace(candidate, result_type='street', confidence_building_level=0.1),
        replace(candidate, latitude=0, longitude=0)]
    for value in variants:
        service = AddressLookupService(FixtureGeocoder([value]), classifier, FiscalResolver({}), Settings())
        fixtures.append(service.lookup('Rua Oscar Freire', '1250').export_values())
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps({'coordinates': coordinate_results, 'decisions': fixtures}, ensure_ascii=False), encoding='utf-8')
print(f'{len(coordinate_results)} coordinates, {len(fixtures)} provider cases: {args.output}')
