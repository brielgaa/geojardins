"""Componentes visuais compartilhados, pequenos e consistentes."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QEasingCurve, QPropertyAnimation, QSize, QTimer, Qt
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import PALETTE, SPACING, TYPE
from .social_link import svg_icon


class PageHeader(QWidget):
    def __init__(self, title: str, description: str, eyebrow: str | None = None, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xs"])
        if eyebrow:
            label = QLabel(eyebrow.upper())
            label.setStyleSheet("color:#52735B; font-size:11px; font-weight:650; letter-spacing:0.7px;")
            layout.addWidget(label)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size:{TYPE['page']}px; font-weight:650; color:{PALETTE['ink']};")
        description_label = QLabel(description)
        description_label.setWordWrap(True)
        description_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['body']}px;")
        layout.addWidget(title_label)
        layout.addWidget(description_label)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, text: str, *, compact: bool = False, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        layout = QVBoxLayout(self)
        padding = 20 if compact else 28
        layout.setContentsMargins(12, padding, 12, padding)
        layout.setSpacing(SPACING["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        icon_label = QLabel()
        icon_label.setPixmap(svg_icon(icon, "#61835C", 32 if compact else 36).pixmap(32 if compact else 36, 32 if compact else 36))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(f"font-size:{TYPE['section'] - 2}px; font-weight:650; color:#234237;")
        detail = QLabel(text)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        detail.setMaximumWidth(470)
        detail.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['body']}px;")
        layout.addWidget(icon_label)
        layout.addWidget(heading)
        layout.addWidget(detail)


class IconButton(QPushButton):
    """Botão de ícone acessível, com tooltip obrigatório e tamanho uniforme."""

    def __init__(self, icon: str, accessible_name: str, *, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setProperty("role", "icon")
        self.setIcon(svg_icon(icon, "#315B4B", 17))
        self.setIconSize(QSize(17, 17))
        self.setToolTip(accessible_name)
        self.setAccessibleName(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class Toast(QFrame):
    """Notificação discreta ancorada no canto inferior direito da área de conteúdo."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.host = host
        self.setObjectName("toast")
        self.setVisible(False)
        self.setMaximumWidth(460)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-size:12px; font-weight:600;")
        layout.addWidget(self.label)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(150)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._finish_fade)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        host.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]  # noqa: N802
        if watched is getattr(self, "host", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._position()
        return super().eventFilter(watched, event)

    def _position(self) -> None:
        if self.isHidden():
            return
        margin = SPACING["xl"]
        self.move(max(margin, self.host.width() - self.width() - margin), max(margin, self.host.height() - self.height() - margin))

    def show_message(self, message: str, *, kind: str = "success", duration_ms: int = 4200) -> None:
        styles = {
            "success": ("#E1F2E5", "#17643B"),
            "warning": ("#FCF5DE", "#765A12"),
            "error": ("#FCEAEC", "#A74342"),
            "info": ("#E8F0ED", "#315B4B"),
        }
        background, foreground = styles.get(kind, styles["info"])
        self.fade.stop()
        self.label.setText(message)
        self.label.setStyleSheet(f"color:{foreground}; font-size:12px; font-weight:600;")
        self.setStyleSheet(f"QFrame#toast {{ background:{background}; border:1px solid {background}; border-radius:8px; }}")
        self.adjustSize()
        self.effect.setOpacity(1.0)
        self.show()
        self._position()
        self.raise_()
        self.timer.start(duration_ms)

    def dismiss(self) -> None:
        if self.isHidden():
            return
        self.fade.stop()
        self.fade.setStartValue(1.0)
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _finish_fade(self) -> None:
        if self.effect.opacity() == 0.0:
            self.hide()


# Mantém a API dos pontos de uso antigos enquanto o feedback é migrado para Toast.
InlineFeedback = Toast
