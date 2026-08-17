from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...user_config import UserConfigStore
from ..theme import PALETTE, SIZES, SPACING, TYPE
from ..widgets.common import PageHeader, Toast
from ..widgets.social_link import SocialLinkButton


class SettingsView(QWidget):
    saved = Signal()

    def __init__(self, store: UserConfigStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)
        scroll_body = QWidget()
        scroll.setWidget(scroll_body)
        outer = QHBoxLayout(scroll_body)
        outer.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        content = QWidget()
        content.setMaximumWidth(SIZES["content_form"])
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xl"])
        outer.addStretch(1)
        outer.addWidget(content)
        outer.addStretch(1)
        layout.addWidget(PageHeader("Configurações", "Preferências locais do GeoJardins. Credenciais permanecem protegidas para o usuário atual."))

        layout.addWidget(self._operation_section())
        layout.addWidget(self._providers_section())
        actions = QHBoxLayout()
        actions.setSpacing(SPACING["xl"])
        self.save_button = QPushButton("Salvar alterações")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save)
        self.clear_button = QPushButton("Remover credenciais salvas")
        self.clear_button.setProperty("role", "danger")
        self.clear_button.clicked.connect(self.clear_credentials)
        actions.addWidget(self.save_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self._about_section())
        layout.addStretch(1)
        self.feedback = Toast(self)
        self.refresh_status()

    @staticmethod
    def _section(title: str, description: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:10px;")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(19, 17, 19, 17)
        layout.setSpacing(SPACING["sm"])
        heading = QLabel(title)
        heading.setStyleSheet(f"font-size:{TYPE['section']}px; font-weight:650; color:{PALETTE['ink']};")
        note = QLabel(description)
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        layout.addWidget(heading)
        layout.addWidget(note)
        return frame, layout

    def _operation_section(self) -> QFrame:
        frame, layout = self._section("Operação", "Defina quando uma demanda próxima da divisa deve ser sinalizada.")
        row = QHBoxLayout()
        row.setContentsMargins(0, SPACING["sm"], 0, 0)
        label_stack = QVBoxLayout()
        label = QLabel("Margem de alerta de divisa")
        label.setStyleSheet(f"font-size:{TYPE['body']}px; font-weight:600;")
        caption = QLabel("Distância usada para recomendar conferência")
        caption.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        label_stack.addWidget(label)
        label_stack.addWidget(caption)
        self.boundary = QDoubleSpinBox()
        self.boundary.setRange(0, 1000)
        self.boundary.setDecimals(1)
        self.boundary.setSuffix(" m")
        self.boundary.setValue(self.store.settings().boundary_warning_m)
        self.boundary.setAccessibleName("Margem de alerta de divisa em metros")
        self.boundary.setFixedWidth(132)
        row.addLayout(label_stack, 1)
        row.addWidget(self.boundary, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        return frame

    def _providers_section(self) -> QFrame:
        frame, layout = self._section("Provedores", "Uma chave salva não é exibida novamente. Informe um valor somente quando quiser substituí-la.")
        self.geoapify = QLineEdit()
        self.geoapify.setEchoMode(QLineEdit.EchoMode.Password)
        self.geoapify.setPlaceholderText("Nova chave da Geoapify")
        self.geoapify.setAccessibleName("Nova chave da Geoapify")
        self.locationiq = QLineEdit()
        self.locationiq.setEchoMode(QLineEdit.EchoMode.Password)
        self.locationiq.setPlaceholderText("Novo token da LocationIQ (opcional)")
        self.locationiq.setAccessibleName("Novo token da LocationIQ")
        geo_row, self.geoapify_status = self._provider_row("Geoapify", "Provider principal", self.geoapify)
        location_row, self.locationiq_status = self._provider_row("LocationIQ", "Fallback opcional", self.locationiq)
        layout.addWidget(geo_row)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background:{PALETTE['line']};")
        layout.addWidget(divider)
        layout.addWidget(location_row)
        return frame

    def _provider_row(self, name: str, detail: str, field: QLineEdit) -> tuple[QWidget, QLabel]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, SPACING["sm"], 0, SPACING["xs"])
        layout.setSpacing(6)
        heading = QHBoxLayout()
        label_stack = QVBoxLayout()
        label_stack.setSpacing(1)
        label = QLabel(name)
        label.setStyleSheet(f"font-size:{TYPE['body']}px; font-weight:650;")
        caption = QLabel(detail)
        caption.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        label_stack.addWidget(label)
        label_stack.addWidget(caption)
        status = QLabel()
        heading.addLayout(label_stack, 1)
        heading.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
        controls = QHBoxLayout()
        controls.setSpacing(SPACING["sm"])
        controls.addWidget(field, 1)
        controls.addWidget(self._secret_toggle(field))
        layout.addLayout(heading)
        layout.addLayout(controls)
        return container, status

    @staticmethod
    def _secret_toggle(field: QLineEdit) -> QPushButton:
        reveal = QPushButton("Mostrar")
        reveal.setProperty("role", "ghost")
        reveal.setAccessibleName(f"Mostrar ou ocultar {field.accessibleName().lower()}")

        def toggle() -> None:
            hidden = field.echoMode() == QLineEdit.EchoMode.Password
            field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            reveal.setText("Ocultar" if hidden else "Mostrar")

        reveal.clicked.connect(toggle)
        return reveal

    def _about_section(self) -> QFrame:
        frame, layout = self._section("Sobre", "")
        product_row = QHBoxLayout()
        product_row.setContentsMargins(0, 0, 0, 0)
        product_row.setSpacing(SPACING["sm"])
        product_title = QLabel("GeoJardins")
        product_title.setWordWrap(False)
        product_title.setStyleSheet(f"font-size:20px; font-weight:650; color:{PALETTE['ink']};")
        product_version = QLabel("v1.0")
        product_version.setWordWrap(False)
        product_version.setStyleSheet(f"font-size:{TYPE['caption']}px; color:{PALETTE['muted']};")
        product_row.addWidget(product_title, 0, Qt.AlignmentFlag.AlignVCenter)
        product_row.addWidget(product_version, 0, Qt.AlignmentFlag.AlignVCenter)
        product_row.addStretch(1)
        description = QLabel("Sistema de Identificação e Distribuição Geográfica de Demandas\nUGR Jardins")
        description.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['body']}px;")
        byline = QLabel("Desenvolvido por\nGabriel da Silva Bitencourt")
        byline.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; margin-top:4px;")
        layout.addLayout(product_row)
        layout.addWidget(description)
        layout.addWidget(byline)
        social_row = QHBoxLayout()
        social_row.setContentsMargins(0, 4, 0, 0)
        social_row.setSpacing(SPACING["sm"])
        self.github_button = SocialLinkButton("github")
        self.linkedin_button = SocialLinkButton("linkedin")
        social_row.addWidget(self.github_button)
        social_row.addWidget(self.linkedin_button)
        social_row.addStretch(1)
        layout.addLayout(social_row)
        return frame

    @staticmethod
    def _set_status_badge(badge: QLabel, configured: bool) -> None:
        text = "CONFIGURADO" if configured else "NÃO CONFIGURADO"
        background = PALETTE["success_surface"] if configured else "#EEF2EE"
        color = PALETTE["success"] if configured else PALETTE["muted"]
        badge.setText(text)
        badge.setStyleSheet(f"background:{background}; color:{color}; border-radius:7px; padding:3px 7px; font-size:10px; font-weight:650;")

    def refresh_status(self) -> None:
        credentials = self.store.credentials()
        self._set_status_badge(self.geoapify_status, bool(credentials["geoapify"]))
        self._set_status_badge(self.locationiq_status, bool(credentials["locationiq"]))

    def save(self) -> None:
        settings = replace(self.store.settings(), boundary_warning_m=self.boundary.value())
        self.store.save_settings(settings)
        persisted = self.store.save_credentials(geoapify=self.geoapify.text() or None, locationiq=self.locationiq.text() or None)
        self.geoapify.clear()
        self.locationiq.clear()
        self.refresh_status()
        self.feedback.show_message("Configurações atualizadas." if persisted else "Preferências salvas; a credencial ficará somente nesta sessão.", kind="success" if persisted else "info")
        self.saved.emit()

    def clear_credentials(self) -> None:
        answer = QMessageBox.question(
            self,
            "Remover credenciais salvas",
            "Remover as credenciais salvas deste usuário? Esta ação não pode ser desfeita.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.clear_credentials()
        self.refresh_status()
        self.feedback.show_message("Credenciais salvas removidas.", kind="success")
        self.saved.emit()
