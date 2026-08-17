"""Ferramenta visual para cadastrar, editar e remover GCPs no mapa PDF."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str((Path.cwd() / ".cache" / "matplotlib").resolve()))

import numpy as np
import pymupdf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from shapely.geometry import shape
from shapely.ops import unary_union
import tkinter as tk
from tkinter import messagebox, ttk

from .control_points import (
    ControlPoint,
    ControlPointError,
    ControlPointStore,
    assess_spatial_distribution,
)
from .polygon_extractor import resolve_pdf_path


@dataclass(frozen=True)
class PdfViewport:
    """Limites de visualização do PDF sem alterar suas coordenadas originais."""

    base_xlim: tuple[float, float]
    base_ylim: tuple[float, float]
    minimum_zoom: float = 1.0
    maximum_zoom: float = 8.0

    @classmethod
    def from_extent(cls, extent: tuple[float, float, float, float]) -> "PdfViewport":
        return cls(base_xlim=(extent[0], extent[1]), base_ylim=(extent[2], extent[3]))

    @property
    def base_width(self) -> float:
        return abs(self.base_xlim[1] - self.base_xlim[0])

    @property
    def base_height(self) -> float:
        return abs(self.base_ylim[1] - self.base_ylim[0])

    def zoom_factor(self, xlim: tuple[float, float]) -> float:
        return self.base_width / abs(xlim[1] - xlim[0])

    def zoom_percentage(self, xlim: tuple[float, float]) -> int:
        return round(self.zoom_factor(xlim) * 100)

    def fit_limits(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return self.base_xlim, self.base_ylim

    def zoom_limits(
        self,
        xlim: tuple[float, float],
        ylim: tuple[float, float],
        anchor_x: float,
        anchor_y: float,
        scale: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Amplia/reduz em torno da âncora preservando a orientação dos eixos."""

        if scale <= 0:
            raise ValueError("A escala de zoom precisa ser positiva.")
        current_zoom = self.zoom_factor(xlim)
        target_zoom = min(self.maximum_zoom, max(self.minimum_zoom, current_zoom * scale))
        x_span = (xlim[1] - xlim[0]) / (target_zoom / current_zoom)
        y_span = (ylim[1] - ylim[0]) / (target_zoom / current_zoom)
        x_fraction = (anchor_x - xlim[0]) / (xlim[1] - xlim[0])
        y_fraction = (anchor_y - ylim[0]) / (ylim[1] - ylim[0])
        proposed_x = (anchor_x - x_fraction * x_span, anchor_x + (1 - x_fraction) * x_span)
        proposed_y = (anchor_y - y_fraction * y_span, anchor_y + (1 - y_fraction) * y_span)
        return self._clamp_limits(proposed_x, self.base_xlim), self._clamp_limits(proposed_y, self.base_ylim)

    def pan_limits(
        self,
        xlim: tuple[float, float],
        ylim: tuple[float, float],
        delta_x: float,
        delta_y: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Desloca a viewport em unidades PDF e impede sair dos limites do mapa."""

        proposed_x = (xlim[0] + delta_x, xlim[1] + delta_x)
        proposed_y = (ylim[0] + delta_y, ylim[1] + delta_y)
        return self._clamp_limits(proposed_x, self.base_xlim), self._clamp_limits(proposed_y, self.base_ylim)

    @staticmethod
    def _clamp_limits(
        proposed: tuple[float, float],
        base: tuple[float, float],
    ) -> tuple[float, float]:
        descending = proposed[1] < proposed[0]
        proposed_low, proposed_high = sorted(proposed)
        base_low, base_high = sorted(base)
        span = proposed_high - proposed_low
        base_span = base_high - base_low
        if span >= base_span:
            return base
        if proposed_low < base_low:
            proposed_high += base_low - proposed_low
            proposed_low = base_low
        if proposed_high > base_high:
            proposed_low -= proposed_high - base_high
            proposed_high = base_high
        return (proposed_high, proposed_low) if descending else (proposed_low, proposed_high)


def display_to_pdf(
    display_x: float,
    display_y: float,
    axes_bounds: tuple[float, float, float, float],
    xlim: tuple[float, float],
    ylim: tuple[float, float],
) -> tuple[float, float] | None:
    """Converte coordenadas de display Matplotlib (origem inferior) para PDF."""

    left, bottom, width, height = axes_bounds
    if not left <= display_x <= left + width or not bottom <= display_y <= bottom + height:
        return None
    x_fraction = (display_x - left) / width
    y_fraction = (display_y - bottom) / height
    return (
        xlim[0] + x_fraction * (xlim[1] - xlim[0]),
        ylim[0] + y_fraction * (ylim[1] - ylim[0]),
    )


def screen_to_pdf(
    screen_x: float,
    screen_y: float,
    canvas_height: float,
    axes_bounds: tuple[float, float, float, float],
    xlim: tuple[float, float],
    ylim: tuple[float, float],
) -> tuple[float, float] | None:
    """Converte uma posição Tk (origem superior) para coordenadas PDF."""

    return display_to_pdf(screen_x, canvas_height - screen_y, axes_bounds, xlim, ylim)


def render_pdf_for_picker(pdf_path: Path, scale: float = 1.5) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Renderiza o PDF e devolve pixels mais o extent exato em coordenadas PDF."""

    if scale <= 0:
        raise ValueError("A escala de renderização precisa ser positiva.")
    document = pymupdf.open(pdf_path)
    if len(document) != 1:
        raise ValueError("O GCP Picker espera um PDF de uma página.")
    page = document[0]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    rgb = image[:, :, :3].copy()
    extent = (page.rect.x0, page.rect.x1, page.rect.y1, page.rect.y0)
    document.close()
    return rgb, extent


def load_map_bounds(geojson_path: Path, fallback_extent: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Usa a extensão das áreas, evitando que margens do layout diluam o diagnóstico."""

    if not geojson_path.is_file():
        return (fallback_extent[0], fallback_extent[3], fallback_extent[1], fallback_extent[2])
    collection = json.loads(geojson_path.read_text(encoding="utf-8"))
    geometries = [shape(feature["geometry"]) for feature in collection.get("features", [])]
    if not geometries:
        return (fallback_extent[0], fallback_extent[3], fallback_extent[1], fallback_extent[2])
    return unary_union(geometries).bounds


class GcpPickerApp:
    """Interface Tkinter/Matplotlib voltada à precisão do clique no PDF."""

    def __init__(
        self,
        root: tk.Tk,
        image: np.ndarray,
        extent: tuple[float, float, float, float],
        store: ControlPointStore,
        map_bounds: tuple[float, float, float, float],
    ) -> None:
        self.root = root
        self.store = store
        self.map_bounds = map_bounds
        self.extent = extent
        self.viewport = PdfViewport.from_extent(extent)
        self.selected_index: int | None = None
        self.clicked_pdf_position: tuple[float, float] | None = None
        self.marker_artists: list[Any] = []
        self.pan_start: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None

        root.title("GeoJardins - GCP Picker")
        root.geometry("1500x930")
        root.minsize(1100, 700)

        self.status_var = tk.StringVar()
        self.cursor_var = tk.StringVar(value="Cursor: -")
        self.pdf_x_var = tk.StringVar()
        self.pdf_y_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.latitude_var = tk.StringVar()
        self.longitude_var = tk.StringVar()
        self.role_var = tk.StringVar(value="FIT")
        self.show_markers_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.StringVar(value="Zoom: 100%")

        paned = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        map_frame = ttk.Frame(paned)
        side_frame = ttk.Frame(paned, padding=8)
        paned.add(map_frame, weight=4)
        paned.add(side_frame, weight=1)

        figure = Figure(figsize=(9, 12), dpi=100)
        self.axis = figure.add_subplot(111)
        self.axis.imshow(image, extent=extent, origin="upper")
        self.axis.set_xlim(extent[0], extent[1])
        self.axis.set_ylim(extent[2], extent[3])
        self.axis.set_aspect("equal")
        self.axis.set_title("Clique em um cruzamento - use a barra para zoom e pan", fontsize=10)
        self.axis.set_xlabel("pdf_x")
        self.axis.set_ylabel("pdf_y")

        self.canvas = FigureCanvasTkAgg(figure, master=map_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, map_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(fill=tk.X)
        self._build_view_controls(map_frame)
        ttk.Label(map_frame, textvariable=self.cursor_var, anchor="w").pack(fill=tk.X, padx=6, pady=3)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_press_event", self._on_map_click)
        self.canvas.mpl_connect("button_press_event", self._on_pan_start)
        self.canvas.mpl_connect("button_release_event", self._on_pan_end)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.bind("<Enter>", lambda _event: canvas_widget.focus_set(), add="+")
        canvas_widget.bind("<KeyPress>", self._on_key_press, add="+")

        self._build_side_panel(side_frame)
        self._refresh_all()

    def _build_view_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, padding=(6, 2))
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="+", width=4, command=lambda: self._zoom_at_center(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="-", width=4, command=lambda: self._zoom_at_center(0.8)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Ajustar à tela", command=self._fit_to_screen).pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="100%", command=self._set_100_percent).pack(side=tk.LEFT, padx=2)
        ttk.Label(controls, textvariable=self.zoom_var).pack(side=tk.RIGHT, padx=6)

    def _current_limits(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return tuple(self.axis.get_xlim()), tuple(self.axis.get_ylim())

    def _set_view_limits(self, xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
        self.axis.set_xlim(xlim)
        self.axis.set_ylim(ylim)
        self.zoom_var.set(f"Zoom: {self.viewport.zoom_percentage(xlim)}%")
        self.canvas.draw_idle()

    def _zoom_at(self, pdf_x: float, pdf_y: float, scale: float) -> None:
        xlim, ylim = self._current_limits()
        next_x, next_y = self.viewport.zoom_limits(xlim, ylim, pdf_x, pdf_y, scale)
        self._set_view_limits(next_x, next_y)

    def _zoom_at_center(self, scale: float) -> None:
        xlim, ylim = self._current_limits()
        self._zoom_at((xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2, scale)

    def _fit_to_screen(self) -> None:
        xlim, ylim = self.viewport.fit_limits()
        self._set_view_limits(xlim, ylim)

    def _set_100_percent(self) -> None:
        self._fit_to_screen()

    def _on_scroll(self, event: Any) -> None:
        """Trata a roda do Windows entregue pelo backend TkAgg como scroll_event."""

        if event.inaxes is not self.axis or event.xdata is None or event.ydata is None:
            return
        step = getattr(event, "step", 0)
        if step > 0 or event.button == "up":
            self._zoom_at(float(event.xdata), float(event.ydata), 1.25)
        elif step < 0 or event.button == "down":
            self._zoom_at(float(event.xdata), float(event.ydata), 0.8)

    def _on_key_press(self, event: tk.Event[Any]) -> str | None:
        keysym = event.keysym
        if keysym in {"plus", "equal", "KP_Add"}:
            self._zoom_at_center(1.25)
            return "break"
        if keysym in {"minus", "KP_Subtract"}:
            self._zoom_at_center(0.8)
            return "break"
        if keysym in {"0", "KP_0"}:
            self._fit_to_screen()
            return "break"
        return None

    def _build_side_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="GCPs cadastrados", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.tree = ttk.Treeview(
            parent,
            columns=("number", "role", "name"),
            show="headings",
            height=12,
            selectmode="browse",
        )
        self.tree.heading("number", text="#")
        self.tree.heading("role", text="Uso")
        self.tree.heading("name", text="Nome")
        self.tree.column("number", width=35, anchor="center", stretch=False)
        self.tree.column("role", width=90, anchor="center", stretch=False)
        self.tree.column("name", width=230)
        self.tree.pack(fill=tk.X, pady=(4, 6))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Checkbutton(
            parent,
            text="Exibir marcadores no mapa",
            variable=self.show_markers_var,
            command=self._draw_markers,
        ).pack(anchor="w", pady=(0, 8))

        form = ttk.LabelFrame(parent, text="Dados do ponto", padding=8)
        form.pack(fill=tk.X)
        self._entry_row(form, 0, "Nome", self.name_var)
        self._entry_row(form, 1, "pdf_x", self.pdf_x_var, readonly=True)
        self._entry_row(form, 2, "pdf_y", self.pdf_y_var, readonly=True)
        self._entry_row(form, 3, "Latitude", self.latitude_var)
        self._entry_row(form, 4, "Longitude", self.longitude_var)

        ttk.Label(form, text="Uso").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Combobox(
            form,
            textvariable=self.role_var,
            values=("FIT", "VALIDATION"),
            state="readonly",
            width=18,
        ).grid(row=5, column=1, sticky="ew", pady=3)

        ttk.Label(form, text="Notas").grid(row=6, column=0, sticky="nw", pady=3)
        self.notes_text = tk.Text(form, height=4, width=28, wrap="word")
        self.notes_text.grid(row=6, column=1, sticky="ew", pady=3)
        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(parent)
        buttons.pack(fill=tk.X, pady=8)
        ttk.Button(buttons, text="Salvar novo GCP", command=self._save_new).pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Atualizar selecionado", command=self._update_selected).pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Remover selecionado", command=self._remove_selected).pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Limpar formulário", command=self._clear_form).pack(fill=tk.X, pady=2)

        recommendation = (
            "Mínimo operacional: 6 pontos (≥4 FIT e ≥2 VALIDATION).\n"
            "Recomendado: 12 a 20, distribuídos por norte, sul, leste, oeste e centro."
        )
        ttk.Label(parent, text=recommendation, wraplength=340, foreground="#374151").pack(anchor="w", pady=(6, 4))
        ttk.Label(parent, textvariable=self.status_var, wraplength=340, foreground="#8a3b12").pack(anchor="w")

    @staticmethod
    def _entry_row(
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        readonly: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable, width=26)
        if readonly:
            entry.configure(state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=3)

    def _on_motion(self, event: Any) -> None:
        if self.pan_start is not None:
            if event.inaxes is self.axis and event.xdata is not None and event.ydata is not None:
                start_x, start_y, start_xlim, start_ylim = self.pan_start
                next_x, next_y = self.viewport.pan_limits(
                    start_xlim,
                    start_ylim,
                    start_x - float(event.xdata),
                    start_y - float(event.ydata),
                )
                self._set_view_limits(next_x, next_y)
            return
        if event.inaxes is not self.axis or event.xdata is None or event.ydata is None:
            self.cursor_var.set("Cursor: -")
            return
        nearest = self._nearest_marker(event.x, event.y)
        if nearest is not None:
            point = self.store.points[nearest]
            self.cursor_var.set(
                f"#{nearest + 1} {point.name} [{point.role}] - "
                f"pdf=({point.pdf_x:.3f}, {point.pdf_y:.3f})"
            )
        else:
            self.cursor_var.set(f"Cursor: pdf_x={event.xdata:.3f}  pdf_y={event.ydata:.3f}")

    def _on_pan_start(self, event: Any) -> None:
        if (
            event.inaxes is self.axis
            and event.button == 2
            and event.xdata is not None
            and event.ydata is not None
            and not bool(self.toolbar.mode)
        ):
            xlim, ylim = self._current_limits()
            self.pan_start = (float(event.xdata), float(event.ydata), xlim, ylim)

    def _on_pan_end(self, event: Any) -> None:
        if event.button == 2:
            self.pan_start = None

    def _nearest_marker(self, display_x: float, display_y: float, tolerance_pixels: float = 12) -> int | None:
        if not self.show_markers_var.get():
            return None
        nearest: tuple[float, int] | None = None
        for index, point in enumerate(self.store.points):
            marker_x, marker_y = self.axis.transData.transform((point.pdf_x, point.pdf_y))
            distance = float(np.hypot(display_x - marker_x, display_y - marker_y))
            if distance <= tolerance_pixels and (nearest is None or distance < nearest[0]):
                nearest = (distance, index)
        return nearest[1] if nearest else None

    def _on_map_click(self, event: Any) -> None:
        if (
            event.inaxes is not self.axis
            or event.button != 1
            or event.xdata is None
            or event.ydata is None
            or bool(self.toolbar.mode)
        ):
            return
        marker_index = self._nearest_marker(event.x, event.y)
        if marker_index is not None:
            self.tree.selection_set(str(marker_index))
            self.tree.focus(str(marker_index))
            self._populate_form(marker_index)
            return
        pdf_position = display_to_pdf(
            float(event.x),
            float(event.y),
            tuple(self.axis.bbox.bounds),
            *self._current_limits(),
        )
        if pdf_position is None:
            return
        self.clicked_pdf_position = pdf_position
        self.pdf_x_var.set(f"{pdf_position[0]:.6f}")
        self.pdf_y_var.set(f"{pdf_position[1]:.6f}")
        self._draw_markers()

    def _on_tree_select(self, _event: Any) -> None:
        selection = self.tree.selection()
        if selection:
            self._populate_form(int(selection[0]))

    def _populate_form(self, index: int) -> None:
        point = self.store.points[index]
        self.selected_index = index
        self.clicked_pdf_position = (point.pdf_x, point.pdf_y)
        self.name_var.set(point.name)
        self.pdf_x_var.set(f"{point.pdf_x:.6f}")
        self.pdf_y_var.set(f"{point.pdf_y:.6f}")
        self.latitude_var.set(f"{point.latitude:.8f}")
        self.longitude_var.set(f"{point.longitude:.8f}")
        self.role_var.set(point.role)
        self.notes_text.delete("1.0", tk.END)
        self.notes_text.insert("1.0", point.notes)
        self._draw_markers()

    def _point_from_form(self) -> ControlPoint:
        if self.clicked_pdf_position is None:
            raise ControlPointError("Clique primeiro em um ponto reconhecível do mapa.")
        try:
            latitude = float(self.latitude_var.get().replace(",", "."))
            longitude = float(self.longitude_var.get().replace(",", "."))
        except ValueError as exc:
            raise ControlPointError("Latitude e longitude precisam ser números decimais.") from exc
        return ControlPoint(
            name=self.name_var.get().strip(),
            pdf_x=self.clicked_pdf_position[0],
            pdf_y=self.clicked_pdf_position[1],
            latitude=latitude,
            longitude=longitude,
            role=self.role_var.get(),  # type: ignore[arg-type]
            notes=self.notes_text.get("1.0", tk.END).strip(),
        )

    def _save_new(self) -> None:
        try:
            self.store.add(self._point_from_form())
        except ControlPointError as exc:
            messagebox.showerror("GCP não salvo", str(exc), parent=self.root)
            return
        self._clear_form()
        self._refresh_all()

    def _update_selected(self) -> None:
        if self.selected_index is None:
            messagebox.showwarning("Seleção necessária", "Selecione um GCP para atualizar.", parent=self.root)
            return
        try:
            point = self._point_from_form()
        except ControlPointError as exc:
            messagebox.showerror("GCP não atualizado", str(exc), parent=self.root)
            return
        if not messagebox.askyesno(
            "Confirmar atualização",
            f"Substituir explicitamente os dados de '{self.store.points[self.selected_index].name}'?",
            parent=self.root,
        ):
            return
        try:
            self.store.update(self.selected_index, point)
        except ControlPointError as exc:
            messagebox.showerror("GCP não atualizado", str(exc), parent=self.root)
            return
        self._refresh_all()

    def _remove_selected(self) -> None:
        if self.selected_index is None:
            messagebox.showwarning("Seleção necessária", "Selecione um GCP para remover.", parent=self.root)
            return
        point = self.store.points[self.selected_index]
        if not messagebox.askyesno(
            "Confirmar remoção",
            f"Remover o GCP '{point.name}'?",
            parent=self.root,
        ):
            return
        self.store.remove(self.selected_index)
        self._clear_form()
        self._refresh_all()

    def _clear_form(self) -> None:
        self.selected_index = None
        self.clicked_pdf_position = None
        self.name_var.set("")
        self.pdf_x_var.set("")
        self.pdf_y_var.set("")
        self.latitude_var.set("")
        self.longitude_var.set("")
        self.role_var.set("FIT")
        self.notes_text.delete("1.0", tk.END)
        self.tree.selection_remove(self.tree.selection())
        self._draw_markers()

    def _refresh_all(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, point in enumerate(self.store.points):
            self.tree.insert("", tk.END, iid=str(index), values=(index + 1, point.role, point.name))
        self._draw_markers()
        distribution = assess_spatial_distribution(self.store.points, self.map_bounds)
        fit_count = sum(point.role == "FIT" for point in self.store.points)
        validation_count = sum(point.role == "VALIDATION" for point in self.store.points)
        warnings = distribution["warnings"]
        summary = f"Total {len(self.store.points)} | FIT {fit_count} | VALIDATION {validation_count}"
        if warnings:
            summary += "\n" + "\n".join(f"• {warning}" for warning in warnings)
        self.status_var.set(summary)

    def _draw_markers(self) -> None:
        for artist in self.marker_artists:
            artist.remove()
        self.marker_artists.clear()
        if self.show_markers_var.get():
            for index, point in enumerate(self.store.points):
                color = "#0066cc" if point.role == "FIT" else "#e06b00"
                marker = "o" if point.role == "FIT" else "s"
                scatter = self.axis.scatter(
                    [point.pdf_x], [point.pdf_y], s=48, c=color, marker=marker, edgecolors="white", linewidths=0.8, zorder=5
                )
                label = self.axis.text(
                    point.pdf_x,
                    point.pdf_y,
                    str(index + 1),
                    color="white",
                    fontsize=7,
                    ha="center",
                    va="center",
                    weight="bold",
                    zorder=6,
                )
                self.marker_artists.extend([scatter, label])
        if self.clicked_pdf_position is not None and self.selected_index is None:
            click = self.axis.scatter(
                [self.clicked_pdf_position[0]],
                [self.clicked_pdf_position[1]],
                s=100,
                facecolors="none",
                edgecolors="#dc2626",
                linewidths=1.5,
                marker="+",
                zorder=7,
            )
            self.marker_artists.append(click)
        self.canvas.draw_idle()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Abre o mapa e cadastra GCPs com zoom e pan.")
    parser.add_argument("--pdf", type=Path, help="PDF-fonte; detectado em data/raw/ por padrão.")
    parser.add_argument(
        "--control-points",
        type=Path,
        default=Path("data/control_points/control_points.csv"),
        help="CSV editado pelo picker.",
    )
    parser.add_argument(
        "--areas",
        type=Path,
        default=Path("data/interim/areas_pdf_coordinates.geojson"),
        help="GeoJSON PDF usado para diagnosticar distribuição.",
    )
    parser.add_argument("--render-scale", type=float, default=1.5, help="Escala raster do mapa; padrão 1,5.")
    parser.add_argument("--check", action="store_true", help="Valida renderização/CSV sem abrir janela.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        pdf_path = resolve_pdf_path(args.pdf)
        image, extent = render_pdf_for_picker(pdf_path, args.render_scale)
        store = ControlPointStore(args.control_points)
        map_bounds = load_map_bounds(args.areas, extent)
    except (ControlPointError, FileNotFoundError, ValueError) as exc:
        print(f"GCP Picker não iniciado: {exc}")
        return 2

    if args.check:
        print(
            f"GCP Picker OK: PDF {pdf_path.name}, imagem {image.shape[1]}x{image.shape[0]}, "
            f"{len(store.points)} GCP(s), extent PDF {extent}."
        )
        return 0

    root = tk.Tk()
    GcpPickerApp(root, image, extent, store, map_bounds)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
