from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...address_lookup import AddressLookupResult, LookupFlag
from ..theme import PALETTE, SPACING, TYPE
from .common import EmptyState, IconButton
from .social_link import svg_icon
from .status_badge import StatusBadge


def _value(value: object, suffix: str = "") -> str:
    if isinstance(value, bool):
        return ("SIM" if value else "NÃO") + suffix
    return "—" if value is None or value == "" else f"{value}{suffix}"


def _area(value: str | None) -> str:
    return value.replace("_", " ") if value else "Área não identificada"


class ResultPanel(QScrollArea):
    """O resultado é a decisão operacional: área, fiscal e contexto primeiro."""

    edit_requested = Signal()
    toast_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.layout = QVBoxLayout(self.body)
        self.layout.setContentsMargins(0, 2, 0, 0)
        self.layout.setSpacing(SPACING["md"])
        self.setWidget(self.body)
        self.empty = EmptyState(
            "location.svg",
            "Aguardando uma consulta",
            "Informe logradouro e altura numérica para localizar a demanda e identificar a área responsável.",
            compact=True,
        )
        self.layout.addWidget(self.empty)
        self.card: QFrame | None = None

    def show_result(self, result: AddressLookupResult, *, expanded: bool = False) -> None:
        self.empty.hide()
        if self.card is not None:
            self.layout.removeWidget(self.card)
            self.card.deleteLater()
        card = QFrame()
        card.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:10px;")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(SPACING["lg"])

        header = QHBoxLayout()
        header.setSpacing(SPACING["sm"])
        header.addWidget(StatusBadge(result.status.value))
        header.addStretch(1)
        copy_address = IconButton("copy.svg", "Copiar endereço")
        copy_address.setEnabled(bool(result.formatted_address))
        copy_address.clicked.connect(lambda: self._copy(result.formatted_address or "", "Endereço copiado."))
        coordinates = IconButton("copy.svg", "Copiar coordenadas")
        coordinates.setEnabled(result.latitude is not None and result.longitude is not None)
        coordinates.clicked.connect(lambda: self._copy_coordinates(result))
        maps = QPushButton("Abrir no Google Maps")
        maps.setProperty("role", "secondary")
        maps.setIcon(svg_icon("location.svg", "#315B4B", 15))
        maps.setToolTip("Abrir localização no navegador padrão")
        maps.setAccessibleName("Abrir localização no Google Maps")
        maps.setEnabled(bool(result.google_maps_url))
        maps.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(result.google_maps_url or "")))
        header.addWidget(copy_address)
        header.addWidget(coordinates)
        header.addWidget(maps)
        outer.addLayout(header)

        hero = QGridLayout()
        hero.setHorizontalSpacing(SPACING["xxl"])
        hero.setVerticalSpacing(3)
        area_label = QLabel("ÁREA OPERACIONAL")
        area_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:650;")
        area_value = QLabel(_area(result.area) if result.area else self._status_title(result))
        area_value.setStyleSheet(f"font-size:28px; font-weight:650; color:{PALETTE['ink']};")
        fiscal_label = QLabel("FISCAL RESPONSÁVEL")
        fiscal_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:650;")
        fiscal = result.fiscal if result.fiscal and result.fiscal != "NÃO CONFIGURADO" else "Nenhum fiscal configurado"
        fiscal_value = QLabel(fiscal)
        confirmed_area = result.confirmed_area
        candidate_area = result.candidate_area
        area_label.setText(
            "AREA OPERACIONAL" if confirmed_area else "AREA CANDIDATA" if candidate_area else "SITUACAO OPERACIONAL"
        )
        area_value.setText(_area(confirmed_area or candidate_area) if (confirmed_area or candidate_area) else self._status_title(result))
        fiscal_label.setText("FISCAL RESPONSAVEL" if confirmed_area else "FISCAL CANDIDATO")
        fiscal_value.setText((result.confirmed_fiscal if confirmed_area else result.fiscal) or "NÃO CONFIGURADO")
        fiscal_value.setWordWrap(True)
        fiscal_value.setStyleSheet(f"font-size:{TYPE['section']}px; font-weight:600; color:{PALETTE['ink']};")
        hero.addWidget(area_label, 0, 0)
        hero.addWidget(fiscal_label, 0, 1)
        hero.addWidget(area_value, 1, 0)
        hero.addWidget(fiscal_value, 1, 1)
        hero.setColumnStretch(0, 3)
        hero.setColumnStretch(1, 2)
        outer.addLayout(hero)

        address = QLabel()
        address.setWordWrap(True)
        formatted = result.formatted_address or f"{result.original_street}, {result.original_house_number}"
        address.setText(f"<span style='color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:650;'>ENDEREÇO</span><br><b>{formatted}</b>")
        address.setStyleSheet(f"color:{PALETTE['ink']}; font-size:15px; line-height:1.45;")
        outer.addWidget(address)

        context = QLabel(result.reason or (result.confidence.decision_reason if result.confidence else "Consulta concluida."))
        context.setWordWrap(True)
        context.setStyleSheet(f"background:{self._context_surface(result)}; color:{PALETTE['muted']}; border-radius:7px; padding:10px 12px; font-size:{TYPE['body']}px;")
        outer.addWidget(context)

        details_button = QPushButton("Detalhes da geocodificação")
        details_button.setProperty("role", "ghost")
        details_button.setCheckable(True)
        details = self._details_widget(result)
        details.hide()

        def toggle_details(visible: bool) -> None:
            details.setVisible(visible)
            details_button.setText("Ocultar detalhes" if visible else "Detalhes da geocodificação")

        details_button.toggled.connect(toggle_details)
        details_button.setChecked(expanded)
        outer.addWidget(details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(details)
        if result.status.value == "ENDERECO_NAO_LOCALIZADO":
            edit = QPushButton("Editar consulta")
            edit.setProperty("role", "secondary")
            edit.clicked.connect(self.edit_requested)
            outer.addWidget(edit, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout.addWidget(card)
        self.card = card

    def _copy(self, text: str, message: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.toast_requested.emit(message, "success")

    def _copy_coordinates(self, result: AddressLookupResult) -> None:
        self._copy(f"{result.latitude:.6f}, {result.longitude:.6f}", "Coordenadas copiadas.")

    @staticmethod
    def _status_title(result: AddressLookupResult) -> str:
        return {
            "FORA_DA_UGR": "Fora da UGR",
            "ENDERECO_NAO_LOCALIZADO": "Endereço não localizado",
            "ERRO_GEOCODIFICACAO": "Consulta não concluída",
        }.get(result.status.value, "Área não identificada")

    @staticmethod
    def _context_surface(result: AddressLookupResult) -> str:
        if result.status.value == "PROXIMO_DA_DIVISA":
            return PALETTE["warning_surface"]
        if result.status.value in {"ENDERECO_NAO_LOCALIZADO", "ERRO_GEOCODIFICACAO", "DADOS_INVALIDOS"}:
            return PALETTE["danger_surface"]
        return PALETTE["surface_subtle"]

    @staticmethod
    def _context(result: AddressLookupResult) -> str:
        status = result.status.value
        if status == "ENDERECO_NAO_LOCALIZADO":
            return "Não foi possível localizar o endereço informado com segurança. Revise os dados e tente novamente."
        if status == "CONFERIR":
            return "Localização encontrada, mas a precisão não é suficiente para confirmação automática."
        if status == "PROXIMO_DA_DIVISA":
            distance = _value(round(result.distance_to_boundary_m, 1) if result.distance_to_boundary_m is not None else None, " m")
            return f"{distance} da divisão operacional. Conferência recomendada."
        if status == "FORA_DA_UGR":
            return "O endereço foi localizado, mas está fora do perímetro operacional do GeoJardins."
        return result.reason

    @staticmethod
    def _details_widget(result: AddressLookupResult) -> QFrame:
        candidate = result.candidate
        diagnostics = result.geocoding_diagnostics
        details = QFrame()
        details.setStyleSheet(f"background:{PALETTE['surface_subtle']}; border-radius:7px;")
        layout = QVBoxLayout(details)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(SPACING["sm"])
        evidence_title = QLabel("Evidências da decisão")
        evidence_title.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['body']}px; font-weight:650;")
        layout.addWidget(evidence_title)
        evidence = QGridLayout()
        evidence.setHorizontalSpacing(20)
        evidence.setVerticalSpacing(8)
        for index, (label, value) in enumerate(ResultPanel._evidence_items(result)):
            cell = QLabel(f"<span style='color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;'>{label}</span><br>{_value(value)}")
            cell.setWordWrap(True)
            cell.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['body']}px;")
            evidence.addWidget(cell, index // 2, index % 2)
        layout.addLayout(evidence)
        technical_title = QLabel("Detalhes técnicos")
        technical_title.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['body']}px; font-weight:650;")
        layout.addWidget(technical_title)
        technical = QGridLayout()
        technical.setHorizontalSpacing(20)
        technical.setVerticalSpacing(8)
        for index, (label, value) in enumerate(ResultPanel._detail_items(result)):
            cell = QLabel(f"<span style='color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;'>{label}</span><br>{_value(value)}")
            cell.setWordWrap(True)
            cell.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['body']}px;")
            technical.addWidget(cell, index // 2, index % 2)
        layout.addLayout(technical)
        return details

    @staticmethod
    def _evidence_items(result: AddressLookupResult) -> tuple[tuple[str, object], ...]:
        selected_assessment = next(
            (assessment for assessment in result.assessments if assessment.candidate == result.candidate),
            None,
        )
        street_compatible = (
            result.street_1_match
            if result.street_1_match is not None
            else selected_assessment.compatibility.road_compatible if selected_assessment else None
        )
        return (
            ("Resultado", result.status.value),
            ("Logradouro compatível", street_compatible),
            ("Número compatível", result.number_compatible),
            ("Precisão do provider", result.precision.value if result.precision else None),
            ("Área territorial", result.confirmed_area or result.candidate_area),
            ("Distância da divisa", _value(result.distance_to_boundary_m, " m")),
            ("Coordenada genérica", LookupFlag.COORDENADA_GENERICA in result.flags),
            ("Divergência de provider", result.providers_divergent or LookupFlag.PROVIDERS_DIVERGENTES in result.flags),
            ("Fiscal", result.confirmed_fiscal or result.fiscal),
            ("Confiança final", result.confidence.level.value if result.confidence else None),
        )

    @staticmethod
    def _detail_items(result: AddressLookupResult) -> tuple[tuple[str, object], ...]:
        candidate = result.candidate
        diagnostics = result.geocoding_diagnostics
        confidence = result.confidence
        return (
            ("Tipo de consulta", result.query_type.value),
            ("Endereco original", result.original_street),
            ("Endereco normalizado", result.normalized_street),
            ("Numero solicitado", result.normalized_house_number),
            ("Numero retornado", result.number_returned),
            ("Precisao", result.precision.value if result.precision else None),
            ("Confianca final", confidence.level.value if confidence else None),
            ("Motivo da decisao", confidence.decision_reason if confidence else result.selection_reason),
            ("Motivo do status", result.reason),
            ("Flags", "; ".join(flag.value for flag in result.flags)),
            ("Sugestao de correcao", result.suggested_correction),
            ("Provider", candidate.provider.upper() if candidate else None),
            ("Distancia da divisa", _value(result.distance_to_boundary_m, " m")),
            ("Latitude", result.latitude),
            ("Longitude", result.longitude),
            ("Tipo de resultado", candidate.result_type if candidate else None),
            ("Tipo de correspondencia", candidate.match_type if candidate else None),
            ("Confianca do provider", candidate.confidence if candidate else None),
            ("Estrategia", diagnostics.mode_label if diagnostics else None),
        )
