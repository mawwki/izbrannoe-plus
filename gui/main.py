"""«Избранное+» — панель управления в стиле glassmorphism (по pyqt6-gm-ui)."""
from __future__ import annotations

import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup, QPropertyAnimation,
                          QRect, QRectF, QTimer, Qt, QUrl, pyqtSignal)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath,
                         QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout,
                             QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTimeEdit,
                             QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from favorites_plus.config import (ANSWER_MODELS, DB_PATH, DECIDER_MODELS, LOGO_PATH,
                                   PROJECT_ROOT, load_config, save_config)
from gui.bot_manager import BotManager
from gui.theme import (DARK, EDGE_BOTTOM, EDGE_LEFT, EDGE_MARGIN, EDGE_RIGHT, EDGE_TOP,
                       LIGHT, RADIUS_WINDOW, accent_fg, build_stylesheet, rgba,
                       shadow_color, system_font)
from gui.widgets import (GlassCard, GradientPanel, HeroCard, HoverButton, MacSlider,
                         SegmentedControl, Sidebar, TitleBar, ToggleSwitch)

PAGES = [("Обзор", "grid"), ("Классификация", "brain"), ("Поиск и ИИ", "search"),
         ("Подключение", "plug"), ("Бот и логи", "terminal"), ("О программе", "info")]

CAT_MODE_LABELS = ["Гибрид", "Локальный", "OpenRouter"]
CAT_MODE_IDS = ["hybrid", "local", "openrouter"]

SVG_EYE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">'
    '<path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>'
    '<path fill-rule="evenodd" d="M1.323 11.447C2.811 6.976 7.028 3.75 12.001 3.75'
    'c4.97 0 9.185 3.223 10.675 7.69.12.362.12.752 0 1.113-1.487 4.471-5.705 7.697-10.677 7.697'
    '-4.97 0-9.186-3.223-10.675-7.69a1.762 1.762 0 0 1 0-1.113ZM17.25 12a5.25 5.25 0 1 1-10.5 0 '
    '5.25 5.25 0 0 1 10.5 0Z" clip-rule="evenodd"/></svg>')
SVG_EYE_SLASH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor">'
    '<path d="m10.79 12.912-1.614-1.615a3.5 3.5 0 0 1-4.474-4.474l-2.06-2.06'
    'C.938 6.278 0 8 0 8s3 5.5 8 5.5a7 7 0 0 0 2.79-.588M5.21 3.088A7 7 0 0 1 8 2.5'
    'c5 0 8 5.5 8 5.5s-.939 1.721-2.641 3.238l-2.062-2.062a3.5 3.5 0 0 0-4.474-4.474z"/>'
    '<path d="M5.525 7.646a2.5 2.5 0 0 0 2.829 2.829zm4.95.708-2.829-2.83a2.5 2.5 0 0 1 '
    '2.829 2.829zm3.171 6-12-12 .708-.708 12 12z"/></svg>')


