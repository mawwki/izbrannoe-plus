"""Запуск программы настроек «Избранное+»: python run_gui.py

В PyInstaller-сборке exe запускает сам себя с флагом --bot как отдельный процесс:
  FavoritesPlus.exe        — GUI
  FavoritesPlus.exe --bot  — процесс бота
"""
import sys

if __name__ == "__main__":
    if "--bot" in sys.argv[1:]:
        from favorites_plus.bot import main
        main()
    else:
        from gui.main import main
        main()
