"""Local Android facade over the unchanged GeoJardins operational engine.

No server, fake data, credentials on disk, or alternate territorial rules.
All public operations are serialized by the Kotlin repository, except cancel().
"""
from __future__ import annotations

from collections import Counter
from dataclasses import fields, is_dataclass, asdict
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import zipfile
import uuid

_cancel = threading.Event()
_state = {}
_prefs = {}
_credentials = {}
_registry = {}
_root = None
_base = None

def _pack(value):
    if isinstance(value, Enum):
        return {'@enum': type(value).__name__, 'value': value.value}
    if is_dataclass(value):
        return {'@type': type(value).__name__, 'fields': {f.name: _pack(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, Counter):
        return {'@counter': dict(value)}
    if isinstance(value, tuple):
        return {'@tuple': [_pack(v) for v in value]}
    if isinstance(value, (datetime, date)):
        return {'@date': value.isoformat(), 'datetime': isinstance(value, datetime)}
    if isinstance(value, dict):
        return {'@dict': [[str(k), _pack(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [_pack(v) for v in value]
    return value

def _unpack(value):
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    if not isinstance(value, dict):
        return value
    if '@type' in value:
        return _registry[value['@type']](**{k: _unpack(v) for k, v in value['fields'].items()})
    if '@enum' in value:
        return _registry[value['@enum']](value['value'])
    if '@counter' in value:
        return Counter(value['@counter'])
    if '@dict' in value:
        return {k: _unpack(v) for k, v in value['@dict']}
    if '@tuple' in value:
        return tuple(_unpack(v) for v in value['@tuple'])
    if '@date' in value:
        return (datetime if value['datetime'] else date).fromisoformat(value['@date'])
    return {k: _unpack(v) for k, v in value.items()}

def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)

def _save():
    _write(_root / 'session.json', _pack(_state))

def initialize(files_dir, base_dir):
    global _root, _base, _state, _prefs
    _root, _base = Path(files_dir), Path(base_dir)
    _root.mkdir(parents=True, exist_ok=True)
    os.environ['GEOJARDINS_ANDROID_FILES'] = str(_root)
    os.environ['GEOJARDINS_ANDROID_BASE'] = str(_base)
    from geojardins import address_lookup, address_normalizer, batch_lookup, classification, confidence, candidate_compatibility
    from geojardins.geocoding import models
    for module in (address_lookup, address_normalizer, batch_lookup, classification, confidence, candidate_compatibility, models):
        for name, value in vars(module).items():
            if isinstance(value, type) and (is_dataclass(value) or issubclass(value, Enum)):
                _registry[name] = value
    _state = {'schema': 1, 'counts': Counter()}
    if (_root / 'session.json').exists():
        try:
            _state = _unpack(json.loads((_root / 'session.json').read_text(encoding='utf-8')))
        except (ValueError, KeyError, TypeError):
            # Keep damaged state for local recovery; never silently overwrite it.
            (_root / 'session.json').replace(_root / 'session-recovery.json')
            _state['notice'] = 'Sessão anterior ilegível; cópia preservada para recuperação.'
    if _state.pop('running', False):
        _state['notice'] = 'Lote interrompido pelo sistema. A fonte foi preservada; processe novamente para retomar usando o cache.'
    _prefs = json.loads((_root / 'preferences.json').read_text(encoding='utf-8')) if (_root / 'preferences.json').exists() else {}
    return snapshot()

def credentials(geoapify='', locationiq=''):
    _credentials.update(geoapify=geoapify, locationiq=locationiq)

def cancel():
    _cancel.set()

def _settings():
    from geojardins.settings import load_settings
    return load_settings(_root / 'settings.json') if (_root / 'settings.json').exists() else load_settings()

def _fiscais():
    from geojardins.fiscais import FiscalResolver
    assignments = FiscalResolver.from_path().assignments
    assignments.update({k: v for k, v in _prefs.get('fiscais', {}).items() if k in assignments})
    return assignments

def _service(use_cache=True):
    from geojardins.address_lookup import AddressLookupService, build_geocoding_service
    from geojardins.classification import GeographicClassifier
    from geojardins.fiscais import FiscalResolver
    from geojardins.geocoding.cache import PersistentGeocodingCache
    from geojardins.geocoding.base import GeocodingTemporaryError
    from geojardins.geocoding.models import GeocodingDiagnostics
    cache_path = _root / 'geocoding_cache.sqlite'
    if _prefs.get('offline', False):
        cache = PersistentGeocodingCache(cache_path)
        class OfflineGeocoder:
            request_count = 0
            last_diagnostics = GeocodingDiagnostics()
            def geocode(self, query):
                found = []
                cached = False
                for provider in ('geoapify', 'locationiq'):
                    values = cache.get(provider, query)
                    if values is not None:
                        cached = True
                        found.extend(values)
                if not cached:
                    raise GeocodingTemporaryError('Endereço ausente do cache local. Desative o modo offline quando houver conexão.')
                return found
        return AddressLookupService(OfflineGeocoder(), GeographicClassifier.from_paths(), FiscalResolver(_fiscais()), _settings(), cache=cache)
    if not _credentials.get('geoapify'):
        raise ValueError('Informe a chave Geoapify em Ajustes ou ative o modo offline para usar o cache.')
    return build_geocoding_service(settings=_settings(), fiscal_assignments=_fiscais(),
        geoapify_api_key=_credentials.get('geoapify', ''), locationiq_token=_credentials.get('locationiq', ''),
        cache_path=cache_path, use_cache=use_cache)

def _result(result):
    values = result.export_values()
    values['evidencias'] = {
        'positivas': list(result.confidence.positive_evidence) if result.confidence else [],
        'negativas': list(result.confidence.negative_evidence) if result.confidence else [],
        'bloqueios': list(result.confidence.promotion_blockers) if result.confidence else [],
        'candidatos': [dict(provider=a.candidate.provider, latitude=a.candidate.latitude,
            longitude=a.candidate.longitude, endereco=a.candidate.display_name, plausivel=a.plausible,
            motivos=list(a.reasons)) for a in result.assessments],
    }
    return values

def snapshot():
    source = _state.get('source')
    summary = _state.get('summary')
    rows = _state.get('results', ())
    return dict(settings=asdict(_settings()), fiscais=_fiscais(), offline=_prefs.get('offline', False),
        geoapify=bool(_credentials.get('geoapify')), locationiq=bool(_credentials.get('locationiq')),
        counts=dict(_state.get('counts', {})), notice=_state.get('notice', ''),
        last=_result(_state['last']) if _state.get('last') else _state.get('coordinate'),
        headers=list(source.headers) if source else [], total=len(source.rows) if source else 0,
        preview=[{k: str(v or '') for k, v in r.items()} for r in source.rows[:3]] if source else [],
        street_column=_state.get('street_column', ''), number_column=_state.get('number_column', ''),
        source_name=_state.get('source_name', ''), source_id=_state.get('source_id', ''),
        summary=summary.to_report() if summary else {},
        rows=[dict(index=r.source_index, street=r.result.original_street,
            number=str(r.result.original_house_number or ''), status=r.result.status.value,
            area=r.result.confirmed_area or '', candidate_area=r.result.candidate_area or '') for r in rows],
        export_ready=bool(rows), map_dir=str(_base / 'map'))

def lookup(street, number):
    result = _service().lookup(street, number)
    _state['last'] = result
    _state['counts'][result.status.value] += 1
    _save()

def coordinate(latitude, longitude):
    from geojardins.classification import GeographicClassifier
    from geojardins.maps_links import google_maps_link
    latitude, longitude = float(latitude), float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError('Coordenadas devem ser finitas.')
    geo = GeographicClassifier.from_paths().classify(latitude, longitude)
    status = ('FORA_DA_UGR' if not geo.inside_ugr else 'CONFERIR' if geo.geometric_ambiguity
              else 'PROXIMO_DA_DIVISA' if geo.distance_to_boundary_m <= _settings().boundary_warning_m else 'CONFIRMADO')
    _state['last'] = None
    _state['coordinate'] = {'Status': status, 'Área': geo.area if status in ('CONFIRMADO', 'PROXIMO_DA_DIVISA') else None,
        'Área Candidata': geo.area, 'Áreas Candidatas': '; '.join(geo.areas),
        'Fiscal': _fiscais().get(geo.area, 'NÃO CONFIGURADO') if status in ('CONFIRMADO', 'PROXIMO_DA_DIVISA') else None,
        'Latitude': latitude, 'Longitude': longitude, 'Distância Divisa (m)': geo.distance_to_boundary_m,
        'Link Google Maps': google_maps_link(latitude, longitude),
        'Motivo Status': 'Consulta territorial da coordenada informada. Não valida a precisão de um endereço.'}
    _state['counts'][status] += 1
    _save()

def _check_input(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('Arquivo excede 20 MB. Divida a planilha para uso em campo.')
    if path.suffix.lower() == '.xlsx':
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 80 * 1024 * 1024:
                raise ValueError('Planilha expandida excede 80 MB.')

def import_source(path='', text=''):
    from geojardins.batch_lookup import read_input, read_paste_input, resolve_batch_columns, BatchInputError
    if path:
        candidate = _private_path(path)
        _check_input(candidate)
        source = read_input(candidate)
    else:
        source = read_paste_input(text)
    if not source.rows or len(source.rows) > 5000:
        raise ValueError('Importe entre 1 e 5.000 linhas por lote.')
    _state.update(source=source, results=(), source_name=Path(path).name if path else 'Dados colados',
                  source_id=uuid.uuid4().hex, summary=None, input_path=path or None, street_column='', number_column='')
    try:
        mapping = resolve_batch_columns(source)
        _state.update(street_column=mapping.street_column, number_column=mapping.number_column)
    except BatchInputError:
        _state['notice'] = 'Selecione as colunas de logradouro e número antes de processar.'
    _save()

def run_batch(street_column, number_column, selected=None, progress=None, use_cache=True):
    from geojardins.batch_lookup import BatchProcessor, InputRows, resolve_batch_columns, _summary_for_results
    source = _state.get('source')
    if source is None:
        raise ValueError('Importe um arquivo ou cole dados primeiro.')
    mapping = resolve_batch_columns(source, street_column=street_column, number_column=number_column)
    service = _service(use_cache=use_cache)
    indices = sorted(set(int(i) for i in selected)) if selected is not None else list(range(len(source.rows)))
    if not indices or any(i < 0 or i >= len(source.rows) for i in indices):
        raise ValueError('Selecione linhas válidas para reprocessar.')
    _cancel.clear()
    _state.update(running=True, street_column=mapping.street_column, number_column=mapping.number_column)
    _save()
    subset = InputRows(source.headers, tuple(source.rows[i] for i in indices), source.has_header)
    try:
        fresh, summary = BatchProcessor(service, progress=(lambda message: progress.update(message)) if progress else None).process(
            subset, street_column=mapping.street_column, number_column=mapping.number_column,
            row_indices=indices, cancel_event=_cancel)
        # Merge by source position, including partial/cancelled runs. Original rows
        # never shift to a different address in an XLSX export.
        merged = {r.source_index: r for r in _state.get('results', ())} if selected is not None else {}
        merged.update({r.source_index: r for r in fresh})
        if selected is not None:
            from dataclasses import replace
            from geojardins.address_lookup import LookupFlag
            keys = {service.query_key(r.result.original_street, r.result.original_house_number): r.result
                    for r in merged.values() if r.result.candidate is not None}
            generic = BatchProcessor(service, progress=None)._generic_coordinate_keys(keys)
            for i, r in list(merged.items()):
                if r.result.candidate is not None and service.query_key(r.result.original_street, r.result.original_house_number) in generic:
                    merged[i] = replace(r, result=r.result.with_flags(LookupFlag.COORDENADA_GENERICA,
                        force_conferir=True, reason='Coordenada genérica no conjunto do lote após reprocessamento; conferir.'))
        _state['results'] = tuple(merged[i] for i in sorted(merged))
        if selected is not None:
            summary = _summary_for_results(_state['results'], summary, total_rows=len(source.rows))
        _state['summary'] = summary
        _state['counts'].update(r.result.status.value for r in fresh)
        _state['notice'] = ('Lote cancelado: resultados parciais preservados.' if _cancel.is_set() else 'Lote concluído.')
    finally:
        _state['running'] = False
        _save()

def _private_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(_root.resolve()):
        raise ValueError('Arquivo fora do armazenamento privado.')
    return path

def export(kind='xlsx', baseline=''):
    from geojardins.batch_lookup import InputRows, write_output, write_batch_report
    rows, summary, source = _state.get('results', ()), _state.get('summary'), _state.get('source')
    if not rows or summary is None or source is None:
        raise ValueError('Não há resultados de lote para exportar.')
    target = _root / ('resultado.json' if kind == 'json' else 'resultado.xlsx')
    if kind == 'json':
        write_batch_report(summary, target, compare_baseline=_private_path(baseline) if baseline else None)
    else:
        # Complete original source, with explicit pending rows after cancellation.
        # Passing no input workbook avoids accidental workbook formula execution.
        # All source columns and original order are retained.
        from geojardins.address_lookup import AddressLookupResult, LookupStatus
        from geojardins.batch_lookup import BatchRowResult
        by_index = {r.source_index: r for r in rows}
        complete = []
        for i, row in enumerate(source.rows):
            item = by_index.get(i)
            if item is None:
                item = BatchRowResult(row, AddressLookupResult(str(row.get(_state['street_column'], '')),
                    row.get(_state['number_column']), None, LookupStatus.CONFERIR,
                    'NÃO PROCESSADO: lote interrompido; não há classificação territorial.'), i)
            complete.append(item)
        write_output(None, source, complete, target, summary=summary, source_label=_state.get('source_name', 'Android'))
        # Spreadsheet formula injection is not a legitimate field value.
        from openpyxl import load_workbook
        wb = load_workbook(target)
        for sheet in wb:
            for row in sheet:
                for cell in row:
                    if cell.data_type == 'f':
                        cell.data_type = 's'
        wb.save(target)
        wb.close()
    return str(target)

def save_settings(values):
    from geojardins.settings import load_settings
    candidate = {**asdict(_settings()), **values.get('settings', {})}
    for key, val in candidate.items():
        if isinstance(val, (int, float)) and not math.isfinite(val):
            raise ValueError('Parâmetros numéricos devem ser finitos.')
    temp = _root / 'validate-settings.json'
    _write(temp, candidate)
    try:
        validated = load_settings(temp)
    finally:
        temp.unlink(missing_ok=True)
    _write(_root / 'settings.json', asdict(validated))
    if 'fiscais' in values:
        known = _fiscais()
        _prefs['fiscais'] = {k: str(v).strip() for k, v in values['fiscais'].items() if k in known}
    if 'offline' in values:
        _prefs['offline'] = bool(values['offline'])
    _write(_root / 'preferences.json', _prefs)

def clear_cache():
    from geojardins.geocoding.cache import PersistentGeocodingCache
    PersistentGeocodingCache(_root / 'geocoding_cache.sqlite').clear()

def call(operation, payload='{}', progress=None):
    """JSON envelope. Only the fixed operation allowlist is callable from Kotlin."""
    args = json.loads(payload)
    try:
        value = None
        if operation == 'snapshot':
            pass
        elif operation == 'lookup':
            lookup(args['street'], args.get('number', ''))
        elif operation == 'coordinate':
            coordinate(args['latitude'], args['longitude'])
        elif operation == 'import':
            import_source(args.get('path', ''), args.get('text', ''))
        elif operation == 'batch':
            run_batch(args['street_column'], args['number_column'], args.get('selected'), progress, args.get('use_cache', True))
        elif operation == 'detail':
            value = _result(next(r.result for r in _state.get('results', ()) if r.source_index == args['index']))
        elif operation == 'export':
            value = export(args.get('kind', 'xlsx'), args.get('baseline', ''))
        elif operation == 'settings':
            save_settings(args)
        elif operation == 'clear_cache':
            clear_cache()
        elif operation == 'import_base':
            from data_bundle import install
            install(_private_path(args['path']), _base)
            clear_cache()
            _state['notice'] = 'Base local atualizada e cache limpo. Resultados anteriores são históricos; reprocese para usar a nova base.'
            _save()
        else:
            raise ValueError('Operação desconhecida.')
        return json.dumps(dict(ok=True, data=snapshot(), value=value), ensure_ascii=False, default=str, allow_nan=False)
    except Exception as exc:
        # Provider exceptions are sanitized in the original engine; redact also
        # here as a second barrier. Do not send tracebacks or URL parameters to UI.
        message = str(exc)
        for secret in _credentials.values():
            if secret:
                message = message.replace(secret, '[credencial removida]')
        return json.dumps(dict(ok=False, error=message or type(exc).__name__), ensure_ascii=False)
