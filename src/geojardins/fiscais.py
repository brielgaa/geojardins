"""Resolução de fiscal por área, sem nomes implícitos."""

from __future__ import annotations

import json
from pathlib import Path

from .runtime import resource_path


DEFAULT_FISCAIS_PATH = resource_path("config", "fiscais.json")
NOT_CONFIGURED = "NÃO CONFIGURADO"


class FiscalResolver:
    def __init__(self, assignments: dict[str, str]) -> None:
        self.assignments = dict(assignments)

    @classmethod
    def from_path(cls, path: Path = DEFAULT_FISCAIS_PATH) -> "FiscalResolver":
        if not path.is_file():
            return cls({})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Não foi possível ler fiscais em {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path} deve conter um objeto área → fiscal.")
        return cls({str(area): str(name).strip() for area, name in raw.items() if name is not None})

    def for_area(self, area: str | None) -> str:
        if not area:
            return NOT_CONFIGURED
        return self.assignments.get(area, "") or NOT_CONFIGURED
