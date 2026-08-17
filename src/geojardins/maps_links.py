"""Links de visualização, sem usar API de geocodificação do Google."""

from __future__ import annotations

from urllib.parse import quote_plus


def google_maps_link(latitude: float, longitude: float) -> str:
    """Gera um link que abre exatamente as coordenadas usadas na classificação."""

    return f"https://www.google.com/maps/search/?api=1&query={latitude:.7f},{longitude:.7f}"


def google_maps_search_link(query: str) -> str:
    """Gera busca manual quando a geocodificação não encontrou coordenadas."""

    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)
