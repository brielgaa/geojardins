import hashlib
import json
import sys
from pathlib import Path

import pytest

MOBILE = Path(__file__).resolve().parents[1]
BASE = MOBILE / 'app/build/generated/core/assets/base'
sys.path.insert(0, str(MOBILE / 'app/src/main/python'))
sys.path.insert(0, str(MOBILE / 'app/build/generated/core/python'))

import mobile_engine as engine

@pytest.fixture(autouse=True)
def private_session(tmp_path):
    engine.initialize(str(tmp_path), str(BASE))
    engine.credentials('', '')
    engine.save_settings({'offline': True})
    return tmp_path

def invoke(operation, **args):
    response = json.loads(engine.call(operation, json.dumps(args)))
    assert response['ok'], response
    return response

def test_sources_are_byte_identical_and_allowlisted():
    root = MOBILE / 'app/build/generated/core'
    manifest = json.loads((BASE / 'manifest.json').read_text())
    for name, digest in manifest['sha256'].items():
        if name != 'config/fiscais.json':
            assert hashlib.sha256((MOBILE.parent / name).read_bytes()).hexdigest() == digest
    fiscais = json.loads((BASE / 'config/fiscais.json').read_text(encoding='utf-8'))
    assert all(value == 'NÃO CONFIGURADO' for value in fiscais.values())
    assert not list(root.rglob('*.sqlite'))
    assert not list(root.rglob('.env*'))
    assert not list(root.rglob('user_config.py'))
    assert not list(root.rglob('mock.py'))

def test_coordinate_matches_canonical_engine():
    from geojardins.classification import GeographicClassifier
    from shapely.geometry import shape
    classifier = GeographicClassifier.from_paths()
    for feature in classifier.areas_collection['features']:
        p = shape(feature['geometry']).representative_point()
        expected = classifier.classify(p.y, p.x)
        actual = invoke('coordinate', latitude=p.y, longitude=p.x)['data']['last']
        assert actual['Área Candidata'] == expected.area
        assert actual['Distância Divisa (m)'] == pytest.approx(expected.distance_to_boundary_m, abs=0.001)
    result = invoke('coordinate', latitude=0, longitude=0)['data']['last']
    assert result['Status'] == 'FORA_DA_UGR'
    assert result['Área'] is None

@pytest.mark.parametrize('lat,lon', [('nan', 0), (0, 'inf'), (91, 0), (0, 181), ('abc', 0)])
def test_invalid_coordinates_fail_without_invented_result(lat, lon):
    response = json.loads(engine.call('coordinate', json.dumps({'latitude': lat, 'longitude': lon})))
    assert not response['ok']

def test_offline_miss_has_actionable_error_and_no_requests():
    service = engine._service()
    result = service.lookup('Rua Oscar Freire', '1250')
    assert result.status.value == 'ERRO_GEOCODIFICACAO'
    assert 'cache' in result.reason
    assert service.geocoder.request_count == 0

def test_offline_hit_reuses_real_cache_schema():
    from geojardins.geocoding.models import GeocodeCandidate
    from shapely.geometry import shape
    service = engine._service()
    p = shape(service.classifier.areas_collection['features'][0]['geometry']).representative_point()
    _, query = service.prepare_query('Rua Oscar Freire', '1250')
    candidate = GeocodeCandidate(latitude=p.y, longitude=p.x, display_name='Rua Oscar Freire, 1250',
        provider='geoapify', road='Rua Oscar Freire', house_number='1250', country_code='br', city='São Paulo', state='SP')
    service.cache.put('geoapify', query, [candidate])
    result = service.lookup('Rua Oscar Freire', '1250')
    assert result.candidate is not None
    assert result.latitude == p.y
    assert service.geocoder.request_count == 0
    assert service.cache.stats.hits >= 1

