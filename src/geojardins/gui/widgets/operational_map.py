"""Mapa operacional offline com raster PDF e overlays de áreas Tomé."""

from __future__ import annotations

import json
from collections.abc import Iterable
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

from .social_link import asset_path


class AreaItem(QGraphicsPathItem):
    def __init__(self, area: str, path: QPainterPath, color: str, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(path, parent)
        self.area = area
        self.base_color = QColor(color)
        self.fiscal = ""
        self.active = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(2)
        self._apply_style()

    def _apply_style(self, *, hover: bool = False) -> None:
        fill = QColor(self.base_color)
        fill.setAlpha(125 if self.active else (88 if hover else 50))
        pen = QPen(QColor("#17372C" if self.active else "#405A4C"))
        pen.setWidthF(2.0 if self.active else (1.5 if hover else 1.0))
        self.setBrush(fill)
        self.setPen(pen)
        self.setToolTip(f"{self.area.replace('_', ' ')}\nFiscal: {self.fiscal or 'Não configurado'}")

    def set_active(self, active: bool) -> None:
        self.active = active
        self._apply_style()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._apply_style(hover=True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._apply_style()
        super().hoverLeaveEvent(event)


class OperationalMapView(QGraphicsView):
    """QGraphicsView sem rede: zoom, pan, enquadramento e seleção por área."""

    area_selected = Signal(str)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background:#EEF2EE; border:1px solid #DCE5DC; border-radius:8px;")
        self.setToolTip("Mapa operacional offline. Use a roda do mouse para zoom, arraste para mover e clique em uma área.")
        self.areas: dict[str, AreaItem] = {}
        self.territory_rect = QRectF()
        self._fitted_once = False
        self._load_assets()

    @staticmethod
    def _rings(geometry: dict) -> Iterable[list[list[float]]]:
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates", [])
        if geometry_type == "Polygon":
            yield from coordinates
        elif geometry_type == "MultiPolygon":
            for polygon in coordinates:
                yield from polygon
        else:
            raise ValueError(f"Geometria de mapa não suportada: {geometry_type}")

    @staticmethod
    def _path(geometry: dict, scale_x: float, scale_y: float) -> tuple[QPainterPath, QPointF]:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        all_points: list[QPointF] = []
        for ring in OperationalMapView._rings(geometry):
            if not ring:
                continue
            points = [QPointF(float(x) * scale_x, float(y) * scale_y) for x, y in ring]
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
            all_points.extend(points)
        if not all_points:
            raise ValueError("Overlay do mapa sem coordenadas.")
        bounds = path.boundingRect()
        return path, bounds.center()

    def _load_assets(self) -> None:
        image_path = asset_path("operational_map.png")
        overlay_path = asset_path("operational_map_overlays.json")
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise RuntimeError("Não foi possível carregar o mapa operacional offline.")
        metadata = json.loads(overlay_path.read_text(encoding="utf-8"))
        image_size = metadata["image_size"]
        scale_x = float(image_size[0]) / float(metadata["page_bounds"][2] - metadata["page_bounds"][0])
        scale_y = float(image_size[1]) / float(metadata["page_bounds"][3] - metadata["page_bounds"][1])
        background = self.scene.addPixmap(pixmap)
        background.setZValue(0)
        for feature in metadata["features"]:
            area = str(feature["area"])
            path, label_point = self._path(feature["geometry"], scale_x, scale_y)
            item = AreaItem(area, path, str(feature.get("fill_color", "#DDE5DE")))
            self.scene.addItem(item)
            self.areas[area] = item
            self.territory_rect = item.boundingRect() if self.territory_rect.isNull() else self.territory_rect.united(item.boundingRect())
            label = QGraphicsSimpleTextItem(area.replace("_", " "))
            font = QFont("Segoe UI Variable", 9)
            font.setWeight(QFont.Weight.DemiBold)
            label.setFont(font)
            label.setBrush(QColor("#17372C"))
            label.setZValue(3)
            label.setPos(label_point - QPointF(label.boundingRect().width() / 2, label.boundingRect().height() / 2))
            label.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(label)
        self.scene.setSceneRect(background.boundingRect())
        pad_x = self.territory_rect.width() * 0.055
        pad_y = self.territory_rect.height() * 0.055
        self.territory_rect = self.territory_rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)

    def set_fiscais(self, assignments: dict[str, str]) -> None:
        for area, item in self.areas.items():
            item.fiscal = assignments.get(area, "")
            item._apply_style()

    def set_selected(self, area: str | None, *, emit: bool = False) -> None:
        for name, item in self.areas.items():
            item.set_active(name == area)
        if area and emit:
            self.area_selected.emit(area)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        scene_position = self.mapToScene(event.position().toPoint())
        hit = self.scene.itemAt(scene_position, self.transform())
        if isinstance(hit, AreaItem):
            self.set_selected(hit.area, emit=True)
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        factor = 1.16 if event.angleDelta().y() > 0 else 1 / 1.16
        self.scale(factor, factor)

    def zoom_in(self) -> None:
        self.scale(1.2, 1.2)

    def zoom_out(self) -> None:
        self.scale(1 / 1.2, 1 / 1.2)

    def fit_to_view(self) -> None:
        self.fitInView(self.territory_rect or self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted_once = True

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().showEvent(event)
        if not self._fitted_once:
            self.fit_to_view()
