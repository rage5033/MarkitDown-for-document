"""Entry point for the MarkItDown batch-conversion GUI."""
import subprocess
import sys
import warnings
from pathlib import Path


def _relaunch_in_project_venv() -> None:
    # Dependencies live only in the project .venv; a plain `python main.py` would hit ModuleNotFoundError.
    if getattr(sys, "frozen", False):
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file() and Path(sys.prefix).resolve() != venv_python.parents[1].resolve():
        sys.exit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))


_relaunch_in_project_venv()
# Audio conversion is optional; pydub's missing-ffmpeg warning only confuses users at startup.
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv")

from PySide6.QtWidgets import QApplication  # noqa: E402

from markitdown_gui.main_window import MainWindow  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
