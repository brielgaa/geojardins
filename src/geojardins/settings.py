"""Configurações centralizadas e validadas do motor GeoJardins."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .runtime import resource_path


DEFAULT_SETTINGS_PATH = resource_path("config", "settings.json")


@dataclass(frozen=True)
class Settings:
    boundary_warning_m: float = 30.0
    geocoding_timeout_s: float = 10.0
    geocoding_max_retries: int = 3
    geocoding_retry_base_s: float = 1.0
    geoapify_requests_per_second: float = 5.0
    geoapify_timeout_s: float = 10.0
    geoapify_max_retries: int = 3
    geoapify_bbox_margin_m: float = 1000.0
    geoapify_result_limit: int = 10
    locationiq_requests_per_second: float = 2.0
    locationiq_result_limit: int = 10
    locationiq_viewbox_margin_deg: float = 0.01
    locationiq_bounded: bool = False
    # Limiar explícito da Fase 3A: acima dele dois providers não são
    # escolhidos silenciosamente. Ajustável no settings ou na CLI do lote.
    provider_divergence_m: float = 75.0
    # Coordenadas dentro desta distância, para alturas distintas da mesma via,
    # são tratadas como possível centroide/resultado genérico no lote.
    generic_coordinate_tolerance_m: float = 5.0
    # Fase 3B: níveis documentados de concordância entre resultados de
    # providers. O nível aceitável é menor que divergência: acima dele dois
    # candidatos fortes não promovem automaticamente.
    provider_agreement_strong_m: float = 15.0
    provider_agreement_acceptable_m: float = 50.0
    # Garante uma folga material acima do aviso operacional de 30 m antes de
    # uma promoção baseada em evidência composta.
    safe_distance_from_divider_m: float = 100.0
    street_fuzzy_suggest_threshold: float = 0.88
    street_fuzzy_auto_threshold: float = 0.97
    street_fuzzy_min_margin: float = 0.04


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    if not path.is_file():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Não foi possível ler configurações em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} deve conter um objeto JSON.")
    settings = Settings(**{key: raw[key] for key in Settings.__dataclass_fields__ if key in raw})
    if (
        settings.boundary_warning_m < 0
        or settings.provider_divergence_m < 0
        or settings.generic_coordinate_tolerance_m < 0
        or settings.provider_agreement_strong_m < 0
        or settings.provider_agreement_acceptable_m < 0
        or settings.safe_distance_from_divider_m < 0
    ):
        raise ValueError("Os limiares métricos não podem ser negativos.")
    if (
        settings.geocoding_timeout_s <= 0
        or settings.geoapify_timeout_s <= 0
        or settings.geocoding_retry_base_s < 0
    ):
        raise ValueError("Timeout e base de retry devem ser válidos.")
    if (
        settings.geocoding_max_retries < 0
        or settings.geoapify_max_retries < 0
        or settings.locationiq_requests_per_second <= 0
        or settings.geoapify_requests_per_second <= 0
    ):
        raise ValueError("Retries e requests por segundo devem ser válidos.")
    if not 1 <= settings.locationiq_result_limit <= 50 or not 1 <= settings.geoapify_result_limit <= 50:
        raise ValueError("Os limites de resultados devem estar entre 1 e 50.")
    if settings.locationiq_viewbox_margin_deg < 0 or settings.geoapify_bbox_margin_m < 0:
        raise ValueError("As margens geográficas não podem ser negativas.")
    if settings.provider_agreement_strong_m > settings.provider_agreement_acceptable_m:
        raise ValueError("provider_agreement_strong_m não pode exceder o nível aceitável.")
    if settings.provider_agreement_acceptable_m > settings.provider_divergence_m:
        raise ValueError("provider_agreement_acceptable_m não pode exceder provider_divergence_m.")
    if not (0.0 <= settings.street_fuzzy_suggest_threshold <= settings.street_fuzzy_auto_threshold <= 1.0):
        raise ValueError("Os limiares de fuzzy devem estar entre 0 e 1 e em ordem crescente.")
    if not 0.0 <= settings.street_fuzzy_min_margin <= 1.0:
        raise ValueError("street_fuzzy_min_margin deve estar entre 0 e 1.")
    return settings
