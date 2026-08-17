"""Enhancement opcional da title bar nativa no Windows 11.

Constantes de ``DWMWINDOWATTRIBUTE``: DWMWA_BORDER_COLOR=34,
DWMWA_CAPTION_COLOR=35 e DWMWA_TEXT_COLOR=36, conforme a documentação do
Windows SDK. Chamadas recusadas por versões antigas do Windows são seguras.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def colorref(hex_color: str) -> int:
    """Converte #RRGGBB para COLORREF (0x00BBGGRR) do Win32."""

    value = hex_color.removeprefix("#")
    if len(value) != 6:
        raise ValueError("A cor precisa estar no formato #RRGGBB.")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    try:
        function = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        function.restype = ctypes.c_long
        color = wintypes.DWORD(value)
        return function(wintypes.HWND(hwnd), attribute, ctypes.byref(color), ctypes.sizeof(color)) == 0
    except (AttributeError, OSError, TypeError):
        return False


def apply_native_titlebar(hwnd: int, *, caption: str, text: str, border: str) -> bool:
    """Aplica cores DWM se disponíveis; mantém a title bar padrão em fallback."""

    if os.name != "nt" or not hwnd:
        return False
    try:
        return all(
            (
                _set_attribute(hwnd, DWMWA_CAPTION_COLOR, colorref(caption)),
                _set_attribute(hwnd, DWMWA_TEXT_COLOR, colorref(text)),
                _set_attribute(hwnd, DWMWA_BORDER_COLOR, colorref(border)),
            )
        )
    except (ValueError, OSError):
        return False
