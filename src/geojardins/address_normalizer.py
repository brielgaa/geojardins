"""Normalização conservadora de logradouros e parser de cruzamentos.

Somente expansões determinísticas e aliases explicitamente cadastrados alteram
a consulta enviada aos providers. Sugestões ortográficas nunca são aplicadas
automaticamente: permanecem auditáveis para conferência humana.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


class AddressValidationError(ValueError):
    """Logradouro ou altura não podem formar uma consulta segura."""


class QueryType(str, Enum):
    ADDRESS = "ENDERECO"
    INTERSECTION = "CRUZAMENTO"


DEFAULT_ALIASES_PATH = Path("data/config/address_aliases.json")
NORMALIZER_VERSION = "3b-1"
_NUMBER_PATTERN = re.compile(r"^\s*(\d+)(?:\.0+)?\s*$")
_RETURNED_RANGE_PATTERN = re.compile(r"^\s*(\d+)\s*(?:-|–|A|AO|ATE|ATÉ)\s*(\d+)\s*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Z0-9]+")
_INTERSECTION = re.compile(r"\s+(?:X|ESQ\.?|ESQUINA|COM)\s+", re.IGNORECASE)
_STREET_PREFIXES = {
    "RUA", "AVENIDA", "ALAMEDA", "TRAVESSA", "ESTRADA", "RODOVIA",
    "PRACA", "LARGO", "VIELA", "VIADUTO", "TUNEL",
}
# Títulos removidos somente depois de as abreviações serem expandidas pela mesma
# tabela configurável usada na entrada e no retorno dos providers.
_STREET_TITLES = {
    "ALMIRANTE", "BARAO", "BRIGADEIRO", "CAPITAO", "CARDEAL", "CONDE",
    "CONEGO", "CONSELHEIRO", "CORONEL", "DESEMBARGADOR", "DOUTOR", "ENGENHEIRO",
    "GENERAL", "JORNALISTA", "MAESTRO", "MINISTRO", "PADRE", "PREFEITO",
    "PROFESSOR", "SENADOR", "TENENTE",
}


@dataclass(frozen=True)
class AliasRules:
    token_aliases: Mapping[str, str]
    full_aliases: Mapping[str, str]
    suggested_corrections: Mapping[str, str]
    contextual_title_aliases: Mapping[str, str] = field(default_factory=dict)
    contextual_phrase_aliases: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedAddress:
    original: str
    normalized: str
    query_type: QueryType
    street_1: str
    street_2: str | None = None
    applied_aliases: tuple[str, ...] = ()
    suggested_correction: str | None = None
    suggestion_type: str | None = None
    suggestion_confidence: str | None = None
    suggestion_decision: str | None = None

    @property
    def was_normalized(self) -> bool:
        return normalize_text(self.original).upper() != normalize_text(self.normalized).upper()

    @property
    def cache_street(self) -> str:
        if self.query_type is QueryType.INTERSECTION:
            return f"{self.street_1}|{self.street_2 or ''}"
        return self.street_1


@dataclass(frozen=True)
class NumberCompatibility:
    requested: str | None
    returned: str | None
    compatible: bool
    available: bool
    evidence: str


def _ascii_upper(value: Any) -> str:
    text = _WHITESPACE.sub(" ", str(value or "").strip())
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).upper()


def _alias_key(value: Any) -> str:
    return " ".join(_TOKEN.findall(_ascii_upper(value)))


@lru_cache(maxsize=None)
def load_alias_rules(path: Path = DEFAULT_ALIASES_PATH) -> AliasRules:
    """Lê aliases configuráveis, sem manter uma lista espalhada pelo código."""

    if not path.is_file():
        return AliasRules({}, {}, {}, {})
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AddressValidationError(f"Não foi possível ler aliases de endereço em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AddressValidationError(f"{path} deve conter um objeto JSON.")

    def mapping(name: str) -> dict[str, str]:
        value = raw.get(name, {})
        if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
            raise AddressValidationError(f"{name} em {path} deve conter pares texto: texto.")
        return {_alias_key(key): _ascii_upper(item) for key, item in value.items() if _alias_key(key)}

    return AliasRules(
        mapping("token_aliases"),
        mapping("full_aliases"),
        mapping("suggested_corrections"),
        mapping("contextual_title_aliases"),
        mapping("contextual_phrase_aliases"),
    )


def normalize_house_number(value: Any) -> str:
    """Converte 1250, 1250.0, '01250' e '1250 ' em '1250'."""

    if isinstance(value, bool) or value is None:
        raise AddressValidationError("Altura deve ser numérica.")
    if isinstance(value, int):
        if value < 0:
            raise AddressValidationError("Altura deve ser numérica não negativa.")
        return str(value)
    if isinstance(value, float):
        if not value.is_integer() or value < 0:
            raise AddressValidationError("Altura deve ser um número inteiro.")
        return str(int(value))
    match = _NUMBER_PATTERN.fullmatch(str(value))
    if not match:
        raise AddressValidationError("Altura deve ser um número inteiro, como 1250.")
    return str(int(match.group(1)))


def normalize_optional_house_number(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return normalize_house_number(value)


def compare_house_numbers(requested: str | None, returned: Any) -> NumberCompatibility:
    """Aceita igualdade e intervalo explícito; diferenças grandes continuam suspeitas."""

    raw_returned = str(returned).strip() if returned is not None else ""
    if not requested:
        return NumberCompatibility(None, raw_returned or None, True, bool(raw_returned), "cruzamento sem altura obrigatória")
    if not raw_returned:
        return NumberCompatibility(requested, None, False, False, "provider não retornou altura")
    try:
        normalized_returned = normalize_house_number(raw_returned)
    except AddressValidationError:
        range_match = _RETURNED_RANGE_PATTERN.fullmatch(_ascii_upper(raw_returned))
        if range_match:
            lower, upper = sorted((int(range_match.group(1)), int(range_match.group(2))))
            return NumberCompatibility(requested, raw_returned, lower <= int(requested) <= upper, True, "faixa retornada pelo provider")
        return NumberCompatibility(requested, raw_returned, False, False, "altura retornada não interpretável")
    return NumberCompatibility(requested, normalized_returned, normalized_returned == requested, True, "altura exata")


def normalize_text(value: Any) -> str:
    """Normaliza para comparação, sem alterar o valor exibido/original."""

    return _ascii_upper(value).casefold()


def validate_street(value: Any) -> str:
    street = _WHITESPACE.sub(" ", str(value or "").strip())
    if not street:
        raise AddressValidationError("Logradouro é obrigatório.")
    return street


_CONTEXTUAL_PHRASE_PRECEDERS = {"DE", "DA", "DO", "DAS", "DOS"}


def _expand_contextual_phrases(
    tokens: list[str],
    rules: AliasRules,
) -> tuple[list[str], tuple[str, ...]]:
    """Expande expressões explícitas somente após conectivo seguro.

    Hoje cobre ``E SANTO`` em ``DO E SANTO``. A regra não trata ``E``
    isoladamente como abreviação, evitando uma substituição global arriscada.
    """

    phrases = {
        tuple(_TOKEN.findall(source)): _TOKEN.findall(target)
        for source, target in rules.contextual_phrase_aliases.items()
    }
    if not phrases:
        return tokens, ()
    expanded: list[str] = []
    applied: list[str] = []
    index = 0
    while index < len(tokens):
        matched = next(
            (
                (source, target)
                for source, target in phrases.items()
                if tokens[index:index + len(source)] == list(source)
                and index > 0
                and tokens[index - 1] in _CONTEXTUAL_PHRASE_PRECEDERS
            ),
            None,
        )
        if matched is None:
            expanded.append(tokens[index])
            index += 1
            continue
        source, target = matched
        expanded.extend(target)
        applied.append(f"frase_contextual:{' '.join(source)}")
        index += len(source)
    return expanded, tuple(applied)


def _normalize_segment(value: str, rules: AliasRules) -> tuple[str, tuple[str, ...], str | None]:
    clean = validate_street(value)
    full_key = _alias_key(clean)
    if full_key in rules.full_aliases:
        return rules.full_aliases[full_key], (f"frase:{full_key}",), None
    tokens, contextual_phrases = _expand_contextual_phrases(
        _TOKEN.findall(_ascii_upper(clean)), rules
    )
    normalized: list[str] = []
    applied: list[str] = list(contextual_phrases)
    has_street_prefix = False
    for index, token in enumerate(tokens):
        replacement = rules.token_aliases.get(token)
        # Títulos como CAP e BRIG só são expandidos imediatamente depois do
        # tipo de via. Assim, a palavra não sofre um replace global ingênuo no
        # meio de um nome próprio.
        title = rules.contextual_title_aliases.get(token)
        if title is not None and index > 0 and normalized and normalized[-1] in _STREET_PREFIXES:
            replacement = title
        elif token in rules.contextual_title_aliases:
            replacement = None
        if replacement is not None and (index == 0 or has_street_prefix):
            normalized.append(replacement)
            applied.append(f"token:{token}")
        else:
            normalized.append(token)
        if normalized[-1] in _STREET_PREFIXES:
            has_street_prefix = True
    result = " ".join(normalized)
    suggested = rules.suggested_corrections.get(_alias_key(result))
    return result, tuple(applied), suggested


def normalize_address(value: Any, *, rules: AliasRules | None = None) -> NormalizedAddress:
    """Normaliza endereço ou cruza duas vias sem autocorreção por distância."""

    original = validate_street(value)
    effective_rules = rules if rules is not None else load_alias_rules()
    parts = _INTERSECTION.split(original, maxsplit=1)
    if len(parts) == 2 and all(_alias_key(part) for part in parts):
        first, aliases_first, suggestion_first = _normalize_segment(parts[0], effective_rules)
        second, aliases_second, suggestion_second = _normalize_segment(parts[1], effective_rules)
        suggestion = suggestion_first or suggestion_second
        return NormalizedAddress(
            original, f"{first} X {second}", QueryType.INTERSECTION, first, second,
            aliases_first + aliases_second, suggestion,
            "CONFIGURADA" if suggestion else None,
            "ALTA" if suggestion else None,
        )
    normalized, aliases, suggestion = _normalize_segment(original, effective_rules)
    return NormalizedAddress(
        original, normalized, QueryType.ADDRESS, normalized, None, aliases, suggestion,
        "CONFIGURADA" if suggestion else None,
        "ALTA" if suggestion else None,
    )


def address_cache_key(street: Any, house_number: Any, *, rules: AliasRules | None = None) -> str:
    normalized = normalize_address(street, rules=rules)
    number = normalize_optional_house_number(house_number)
    if normalized.query_type is QueryType.ADDRESS and number is None:
        raise AddressValidationError("Altura deve ser numérica.")
    return f"{NORMALIZER_VERSION}|{normalized.query_type.value}|{normalized.cache_street}|{number or '-'}"


def header_key(value: Any) -> str:
    return normalize_text(value).replace("º", "o").replace("°", "o")


def canonical_street_tokens(value: Any, *, rules: AliasRules | None = None) -> tuple[str, ...]:
    """Produz os tokens semânticos compartilhados por entrada e providers.

    Tipos de via e títulos conhecidos não distinguem o logradouro. Todos os
    demais tokens, inclusive conectivos e sua ordem, continuam significativos.
    Isso impede que um nome menor seja aceito como prefixo de outro.
    """

    effective_rules = rules if rules is not None else load_alias_rules()
    tokens, _ = _expand_contextual_phrases(
        _TOKEN.findall(_ascii_upper(value)), effective_rules
    )
    canonical: list[str] = []
    previous_expanded = ""
    for token in tokens:
        expanded = effective_rules.token_aliases.get(token, token)
        title = effective_rules.contextual_title_aliases.get(token)
        if title is not None:
            expanded = title if previous_expanded in _STREET_PREFIXES else token
        # As expansões são valores canônicos simples, mas preservamos todos os
        # tokens se uma configuração futura contiver mais de uma palavra.
        for item in _TOKEN.findall(expanded):
            if item not in _STREET_PREFIXES and item not in _STREET_TITLES:
                canonical.append(item.casefold())
            previous_expanded = item
    return tuple(canonical)


def street_tokens_compatible(query_street: str, candidate_road: str | None) -> bool:
    """Exige igualdade ordenada dos tokens significativos de duas vias."""

    query_tokens = canonical_street_tokens(query_street)
    candidate_tokens = canonical_street_tokens(candidate_road)
    return bool(query_tokens and candidate_tokens and query_tokens == candidate_tokens)


def street_similarity(
    query_street: str,
    candidate_road: str | None,
    *,
    allow_containment: bool = False,
) -> float:
    """Similaridade para ordenação; compatibilidade regular exige igualdade.

    ``allow_containment`` existe exclusivamente para procurar cada via dentro
    do ``display_name`` de uma consulta de cruzamento. Ele nunca é usado para
    validar um endereço comum.
    """

    query_tokens = canonical_street_tokens(query_street)
    candidate_tokens = canonical_street_tokens(candidate_road)
    if not query_tokens or not candidate_tokens:
        return 0.0
    if query_tokens == candidate_tokens:
        return 1.0
    if allow_containment and len(query_tokens) <= len(candidate_tokens) and any(
        candidate_tokens[index:index + len(query_tokens)] == query_tokens
        for index in range(len(candidate_tokens) - len(query_tokens) + 1)
    ):
        return 1.0
    # Mantém um sinal de ordenação, mas sempre abaixo do limiar de aceitação.
    overlap = len(set(query_tokens) & set(candidate_tokens)) / len(set(query_tokens) | set(candidate_tokens))
    return min(0.49, overlap * 0.49)
