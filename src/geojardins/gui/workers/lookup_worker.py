from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from ...address_lookup import AddressLookupResult, AddressLookupService, lookup_address
from ...geocoding.base import GeocodingConfigurationError


class LookupWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self, street: str, number: str, service_factory: Callable[[], AddressLookupService], parent=None
    ) -> None:
        super().__init__(parent)
        self.street = street
        self.number = number
        self.service_factory = service_factory

    def run(self) -> None:
        try:
            result: AddressLookupResult = lookup_address(
                self.street, self.number, service=self.service_factory()
            )
        except GeocodingConfigurationError:
            logging.getLogger(__name__).exception("Geoapify nao configurado para consulta")
            self.failed.emit("Geoapify nao configurado. Abra Configuracoes e informe a chave de API.")
            return
        except Exception as exc:  # erro reportado ao usuário, detalhes ficam em log
            logging.getLogger(__name__).exception("Falha no worker de consulta")
            self.failed.emit(f"Não foi possível concluir a consulta: {exc}")
            return
        self.completed.emit(result)