def test_paste_batch_export_and_restart(private_session):
    from openpyxl import load_workbook
    source = private_session / 'source.csv'
    source.write_text('Logradouro;Numero;Ordem\nRua Oscar Freire;1250;A\nRua Augusta;42;B', encoding='utf-8')
    invoke('import', path=str(source))
    invoke('batch', street_column='Logradouro', number_column='Numero')
    rows = engine.snapshot()['rows']
    assert len(rows) == 2
    assert all(r['status'] == 'ERRO_GEOCODIFICACAO' for r in rows)
    target = Path(invoke('export')['value'])
    wb = load_workbook(target)
    assert 'Resumo' in wb.sheetnames
    assert wb.active.cell(2, 3).value == 'A'
    assert wb.active.cell(3, 3).value == 'B'
    wb.close()
    restored = engine.initialize(str(private_session), str(BASE))
    assert restored['rows'] == rows
    invoke('batch', street_column='Logradouro', number_column='Numero', selected=[1])
    assert len(engine.snapshot()['rows']) == 2
    report = json.loads(Path(invoke('export', kind='json')['value']).read_text(encoding='utf-8-sig'))
    assert report

def test_cancellation_preserves_source_indices_and_pending_rows(monkeypatch):
    from openpyxl import load_workbook
    invoke('import', text='Logradouro;Numero\nRua Oscar Freire;1\nRua Augusta;2\nRua Brasil;3')
    service = engine._service()
    original = service.lookup
    def stop_after_first(street, number, **kwargs):
        result = original(street, number, **kwargs)
        engine.cancel()
        return result
    monkeypatch.setattr(service, 'lookup', stop_after_first)
    monkeypatch.setattr(engine, '_service', lambda **kwargs: service)
    invoke('batch', street_column='Logradouro', number_column='Numero')
    assert len(engine.snapshot()['rows']) == 1
    path = invoke('export')['value']
    wb = load_workbook(path)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    assert ws.cell(3, 1).value == 'Rua Augusta'
    assert 'NÃO PROCESSADO' in ws.cell(3, headers.index('Motivo Status') + 1).value
    wb.close()

def test_settings_fiscais_and_secrets_never_serialized(private_session):
    names = engine.snapshot()['fiscais']
    area = next(iter(names))
    engine.credentials('TEST-SECRET-ONLY', 'TEST-TOKEN-ONLY')
    invoke('settings', settings={'boundary_warning_m': 42}, fiscais={area: 'Fiscal de teste'}, offline=True)
    invoke('coordinate', latitude=0, longitude=0)
    assert engine.snapshot()['settings']['boundary_warning_m'] == 42
    assert engine.snapshot()['fiscais'][area] == 'Fiscal de teste'
    for path in private_session.glob('*.json'):
        assert 'TEST-SECRET-ONLY' not in path.read_text(encoding='utf-8')
        assert 'TEST-TOKEN-ONLY' not in path.read_text(encoding='utf-8')
    assert not json.loads(engine.call('settings', '{"settings":{"boundary_warning_m":-1}}'))['ok']
    assert engine.snapshot()['settings']['boundary_warning_m'] == 42

def test_untrusted_column_names_do_not_change_serialization(private_session):
    value = {'@type': 'anything', '@enum': 'anything', '@tuple': [1], '@dict': 'user column'}
    assert engine._unpack(engine._pack(value)) == value

def test_import_cannot_escape_private_storage():
    response = json.loads(engine.call('import', json.dumps({'path': str(MOBILE.parent / 'Pasta_geojardins.xlsx')})))
    assert not response['ok']
    assert 'privado' in response['error']

def test_spreadsheet_formula_is_exported_as_text(private_session):
    from openpyxl import load_workbook
    source = private_session / 'source.csv'
    source.write_text('Logradouro;Numero;Observacao\nRua Augusta;2;=1+1', encoding='utf-8')
    invoke('import', path=str(source))
    invoke('batch', street_column='Logradouro', number_column='Numero')
    wb = load_workbook(invoke('export')['value'])
    assert wb.active.cell(2, 3).value == "'=1+1"
    assert wb.active.cell(2, 3).data_type == 's'
    wb.close()
