"""Main window: batch file list, output-name editing, output folder, and conversion trigger."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QSettings, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QLineEdit,
    QLabel,
    QCheckBox,
    QFileDialog,
    QProgressBar,
    QPlainTextEdit,
    QMessageBox,
    QHeaderView,
    QAbstractItemView,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QComboBox,
)

from markitdown_gui.worker import (
    ConversionJob, ConversionWorker, LAYOUT_DOC_FOLDER, LAYOUT_SUBFOLDER, output_paths,
)
from markitdown_gui import ocr as ocr_engine
from markitdown_gui import youtube
from pathvalidate import sanitize_filename

SUPPORTED_EXTS = (
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm",
    ".csv", ".json", ".xml", ".txt", ".zip", ".epub", ".msg",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".wav", ".mp3",
)
SUPPORTED_FILTER = (
    f"지원 문서 ({' '.join('*' + e for e in SUPPORTED_EXTS)});;"
    "모든 파일 (*.*)"
)
PREVIEW_LIMIT = 1_000_000  # chars; very large documents make QTextBrowser sluggish


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MarkItDown 변환기 - 다중 파일 → Markdown")
        self.resize(1200, 720)
        self.setAcceptDrops(True)

        self.settings = QSettings("MarkItDownGUI", "MarkItDownGUI")
        self._thread: QThread | None = None
        self._worker: ConversionWorker | None = None
        self._output_dir = ""
        self._success_count = 0
        self._last_success_path = ""

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        hint = QLabel(
            "파일/폴더를 추가하거나 이 창으로 끌어다 놓으세요. 출력 파일명은 더블클릭으로 수정할 수 있습니다. "
            "변환이 끝난 항목을 선택하면 오른쪽에서 결과를 미리볼 수 있습니다."
        )
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("파일 추가...")
        self.add_folder_btn = QPushButton("폴더 추가...")
        self.remove_btn = QPushButton("선택 삭제")
        self.clear_btn = QPushButton("전체 삭제")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.add_folder_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        self.open_result_btn = QPushButton("결과 폴더 열기")
        self.open_result_btn.setToolTip("선택한 항목의 MD·이미지가 있는 폴더를 엽니다. (Ctrl+A → AI 채팅창으로 끌어놓기)")
        btn_row.addWidget(self.open_result_btn)
        layout.addLayout(btn_row)

        yt_row = QHBoxLayout()
        yt_row.addWidget(QLabel("YouTube 링크:"))
        self.youtube_edit = QLineEdit()
        self.youtube_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        yt_row.addWidget(self.youtube_edit)
        self.youtube_add_btn = QPushButton("추가")
        yt_row.addWidget(self.youtube_add_btn)
        layout.addLayout(yt_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["원본 파일", "출력 파일명 (.md)", "상태"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )

        self.preview_view = QTextBrowser()
        self.preview_view.setOpenExternalLinks(True)
        self.preview_view.setPlaceholderText("변환이 완료된 항목을 선택하면 결과를 미리볼 수 있습니다.")
        self.preview_raw = QPlainTextEdit()
        self.preview_raw.setReadOnly(True)
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(self.preview_view, "미리보기")
        self.preview_tabs.addTab(self.preview_raw, "Markdown 원문")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview_tabs)
        splitter.setSizes([680, 500])
        layout.addWidget(splitter, stretch=1)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("저장 폴더:"))
        default_dir = str(Path.home() / "Desktop")
        self.output_edit = QLineEdit(str(self.settings.value("output_dir", default_dir)))
        out_row.addWidget(self.output_edit)
        self.browse_btn = QPushButton("찾아보기...")
        out_row.addWidget(self.browse_btn)
        out_row.addWidget(QLabel("저장 방식:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("문서별 폴더 (AI 업로드용)", LAYOUT_DOC_FOLDER)
        self.layout_combo.addItem("MD는 저장 폴더에, 이미지는 _images 하위 폴더", LAYOUT_SUBFOLDER)
        self.layout_combo.setToolTip(
            "문서별 폴더: 저장폴더\\문서명\\에 MD와 이미지를 한데 모음 → 폴더를 열고 Ctrl+A 후 AI 채팅창에 끌어놓기\n"
            "이미지 파일명에는 문서명이 붙어 여러 문서를 함께 올려도 구분됩니다."
        )
        idx = self.layout_combo.findData(self.settings.value("layout", LAYOUT_DOC_FOLDER))
        self.layout_combo.setCurrentIndex(max(idx, 0))
        out_row.addWidget(self.layout_combo)
        layout.addLayout(out_row)

        conv_row = QHBoxLayout()
        self.convert_btn = QPushButton("Markdown으로 변환")
        self.convert_btn.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        conv_row.addWidget(self.convert_btn)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        conv_row.addWidget(self.cancel_btn)
        self.pdf_checkbox = QCheckBox("PDF로도 저장")
        conv_row.addWidget(self.pdf_checkbox)
        self.images_checkbox = QCheckBox("이미지 추출")
        self.images_checkbox.setToolTip("PDF·Word·PPT 안의 그림을 파일로 저장하고 MD에 이미지 링크와 파일 목록을 넣습니다.")
        self.images_checkbox.setChecked(self.settings.value("extract_images", True, type=bool))
        conv_row.addWidget(self.images_checkbox)
        self.bundle_checkbox = QCheckBox("이미지 모음 PDF (AI 업로드용)")
        self.bundle_checkbox.setToolTip(
            "추출한 이미지를 '문서명_images.pdf' 하나에 모읍니다 (한 쪽에 1개, 상단에 파일명 표시).\n"
            "AI에는 MD + 이미지 모음 PDF 2개 파일만 올리면 됩니다. 개별 PNG는 images 폴더에 보관됩니다."
        )
        self.bundle_checkbox.setChecked(self.settings.value("bundle_pdf", True, type=bool))
        self.bundle_checkbox.setEnabled(self.images_checkbox.isChecked())
        conv_row.addWidget(self.bundle_checkbox)
        self.ocr_checkbox = QCheckBox("이미지 글자 인식(OCR)")
        self.ocr_checkbox.setToolTip(
            "PDF·Word·PPT 안 그림과 스캔 페이지의 글자를 Tesseract로 인식해 MD에 텍스트로 넣습니다.\n" + ocr_engine.status()
        )
        self.ocr_checkbox.setEnabled(ocr_engine.is_available())
        self.ocr_checkbox.setChecked(ocr_engine.is_available() and self.settings.value("ocr", True, type=bool))
        conv_row.addWidget(self.ocr_checkbox)
        self.open_folder_checkbox = QCheckBox("완료 후 폴더 열기")
        self.open_folder_checkbox.setChecked(self.settings.value("open_folder", True, type=bool))
        conv_row.addWidget(self.open_folder_checkbox)
        self.progress = QProgressBar()
        conv_row.addWidget(self.progress)
        layout.addLayout(conv_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        layout.addWidget(self.log)

        self.add_btn.clicked.connect(self.on_add_files)
        self.add_folder_btn.clicked.connect(self.on_add_folder)
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.table.itemSelectionChanged.connect(self._update_preview)
        self.open_folder_checkbox.toggled.connect(lambda v: self.settings.setValue("open_folder", v))
        self.images_checkbox.toggled.connect(lambda v: self.settings.setValue("extract_images", v))
        self.images_checkbox.toggled.connect(self.bundle_checkbox.setEnabled)
        self.bundle_checkbox.toggled.connect(lambda v: self.settings.setValue("bundle_pdf", v))
        self.ocr_checkbox.toggled.connect(lambda v: self.settings.setValue("ocr", v))
        self.layout_combo.currentIndexChanged.connect(
            lambda _: self.settings.setValue("layout", self.layout_combo.currentData())
        )
        self.open_result_btn.clicked.connect(self.on_open_result_folder)
        self.log.appendPlainText(ocr_engine.status())
        self.remove_btn.clicked.connect(self.on_remove_selected)
        self.clear_btn.clicked.connect(self.on_clear_all)
        self.browse_btn.clicked.connect(self.on_browse_output)
        self.convert_btn.clicked.connect(self.on_convert)
        self.youtube_add_btn.clicked.connect(self.on_add_youtube)
        self.youtube_edit.returnPressed.connect(self.on_add_youtube)

    # ------------------------------------------------------------ Drag&Drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self._add_paths(paths)

    # ------------------------------------------------------------- Actions
    def on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "변환할 파일 선택", "", SUPPORTED_FILTER)
        self._add_paths(paths)

    def on_add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "변환할 폴더 선택 (하위 폴더 포함)", str(self.settings.value("input_dir", ""))
        )
        if d:
            self.settings.setValue("input_dir", d)
            self._add_paths([d])

    @staticmethod
    def _expand_paths(paths: list[str]) -> list[str]:
        files: list[str] = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                files.extend(
                    str(f) for f in sorted(path.rglob("*"))
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
                )
            elif path.is_file():
                files.append(str(path))
        return files

    def _add_paths(self, paths: list[str]) -> None:
        existing = {
            self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())
        }
        added = 0
        for p in self._expand_paths(paths):
            if p in existing:
                continue
            self._add_row(source=p, default_output_name=Path(p).stem + ".md")
            existing.add(p)
            added += 1
        if any(Path(p).is_dir() for p in paths):
            self.log.appendPlainText(f"폴더에서 {added}개 파일을 추가했습니다.")

    def _add_row(self, source: str, default_output_name: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        src_item = QTableWidgetItem(source)
        src_item.setData(Qt.ItemDataRole.UserRole, source)
        src_item.setFlags(src_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, src_item)

        self.table.setItem(row, 1, QTableWidgetItem(default_output_name))

        status_item = QTableWidgetItem("대기")
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 2, status_item)

    def on_add_youtube(self) -> None:
        url = self.youtube_edit.text().strip()
        if not url:
            return
        if not youtube.is_youtube_url(url):
            QMessageBox.warning(self, "안내", "올바른 YouTube 링크를 입력하세요.")
            return

        existing = {
            self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())
        }
        if url in existing:
            QMessageBox.information(self, "안내", "이미 목록에 추가된 링크입니다.")
            return

        self.youtube_add_btn.setEnabled(False)
        try:
            title = youtube.fetch_title(url)
        finally:
            self.youtube_add_btn.setEnabled(True)

        if title:
            default_name = str(sanitize_filename(title)) + ".md"
        else:
            default_name = "youtube_video.md"

        self._add_row(source=url, default_output_name=default_name)
        self.youtube_edit.clear()

    def on_remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def on_clear_all(self) -> None:
        self.table.setRowCount(0)

    def on_browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.output_edit.text())
        if d:
            self.output_edit.setText(d)

    def on_convert(self) -> None:
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "안내", "변환할 파일을 먼저 추가하세요.")
            return
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "안내", "저장 폴더를 지정하세요.")
            return

        save_pdf = self.pdf_checkbox.isChecked()
        out_dir = Path(output_dir)
        out_layout = self.layout_combo.currentData()

        def targets(name: str) -> list[Path]:
            md_path = output_paths(out_dir, name, out_layout)[0]
            return [md_path, md_path.with_suffix(".pdf")] if save_pdf else [md_path]

        entries: list[tuple[int, str, str]] = []
        names_seen: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            src = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            name = self.table.item(row, 1).text().strip() or Path(src).stem
            if not name.lower().endswith(".md"):
                name += ".md"

            key = name.lower()
            count = names_seen.get(key, 0)
            if count:
                name = f"{Path(name).stem}_{count}{Path(name).suffix}"
            names_seen[key] = count + 1

            self.table.item(row, 1).setText(name)
            entries.append((row, src, name))

        conflicts = {row for row, _, name in entries if any(p.exists() for p in targets(name))}
        policy = "overwrite"
        if conflicts:
            policy = self._ask_overwrite_policy([name for row, _, name in entries if row in conflicts])
            if policy is None:
                return

        used = {name.lower() for _, _, name in entries}
        jobs: list[ConversionJob] = []
        for row, src, name in entries:
            status = self.table.item(row, 2)
            if row in conflicts and policy == "skip":
                status.setText("건너뜀")
                continue
            if row in conflicts and policy == "rename":
                name = self._unique_name(name, used, targets)
                self.table.item(row, 1).setText(name)
            status.setText("대기중")
            status.setData(Qt.ItemDataRole.UserRole, None)
            jobs.append(ConversionJob(
                row=row, source_path=src, output_name=name, save_pdf=save_pdf,
                extract_images=self.images_checkbox.isChecked(),
                ocr=self.ocr_checkbox.isChecked(),
                layout=out_layout,
                bundle_pdf=self.bundle_checkbox.isChecked(),
            ))

        if not jobs:
            self.log.appendPlainText("변환할 항목이 없습니다 (모두 건너뜀).")
            return

        self._output_dir = output_dir
        self._success_count = 0
        self.settings.setValue("output_dir", output_dir)
        self._set_running(True)
        self.progress.setMaximum(len(jobs))
        self.progress.setValue(0)
        self.log.appendPlainText(f"--- 변환 시작: {len(jobs)}개 파일 → {output_dir} ---")

        self._thread = QThread()
        self._worker = ConversionWorker(jobs, output_dir)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _ask_overwrite_policy(self, names: list[str]) -> str | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("동일 파일명 확인")
        box.setText(f"저장 폴더에 같은 이름의 파일이 {len(names)}개 있습니다.\n어떻게 처리할까요?")
        box.setDetailedText("\n".join(names))
        overwrite = box.addButton("덮어쓰기", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("건너뛰기", QMessageBox.ButtonRole.ActionRole)
        rename = box.addButton("새 이름으로 저장", QMessageBox.ButtonRole.ActionRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(rename)
        box.exec()
        clicked = box.clickedButton()
        if clicked == overwrite:
            return "overwrite"
        if clicked == skip:
            return "skip"
        if clicked == rename:
            return "rename"
        return None

    @staticmethod
    def _unique_name(name: str, used: set[str], targets) -> str:
        stem = Path(name).stem
        n = 1
        while True:
            candidate = f"{stem} ({n}).md"
            if candidate.lower() not in used and not any(p.exists() for p in targets(candidate)):
                used.add(candidate.lower())
                return candidate
            n += 1

    def on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.log.appendPlainText("취소 요청됨 - 현재 처리 중인 파일이 끝나면 중단합니다.")

    def _set_running(self, running: bool) -> None:
        for w in (
            self.add_btn, self.add_folder_btn, self.remove_btn, self.clear_btn,
            self.youtube_edit, self.youtube_add_btn, self.output_edit, self.browse_btn,
            self.convert_btn, self.pdf_checkbox, self.images_checkbox, self.layout_combo,
        ):
            w.setEnabled(not running)
        self.bundle_checkbox.setEnabled(not running and self.images_checkbox.isChecked())
        self.ocr_checkbox.setEnabled(not running and ocr_engine.is_available())
        self.cancel_btn.setEnabled(running)
        self.setAcceptDrops(not running)

    def _update_preview(self) -> None:
        row = self.table.currentRow()
        status = self.table.item(row, 2) if row >= 0 else None
        out_path = status.data(Qt.ItemDataRole.UserRole) if status else None
        if not out_path or not Path(out_path).is_file():
            self.preview_view.clear()
            self.preview_raw.clear()
            return
        text = Path(out_path).read_text(encoding="utf-8", errors="replace")
        if len(text) > PREVIEW_LIMIT:
            text = text[:PREVIEW_LIMIT] + "\n\n... (미리보기는 앞부분만 표시합니다)"
        self.preview_view.document().setBaseUrl(QUrl.fromLocalFile(str(Path(out_path).parent) + "/"))
        self.preview_view.setMarkdown(text)
        self.preview_raw.setPlainText(text)

    # ----------------------------------------------------------- Callbacks
    def _on_progress(self, row: int, out_path: str, success: bool, message: str) -> None:
        status = self.table.item(row, 2)
        status.setText("완료" if success else "실패")
        if success:
            status.setData(Qt.ItemDataRole.UserRole, out_path)
            self._success_count += 1
            self._last_success_path = out_path
        self.progress.setValue(self.progress.value() + 1)
        self.log.appendPlainText(f"[{'성공' if success else '오류'}] {out_path} - {message}")
        if row == self.table.currentRow():
            self._update_preview()

    def _on_finished(self) -> None:
        cancelled = 0
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 2)
            if status.text() == "대기중":
                status.setText("취소됨")
                cancelled += 1
        self._set_running(False)
        summary = f"성공 {self._success_count}건" + (f", 취소 {cancelled}건" if cancelled else "")
        self.log.appendPlainText(f"--- 변환 종료 ({summary}) ---")
        if self.open_folder_checkbox.isChecked() and self._success_count:
            # A single document opens straight into its folder, ready for Ctrl+A -> drag to the AI chat.
            target = str(Path(self._last_success_path).parent) if self._success_count == 1 else self._output_dir
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def on_open_result_folder(self) -> None:
        row = self.table.currentRow()
        status = self.table.item(row, 2) if row >= 0 else None
        out_path = status.data(Qt.ItemDataRole.UserRole) if status else None
        if not out_path:
            QMessageBox.information(self, "안내", "변환이 완료된 항목을 선택하세요.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_path).parent)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._worker.cancel()
            self._thread.quit()
            self._thread.wait(3000)
        event.accept()
