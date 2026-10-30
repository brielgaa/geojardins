"""Preferências locais e credenciais protegidas do GeoJardins.

Em Windows, tokens persistentes usam DPAPI ligado ao perfil do usuário. Em
outros sistemas ou se DPAPI estiver indisponível, nenhum segredo é persistido:
as variáveis de ambiente continuam funcionando e a GUI mantém o valor apenas
durante a sessão.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .fiscais import FiscalResolver
from .runtime import resource_path, user_config_path
from .settings import Settings, load_settings


class SecretStore:
    """Pequena fachada DPAPI, deliberadamente sem criptografia caseira."""

    def __init__(self) -> None:
        self.available = os.name == "nt"

    @staticmethod
    def _blob(value: bytes):  # type: ignore[no-untyped-def]
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(value, len(value))
        return DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer, DATA_BLOB

    def protect(self, value: str) -> str | None:
        if not self.available or not value:
            return None
        try:
            source, _source_buffer, data_blob = self._blob(value.encode("utf-8"))
            target = data_blob()
            crypt_protect = ctypes.windll.crypt32.CryptProtectData
            crypt_protect.argtypes = [
                ctypes.POINTER(data_blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(data_blob),
            ]
            crypt_protect.restype = wintypes.BOOL
            if not crypt_protect(ctypes.byref(source), "GeoJardins", None, None, None, 0, ctypes.byref(target)):
                return None
            try:
                encoded = base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode("ascii")
                return "dpapi:" + encoded
            finally:
                ctypes.windll.kernel32.LocalFree(target.pbData)
        except (AttributeError, OSError, ValueError):
            return None

    def unprotect(self, value: object) -> str | None:
        if not self.available or not isinstance(value, str) or not value.startswith("dpapi:"):
            return None
        try:
            encrypted = base64.b64decode(value.removeprefix("dpapi:"), validate=True)
            source, _source_buffer, data_blob = self._blob(encrypted)
            target = data_blob()
            crypt_unprotect = ctypes.windll.crypt32.CryptUnprotectData
            crypt_unprotect.argtypes = [
                ctypes.POINTER(data_blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(data_blob),
            ]
            crypt_unprotect.restype = wintypes.BOOL
            if not crypt_unprotect(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
                return None
            try:
                return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
            finally:
                ctypes.windll.kernel32.LocalFree(target.pbData)
        except (AttributeError, OSError, ValueError, UnicodeDecodeError):
            return None


class UserConfigStore:
    """Leitura e escrita atômica de configuração não secreta e secrets DPAPI."""

    def __init__(self, path: Path | None = None, secrets: SecretStore | None = None) -> None:
        self.path = path or user_config_path()
        self.secrets = secrets or SecretStore()
        self._session_credentials: dict[str, str] = {}

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def settings(self) -> Settings:
        defaults = load_settings(resource_path("config", "settings.json"))
        saved = self._read().get("settings", {})
        if not isinstance(saved, dict):
            return defaults
        allowed = {name: saved[name] for name in Settings.__dataclass_fields__ if name in saved}
        try:
            return Settings(**{**asdict(defaults), **allowed})
        except (TypeError, ValueError):
            return defaults

    def save_settings(self, settings: Settings) -> None:
        data = self._read()
        data["version"] = 1
        data["settings"] = asdict(settings)
        self._write(data)

    def fiscais(self) -> dict[str, str]:
        defaults = FiscalResolver.from_path(resource_path("config", "fiscais.json")).assignments
        saved = self._read().get("fiscais", {})
        if not isinstance(saved, dict):
            return dict(defaults)
        merged = dict(defaults)
        merged.update({str(area): str(name).strip() for area, name in saved.items() if area in defaults})
        return merged

    def save_fiscais(self, assignments: dict[str, str]) -> None:
        data = self._read()
        data["version"] = 1
        data["fiscais"] = {area: str(name).strip() for area, name in assignments.items()}
        self._write(data)

    def preferences(self) -> dict[str, str]:
        saved = self._read().get("preferences", {})
        if not isinstance(saved, dict):
            return {}
        allowed = {"last_import_dir", "batch_status_filter", "batch_mode"}
        return {
            key: str(value)
            for key, value in saved.items()
            if key in allowed and isinstance(value, str) and value.strip()
        }

    def save_preferences(self, **values: object) -> None:
        allowed = {"last_import_dir", "batch_status_filter", "batch_mode"}
        data = self._read()
        saved = data.get("preferences", {})
        preferences = dict(saved) if isinstance(saved, dict) else {}
        for key, value in values.items():
            if key not in allowed or value is None:
                continue
            text = str(value).strip()
            if text:
                preferences[key] = text
        data["version"] = 1
        data["preferences"] = {key: preferences[key] for key in allowed if key in preferences}
        self._write(data)

    def credentials(self) -> dict[str, str]:
        data = self._read().get("secrets", {})
        data = data if isinstance(data, dict) else {}
        values: dict[str, str] = {}
        for key, environment in (
            ("geoapify", "GEOJARDINS_GEOAPIFY_API_KEY"),
            ("locationiq", "GEOJARDINS_LOCATIONIQ_TOKEN"),
        ):
            protected = self.secrets.unprotect(data.get(key))
            value = protected or self._session_credentials.get(key) or os.environ.get(environment, "")
            values[key] = value.strip()
        return values

    def save_credentials(self, *, geoapify: str | None = None, locationiq: str | None = None) -> bool:
        data = self._read()
        stored = data.get("secrets", {})
        stored = dict(stored) if isinstance(stored, dict) else {}
        persisted = True
        for key, value in (("geoapify", geoapify), ("locationiq", locationiq)):
            if value is None or not value.strip():
                continue
            clean = value.strip()
            protected = self.secrets.protect(clean)
            if protected:
                stored[key] = protected
            else:
                self._session_credentials[key] = clean
                persisted = False
        if stored:
            data["version"] = 1
            data["secrets"] = stored
            self._write(data)
        return persisted

    def clear_credentials(self) -> None:
        data = self._read()
        data.pop("secrets", None)
        self._session_credentials.clear()
        self._write(data)
