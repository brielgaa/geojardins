"""Captura vistas de revisão visual sem acessar rede nem alterar dados operacionais."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtWidgets import QApplication

from geojardins.address_lookup import AddressLookupResult, GeocodingPrecision, LookupStatus
from geojardins.batch_lookup import BatchRowResult, BatchRunSummary
from geojardins.classification import GeographicClassification
from geojardins.geocoding.models import GeocodeCandidate
from geojardins.gui import main_window as main_window_module
from geojardins.gui.main_window import MainWindow
from geojardins.gui.theme import apply_theme
from geojardins.user_config import UserConfigStore


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "ui_review"


def sample_result(status: LookupStatus = LookupStatus.CONFIRMADO, area: str = "Tomé_4") -> AddressLookupResult:
    return AddressLookupResult(
        original_street="Rua Oscar Freire",
        original_house_number="1250",
        normalized_house_number="1250",
        status=status,
        reason="Localização confirmada dentro da área operacional.",
        candidate=GeocodeCandidate(
            latitude=-23.5627,
            longitude=-46.6716,
            display_name="Rua Oscar Freire, 1250 - Jardins, São Paulo - SP",
            house_number="1250",
            provider="geoapify",
            result_type="building",
            confidence=0.94,
        ),
        geography=GeographicClassification(True, area, (area,), 42.0, False),
        fiscal="Equipe Oeste",
        precision=GeocodingPrecision.ALTA,
    )


def capture(window: MainWindow, name: str) -> None:
    QApplication.processEvents()
    QApplication.processEvents()
    window.grab().save(str(OUTPUT / name))


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(["GeoJardins-review", "-platform", "offscreen"])
    apply_theme(app)
    # A revisão é efêmera: não grava posição/tamanho no perfil real do usuário.
    main_window_module.save_window = lambda _window: None
    window = MainWindow(UserConfigStore(OUTPUT / "review_config.json"))
    window.resize(1600, 980)
    window.show()
    capture(window, "dashboard.png")

    window.show_page(1)
    capture(window, "consulta-vazia.png")
    window.individual.result_panel.show_result(sample_result())
    capture(window, "consulta-resultado.png")

    window.show_page(2)
    capture(window, "lote-vazio.png")
    rows = (
        BatchRowResult({"Logradouro": "Rua Oscar Freire", "Altura": "1250"}, sample_result()),
        BatchRowResult({"Logradouro": "Rua Haddock Lobo", "Altura": "611"}, sample_result(LookupStatus.CONFERIR, "Tomé_1")),
    )
    summary = BatchRunSummary(2, Counter({"CONFIRMADO": 1, "PROXIMO_DA_DIVISA": 0, "CONFERIR": 1, "FORA_DA_UGR": 0}), Counter({"Tomé_4": 1, "Tomé_1": 1}), 2, 0)
    window.batch._set_results(rows, summary, OUTPUT / "resultado-exemplo.xlsx")
    capture(window, "lote-processado.png")

    window.show_page(3)
    window.fiscais.select_area("Tomé_4")
    capture(window, "fiscais-mapa.png")
    window.show_page(4)
    capture(window, "configuracoes.png")
    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
