"""Gera o dicionário local de ruas a partir dos rótulos do PDF da UGR."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

from .street_dictionary import StreetEntry, dictionary_payload, street_core


DEFAULT_SOURCE = Path("data/raw/ÁREA UGR JARDINS.pdf")
DEFAULT_OUTPUT = Path("data/processed/ugr_streets.json")
_SPACE = re.compile(r"\s+")
_EXACTLY_REJECTED = {
    "a", "b", "c", "i", "j", "l", "o", "sem denominacao", "projetada v mna",
    "p indl tomas e fun", "original",
}


def _clean_label(value: str) -> str:
    text = _SPACE.sub(" ", value.strip())
    # O PDF contém anotações de orientação como "A AV ..."; elas não fazem
    # parte do logradouro e são removidas somente nessa posição conhecida.
    return re.sub(r"^(?:A\s+)+(?:AV|R)\s+", "", text, flags=re.IGNORECASE)


def filter_pdf_labels(labels: Iterable[tuple[int, str]]) -> tuple[list[StreetEntry], list[str]]:
    accepted: dict[str, StreetEntry] = {}
    rejected: list[str] = []
    for page, raw in labels:
        canonical = _clean_label(raw)
        key = street_core(canonical)
        letters = "".join(char for char in key if char.isalpha())
        if not key or key in _EXACTLY_REJECTED or len(letters) < 2 or any(char.isdigit() for char in key):
            rejected.append(raw)
            continue
        # Linhas de rótulo do mapa que ainda contêm pontuação de desenho não
        # são bons candidatos ao fuzzy matching local.
        if any(marker in canonical for marker in ("(", ")", "/")):
            rejected.append(raw)
            continue
        accepted.setdefault(key, StreetEntry(canonical.upper(), key, "PDF_TEXT_LABEL", page))
    return list(accepted.values()), rejected


def extract_pdf_labels(path: Path) -> list[tuple[int, str]]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependência já usada pelo projeto
        raise RuntimeError("PyMuPDF é necessário para extrair o PDF.") from exc
    document = pymupdf.open(path)
    try:
        return [
            (page_number, line)
            for page_number, page in enumerate(document, start=1)
            for line in page.get_text("text").splitlines()
            if line.strip()
        ]
    finally:
        document.close()


def build_dictionary(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    labels = extract_pdf_labels(source)
    entries, rejected = filter_pdf_labels(labels)
    payload = dictionary_payload(entries, source=str(source), rejected_labels=rejected)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera dicionário local de logradouros a partir do PDF da UGR.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    payload = build_dictionary(args.source, args.output)
    print(f"Dicionário gerado: {args.output}")
    print(f"Rótulos aceitos: {payload['street_count']}; rejeitados: {payload['rejected_label_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
