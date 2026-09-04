from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...address_lookup import AddressLookupService
from ...batch_lookup import BatchRowResult, BatchRunSummary, InputRows, build_paste_export_path, process_batch, process_batch_source
from ...geocoding.base import GeocodingConfigurationError


class BatchWorker(QThread):
    progress = Signal(str)
    completed = Signal(object, object, object)
    failed = Signal(str)

    def __init__(
        self,
        source: Path | InputRows,
        service_factory: Callable[[], AddressLookupService],
        parent=None,
        *,
        output_path: Path | None = None,
        street_column: str | None = None,
        number_column: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.source = source
        self.service_factory = service_factory
        self.output_path = output_path
        self.street_column = street_column
        self.number_column = number_column

    def run(self) -> None:
        try:
            results: tuple[BatchRowResult, ...]
            summary: BatchRunSummary
            output: Path
            service = self.service_factory()
            if isinstance(self.source, Path):
                results, summary, output = process_batch(
                    self.source, service=service, progress=self.progress.emit
                )
            else:
                results, summary, output = process_batch_source(
                    self.source,
                    service=service,
                    output_path=self.output_path or build_paste_export_path(),
                    street_column=self.street_column,
                    number_column=self.number_column,
                    progress=self.progress.emit,
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
