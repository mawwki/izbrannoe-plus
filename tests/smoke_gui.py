"""Smoke-тест GUI «Избранное+» (glassmorphism): страницы + интеракции + скриншоты."""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from gui.main import MainWindow  # noqa: E402
from gui.theme import DARK, LIGHT  # noqa: E402

QA = ROOT / "assets" / "_qa"
QA.mkdir(parents=True, exist_ok=True)

failures: list[str] = []


def step(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name} {extra if not cond else ''}")
    if not cond:
        failures.append(name)


def wait_ms(app, ms: int) -> None:
    w = QEventLoop()
    QTimer.singleShot(ms, w.quit)
    w.exec()
    app.processEvents()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow(autostart=False, interactive=False)
    win.show()
    wait_ms(app, 400)
    # таймер хвоста лога: файл bot.log существует → через ~4.5с он должен попасть в окно
    if (ROOT / "logs" / "bot.log").exists():
        wait_ms(app, 4600)
        step("log tail live", len(win.log_view.toPlainText().strip()) > 0)

    pages = ["Обзор", "Классификация", "Поиск и ИИ", "Подключение", "Бот и логи", "О программе"]
    shots = ["overview", "classifier", "ai", "connection", "bot", "about"]
    for i in range(6):
        win._sidebar.buttons[i].click()
        wait_ms(app, 330)  # дождаться fade-перехода
        step(f"page '{pages[i]}'", win.stack.currentIndex() == i and win._current_page == i
             and win._sidebar.buttons[i].isChecked())
        win.grab().save(str(QA / f"dark_{shots[i]}.png"))

    # интеракции страницы классификации
    win.switch_page(1)
    wait_ms(app, 300)
    win.threshold_slider.setValue(60)
    step("threshold slider", win.threshold_label.text() == "60%")
    n_cats = len(win.cfg.categories)
    win._cat_add()
    step("category add", len(win.cfg.categories) == n_cats + 1)
    win._cat_del()
    step("category del", len(win.cfg.categories) == n_cats)
    win.mode_seg.set_current(1)
    step("segmented control", win.mode_seg.current() == 1)

    # поиск и ИИ
    win.switch_page(2)
    wait_ms(app, 300)
    win.ctx_slider.setValue(8)
    step("ctx slider", win.ctx_slider.value() == 8)
    win.digest_switch.setChecked(True)
    step("digest switch", win.digest_switch.isChecked())

    # бот и логи
    win.switch_page(4)
    wait_ms(app, 300)
    step("start btn enabled", win.bot_start_btn.isEnabled() or win.bot_manager.is_running())

    # hover-анимация кнопок (str/bytes props)
    try:
        win.btn_start.set_hover(1.0)
        app.processEvents()
        win.btn_start.set_hover(0.0)
        step("hoverbutton anim", True)
    except Exception as ex:  # noqa: BLE001
        step("hoverbutton anim", False, str(ex))

    # тосты (регрессия deleteLater)
    try:
        win._toast("первый")
        app.processEvents()
        win._toast("второй")
        app.processEvents()
        win._toast("третий")
        app.processEvents()
        old_frame = win._toast_frame
        old_frame.deleteLater()
        app.processEvents()
        win._fade_toast(old_frame, old_frame.graphicsEffect())
        app.processEvents()
        step("double toast safe", True)
    except RuntimeError as ex:  # noqa: BLE001
        step("double toast safe", False, str(ex))

    # смена темы: пересборка, страница сохраняется, скриншоты светлой темы
    win.switch_page(5)
    wait_ms(app, 300)
    win._on_theme_toggled(False)
    wait_ms(app, 400)
    step("theme light rebuild", win.theme is LIGHT and win.stack.count() == 6
         and win._current_page == 5)
    for i in range(6):
        win._sidebar.buttons[i].click()
        wait_ms(app, 330)
        win.grab().save(str(QA / f"light_{shots[i]}.png"))
    win._on_theme_toggled(True)
    wait_ms(app, 400)
    step("theme dark rebuild", win.theme is DARK and win._current_page == 5)

    # sidebar collapse/expand
    win.toggle_sidebar()
    wait_ms(app, 350)
    win.toggle_sidebar()
    wait_ms(app, 350)

    # ресайз-края
    for edges, name in ((1, "top"), (2, "bottom"), (4, "left"), (8, "right"), (5, "tl"),
                        (10, "br"), (0, "center")):
        step(f"resize edges {name}", win._cursor_for_edges(edges) is not None)

    if getattr(win, "_toast_timer", None) is not None:
        win._toast_timer.stop()
    win.close()
    app.processEvents()

    if failures:
        print(f"\nSMOKE FAILED: {failures}")
        sys.exit(1)
    print("\nSMOKE OK — все проверки пройдены, скриншоты в assets/_qa/")
    sys.exit(0)


main()
