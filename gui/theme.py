"""Токены glassmorphism-темы «Избранное+» — по спецификации pyqt6-gm-ui.

Тёмное стекло: тёплый градиентный фон с оранжевым свечением, «молочные» карточки
с backdrop-blur, оранжевый акцент #F97316.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPixmap


@dataclass(frozen=True)
class Theme:
    bg: str
    surface: str
    sidebar: str
    text: str
    text_secondary: str
    accent: str
    separator_rgb: str
    separator_alpha: int
    shadow_alpha: int
    # --- glassmorphism ---
    is_dark: bool = False
    bg_top: str = "#2A2622"
    bg_bottom: str = "#141210"
    glow_warm: str = "#9A5B2B"
    glow_warm_alpha: int = 120
    glow_cool: str = "#7C6BD9"
    glow_cool_alpha: int = 46
    glass_rgb: str = "#FFFFFF"
    glass_alpha: int = 30
    glass_nested_alpha: int = 16
    glass_border_rgb: str = "#FFFFFF"
    glass_border_alpha: int = 28
    sidebar_alpha: int = 205
    data_yellow: str = "#FFD84D"
    data_lime: str = "#C6F26E"


LIGHT = Theme(
    bg="#F2EFEA", surface="#FFFFFF", sidebar="#FFFFFF",
    text="#1D1D1F", text_secondary="#6E6A64",
    accent="#F97316", separator_rgb="#000000", separator_alpha=18, shadow_alpha=55,
    is_dark=False,
    bg_top="#F7F5F2", bg_bottom="#E8E4DE",
    glow_warm="#FFC79A", glow_warm_alpha=110,
    glow_cool="#C4B5FD", glow_cool_alpha=90,
    glass_rgb="#FFFFFF", glass_alpha=115, glass_nested_alpha=95,
    glass_border_rgb="#FFFFFF", glass_border_alpha=150,
    sidebar_alpha=170,
)

DARK = Theme(
    bg="#141210", surface="#2A2622", sidebar="#0A0A0A",
    text="#F5F2EE", text_secondary="#A8A29C",
    accent="#F97316", separator_rgb="#FFFFFF", separator_alpha=25, shadow_alpha=110,
    is_dark=True,
    bg_top="#2A2622", bg_bottom="#141210",
    glow_warm="#9A5B2B", glow_warm_alpha=120,
    glow_cool="#7C6BD9", glow_cool_alpha=46,
    glass_rgb="#FFFFFF", glass_alpha=30, glass_nested_alpha=16,
    glass_border_rgb="#FFFFFF", glass_border_alpha=28,
    sidebar_alpha=205,
)

RADIUS_WINDOW = 26
RADIUS_CARD = 20
RADIUS_SIDEBAR = 22
SIDEBAR_WIDTH = 200
RADIUS_CONTROL = 10
EDGE_MARGIN = 6
EDGE_TOP, EDGE_BOTTOM, EDGE_LEFT, EDGE_RIGHT = 1, 2, 4, 8

DUR_FAST = 140
DUR_PRESS = 90
DUR_RELEASE = 200
DUR_MED = 250
EASE = None   # QEasingCurve.Type.OutCubic — импортируется в widgets (QtCore)
EASE_PRESS = None


def system_font(point_size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    families = QFontDatabase.families()
    for candidate in ("Inter", "Segoe UI Variable Text", ".AppleSystemUIFont",
                      "SF Pro Text", "Helvetica Neue", "Manrope", "Segoe UI"):
        if candidate in families:
            font = QFont(candidate, point_size)
            font.setWeight(weight)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            return font
    font = QFont()
    font.setPointSize(point_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def rgba(hex_color: str, alpha_0_255: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha_0_255})"


def accent_fg(theme: Theme) -> str:
    color = QColor(theme.accent)
    luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
    return "#1D1D1F" if luminance > 0.6 else "#FFFFFF"


def shadow_color(theme: Theme) -> QColor:
    color = QColor(0, 0, 0)
    color.setAlpha(theme.shadow_alpha)
    return color


def lerp_color(start: QColor, end: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
    )


def blur_pixmap(pm: QPixmap, factor: int = 8) -> QPixmap:
    """Box-blur: downscale со сглаживанием и обратно (по спецификации gm-ui)."""
    w, h = pm.width(), pm.height()
    if w <= 0 or h <= 0:
        return pm
    sw, sh = max(1, w // factor), max(1, h // factor)
    small = pm.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def build_stylesheet(t: Theme) -> str:
    """QSS поверх стекла: поля, списки, комбобоксы, скроллбары (в языке gm-ui)."""
    return f"""
