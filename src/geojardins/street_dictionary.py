"""Dicionário local e conservador de logradouros da UGR Jardins.

O dicionário não é uma base de São Paulo: ele contém apenas rótulos extraídos
do PDF oficial da UGR e filtrados pelo gerador deste módulo.  Ele serve para
sugerir revisão humana; jamais substitui silenciosamente o texto informado.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .address_normalizer import canonical_street_tokens, normalize_text


DEFAULT_STREET_DICTIONARY_PATH = Path("data/processed/ugr_streets.json")
_STREET_PREFIXES = {
    "rua", "avenida", "alameda", "travessa", "estrada", "rodovia", "praca",
    "largo", "viela", "viaduto",
}


def street_core(value: str) -> str:
    """Chave de comparação que não confunde o tipo de via com o nome."""

    return " ".join(canonical_street_tokens(value))


@dataclass(frozen=True)
class StreetEntry:
    canonical: str
    comparison_key: str
    source: str
    page: int | None = None


@dataclass(frozen=True)
class StreetSuggestion:
    decision: str  # SUGGEST, AUTO_SAFE ou NONE; AUTO_SAFE continua auditável.
    canonical: str | None = None
    score: float | None = None
    confidence: str | None = None
    reason: str | None = None


class StreetDictionary:
    def __init__(self, entries: Iterable[StreetEntry] = ()) -> None:
        by_key: dict[str, StreetEntry] = {}
        for entry in entries:
            key = street_core(entry.comparison_key or entry.canonical)
            if key:
                by_key.setdefault(key, StreetEntry(entry.canonical, key, entry.source, entry.page))
        self._by_key = by_key

    @classmethod
    def from_path(cls, path: Path = DEFAULT_STREET_DICTIONARY_PATH) -> "StreetDictionary":
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Não foi possível ler dicionário local de ruas em {path}: {exc}") from exc
        values = raw.get("streets", []) if isinstance(raw, dict) else []
        if not isinstance(values, list):
            raise ValueError(f"{path} deve conter a lista streets.")
        entries: list[StreetEntry] = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("canonical"), str):
                continue
            entries.append(
                StreetEntry(
                    canonical=value["canonical"],
                    comparison_key=str(value.get("comparison_key") or value["canonical"]),
                    source=str(value.get("source") or "unknown"),
                    page=int(value["page"]) if isinstance(value.get("page"), int) else None,
                )
            )
        return cls(entries)

    @property
    def entries(self) -> tuple[StreetEntry, ...]:
        return tuple(sorted(self._by_key.values(), key=lambda entry: entry.comparison_key))

    def contains(self, street: str) -> bool:
        return street_core(street) in self._by_key

    def suggest(
        self,
        street: str,
        *,
        suggest_threshold: float,
        auto_threshold: float,
        min_margin: float,
    ) -> StreetSuggestion:
        """Sugere somente quando há candidato local claro e conservador.

        A margem para o segundo colocado impede que uma grafia parecida em uma
        base grande seja tratada como correção inequívoca.  O resultado
        ``AUTO_SAFE`` é informativo: a aplicação ainda preserva o original e
        não o envia corrigido ao provider sem ação humana explícita.
        """

        query = street_core(street)
        if not query or query in self._by_key:
            return StreetSuggestion("NONE")
        query_tokens = set(query.split())

        def swap_typo_score(left: str, right: str) -> float | None:
            """Reconhece só uma troca clara de caracteres, não anagramas livres."""

            if len(left) != len(right) or len(left) < 5 or Counter(left) != Counter(right):
                return None
            mismatches = [index for index, (first, second) in enumerate(zip(left, right)) if first != second]
            if len(mismatches) != 2:
                return None
            first, second = mismatches
            return 0.93 if left[first] == right[second] and left[second] == right[first] else None

        scored: list[tuple[float, StreetEntry]] = []
        for key, entry in self._by_key.items():
            if not key or query[0] != key[0]:
                continue
            ratio = SequenceMatcher(None, query, key).ratio()
            token_overlap = len(query_tokens & set(key.split())) / len(query_tokens | set(key.split()))
            # A grafia sustenta a maior parte da evidência; tokens protegem
            # nomes compostos contra aproximações por uma palavra genérica.
            score = max(ratio, 0.75 * ratio + 0.25 * token_overlap)
            if len(query_tokens) == 1:
                score = max(score, swap_typo_score(query, key) or 0.0)
            if score >= suggest_threshold:
                scored.append((score, entry))
        if not scored:
            return StreetSuggestion("NONE")
        scored.sort(key=lambda item: (-item[0], item[1].comparison_key))
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < suggest_threshold or best_score - second_score < min_margin:
            return StreetSuggestion("NONE")
        prefix = normalize_text(street).split()[:1]
        canonical = f"{prefix[0].upper()} {best.canonical}" if prefix and prefix[0] in _STREET_PREFIXES else best.canonical
        margin = best_score - second_score
        auto_safe_blockers: list[str] = []
        if best_score < auto_threshold:
            auto_safe_blockers.append(
                f"melhor score {best_score:.3f} abaixo de {auto_threshold:.3f}"
            )
        if margin < 2 * min_margin:
            auto_safe_blockers.append(
                f"margem {margin:.3f} abaixo de {2 * min_margin:.3f}"
            )
        decision = "AUTO_SAFE" if not auto_safe_blockers else "SUGGEST"
        audit = "AUTO_SAFE elegível" if decision == "AUTO_SAFE" else "AUTO_SAFE bloqueado: " + "; ".join(auto_safe_blockers)
        return StreetSuggestion(
            decision,
            canonical=canonical,
            score=round(best_score, 3),
            confidence="ALTA" if decision == "AUTO_SAFE" else "MEDIA",
            reason=(
                f"candidato local do PDF; melhor {best_score:.3f}; segundo {second_score:.3f}; "
                f"margem {margin:.3f}; {audit}"
            ),
        )


def dictionary_payload(entries: Iterable[StreetEntry], *, source: str, rejected_labels: Iterable[str]) -> dict[str, Any]:
    ordered = sorted({entry.comparison_key: entry for entry in entries}.values(), key=lambda entry: entry.comparison_key)
    rejected = tuple(sorted(set(rejected_labels)))
    return {
        "schema_version": "3b-1",
        "source": source,
        "method": "rótulos textuais do PDF filtrados por regras explícitas; não há correção automática",
        "street_count": len(ordered),
        "rejected_label_count": len(rejected),
        "rejected_labels": list(rejected),
        "streets": [
            {
                "canonical": entry.canonical,
                "comparison_key": entry.comparison_key,
                "source": entry.source,
                "page": entry.page,
            }
            for entry in ordered
        ],
    }