class NetCheck(QWidget):
    result = pyqtSignal(bool, str)

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def run_telegram(self, token: str) -> None:
        def work():
            try:
                import requests
                r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=12)
                data = r.json()
                if data.get("ok"):
                    u = data["result"]
                    self.result.emit(True, f"@{u.get('username')} — «{u.get('first_name')}»")
                else:
                    self.result.emit(False, str(data.get("description", "ошибка API")))
            except Exception as e:  # noqa: BLE001
                self.result.emit(False, str(e)[:120])
        threading.Thread(target=work, daemon=True).start()

    def run_openrouter(self, key: str) -> None:
        def work():
            import asyncio
            from favorites_plus.config import Config
            from favorites_plus.llm import ping
            cfg = Config()
            cfg.openrouter_key = key
            ok, msg = asyncio.run(ping(cfg))
            self.result.emit(ok, msg)
        threading.Thread(target=work, daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(self, autostart: bool = True, interactive: bool = True) -> None:
        super().__init__()
        self.theme = DARK
        self.cfg = load_config()
        self.bot_manager = BotManager()
        self._interactive = interactive
        self._current_page = 0
        self._sidebar_collapsed = False
        self._page_anim: QParallelAnimationGroup | None = None
        self._sidebar_anim: QParallelAnimationGroup | None = None
        self._toast_timer: QTimer | None = None
        self._log_pos = 0
        self._resize_edges = 0
        self._db_conn: sqlite3.Connection | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(880, 560)
        self.resize(1120, 720)
        self.setWindowTitle("Избранное+ — настройки")
        self.setWindowIcon(QIcon(str(PROJECT_ROOT / "assets" / "logo.ico")))
        self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

        self._build_ui()
        self._overview_timer = QTimer(self)
        self._overview_timer.setInterval(4000)
        self._overview_timer.timeout.connect(self._tick_refresh)
        self._overview_timer.start()
        self.bot_manager.log_line.connect(self._on_bot_log)
        self.bot_manager.status_changed.connect(self._on_bot_status)
        if self.cfg.autostart_bot and autostart:
            QTimer.singleShot(600, self._start_bot)

    # ================= каркас =================

    def _build_ui(self) -> None:
        theme = self.theme
        root = QWidget()
        self.setCentralWidget(root)
        root_l = QVBoxLayout(root)
        root_l.setContentsMargins(0, 0, 0, 0)

        panel = GradientPanel(theme)
        self._panel = panel
        root_l.addWidget(panel)

        panel_l = QVBoxLayout(panel)
        panel_l.setContentsMargins(0, 0, 0, 0)
        panel_l.setSpacing(0)

        title_bar = TitleBar(theme, self)
        self._title_bar = title_bar
        panel_l.addWidget(title_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(12, 2, 12, 12)
        body_layout.setSpacing(0)
        panel_l.addWidget(body)

        sidebar = Sidebar(theme, PAGES, on_select=self.switch_page, current=self._current_page)
        self._sidebar = sidebar
        body_layout.addWidget(sidebar)
        gap = QFrame()
        gap.setFixedWidth(12)
        gap.setStyleSheet("background: transparent;")
        self._sidebar_gap = gap
        body_layout.addWidget(gap)
        if self._sidebar_collapsed:
            sidebar.setFixedWidth(0)
            gap.setFixedWidth(0)

        self.stack = QStackedWidget()
        self.pages = [self._page_overview(), self._page_classifier(), self._page_ai(),
                      self._page_connection(), self._page_bot(), self._page_about()]
        for page in self.pages:
            self.stack.addWidget(page)
        self.stack.setCurrentIndex(self._current_page)
        body_layout.addWidget(self.stack, 1)

        panel.setStyleSheet(build_stylesheet(theme))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._panel.start_background_animation()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._panel.stop_background_animation()

    def show(self) -> None:  # noqa: N802
        super().show()
        self._panel.start_background_animation()

    # ================= служебные =================

    def _page_shell(self, name: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 12, 4, 8)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("PageScroll")
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        content = QWidget()
        content.setObjectName("PageBody")
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(16)
        title = QLabel(name)
        title.setFont(system_font(22, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {self.theme.text}; background: transparent;")
        layout.addWidget(title)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page, layout

    def _add_card(self, layout, card_title: str, hero: bool = False) -> tuple[QWidget, QVBoxLayout]:
        card = HeroCard(self.theme) if hero else GlassCard(self.theme)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(shadow_color(self.theme))
        card.setGraphicsEffect(shadow)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(14)
        if card_title:
            head = QLabel(card_title)
            head.setFont(system_font(14, QFont.Weight.DemiBold))
            head.setStyleSheet(f"color: {self.theme.text}; background: transparent;")
            card_layout.addWidget(head)
        layout.addWidget(card)
        return card, card_layout

    def _row(self, card_layout, label_text: str, control, control_stretch: int = 0,
             label_size: int = 13) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        lab = QLabel(label_text)
        lab.setFont(system_font(label_size))
        lab.setStyleSheet(f"color: {self.theme.text}; background: transparent;")
        row.addWidget(lab)
        if control_stretch:
            row.addWidget(control, control_stretch)
        else:
            row.addStretch(1)
            row.addWidget(control)
        card_layout.addLayout(row)

    def _secondary(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setFont(system_font(12))
        lbl.setStyleSheet(f"color: {self.theme.text_secondary}; background: transparent;")
        return lbl

    def _status_chip(self, running: bool) -> QLabel:
        chip = QLabel("● Работает" if running else "● Остановлен")
        self._style_chip(chip, running)
        return chip

    def _style_chip(self, chip: QLabel, running: bool) -> None:
        t = self.theme
        color = "#7BE58B" if running else "#F27B72"
        chip.setText("● Работает" if running else "● Остановлен")
        chip.setFont(system_font(12, QFont.Weight.DemiBold))
        chip.setStyleSheet(
            f"background: {rgba(t.glass_rgb, t.glass_nested_alpha)};"
            f" color: {color}; border: 1px solid {rgba(t.separator_rgb, 40)};"
            f" border-radius: 14px; padding: 5px 14px;")

    def _toast(self, text: str) -> None:
        if getattr(self, "_toast_timer", None) is not None:
            self._toast_timer.stop()
        old = getattr(self, "_toast_frame", None)
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        frame = QFrame(self._panel)
        frame.setObjectName("Toast")
        frame.setStyleSheet(
            f"#Toast {{ background: {rgba(self.theme.sidebar if self.theme.is_dark else self.theme.surface, 230)};"
            f" color: {self.theme.text}; border-radius: 14px;"
            f" border: 1px solid {rgba(self.theme.glass_border_rgb, self.theme.glass_border_alpha)}; }}")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(18, 10, 18, 10)
        lbl = QLabel(text)
        lbl.setFont(system_font(13))
        lbl.setStyleSheet("font-size: 13px; background: transparent;")
        lay.addWidget(lbl)
        frame.adjustSize()
        pw = self._panel.width()
        frame.move((pw - frame.width()) // 2, self._panel.height() - frame.height() - 28)
        frame.show()
        frame.raise_()
        self._toast_frame = frame
        eff = QGraphicsOpacityEffect(frame)
        frame.setGraphicsEffect(eff)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._fade_toast(frame, eff))
        self._toast_timer.start(2600)

    def _fade_toast(self, frame: QFrame, eff: QGraphicsOpacityEffect) -> None:
        try:
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setDuration(280)
            a.setEndValue(0.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.finished.connect(frame.deleteLater)
            a.start()
        except RuntimeError:
            pass

    def _svg_icon(self, svg: str, size: int = 18) -> QIcon:
        source = svg.replace("currentColor", self.theme.text_secondary)
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(source.encode("utf-8"))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return QIcon(pm)

    def _make_line_edit(self, placeholder: str = "", password: bool = False,
                        pill: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFont(system_font(13))
        edit.setFixedHeight(34)
        edit.setCursor(Qt.CursorShape.IBeamCursor)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        radius = 17 if pill else 10
        t = self.theme
        edit.setStyleSheet(
            f"QLineEdit {{ background-color: {rgba(t.glass_rgb, t.glass_nested_alpha)};"
            f" border: 1px solid {rgba(t.separator_rgb, 40)}; border-radius: {radius}px;"
            f" padding: 4px 12px; color: {t.text};"
            f" selection-background-color: {t.accent}; selection-color: {accent_fg(t)}; }}"
            f"QLineEdit:hover {{ border-color: {rgba(t.separator_rgb, 90)}; }}"
            f"QLineEdit:focus {{ border: 1px solid {t.accent};"
            f" background-color: {rgba(t.accent, 10)}; }}"
            f"QLineEdit::placeholder {{ color: {t.text_secondary}; }}")
        return edit

    def _password_field(self, placeholder: str) -> QLineEdit:
        edit = self._make_line_edit(placeholder, password=True)
        eye = self._svg_icon(SVG_EYE)
        eye_slash = self._svg_icon(SVG_EYE_SLASH)
        action = edit.addAction(eye, QLineEdit.ActionPosition.TrailingPosition)

        def toggle_visibility() -> None:
            show = edit.echoMode() == QLineEdit.EchoMode.Password
            edit.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
            action.setIcon(eye_slash if show else eye)

        action.triggered.connect(toggle_visibility)
        return edit

    def _make_slider(self, value: int, minimum: int, maximum: int) -> tuple[MacSlider, QWidget]:
        holder = QWidget()
        holder.setFixedHeight(28)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        slider = MacSlider(self.theme, value, minimum, maximum)
        slider.setMinimumWidth(140)
        value_label = QLabel(str(value))
        value_label.setFixedWidth(34)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setFont(system_font(12))
        value_label.setStyleSheet(f"color: {self.theme.text_secondary}; background: transparent;")
        slider.valueChanged.connect(lambda v, lab=value_label: lab.setText(str(v)))
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        return slider, holder

    # ================= страница 1: Обзор =================

    def _page_overview(self) -> QWidget:
        page, layout = self._page_shell("Обзор")

        # hero с метриками
        hero = HeroCard(self.theme)
        shadow = QGraphicsDropShadowEffect(hero)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(shadow_color(self.theme))
        hero.setGraphicsEffect(shadow)
        hero_l = QVBoxLayout(hero)
        hero_l.setContentsMargins(20, 18, 20, 18)
        hero_l.setSpacing(12)
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        small = QLabel("Ваше второе «Сохранённое» — но с поиском и ИИ")
        small.setFont(system_font(12))
        small.setStyleSheet(f"color: {self.theme.text_secondary}; background: transparent;")
        big = QLabel("Избранное+ уже сортирует всё за вас")
        big.setFont(system_font(19, QFont.Weight.Bold))
        big.setStyleSheet(f"color: {self.theme.text}; background: transparent;")
        col.addWidget(small)
        col.addWidget(big)
        top_row.addLayout(col, 1)
        open_bot = HoverButton("Открыть бота", self.theme, kind="primary")
        open_bot.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://t.me/favorites_plus_bot")))
        top_row.addWidget(open_bot, 0, Qt.AlignmentFlag.AlignTop)
        hero_l.addLayout(top_row)

        self.hero_metrics_l = QHBoxLayout()
        self.hero_metrics_l.setSpacing(24)
        metrics_holder = QWidget()
        metrics_holder.setStyleSheet("background: transparent;")
        metrics_holder.setLayout(self.hero_metrics_l)
        metrics_glass = GlassCard(self.theme, nested=True)
        mg_l = QVBoxLayout(metrics_glass)
        mg_l.setContentsMargins(18, 12, 18, 12)
        mg_l.setSpacing(4)
        mg_l.addWidget(metrics_holder)
        hero_l.addWidget(metrics_glass)
        layout.addWidget(hero)

        _, status_l = self._add_card(layout, "Состояние бота")
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.status_chip = self._status_chip(self.bot_manager.is_running())
        self.uptime_label = QLabel("аптайм: —")
        self.uptime_label.setFont(system_font(12))
        self.uptime_label.setStyleSheet(
            f"color: {self.theme.text_secondary}; background: transparent;")
        handle = QLabel("t.me/favorites_plus_bot")
        handle.setFont(system_font(12))
        handle.setStyleSheet(f"color: {self.theme.text_secondary}; background: transparent;")
        status_row.addWidget(self.status_chip)
        status_row.addWidget(self.uptime_label)
        status_row.addStretch(1)
        status_row.addWidget(handle)
        status_l.addLayout(status_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_start = HoverButton("Запустить бота", self.theme, kind="primary")
        self.btn_stop = HoverButton("Остановить", self.theme, kind="danger")
        self.btn_start.clicked.connect(self._start_bot)
        self.btn_stop.clicked.connect(self._stop_bot)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        status_l.addLayout(btn_row)

        self.cats_card, cats_l = self._add_card(layout, "Заметки по категориям")
        self.cats_grid_holder = QVBoxLayout()
        self.cats_grid_holder.setSpacing(8)
        cats_l.addLayout(self.cats_grid_holder)
        self.cats_total = self._secondary("—")
        cats_l.addWidget(self.cats_total)

        _, recent_l = self._add_card(layout, "Последнее сохранённое")
        self.recent_holder = QVBoxLayout()
        self.recent_holder.setSpacing(6)
        recent_l.addLayout(self.recent_holder)
        layout.addStretch(1)

        self._refresh_overview()
        return page

    def _db(self) -> sqlite3.Connection | None:
        if not DB_PATH.exists():
            return None
        if self._db_conn is None:
            try:
                self._db_conn = sqlite3.connect(str(DB_PATH), timeout=3)
                self._db_conn.row_factory = sqlite3.Row
            except sqlite3.Error:
                return None
        return self._db_conn

    def _refresh_overview(self) -> None:
        running = self.bot_manager.is_running()
        self._style_chip(self.status_chip, running)
        self.uptime_label.setText(f"аптайм: {self.bot_manager.uptime() if running else '—'}")
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

        t = self.theme
        conn = self._db()
        counts: dict[str, int] = {}
        total = 0
        dated = 0
        recent = []
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT category, COUNT(*) AS n FROM notes GROUP BY category").fetchall()
                counts = {r["category"]: r["n"] for r in rows}
                total = int(conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"])
                dated = int(conn.execute(
                    "SELECT COUNT(*) AS n FROM notes WHERE when_date IS NOT NULL").fetchone()["n"])
                recent = conn.execute(
                    "SELECT category, text, created_at FROM notes ORDER BY id DESC LIMIT 4").fetchall()
            except sqlite3.Error:
                pass

        # метрики hero
        while self.hero_metrics_l.count():
            item = self.hero_metrics_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for value, label, dot in ((str(total), "заметок", "#F97316"),
                                  (str(len([c for c in self.cfg.categories if counts.get(c["id"])])),
                                   "категорий", "#C6F26E"),
                                  (str(dated), "с датами", "#FFD84D")):
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(0)
            v = QLabel(value)
            v.setFont(system_font(22, QFont.Weight.Bold))
            v.setStyleSheet(f"color: {t.text}; background: transparent;")
            lrow = QHBoxLayout()
            lrow.setSpacing(6)
            dot_lbl = QLabel("●")
            dot_lbl.setStyleSheet(f"color: {dot}; font-size: 10px; background: transparent;")
            lt = QLabel(label)
            lt.setFont(system_font(12))
            lt.setStyleSheet(f"color: {t.text_secondary}; background: transparent;")
            lrow.addWidget(dot_lbl)
            lrow.addWidget(lt)
            lrow.addStretch(1)
            wl.addWidget(v)
            wl.addLayout(lrow)
            self.hero_metrics_l.addWidget(w)
        self.hero_metrics_l.addStretch(1)

        # чипы категорий
        while self.cats_grid_holder.count():
            item = self.cats_grid_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        grid = QGridLayout()
        grid.setSpacing(8)
        cols = 4
        for i, c in enumerate(self.cfg.categories):
            chip = QLabel(f"{c.get('emoji', '📦')} {c['title']} — {counts.get(c['id'], 0)}")
            chip.setFont(system_font(12))
            chip.setStyleSheet(
                f"background: {rgba(t.glass_rgb, t.glass_nested_alpha)};"
                f" color: {t.text}; border: 1px solid {rgba(t.separator_rgb, 40)};"
                f" border-radius: 12px; padding: 7px 12px;")
            grid.addWidget(chip, i // cols, i % cols)
        holder = QWidget()
        holder.setLayout(grid)
        holder.setStyleSheet("background: transparent;")
        self.cats_grid_holder.addWidget(holder)
        self.cats_total.setText(f"Всего заметок: {total}")

        while self.recent_holder.count():
            item = self.recent_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not recent:
            empty = self._secondary(
                "Пока ничего не сохранено — отправьте боту первое сообщение!")
            self.recent_holder.addWidget(empty)
        else:
            title_of = {c["id"]: f"{c.get('emoji','📦')} {c['title']}" for c in self.cfg.categories}
            for r in recent:
                txt = (r["text"] or "").strip().replace("\n", " ")
                if len(txt) > 90:
                    txt = txt[:89] + "…"
                row_lbl = QLabel(f"<b style='color:{t.accent}'>"
                                 f"{title_of.get(r['category'], '📦')}</b>  "
                                 f"{txt.replace('<', '&lt;')}")
                row_lbl.setWordWrap(True)
                row_lbl.setFont(system_font(12))
                row_lbl.setStyleSheet(
                    f"background: {rgba(t.glass_rgb, t.glass_nested_alpha)};"
                    f" color: {t.text}; border: 1px solid {rgba(t.separator_rgb, 30)};"
                    f" border-radius: 10px; padding: 8px 12px;")
                self.recent_holder.addWidget(row_lbl)

    # ================= страница 2: Классификация =================

    def _page_classifier(self) -> QWidget:
        page, layout = self._page_shell("Классификация")

        _, c1 = self._add_card(layout, "Режим и модели")
        self.mode_seg = SegmentedControl(CAT_MODE_LABELS, self.theme,
                                         current=CAT_MODE_IDS.index(self.cfg.classifier_mode))
        self._row(c1, "Режим классификации", self.mode_seg)
        c1.addWidget(self._secondary(
            "Гибрид — локальный классификатор отвечает мгновенно, а при низкой уверенности "
            "запрос уходит в decision-модель OpenRouter. Локальный — полностью офлайн и бесплатен. "
            "OpenRouter — всегда модель (нужен ключ)."))

        self.model_combo = QComboBox()
        self.model_combo.addItems(DECIDER_MODELS)
        if self.cfg.classifier_model in DECIDER_MODELS:
            self.model_combo.setCurrentText(self.cfg.classifier_model)
        self._row(c1, "Модель классификатора (OpenRouter)", self.model_combo, control_stretch=1)

        self.fallback_combo = QComboBox()
        self.fallback_combo.addItems(DECIDER_MODELS)
        if self.cfg.classifier_fallback_model in DECIDER_MODELS:
            self.fallback_combo.setCurrentText(self.cfg.classifier_fallback_model)
        self._row(c1, "Фолбэк-модель (вторая в цепочке)", self.fallback_combo, control_stretch=1)

        thr_holder = QWidget()
        thr_lay = QHBoxLayout(thr_holder)
        thr_lay.setContentsMargins(0, 0, 0, 0)
        thr_lay.setSpacing(12)
        self.threshold_slider = MacSlider(self.theme, int(self.cfg.confidence_threshold * 100), 20, 80)
        self.threshold_slider.setMinimumWidth(140)
        self.threshold_label = QLabel(f"{int(self.cfg.confidence_threshold * 100)}%")
        self.threshold_label.setFixedWidth(34)
        self.threshold_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.threshold_label.setFont(system_font(12))
        self.threshold_label.setStyleSheet(
            f"color: {self.theme.text_secondary}; background: transparent;")
        self.threshold_slider.valueChanged.connect(
            lambda v: self.threshold_label.setText(f"{v}%"))
        thr_lay.addWidget(self.threshold_slider, 1)
        thr_lay.addWidget(self.threshold_label)
        self._row(c1, "Порог уверенности локальной модели", thr_holder, control_stretch=1)

        _, c2 = self._add_card(layout, "Категории (передаются классификатору)")
        split = QHBoxLayout()
        split.setSpacing(12)
        from PyQt6.QtWidgets import QListWidget, QPlainTextEdit
        self.cat_list = QListWidget()
        self.cat_list.setMinimumHeight(190)
        self.cat_list.currentRowChanged.connect(self._on_cat_selected)
        split.addWidget(self.cat_list, 1)

        edit_col = QVBoxLayout()
        edit_col.setSpacing(8)
        self.cat_emoji = self._make_line_edit("Эмодзи (напр. ✅)")
        self.cat_title = self._make_line_edit("Название категории")
        self.cat_criteria = QPlainTextEdit()
        self.cat_criteria.setPlaceholderText("Описание для классификатора: чем отличается категория")
        self.cat_criteria.setFixedHeight(100)
        t = self.theme
        self.cat_criteria.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {rgba(t.glass_rgb, t.glass_nested_alpha)};"
            f" border: 1px solid {rgba(t.separator_rgb, 40)}; border-radius: 10px; padding: 6px;"
            f" color: {t.text}; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {t.accent}; }}")
        edit_col.addWidget(self.cat_emoji)
        edit_col.addWidget(self.cat_title)
        edit_col.addWidget(self.cat_criteria)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        add_btn = HoverButton("Добавить", self.theme, kind="accent")
        add_btn.clicked.connect(self._cat_add)
        del_btn = HoverButton("Удалить", self.theme, kind="danger")
        del_btn.clicked.connect(self._cat_del)
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        edit_col.addLayout(btns)
        split.addLayout(edit_col, 1)
        c2.addLayout(split)

        save_row = QHBoxLayout()
        self.save_cats_btn = HoverButton("Сохранить настройки классификации", self.theme, kind="primary")
        self.save_cats_btn.clicked.connect(self._save_classifier)
        save_row.addWidget(self.save_cats_btn)
        save_row.addStretch(1)
        layout.addLayout(save_row)
        layout.addStretch(1)

        self._load_categories()
        return page

    def _load_categories(self) -> None:
        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        for c in self.cfg.categories:
            self.cat_list.addItem(f"{c.get('emoji', '📦')} {c['title']}")
        self.cat_list.blockSignals(False)
        if self.cfg.categories:
            self.cat_list.setCurrentRow(0)

    def _on_cat_selected(self, row: int) -> None:
        if not (0 <= row < len(self.cfg.categories)):
            return
        c = self.cfg.categories[row]
        self.cat_emoji.setText(c.get("emoji", "📦"))
        self.cat_title.setText(c["title"])
        self.cat_criteria.setPlainText(c.get("criteria", ""))

    def _apply_cat_edit_fields(self) -> None:
        row = self.cat_list.currentRow()
        if not (0 <= row < len(self.cfg.categories)):
            return
        c = self.cfg.categories[row]
        c["emoji"] = self.cat_emoji.text().strip() or "📦"
        c["title"] = self.cat_title.text().strip() or c["title"]
        c["criteria"] = self.cat_criteria.toPlainText().strip()

    def _cat_add(self) -> None:
        self._apply_cat_edit_fields()
        self.cfg.categories.append({"id": f"cat{int(datetime.now().timestamp()) % 100000}",
                                    "title": "Новая", "emoji": "🗂", "criteria": ""})
        self._load_categories()
        self.cat_list.setCurrentRow(len(self.cfg.categories) - 1)

    def _cat_del(self) -> None:
        row = self.cat_list.currentRow()
        if not (0 <= row < len(self.cfg.categories)) or len(self.cfg.categories) <= 2:
            self._toast("Нужно минимум две категории")
            return
        self.cfg.categories.pop(row)
        self._load_categories()

    def _save_classifier(self) -> None:
        self._apply_cat_edit_fields()
        self.cfg.classifier_mode = CAT_MODE_IDS[self.mode_seg.current()]
        self.cfg.classifier_model = self.model_combo.currentText()
        self.cfg.classifier_fallback_model = self.fallback_combo.currentText()
        self.cfg.confidence_threshold = self.threshold_slider.value() / 100
        save_config(self.cfg)
        self._restart_if_running()
        self._toast("✅ Классификация сохранена")

    # ================= страница 3: Поиск и ИИ =================

    def _page_ai(self) -> QWidget:
        page, layout = self._page_shell("Поиск и ИИ")

        _, c1 = self._add_card(layout, "Модель «нахождения инфы»")
        self.answer_combo = QComboBox()
        self.answer_combo.addItems(ANSWER_MODELS)
        if self.cfg.answer_model not in ANSWER_MODELS:
            self.answer_combo.addItem(self.cfg.answer_model)
        self.answer_combo.setCurrentText(self.cfg.answer_model)
        self._row(c1, "Модель ответов на вопросы по заметкам", self.answer_combo, control_stretch=1)
        c1.addWidget(self._secondary(
            "openrouter/free — бесплатный роутер OpenRouter: подбирает доступную бесплатную "
            "модель сам. Используется для /ask (ответы по заметкам) и дайджестов."))

        self.ctx_slider, ctx_holder = self._make_slider(self.cfg.max_context_notes, 3, 10)
        self._row(c1, "Заметок в контексте ответа", ctx_holder, control_stretch=1)
        self.lim_slider, lim_holder = self._make_slider(self.cfg.search_limit, 3, 10)
        self._row(c1, "Результатов поиска на /find", lim_holder, control_stretch=1)

        _, c2 = self._add_card(layout, "Ежедневный дайджест")
        self.digest_switch = ToggleSwitch(self.theme, checked=self.cfg.digest_enabled)
        self.digest_switch.toggled.connect(lambda v: self.digest_time.setEnabled(v))
        self._row(c2, "Присылать дайджест раз в день", self.digest_switch)
        from PyQt6.QtCore import QTime
        self.digest_time = QTimeEdit()
        try:
            hh, mm = (self.cfg.digest_time or "21:00").split(":")[:2]
            self.digest_time.setTime(QTime(int(hh), int(mm)))
        except Exception:  # noqa: BLE001
            pass
        self.digest_time.setEnabled(self.cfg.digest_enabled)
        self._row(c2, "Время дайджеста", self.digest_time)

        _, c3 = self._add_card(layout, "Напоминания")
        self.reminder_switch = ToggleSwitch(self.theme, checked=self.cfg.reminder_enabled)
        self._row(c3, "Напоминать о заметках с датой на сегодня (утром или к времени из текста)",
                  self.reminder_switch)

        save_row = QHBoxLayout()
        save_btn = HoverButton("Сохранить настройки ИИ", self.theme, kind="primary")
        save_btn.clicked.connect(self._save_ai)
        save_row.addWidget(save_btn)
        save_row.addStretch(1)
        layout.addLayout(save_row)
        layout.addStretch(1)
        return page

    def _save_ai(self) -> None:
        self.cfg.answer_model = self.answer_combo.currentText()
        self.cfg.max_context_notes = self.ctx_slider.value()
        self.cfg.search_limit = self.lim_slider.value()
        self.cfg.digest_enabled = self.digest_switch.isChecked()
        self.cfg.digest_time = self.digest_time.time().toString("HH:mm")
        self.cfg.reminder_enabled = self.reminder_switch.isChecked()
        save_config(self.cfg)
        self._restart_if_running()
        self._toast("✅ Настройки ИИ сохранены")

    # ================= страница 4: Подключение =================

    def _page_connection(self) -> QWidget:
        page, layout = self._page_shell("Подключение")

        _, c1 = self._add_card(layout, "Telegram Bot API")
        self.token_edit = self._password_field("Токен бота от @BotFather")
        self.token_edit.setText(self.cfg.bot_token)
        self._row(c1, "Токен", self.token_edit, control_stretch=1)
        tg_row = QHBoxLayout()
        tg_row.setSpacing(12)
        self.tg_check_btn = HoverButton("Проверить токен", self.theme, kind="accent")
        self.tg_check_btn.clicked.connect(self._check_tg)
        self.tg_status = self._secondary("Не проверено")
        tg_row.addWidget(self.tg_check_btn)
        tg_row.addWidget(self.tg_status, 1)
        c1.addLayout(tg_row)

        _, c2 = self._add_card(layout, "OpenRouter API")
        self.key_edit = self._password_field("Ключ sk-or-v1-…")
        self.key_edit.setText(self.cfg.openrouter_key)
        self._row(c2, "Ключ", self.key_edit, control_stretch=1)
        or_row = QHBoxLayout()
        or_row.setSpacing(12)
        self.or_check_btn = HoverButton("Проверить ключ", self.theme, kind="accent")
        self.or_check_btn.clicked.connect(self._check_or)
        self.or_status = self._secondary("Не проверено")
        or_row.addWidget(self.or_check_btn)
        or_row.addWidget(self.or_status, 1)
        c2.addLayout(or_row)

        save_row = QHBoxLayout()
        save_btn = HoverButton("Сохранить и проверить", self.theme, kind="primary")
        save_btn.clicked.connect(self._save_connection)
        save_row.addWidget(save_btn)
        save_row.addStretch(1)
        layout.addLayout(save_row)
        layout.addStretch(1)
        return page

    def _check_tg(self) -> None:
        self.tg_status.setText("Проверяю…")
        self._tg_check = NetCheck("tg")
        self._tg_check.result.connect(
            lambda ok, msg: self.tg_status.setText(("✅ " if ok else "❌ ") + msg))
        self._tg_check.run_telegram(self.token_edit.text().strip())

    def _check_or(self) -> None:
        self.or_status.setText("Проверяю… (до 50 секунд)")
        self._or_check = NetCheck("or")
        self._or_check.result.connect(
            lambda ok, msg: self.or_status.setText(("✅ " if ok else "❌ ") + msg))
        self._or_check.run_openrouter(self.key_edit.text().strip())

    def _save_connection(self) -> None:
        self.cfg.bot_token = self.token_edit.text().strip()
        self.cfg.openrouter_key = self.key_edit.text().strip()
        save_config(self.cfg)
        self._restart_if_running()
        self._toast("✅ Подключение сохранено")
        self._check_tg()
        self._check_or()

    # ================= страница 5: Бот и логи =================

    def _page_bot(self) -> QWidget:
        page, layout = self._page_shell("Бот и логи")

        _, c1 = self._add_card(layout, "Управление процессом")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.bot_chip = self._status_chip(self.bot_manager.is_running())
        self.bot_uptime = QLabel("аптайм: —")
        self.bot_uptime.setFont(system_font(12))
        self.bot_uptime.setStyleSheet(
            f"color: {self.theme.text_secondary}; background: transparent;")
        row.addWidget(self.bot_chip)
        row.addWidget(self.bot_uptime)
        row.addStretch(1)
        c1.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.bot_start_btn = HoverButton("Запустить бота", self.theme, kind="primary")
        self.bot_stop_btn = HoverButton("Остановить", self.theme, kind="danger")
        self.bot_restart_btn = HoverButton("Перезапустить", self.theme, kind="accent")
        self.bot_start_btn.clicked.connect(self._start_bot)
        self.bot_stop_btn.clicked.connect(self._stop_bot)
        self.bot_restart_btn.clicked.connect(self._restart_bot)
        btns.addWidget(self.bot_start_btn)
        btns.addWidget(self.bot_stop_btn)
        btns.addWidget(self.bot_restart_btn)
        btns.addStretch(1)
        c1.addLayout(btns)

        self.autostart_switch = ToggleSwitch(self.theme, checked=self.cfg.autostart_bot)
        self.autostart_switch.toggled.connect(self._on_autostart)
        self._row(c1, "Запускать бота вместе с программой", self.autostart_switch)

        _, c2 = self._add_card(layout, "Живой лог")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(240)
        self.log_view.setFont(system_font(12))
        self.log_view.setMaximumBlockCount(800)
        c2.addWidget(self.log_view)
        clear_row = QHBoxLayout()
        clear_btn = HoverButton("Очистить экран", self.theme, kind="accent")
        clear_btn.clicked.connect(self.log_view.clear)
        clear_row.addWidget(clear_btn)
        clear_row.addStretch(1)
        c2.addLayout(clear_row)
        layout.addStretch(1)
        return page

    def _on_autostart(self, v: bool) -> None:
        self.cfg.autostart_bot = v
        save_config(self.cfg)
        self._toast("Автозапуск " + ("включён" if v else "выключен"))

    def _start_bot(self) -> None:
        if self.bot_manager.is_running():
            self._toast("Бот уже работает")
            return
        save_config(self.cfg)
        self.bot_manager.start()

    def _stop_bot(self) -> None:
        if not self.bot_manager.is_running():
            self._toast("Бот и так остановлен")
            return
        self.bot_manager.stop()

    def _restart_bot(self) -> None:
        self._toast("Перезапуск бота…")
        save_config(self.cfg)
        self.bot_manager.restart()

    def _restart_if_running(self) -> None:
        if self.bot_manager.is_running():
            self._toast("Настройки сохранены — перезапускаю бота…")
            self.bot_manager.restart()

    def _on_bot_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_bot_status(self, running: bool) -> None:
        self._style_chip(self.bot_chip, running)
        self.bot_uptime.setText(
            f"аптайм: {self.bot_manager.uptime() if running else '—'}")
        self.bot_start_btn.setEnabled(not running)
        self.bot_stop_btn.setEnabled(running)
        self._refresh_overview()

    def _tail_log_file(self) -> None:
        path = PROJECT_ROOT / "logs" / "bot.log"
        try:
            if not path.exists():
                return
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(self._log_pos)
                chunk = f.read()
                self._log_pos = f.tell()
            if chunk:
                self.log_view.appendPlainText(chunk.rstrip())
        except OSError:
            pass

    # ================= страница 6: О программе =================

    def _page_about(self) -> QWidget:
        page, layout = self._page_shell("О программе")

        _, c = self._add_card(layout, "")
        head = QHBoxLayout()
        head.setSpacing(16)
        logo = QLabel()
        pm = QPixmap(str(LOGO_PATH))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        txt = QLabel(
            "<b>Избранное+ — умное «Сохранённое» для Telegram</b><br><br>"
            "Бот превращает пересланные сообщения в структурированную базу знаний: "
            "автоклассификация, автотеги, распознавание дат, гибридный поиск (BM25 + векторный) "
            "и ответы на вопросы по заметкам через ИИ.<br><br>"
            "<b>Классификатор:</b> локальный TF-IDF + LogisticRegression с дообучением на ваших "
            "правках и/или бесплатные decision-модели OpenRouter.<br>"
            "<b>Поиск информации:</b> openrouter/free.<br><br>"
            "Интерфейс — glassmorphism по спецификации pyqt6-gm-ui.")
        txt.setWordWrap(True)
        txt.setFont(system_font(13))
        txt.setStyleSheet(f"color: {self.theme.text}; background: transparent;")
        head.addWidget(txt, 1)
        c.addLayout(head)

        self.dark_switch = ToggleSwitch(self.theme, checked=self.theme.is_dark)
        self.dark_switch.toggled.connect(self._on_theme_toggled)
        self._row(c, "Тёмная тема", self.dark_switch)

        stack = self._add_card(layout, "Стек")[0]
        stack.layout().addWidget(self._secondary(
            "Python 3.12 · aiogram 3 · scikit-learn · SQLite + FTS5 · httpx · PyQt6 "
            "(glassmorphism по mawwki/pyqt6-ui-skills)"))
        layout.addStretch(1)
        return page

    def _on_theme_toggled(self, is_dark: bool) -> None:
        self.theme = DARK if is_dark else LIGHT
        old_central = self.takeCentralWidget()
        if old_central is not None:
            old_central.deleteLater()
        self._build_ui()
        self._panel.start_background_animation()

    # ================= навигация и события =================

    def switch_page(self, idx: int) -> None:
        if idx == self._current_page:
            return
        self._current_page = idx
        prev = self.stack.currentWidget()
        nxt = self.pages[idx]
        self.stack.setCurrentWidget(nxt)
        if idx == 0:
            self._refresh_overview()
        self._stop_page_anim()
        eff_p = QGraphicsOpacityEffect(prev)
        eff_n = QGraphicsOpacityEffect(nxt)
        prev.setGraphicsEffect(eff_p)
        nxt.setGraphicsEffect(eff_n)
        group = QParallelAnimationGroup(self)
        a1 = QPropertyAnimation(eff_p, b"opacity")
        a1.setDuration(250)
        a1.setEndValue(0.0)
        a2 = QPropertyAnimation(eff_n, b"opacity")
        a2.setDuration(250)
        a2.setStartValue(0.0)
        a2.setEndValue(1.0)
        group.addAnimation(a1)
        group.addAnimation(a2)
        self._page_anim = group
        group.finished.connect(lambda: (prev.setGraphicsEffect(None),
                                        nxt.setGraphicsEffect(None)))
        group.start()

    def _stop_page_anim(self) -> None:
        if self._page_anim is not None:
            self._page_anim.stop()
            self._page_anim.deleteLater()
            self._page_anim = None

    def toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        self._title_bar.sidebar_toggle.set_collapsed(self._sidebar_collapsed)
        widgets = [self._sidebar, self._sidebar_gap]
        widths = [200, 12]
        group = QParallelAnimationGroup(self)
        for widget, expanded_width in zip(widgets, widths):
            start = widget.width()
            target = 0 if self._sidebar_collapsed else expanded_width
            for prop in (b"minimumWidth", b"maximumWidth"):
                anim = QPropertyAnimation(widget, prop, self)
                anim.setDuration(250)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.setStartValue(start)
                anim.setEndValue(target)
                group.addAnimation(anim)
        self._sidebar_anim = group
        group.start()

    def _tick_refresh(self) -> None:
        self._tail_log_file()
        running = self.bot_manager.is_running()
        if self._current_page == 0:
            self._refresh_overview()
        elif self._current_page == 4:
            self._style_chip(self.bot_chip, running)
            self.bot_uptime.setText(
                f"аптайм: {self.bot_manager.uptime() if running else '—'}")
            self.bot_start_btn.setEnabled(not running)
            self.bot_stop_btn.setEnabled(running)

    # ресайз за края: свои бит-константы + сборка Qt.Edge
    def _resize_edges_for(self, pos) -> int:
        if not self.isVisible() or self.isMaximized():
            return 0
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        edges = 0
        if x <= EDGE_MARGIN:
            edges |= EDGE_LEFT
        if x >= w - EDGE_MARGIN:
            edges |= EDGE_RIGHT
        if y <= EDGE_MARGIN:
            edges |= EDGE_TOP
        if y >= h - EDGE_MARGIN:
            edges |= EDGE_BOTTOM
        return edges

    def _start_system_resize(self, edges: int) -> None:
        handle = self.windowHandle()
        if handle is None or edges == 0:
            return
        flag = Qt.Edge(0)
        if edges & EDGE_TOP:
            flag |= Qt.Edge.TopEdge
        if edges & EDGE_BOTTOM:
            flag |= Qt.Edge.BottomEdge
        if edges & EDGE_LEFT:
            flag |= Qt.Edge.LeftEdge
        if edges & EDGE_RIGHT:
            flag |= Qt.Edge.RightEdge
        handle.startSystemResize(flag)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            if obj is self and hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton:
                self._resize_edges = self._resize_edges_for(event.position())
                if self._resize_edges:
                    self._start_system_resize(self._resize_edges)
                    return True
        elif t == QEvent.Type.MouseMove:
            if obj is self:
                self.setCursor(self._cursor_for_edges(self._resize_edges_for(event.position())))
        elif t == QEvent.Type.Leave:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        return super().eventFilter(obj, event)

    def _cursor_for_edges(self, edges: int) -> Qt.CursorShape:
        if edges == 0:
            return Qt.CursorShape.ArrowCursor
        if edges in (EDGE_LEFT, EDGE_RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edges in (EDGE_TOP, EDGE_BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        if edges in (EDGE_TOP | EDGE_LEFT, EDGE_BOTTOM | EDGE_RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    def closeEvent(self, e) -> None:  # noqa: N802
        self._panel.stop_background_animation()
        if self.bot_manager.is_running() and self._interactive:
            box = QMessageBox(self)
            box.setWindowTitle("Избранное+")
            box.setText("Бот сейчас работает. Остановить его при выходе из программы?")
            btn_stop = box.addButton("Остановить", QMessageBox.ButtonRole.YesRole)
            btn_keep = box.addButton("Оставить работать", QMessageBox.ButtonRole.NoRole)
            box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == btn_stop:
                self.bot_manager.stop()
                e.accept()
            elif clicked == btn_keep:
                e.accept()
            else:
                e.ignore()
        else:
            e.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(system_font(13))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
