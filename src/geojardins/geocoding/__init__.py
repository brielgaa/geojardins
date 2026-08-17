"""Abstrações e providers de geocodificação do GeoJardins."""

from .base import Geocoder, GeocodingError
from .fallback import FallbackGeocoder
from .geoapify import GeoapifyGeocoder
from .locationiq import LocationIQGeocoder
from .mock import MockGeocoder
from .models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics

__all__ = [
    "AddressQuery",
    "GeocodeCandidate",
    "GeocodingDiagnostics",
    "Geocoder",
    "GeocodingError",
    "FallbackGeocoder",
    "GeoapifyGeocoder",
    "LocationIQGeocoder",
    "MockGeocoder",
]
