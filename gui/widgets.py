"""Стеклянные виджеты «Избранное+» — перенос спецификации pyqt6-gm-ui.

Все капканы SKILL.md учтены: QPen только с QColor-альфой, drawLine — int-координаты,
rgba-альфа 0..255, Qt.Edge из своих бит-констант, ссылки на анимации в атрибутах,
WA_StyledBackground на QSS-фонах.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QParallelAnimationGroup,
                          QPropertyAnimation, QPoint, QPointF, QRect, QRectF, QTimer, Qt,
                          pyqtProperty, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

from .theme import (DUR_FAST, DUR_MED, DUR_PRESS, DUR_RELEASE, SIDEBAR_WIDTH, DARK, Theme,
                    accent_fg, blur_pixmap, lerp_color, rgba, shadow_color, system_font)

EASE = QEasingCurve.Type.OutCubic
EASE_PRESS = QEasingCurve.Type.OutQuad


def animate_property(anim: QPropertyAnimation, current: float, target: float,
                     duration: int, easing: QEasingCurve.Type) -> None:
    anim.stop()
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.setStartValue(current)
    anim.setEndValue(target)
    anim.start()


# ---------------------------------------------------------------------------
# Градиентная подложка окна с «дышащими» свечениями
# ---------------------------------------------------------------------------

class GradientPanel(QFrame):
    BG_PERIOD_MS = 21000
    BG_FPS = 30

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("Panel")
        self._bg_cache = None
        self._bg_revision = 0
        self._phase = 0.0
        self._last_tick = 0.0
        self._bg_anim: QPropertyAnimation | None = None

    def paint_background(self, painter: QPainter, rect: QRectF) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        t = self._theme
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(t.bg_top))
        grad.setColorAt(1.0, QColor(t.bg_bottom))
        painter.setBrush(grad)
        painter.drawRect(rect)

        w, h = rect.width(), rect.height()
        ph = self._phase

        def osc(k: int, phi: float) -> float:
            return 0.5 * (math.sin(k * ph + phi) - math.sin(phi))

        base = max(w, h)
        self._radial(painter,
                     QPointF(rect.x() + w * (0.52 + 0.09 * osc(1, 0.7)),
                             rect.y() + h * (0.08 + 0.07 * osc(2, 2.0))),
                     base * 0.62 * (1.0 + 0.12 * osc(1, 3.1)),
                     t.glow_warm, round(t.glow_warm_alpha * (1.0 + 0.14 * osc(2, 1.1))))
        self._radial(painter,
                     QPointF(rect.x() + w * (0.10 + 0.08 * osc(1, 2.9)),
                             rect.y() + h * (0.04 + 0.06 * osc(3, 0.4))),
                     base * 0.45 * (1.0 + 0.11 * osc(2, 4.2)),
                     t.glow_cool, round(t.glow_cool_alpha * (1.0 + 0.15 * osc(1, 5.0))))
        self._radial(painter,
                     QPointF(rect.x() + w * (0.92 + 0.07 * osc(2, 1.6)),
                             rect.y() + h * (0.98 + 0.06 * osc(1, 4.4))),
                     base * 0.50 * (1.0 + 0.10 * osc(3, 2.2)),
                     t.glow_warm, round(t.glow_warm_alpha * 0.35 * (1.0 + 0.15 * osc(2, 3.3))))
        painter.restore()

    @staticmethod
    def _radial(painter: QPainter, center: QPointF, radius: float,
                hex_color: str, alpha: int) -> None:
        if radius <= 0 or alpha <= 0:
            return
        g = QRadialGradient(center, radius)
        c0 = QColor(hex_color)
        c0.setAlpha(max(0, min(255, alpha)))
        c1 = QColor(hex_color)
        c1.setAlpha(0)
        g.setColorAt(0.0, c0)
        g.setColorAt(1.0, c1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(g)
        painter.drawEllipse(center, radius, radius)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._bg_cache = None
        self._bg_revision += 1
        super().resizeEvent(event)
        self.update()

    def background_pixmap(self) -> QPixmap | None:
        if self.width() <= 0 or self.height() <= 0:
            return None
        if self._bg_cache is None or self._bg_cache.size() != self.size():
            self._render_bg_cache()
        return self._bg_cache

    def _render_bg_cache(self) -> None:
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint_background(p, QRectF(0, 0, self.width(), self.height()))
        p.end()
        self._bg_cache = pm

    def start_background_animation(self) -> None:
        if self._bg_anim is None:
            anim = QPropertyAnimation(self, b"phase", self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(self.BG_PERIOD_MS)
            anim.setLoopCount(-1)
            self._bg_anim = anim
        anim = self._bg_anim
        if anim.state() == QAbstractAnimation.State.Running:
            return
        anim.start()

    def stop_background_animation(self) -> None:
        if self._bg_anim is not None:
            self._bg_anim.stop()
            self._bg_anim = None

    @pyqtProperty(float)
    def phase(self) -> float:
        return self._phase / (2.0 * math.pi)

    @phase.setter
    def phase(self, v: float) -> None:
        now = time.monotonic()
        if now - self._last_tick < 1.0 / self.BG_FPS:
            return
        self._last_tick = now
        self._phase = float(v) * 2.0 * math.pi
        if self.width() <= 0 or self.height() <= 0:
            return
        self._render_bg_cache()
        self._bg_revision += 1
        self.update()
        for glass in self.findChildren(GlassSurface):
            if glass._blur:
                glass.invalidate_backdrop()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.width() <= 0 or self.height() <= 0:
            return
        pm = self.background_pixmap()
        if pm is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 26, 26)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pm)
        painter.setClipping(False)
        border = QColor(self._theme.glass_border_rgb)
        border.setAlpha(30 if self._theme.is_dark else 120)
        pen = QPen(border)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 26, 26)
        painter.end()


# ---------------------------------------------------------------------------
# Стекло с backdrop-blur из кеша фона панели
# ---------------------------------------------------------------------------

class GlassSurface(QFrame):
    def __init__(self, theme: Theme, radius: float, tint_rgb: str, tint_alpha: int,
                 border_alpha: int, blur: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._radius = radius
        self._tint_rgb = tint_rgb
        self._tint_alpha = tint_alpha
        self._border_alpha = border_alpha
        self._blur = blur
        self._bd_key = None
        self._bd_pm: QPixmap | None = None

    def invalidate_backdrop(self) -> None:
        self._bd_key = None
        self._bd_pm = None
        self.update()

    def _backdrop(self) -> QPixmap | None:
        if not self._blur:
            return None
        win = self.window()
        if win is None:
            return None
        panel = win.findChild(GradientPanel)
        if panel is None or panel.width() <= 0 or panel.height() <= 0:
            return None
        if not panel.isAncestorOf(self):
            return None
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        offset = self.mapTo(panel, QPoint(0, 0))
        key = (w, h, offset.x(), offset.y(), panel._bg_revision)
        if self._bd_key == key and self._bd_pm is not None:
            return self._bd_pm
        src = panel.background_pixmap()
        if src is None:
            return None
        rect = QRect(offset.x(), offset.y(), w, h)
        clipped = rect.intersected(src.rect())
        if clipped.isEmpty():
            return None
        if clipped == rect:
            region = src.copy(clipped)
        else:
            region = QPixmap(w, h)
            region.fill(Qt.GlobalColor.transparent)
            p = QPainter(region)
            p.drawPixmap(clipped.topLeft() - rect.topLeft(), src.copy(clipped))
            p.end()
        blurred = blur_pixmap(region)
        self._bd_key = key
        self._bd_pm = blurred
        return blurred

    def _paint_extra(self, painter: QPainter, rect: QRectF) -> None:
        """Хук для наследников (hero-свечение, статус и т.п.)."""

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = self._radius
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        backdrop = self._backdrop()
        if backdrop is not None:
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, backdrop)
            painter.restore()

        tint = QColor(self._tint_rgb)
        tint.setAlpha(max(0, min(255, self._tint_alpha)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(rect, r, r)

        self._paint_extra(painter, rect)

        border = QColor(self._theme.glass_border_rgb)
        border.setAlpha(max(0, min(255, self._border_alpha)))
        pen = QPen(border)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawRoundedRect(rect, r, r)
        painter.end()


class GlassCard(GlassSurface):
    def __init__(self, theme: Theme, nested: bool = False, parent=None) -> None:
        alpha = theme.glass_nested_alpha if nested else theme.glass_alpha
        border = round(theme.glass_border_alpha * (0.6 if nested else 1.0))
        radius = 14 if nested else 20
        super().__init__(theme, radius, theme.glass_rgb, alpha, border,
                         blur=not nested, parent=parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.invalidate_backdrop()
        self.update()


class HeroCard(GlassCard):
    """Hero-зона с оранжевым свечением, как на референсе."""

    def _paint_extra(self, painter: QPainter, rect: QRectF) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)
        center = QPointF(rect.x() + rect.width() * 0.60, rect.y() + rect.height() * 1.22)
        radius = max(rect.width(), rect.height()) * 0.62
        g = QRadialGradient(center, radius)
        c0 = QColor(self._theme.accent)
        c0.setAlpha(140 if self._theme.is_dark else 80)
        c1 = QColor(self._theme.accent)
        c1.setAlpha(0)
        g.setColorAt(0.0, c0)
        g.setColorAt(1.0, c1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(g)
        painter.drawEllipse(center, radius, radius)
        painter.restore()


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------

class HoverButton(QPushButton):
    """Кнопка с анимированным hover/press. kind: accent | primary | danger."""

    def __init__(self, text: str, theme: Theme, parent=None, kind: str = "accent") -> None:
        super().__init__(text, parent)
        self._theme = theme
        self.kind = kind
        self._hover = 0.0
        self._press = 0.0
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(system_font(13, QFont.Weight.Medium))
        self.setFixedHeight(34)
        self._apply_qss()
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(DUR_FAST)
        self._hover_anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)
        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._apply_qss()
        self.update()

    def _apply_qss(self) -> None:
        fg = "#1D1D1F" if self.kind == "primary" else (
            "#3F0808" if self.kind == "danger" else accent_fg(self._theme))
        self.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {fg};"
            f" padding: 0 18px; border-radius: 10px; }}"
            f"QPushButton:disabled {{ color: {rgba(fg, 110)}; }}")

    def _base_color(self) -> QColor:
        if self.kind == "primary":
            return QColor("#FFFFFF")
        if self.kind == "danger":
            c = QColor("#E05252")
            return c
        return QColor(self._theme.accent)

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def _on_pressed(self) -> None:
        animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)

    def _on_released(self) -> None:
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 0.0, DUR_FAST, EASE)
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = 1.2 * self._press
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter.setPen(Qt.PenStyle.NoPen)
        base = self._base_color()
        if not self.isEnabled():
            base = lerp_color(base, QColor("#000000" if self._theme.is_dark else "#FFFFFF"), 0.45)
        shade = lerp_color(base, QColor("#D9D4CD" if self.kind == "primary" else "#000000"),
                           min(1.0, 0.45 * self._hover + 0.65 * self._press) *
                           (0.6 if self.kind != "primary" else 1.0))
        painter.setBrush(shade)
        painter.drawRoundedRect(rect, 10, 10)

        if self._hover > 0.01:
            ring = QColor("#FFFFFF" if self.kind == "primary" else base)
            ring.setAlphaF(0.10 * self._hover)
            pen = QPen(ring)
            pen.setWidthF(1.2)
            painter.setPen(pen)
            painter.setBrush(QBrush())
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 11, 11)
        painter.end()
        super().paintEvent(event)


# ---------------------------------------------------------------------------
# Тумблер и слайдер
# ---------------------------------------------------------------------------

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, theme: Theme, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._checked = checked
        self._knob_pos = 1.0 if checked else 0.0
        self._hover = 0.0
        self._press = 0.0
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(DUR_MED)
        self._anim.setEasingCurve(EASE)
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(DUR_FAST)
        self._hover_anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def get_knob_pos(self) -> float:
        return self._knob_pos

    def set_knob_pos(self, value: float) -> None:
        self._knob_pos = value
        self.update()

    knobPos = pyqtProperty(float, fget=get_knob_pos, fset=set_knob_pos)

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if checked == self._checked:
            self.update()
            return
        self._checked = checked
        animate_property(self._anim, self._knob_pos, 1.0 if checked else 0.0, DUR_MED, EASE)

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 0.0, DUR_FAST, EASE)
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_rect = QRectF(self.rect().adjusted(0, 0, -1, -1))
        off = QColor(self._theme.separator_rgb)
        off_alpha = 46 if self._theme.is_dark else 40
        on = QColor(self._theme.accent)
        t = self._knob_pos
        track_color = lerp_color(off, on, t)
        track_color.setAlpha(round(off_alpha + (255 - off_alpha) * t))
        if self._hover > 0.01 or self._press > 0.01:
            track_color = lerp_color(
                track_color, QColor(self._theme.text), 0.05 * self._hover + 0.04 * self._press)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        radius = track_rect.height() / 2
        painter.drawRoundedRect(track_rect, radius, radius)

        knob_d = track_rect.height() - 4
        knob_w = knob_d + 4.0 * self._press
        x_min, x_max = 2.0, track_rect.width() - knob_w - 2.0
        knob_x = x_min + (x_max - x_min) * t
        knob_rect = QRectF(knob_x, 2.0, knob_w, knob_d)

        glow = QColor(self._theme.accent)
        glow.setAlphaF(0.09 * self._hover + 0.08 * self._press)
        if glow.alphaF() > 0.005:
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(knob_rect.center().x(), knob_rect.center().y()),
                                knob_w / 2 + 3.0, knob_d / 2 + 3.0)

        knob_shadow = QColor(0, 0, 0)
        knob_shadow.setAlpha(50)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_shadow)
        painter.drawEllipse(QRectF(knob_rect).adjusted(0, 1, 0, 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob_rect)
        painter.end()


class MacSlider(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, theme: Theme, value: int = 50, minimum: int = 0,
                 maximum: int = 100, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._min = minimum
        self._max = maximum
        self._value = max(minimum, min(maximum, value))
        self._hover = 0.0
        self._pressed = False
        self._press = 0.0
        self._fill = (self._value - self._min) / max(1, self._max - self._min)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(DUR_FAST)
        self._hover_anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)
        self._fill_anim = QPropertyAnimation(self, b"fill", self)
        self._fill_anim.setDuration(DUR_FAST)
        self._fill_anim.setEasingCurve(EASE)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def get_fill(self) -> float:
        return self._fill

    def set_fill(self, value: float) -> None:
        self._fill = max(0.0, min(1.0, value))
        self.update()

    fill = pyqtProperty(float, fget=get_fill, fset=set_fill)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int, animate: bool = True) -> None:
        v = max(self._min, min(self._max, v))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
        target = (self._value - self._min) / max(1, self._max - self._min)
        if self._pressed or not animate:
            self.set_fill(target)
        else:
            animate_property(self._fill_anim, self._fill, target, DUR_FAST, EASE)

    def _value_from_x(self, x: float) -> int:
        pad = 9.5
        track_w = self.width() - 2 * pad
        if track_w <= 0:
            return self._min
        ratio = min(1.0, max(0.0, (x - pad) / track_w))
        return round(self._min + ratio * (self._max - self._min))

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 0.0, DUR_FAST, EASE)
        if not self._pressed:
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)
            self.setValue(self._value_from_x(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pressed:
            self.setValue(self._value_from_x(event.position().x()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
            self.setValue(self._value)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        pad = 9.5
        track_h = 6.0 + 1.2 * self._hover + 0.6 * self._press
        cy = h / 2
        track_x0, track_x1 = pad, self.width() - pad
        ratio = max(0.0, min(1.0, self._fill))
        fill_end = track_x0 + ratio * (track_x1 - track_x0)

        painter.setPen(Qt.PenStyle.NoPen)
        track_bg = QColor(self._theme.separator_rgb)
        track_bg.setAlpha(30)
        painter.setBrush(track_bg)
        painter.drawRoundedRect(
            QRectF(track_x0, cy - track_h / 2, track_x1 - track_x0, track_h),
            track_h / 2, track_h / 2)

        if ratio > 0:
            painter.setBrush(QColor(self._theme.accent))
            painter.drawRoundedRect(
                QRectF(track_x0, cy - track_h / 2, fill_end - track_x0, track_h),
                track_h / 2, track_h / 2)

        knob_x = min(max(fill_end, track_x0), track_x1)
        r = 9.5 * (1.0 + 0.12 * self._hover - 0.18 * self._press)
        glow = QColor(self._theme.accent)
        glow.setAlphaF(0.22 * self._hover + 0.34 * self._press)
        if glow.alphaF() > 0.005:
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(knob_x, cy), r + 4, r + 4)
        knob_shadow = QColor(0, 0, 0)
        knob_shadow.setAlpha(26)
        painter.setBrush(knob_shadow)
        painter.drawEllipse(QPointF(knob_x, cy + 1), r, r)
        knob = QColor(self._theme.bg)
        painter.setBrush(knob)
        painter.drawEllipse(QPointF(knob_x, cy), r, r)
        ring = QColor(self._theme.separator_rgb)
        ring.setAlpha(255 if self._press else 90)
        pen = QPen(ring)
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawEllipse(QPointF(knob_x, cy), r, r)
        painter.end()


# ---------------------------------------------------------------------------
# Сегменты
# ---------------------------------------------------------------------------

class SegmentButton(QPushButton):
    def __init__(self, text: str, theme: Theme, parent=None) -> None:
        super().__init__(text, parent)
        self._theme = theme
        self.setCheckable(True)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setFont(system_font(12, QFont.Weight.Medium))

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._sync_style()

    def _sync_style(self) -> None:
        checked = self.isChecked()
        self.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; padding: 0 16px;"
            f" border-radius: 8px; color: {accent_fg(self._theme) if checked else self._theme.text_secondary}; }}")

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._sync_style()


class SegmentedControl(QWidget):
    changed = pyqtSignal(int)

    def __init__(self, items: list[str], theme: Theme, current: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._current = current
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self._indicator = QFrame(self)
        self._indicator.setObjectName("SegIndicator")
        self._indicator.setStyleSheet(
            f"background: {theme.accent}; border-radius: 8px;")
        self._buttons: list[SegmentButton] = []
        for i, label in enumerate(items):
            b = SegmentButton(label, theme, self)
            b.setChecked(i == current)
            b.clicked.connect(lambda _=False, idx=i: self._on_click(idx))
            row.addWidget(b)
            self._buttons.append(b)
        self._indicator.lower()
        self._anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._anim.setDuration(DUR_MED)
        self._anim.setEasingCurve(EASE)
        QTimer.singleShot(0, lambda: self._move_indicator(False))

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._indicator.setStyleSheet(f"background: {theme.accent}; border-radius: 8px;")
        for b in self._buttons:
            b.set_theme(theme)
        self.update()

    def current(self) -> int:
        return self._current

    def _on_click(self, idx: int) -> None:
        if idx == self._current:
            return
        self.set_current(idx)
        self.changed.emit(idx)

    def set_current(self, idx: int) -> None:
        self._current = idx
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)
        self._move_indicator(True)

    def _indicator_geo(self) -> QRect:
        if not self._buttons:
            return QRect(0, 0, 0, 0)
        b = self._buttons[self._current]
        return b.geometry()

    def _move_indicator(self, animate: bool) -> None:
        target = self._indicator_geo()
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._indicator.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._indicator.setGeometry(target)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._indicator.lower()
        self._move_indicator(False)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._move_indicator(False)


# ---------------------------------------------------------------------------
# Светофор, toggle сайдбара, тулбар
# ---------------------------------------------------------------------------

class TrafficLight(QPushButton):
    def __init__(self, theme: Theme, symbol: str, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._symbol = symbol  # minimize | maximize | close
        self._hover = 0.0
        self._press = 0.0
        self._maximized = False
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(DUR_FAST)
        self._anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def set_maximized(self, maximized: bool) -> None:
        if self._maximized != maximized:
            self._maximized = maximized
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._anim, self._hover, 0.0, DUR_FAST, EASE)
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        p = self._press

        if self._hover > 0.01:
            ring = (QColor("#FF5F57") if self._symbol == "close"
                    else QColor(self._theme.text_secondary))
            ring.setAlpha(round(55 * self._hover))
            ring_pen = QPen(ring)
            ring_pen.setWidthF(1.5)
            painter.setPen(ring_pen)
            painter.setBrush(QBrush())
            painter.drawEllipse(QRectF(rect).adjusted(-1.5, -1.5, 1.5, 1.5))

        inset = 1.0 + 1.4 * p
        circle_rect = QRectF(rect).adjusted(inset, inset, -inset, -inset)
        if self._symbol == "close":
            bg = QColor("#FF5F57")
            bg.setAlpha(round(200 + 35 * self._hover))
        else:
            bg = QColor(self._theme.text_secondary)
            bg.setAlpha(round(60 + 40 * self._hover))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(circle_rect)

        h = self._hover
        if self._symbol == "close":
            glyph = QColor("#FFFFFF")
            glyph.setAlpha(round(205 + 30 * h))
        else:
            glyph = lerp_color(QColor(self._theme.text_secondary), QColor(self._theme.text), h)
        pen = QPen(glyph)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QBrush())

        cx = rect.center().x() + 1
        cy = rect.center().y() + 1
        s = 1.0 - 0.14 * p

        def d(value: int) -> int:
            return round(value * s)

        if self._symbol == "close":
            painter.drawLine(cx - d(4), cy - d(4), cx + d(4), cy + d(4))
            painter.drawLine(cx + d(4), cy - d(4), cx - d(4), cy + d(4))
        elif self._symbol == "minimize":
            painter.drawLine(cx - d(5), cy - d(3), cx, cy + d(3))
            painter.drawLine(cx, cy + d(3), cx + d(5), cy - d(3))
        elif self._symbol == "maximize":
            if self._maximized:
                painter.drawLine(cx - d(5), cy - d(5), cx - d(5), cy - d(2))
                painter.drawLine(cx - d(5), cy - d(5), cx - d(2), cy - d(5))
                painter.drawLine(cx + d(5), cy - d(5), cx + d(5), cy - d(2))
                painter.drawLine(cx + d(5), cy - d(5), cx + d(2), cy - d(5))
                painter.drawLine(cx - d(5), cy + d(5), cx - d(5), cy + d(2))
                painter.drawLine(cx - d(5), cy + d(5), cx - d(2), cy + d(5))
                painter.drawLine(cx + d(5), cy + d(5), cx + d(5), cy + d(2))
                painter.drawLine(cx + d(5), cy + d(5), cx + d(2), cy + d(5))
            else:
                painter.drawLine(cx - d(5), cy + d(3), cx, cy - d(3))
                painter.drawLine(cx, cy - d(3), cx + d(5), cy + d(3))
        painter.end()
        super().paintEvent(event)


class SidebarToggle(QPushButton):
    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._hover = 0.0
        self._press = 0.0
        self._collapsed = False
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(DUR_FAST)
        self._anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed != collapsed:
            self._collapsed = collapsed
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._anim, self._hover, 0.0, DUR_FAST, EASE)
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._theme.separator_rgb)
        bg.setAlpha(round(26 * self._hover + 18 * self._press))
        if bg.alpha() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        rect = self.rect()
        cx = rect.center().x() + 1
        cy = rect.center().y() + 1
        s = 1.0 - 0.12 * self._press

        def d(value: int) -> int:
            return round(value * s)

        pen = QPen(lerp_color(QColor(self._theme.text_secondary), QColor(self._theme.text),
                              self._hover))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawRoundedRect(QRectF(cx - d(7), cy - d(7), d(14), d(14)), 2, 2)
        if self._collapsed:
            painter.drawLine(cx + d(2), cy - d(5), cx + d(2), cy + d(5))
        else:
            painter.drawLine(cx - d(2), cy - d(5), cx - d(2), cy + d(5))
            painter.drawLine(cx + d(4), cy - d(2), cx, cy)
            painter.drawLine(cx, cy, cx + d(4), cy + d(2))
        painter.end()


class TitleBar(QWidget):
    def __init__(self, theme: Theme, window: QMainWindow, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("TitleBar")
        self.setFixedHeight(40)
        self._window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        nav_btn = SidebarToggle(theme)
        nav_btn.setToolTip("Свернуть/развернуть панель навигации")
        nav_btn.set_collapsed(getattr(self._window, "_sidebar_collapsed", False))
        nav_btn.clicked.connect(self._window.toggle_sidebar)
        self.sidebar_toggle = nav_btn

        title = QLabel("Избранное+ — панель управления")
        title.setFont(system_font(13, QFont.Weight.Medium))
        title.setStyleSheet(f"color: {theme.text_secondary};")

        layout.addWidget(nav_btn)
        layout.addWidget(title)
        layout.addStretch(1)

        min_btn = TrafficLight(theme, "minimize")
        zoom_btn = TrafficLight(theme, "maximize")
        close_btn = TrafficLight(theme, "close")
        self._zoom_btn = zoom_btn
        min_btn.clicked.connect(self._window.showMinimized)
        zoom_btn.clicked.connect(self._toggle_maximize)
        close_btn.clicked.connect(self._window.close)
        for b in (min_btn, zoom_btn, close_btn):
            layout.addWidget(b)

        self._window.installEventFilter(self)
        self._sync_zoom_state()

    def set_theme(self, theme: Theme) -> None:
        self.sidebar_toggle.set_theme(theme)
        self._zoom_btn.set_theme(theme)
        for b in self.findChildren(TrafficLight):
            b.set_theme(theme)

    def _sync_zoom_state(self) -> None:
        self._zoom_btn.set_maximized(self._window.isMaximized())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._window and event.type() == QEvent.Type.WindowStateChange:
            self._sync_zoom_state()
        return super().eventFilter(obj, event)

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._sync_zoom_state()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._toggle_maximize()
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Навигация
# ---------------------------------------------------------------------------

class NavButton(QPushButton):
    def __init__(self, text: str, theme: Theme, parent=None, glyph: str = "") -> None:
        super().__init__(text, parent)
        self._theme = theme
        self._glyph = glyph
        self._hover = 0.0
        self._press = 0.0
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setFlat(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(DUR_FAST)
        self._hover_anim.setEasingCurve(EASE)
        self._press_anim = QPropertyAnimation(self, b"press", self)
        self._press_anim.setDuration(DUR_PRESS)
        self._press_anim.setEasingCurve(EASE_PRESS)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = pyqtProperty(float, fget=get_hover, fset=set_hover)

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = value
        self.update()

    press = pyqtProperty(float, fget=get_press, fset=set_press)

    def nextCheckState(self) -> None:  # noqa: N802
        if not self.isChecked():
            self.setChecked(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 1.0, DUR_FAST, EASE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate_property(self._hover_anim, self._hover, 0.0, DUR_FAST, EASE)
        animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 1.0, DUR_PRESS, EASE_PRESS)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            animate_property(self._press_anim, self._press, 0.0, DUR_RELEASE, EASE)
        super().mouseReleaseEvent(event)

    def _draw_glyph(self, painter: QPainter, cx: int, cy: int, color: QColor) -> None:
        pen = QPen(color)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush())
        k = self._glyph
        if k == "grid":
            for dx, dy in ((-6, -6), (1, -6), (-6, 1), (1, 1)):
                painter.drawRoundedRect(QRectF(cx + dx, cy + dy, 5, 5), 1.5, 1.5)
        elif k == "brain":
            painter.drawEllipse(QRectF(cx - 6, cy - 6, 12, 12))
            path = QPainterPath(QPointF(cx - 3, cy + 3))
            path.lineTo(QPointF(cx, cy - 3))
            path.lineTo(QPointF(cx + 3, cy + 3))
            painter.drawPath(path)
            painter.drawLine(cx - 5, cy + 3, cx + 5, cy + 3)
        elif k == "search":
            painter.drawEllipse(QRectF(cx - 5, cy - 5, 9, 9))
            painter.drawLine(cx + 3, cy + 3, cx + 6, cy + 6)
        elif k == "plug":
            painter.drawRect(QRectF(cx - 4, cy - 2, 8, 5))
            painter.drawLine(cx - 2, cy - 2, cx - 2, cy - 6)
            painter.drawLine(cx + 2, cy - 2, cx + 2, cy - 6)
            painter.drawLine(cx, cy + 3, cx, cy + 6)
        elif k == "terminal":
            painter.drawRoundedRect(QRectF(cx - 6, cy - 5, 12, 10), 2, 2)
            painter.drawLine(cx - 3, cy - 2, cx - 1, cy)
            painter.drawLine(cx - 1, cy, cx - 3, cy + 2)
            painter.drawLine(cx + 1, cy + 2, cx + 4, cy + 2)
        elif k == "info":
            painter.drawEllipse(QRectF(cx - 6, cy - 6, 12, 12))
            dot = QColor(color)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(cx, cy - 3), 1.2, 1.2)
            painter.drawLine(cx, cy, cx, cy + 4)
            painter.setBrush(QBrush())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isChecked():
            if self._press > 0.01:
                overlay = QColor(accent_fg(self._theme))
                overlay.setAlphaF(0.12 * self._press)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(overlay)
                painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        elif self._hover > 0.01 or self._press > 0.01:
            bg = QColor(self._theme.separator_rgb)
            bg.setAlphaF(0.10 * self._hover + 0.12 * self._press)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        if self._glyph:
            base = QColor(self._theme.text if self.isChecked() else self._theme.text_secondary)
            glyph_color = lerp_color(base, QColor(self._theme.text), self._hover)
            self._draw_glyph(painter, 19, self.height() // 2, glyph_color)
        painter.end()
        super().paintEvent(event)


class Sidebar(GlassSurface):
    def __init__(self, theme: Theme, pages: list[tuple[str, str]], on_select,
                 current: int = 0, parent=None) -> None:
        super().__init__(theme, 22, theme.sidebar, theme.sidebar_alpha,
                         22 if theme.is_dark else 140, blur=True, parent=parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.on_select = on_select
        self._current = current
        self.buttons: list[NavButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(4)

        # шапка с логотипом
        head = QHBoxLayout()
        head.setSpacing(8)
        logo = QLabel()
        from favorites_plus.config import LOGO_PATH
        pm = QPixmap(str(LOGO_PATH))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        brand = QLabel("Избранное+")
        brand.setFont(system_font(13, QFont.Weight.Bold))
        brand.setStyleSheet(f"color: {theme.text}; background: transparent;")
        head.addWidget(logo)
        head.addWidget(brand)
        head.addStretch(1)
        layout.addLayout(head)
        layout.addSpacing(10)

        self.indicator = QFrame(self)
        self.indicator.setObjectName("Indicator")
        self._sync_indicator_style()

        for i, (name, glyph) in enumerate(pages):
            btn = NavButton(name, theme, glyph=glyph)
            btn.setChecked(i == current)
            btn.clicked.connect(lambda _checked, idx=i: self._handle_click(idx))
            layout.addWidget(btn)
            self.buttons.append(btn)

        layout.addStretch(1)
        self.indicator.lower()
        self._anim = QPropertyAnimation(self.indicator, b"geometry", self)
        self._anim.setDuration(DUR_MED)
        self._anim.setEasingCurve(EASE)

    def _sync_indicator_style(self) -> None:
        base = ("background: rgba(255, 255, 255, 36);" if self._theme.is_dark
                else "background: rgba(0, 0, 0, 16);")
        self.indicator.setStyleSheet(base + " border-radius: 10px;")

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._sync_indicator_style()
        for b in self._buttons:
            b.set_theme(theme)
        self.invalidate_backdrop()
        self.update()

    def _handle_click(self, idx: int) -> None:
        if idx == self._current:
            return
        self.set_current(idx)
        self.on_select(idx)

    def set_current(self, idx: int) -> None:
        if idx == self._current or not (0 <= idx < len(self.buttons)):
            return
        self._current = idx
        for i, btn in enumerate(self.buttons):
            btn.setChecked(i == idx)
        self._move_indicator(True)

    def _move_indicator(self, animate: bool) -> None:
        target = self.buttons[self._current].geometry()
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self.indicator.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.indicator.setGeometry(target)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._move_indicator(False)
