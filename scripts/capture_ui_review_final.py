"""Capturas determinísticas da revisão final, sem rede nem alteração de dados operacionais."""

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
OUTPUT = ROOT / "output" / "ui_review_final"


def sample_result(status: LookupStatus, area: str = "Tomé_4", distance: float = 42.0) -> AddressLookupResult:
    return AddressLookupResult(
        original_street="Rua Oscar Freire",
        original_house_number="1250",
        normalized_house_number="1250",
        status=status,
        reason="Localização confirmada dentro da área operacional.",
        candidate=GeocodeCandidate(
            latitude=-23.5627,
            longitude=-46.6716,
            display_name="Rua Oscar Freire, 1250 — Cerqueira César, São Paulo — SP",
            house_number="1250",
            provider="geoapify",
            result_type="building",
            confidence=0.94,
            confidence_street_level=0.95,
            confidence_building_level=0.93,
        ),
        geography=GeographicClassification(True, area, (area,), distance, status is LookupStatus.PROXIMO_DA_DIVISA),
        fiscal="João da Silva",
        precision=GeocodingPrecision.ALTA if status is not LookupStatus.CONFERIR else GeocodingPrecision.MEDIA,
    )


def capture(window: MainWindow, name: str) -> None:
    QApplication.processEvents()
    QApplication.processEvents()
    window.grab().save(str(OUTPUT / name))


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(["GeoJardins-review-final", "-platform", "offscreen"])
    apply_theme(app)
    # A revisão é efêmera e não altera o estado de janela do perfil do usuário.
    main_window_module.save_window = lambda _window: None
    window = MainWindow(UserConfigStore(OUTPUT / "review_config.json"))
    window.resize(1920, 1080)
    window.show()

    capture(window, "01_dashboard.png")
    window.show_page(1)
    capture(window, "02_consulta_empty.png")
    window.individual.result_panel.show_result(sample_result(LookupStatus.CONFIRMADO))
    capture(window, "03_consulta_confirmado.png")
    window.individual.result_panel.show_result(sample_result(LookupStatus.PROXIMO_DA_DIVISA, "Tomé_1", 17.0))
    capture(window, "04_consulta_divisa.png")
    window.individual.result_panel.show_result(sample_result(LookupStatus.CONFERIR, "Tomé_3", 86.0))
    capture(window, "05_consulta_conferir.png")

    window.show_page(2)
    capture(window, "06_lote_empty.png")
    rows = (
        BatchRowResult({"Logradouro": "Rua Oscar Freire", "Altura": "1250"}, sample_result(LookupStatus.CONFIRMADO)),
        BatchRowResult({"Logradouro": "Rua Haddock Lobo", "Altura": "611"}, sample_result(LookupStatus.PROXIMO_DA_DIVISA, "Tomé_1", 17.0)),
        BatchRowResult({"Logradouro": "Alameda Itu", "Altura": "640"}, sample_result(LookupStatus.CONFERIR, "Tomé_3", 86.0)),
    )
    summary = BatchRunSummary(3, Counter({"CONFIRMADO": 1, "PROXIMO_DA_DIVISA": 1, "CONFERIR": 1, "FORA_DA_UGR": 0}), Counter({"Tomé_4": 1, "Tomé_1": 1, "Tomé_3": 1}), 3, 0)
    window.batch._set_results(rows, summary, OUTPUT / "resultado-exemplo.xlsx")
    capture(window, "07_lote_results.png")

    window.show_page(3)
    capture(window, "08_fiscais.png")
    window.fiscais.select_area("Tomé_4")
    capture(window, "09_fiscais_selected.png")
    window.show_page(4)
    capture(window, "10_configuracoes.png")
    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
