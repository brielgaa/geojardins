from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...address_lookup import AddressLookupService
from ...batch_lookup import BatchRowResult, BatchRunSummary, process_batch
from ...geocoding.base import GeocodingConfigurationError


class BatchWorker(QThread):
    progress = Signal(str)
    completed = Signal(object, object, object)
    failed = Signal(str)

    def __init__(self, input_path: Path, service_factory: Callable[[], AddressLookupService], parent=None) -> None:
        super().__init__(parent)
        self.input_path = input_path
        self.service_factory = service_factory

    def run(self) -> None:
        try:
            results: tuple[BatchRowResult, ...]
            summary: BatchRunSummary
            output: Path
            results, summary, output = process_batch(
                self.input_path, service=self.service_factory(), progress=self.progress.emit
            )
        except GeocodingConfigurationError:
            logging.getLogger(__name__).exception("Geoapify nao configurado para lote")
            self.failed.emit("Geoapify nao configurado. Abra Configuracoes e informe a chave de API.")
            return
        except Exception as exc:
            logging.getLogger(__name__).exception("Falha no worker de lote")
            self.failed.emit(f"Não foi possível processar o arquivo: {exc}")
            return
        self.completed.emit(results, summary, output)