* {{ outline: none; }}
QWidget {{ font-family: 'Inter', 'Segoe UI'; color: {t.text}; }}
#PageScroll, #PageBody {{ background: transparent; }}
QLabel {{ background: transparent; }}
#TitleBar {{ background-color: transparent; }}
QPushButton#NavButton {{
  text-align: left; border: none; border-radius: 10px; padding-left: 36px;
  color: {t.text_secondary}; background: transparent; }}
QPushButton#NavButton:checked {{ color: {t.text}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QTimeEdit {{
  background-color: {rgba(t.glass_rgb, t.glass_nested_alpha)};
  border: 1px solid {rgba(t.separator_rgb, 40)};
  border-radius: {RADIUS_CONTROL}px; padding: 4px 12px;
  color: {t.text}; font-size: 13px;
  selection-background-color: {t.accent}; selection-color: {accent_fg(t)}; }}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QComboBox:hover {{
  border-color: {rgba(t.separator_rgb, 90)}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
  border: 1px solid {t.accent}; background-color: {rgba(t.accent, 10)}; }}
QLineEdit::placeholder {{ color: {t.text_secondary}; }}

QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;
  border-right: 4px solid transparent; border-top: 5px solid {t.text_secondary};
  margin-right: 10px; }}
QComboBox QAbstractItemView {{
  background-color: {rgba(t.sidebar if t.is_dark else t.surface, 235)};
  color: {t.text}; border: 1px solid {rgba(t.glass_border_rgb, t.glass_border_alpha)};
  border-radius: 12px; padding: 6px;
  selection-background-color: {t.accent}; selection-color: {accent_fg(t)}; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 4px 10px; border-radius: 8px; }}

QPlainTextEdit#LogView {{
  background-color: {rgba("#000000", 120 if t.is_dark else 26)};
  color: {t.data_yellow if t.is_dark else "#8A5A2B"};
  font-family: 'Consolas', monospace; font-size: 12px;
  border: 1px solid {rgba(t.separator_rgb, 40)}; border-radius: 14px; }}

QListWidget {{
  background-color: {rgba(t.glass_rgb, t.glass_nested_alpha)};
  border: 1px solid {rgba(t.separator_rgb, 40)}; border-radius: 14px;
  color: {t.text}; font-size: 13px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; }}
QListWidget::item:hover {{ background: {rgba(t.separator_rgb, 26)}; }}
QListWidget::item:selected {{ background: {t.accent}; color: {accent_fg(t)}; }}

QCheckBox {{ spacing: 10px; color: {t.text}; font-size: 13px; }}
QCheckBox::indicator {{ width: 18px; height: 18px;
  border: 1px solid {rgba(t.separator_rgb, 120)}; border-radius: 6px; background: transparent; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}

QToolTip {{ background: {t.surface}; color: {t.text};
  border: 1px solid {rgba(t.glass_border_rgb, t.glass_border_alpha)}; border-radius: 8px; padding: 6px; }}

QScrollBar {{ background: transparent; }}
QScrollBar:vertical {{ width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {rgba(t.separator_rgb, 60)};
  border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {rgba(t.separator_rgb, 120)}; }}
QScrollBar:horizontal {{ height: 6px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {rgba(t.separator_rgb, 60)};
  border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {rgba(t.separator_rgb, 120)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: transparent; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox {{ background: {t.bg}; }}
QMessageBox QLabel {{ color: {t.text}; font-size: 13px; }}
"""
