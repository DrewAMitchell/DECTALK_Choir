"""PySide6 operator interface that invokes the existing DECTALK Choir CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QRunnable, QSettings, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from choir_gui.inspector import RoleInspection, SongInspection, inspect_song


APP_STYLE_TEMPLATE = """
QWidget {
    background: #171a1b;
    color: #e8eded;
    font-size: __NORMAL_PT__pt;
}
QFrame#topBar, QFrame#overview, QFrame#detailPanel, QFrame#logPanel {
    background: #202526;
    border: 1px solid #384143;
    border-radius: 6px;
}
QLabel#title {
    color: #f5f8f7;
    font-size: __TITLE_PT__pt;
    font-weight: 600;
}
QLabel#eyebrow, QLabel#metricLabel {
    color: #9aa8a7;
    font-size: __SMALL_PT__pt;
    font-weight: 600;
}
QLabel#metricValue {
    color: #edf4f1;
    font-size: __METRIC_PT__pt;
    font-weight: 600;
}
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
    background: #151819;
    border: 1px solid #3c4748;
    border-radius: 4px;
    color: #edf4f1;
    selection-background-color: #276d58;
    selection-color: #ffffff;
}
QLineEdit, QComboBox {
    min-height: __CONTROL_HEIGHT__px;
    padding: __CONTROL_PADDING__px __CONTROL_HORIZONTAL_PADDING__px;
}
QLineEdit:read-only { color: #c5d0ce; }
QComboBox::drop-down { border: 0; width: 24px; }
QPushButton, QToolButton {
    background: #2a3233;
    border: 1px solid #465253;
    border-radius: 4px;
    color: #eef5f2;
    min-height: __CONTROL_HEIGHT__px;
    padding: __CONTROL_PADDING__px __BUTTON_HORIZONTAL_PADDING__px;
}
QPushButton:hover, QToolButton:hover { background: #354143; }
QPushButton:disabled, QToolButton:disabled { color: #74807f; background: #202526; }
QPushButton#renderButton { background: #247455; border-color: #49bc8f; font-weight: 600; }
QPushButton#renderButton:hover { background: #2d8a65; }
QCheckBox { color: #cfd9d6; spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked { border: 1px solid #647270; background: #151819; border-radius: 3px; }
QCheckBox::indicator:checked { border: 1px solid #5ed4a4; background: #2c9b71; border-radius: 3px; }
QTableWidget { gridline-color: #303839; border-radius: 5px; }
QTableWidget::item { padding: 5px 6px; border-bottom: 1px solid #293031; }
QTableWidget::item:selected { background: #245f4d; }
QHeaderView::section {
    background: #242b2c;
    color: #aebdb9;
    border: 0;
    border-bottom: 1px solid #46504e;
    padding: 6px;
    font-size: 8pt;
    font-weight: 600;
}
QPlainTextEdit { font-size: __MONO_PT__pt; padding: __LOG_PADDING__px; }
QSplitter::handle { background: #384143; }
QSplitter::handle:hover { background: #56b990; }
"""


def style_for_scale(scale: float) -> str:
    """Scale the compact Qt surface without changing the data layout contract."""

    scale = max(0.85, min(1.6, float(scale)))
    replacements = {
        "__NORMAL_PT__": f"{10.5 * scale:.1f}",
        "__TITLE_PT__": f"{19 * scale:.1f}",
        "__SMALL_PT__": f"{8.5 * scale:.1f}",
        "__METRIC_PT__": f"{10.5 * scale:.1f}",
        "__MONO_PT__": f"{9 * scale:.1f}",
        "__CONTROL_HEIGHT__": str(round(26 * scale)),
        "__CONTROL_PADDING__": str(max(2, round(2 * scale))),
        "__CONTROL_HORIZONTAL_PADDING__": str(round(7 * scale)),
        "__BUTTON_HORIZONTAL_PADDING__": str(round(9 * scale)),
        "__LOG_PADDING__": str(round(7 * scale)),
    }
    style = APP_STYLE_TEMPLATE
    for token, value in replacements.items():
        style = style.replace(token, value)
    return style


# Kept for callers that apply the GUI theme at the QApplication level.
APP_STYLE = style_for_scale(1.2)


class InspectSignals(QObject):
    """Signals emitted by the background song inspection task."""

    completed = Signal(int, object)
    failed = Signal(int, str)


class InspectTask(QRunnable):
    """Measure files off the UI thread so large songs remain responsive."""

    def __init__(self, token: int, repo_root: Path, song_name: str):
        super().__init__()
        self.token = token
        self.repo_root = repo_root
        self.song_name = song_name
        self.signals = InspectSignals()

    def run(self) -> None:
        try:
            inspection = inspect_song(self.repo_root, self.song_name, include_audio=True)
        except Exception as error:  # Keep a malformed user file from killing the GUI.
            self.signals.failed.emit(self.token, str(error))
            return
        self.signals.completed.emit(self.token, inspection)


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = max(0, round(seconds))
    return f"{total // 60}:{total % 60:02d}"


class ChoirWindow(QMainWindow):
    """The native control surface. Rendering always happens through subprocess CLI calls."""

    def __init__(self, repo_root: Path | None = None) -> None:
        super().__init__()
        self.settings = QSettings("Drew", "DECTALK Choir")
        self.ui_scale = self._saved_ui_scale()
        self.help_buttons: list[QToolButton] = []
        self.thread_pool = QThreadPool.globalInstance()
        self.inspect_token = 0
        self.inspection: SongInspection | None = None
        self.process: QProcess | None = None
        self.active_task_name = ""
        self.current_role: RoleInspection | None = None
        self.repo_root = (repo_root or self._saved_repo_root()).resolve()

        self.setWindowTitle("DECTALK Choir")
        self.setMinimumSize(1180, 720)
        self.resize(1540, 940)
        self._build_ui()
        self._apply_ui_scale(self.ui_scale)
        self._restore_window_state()
        self._load_song_choices()

    def _saved_repo_root(self) -> Path:
        stored = self.settings.value("repo_root", "")
        candidate = Path(str(stored)).expanduser() if stored else default_repo_root()
        return candidate if (candidate / "choir.py").is_file() else default_repo_root()

    def _saved_ui_scale(self) -> float:
        try:
            value = float(self.settings.value("ui_scale", 1.2))
        except (TypeError, ValueError):
            value = 1.2
        return max(0.85, min(1.6, value))

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("repo_root", str(self.repo_root))
        self.settings.setValue("song_name", self.song_combo.currentText())
        self.settings.setValue("ui_scale", self.ui_scale)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        root_layout.addWidget(self._build_top_bar())
        root_layout.addWidget(self._build_overview())

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.addWidget(self._build_role_table())
        self.side_panel = self._build_side_panel()
        workspace.addWidget(self.side_panel)
        workspace.setStretchFactor(0, 5)
        workspace.setStretchFactor(1, 2)
        workspace.setSizes([1040, 400])
        root_layout.addWidget(workspace, 1)

        self.status_label = QLabel("Choose a song to inspect.")
        self.status_label.setObjectName("eyebrow")
        root_layout.addWidget(self.status_label)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QGridLayout(bar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)

        title = QLabel("DECTALK Choir")
        title.setObjectName("title")
        subtitle = QLabel("Native render control and song inspection")
        subtitle.setObjectName("eyebrow")
        title_stack = QVBoxLayout()
        title_stack.setSpacing(0)
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        layout.addLayout(title_stack, 0, 0, 2, 1)

        layout.addWidget(QLabel("Project"), 0, 1)
        self.repo_edit = QLineEdit(str(self.repo_root))
        self.repo_edit.setReadOnly(True)
        self.repo_edit.setToolTip("Choir project root containing choir.py, songs, and outputs.")
        layout.addWidget(self.repo_edit, 0, 2)
        project_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "Choose Choir project folder",
            self.choose_project,
        )
        layout.addWidget(project_button, 0, 3)

        layout.addWidget(QLabel("Song"), 1, 1)
        self.song_combo = QComboBox()
        self.song_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.song_combo.currentTextChanged.connect(self.inspect_current_song)
        layout.addWidget(self.song_combo, 1, 2)
        song_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "Choose an existing song folder under this project's songs directory",
            self.choose_song_folder,
        )
        layout.addWidget(song_button, 1, 3)

        layout.addWidget(QLabel("Scale"), 0, 4)
        self.scale_combo = QComboBox()
        self.scale_combo.setToolTip(
            "Visual scale only. It enlarges typography, controls, table rows, and the inspector while keeping the same Choir render settings."
        )
        for percent in (90, 100, 110, 120, 135, 150):
            self.scale_combo.addItem(f"{percent}%", percent / 100)
        closest_scale = min(
            range(self.scale_combo.count()),
            key=lambda index: abs(float(self.scale_combo.itemData(index)) - self.ui_scale),
        )
        self.scale_combo.setCurrentIndex(closest_scale)
        self.scale_combo.currentIndexChanged.connect(self._change_ui_scale)
        layout.addWidget(self.scale_combo, 0, 5)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-scan the selected song's settings, MIDI, lyric files, rendered stems, and volume measurements.")
        self.refresh_button.clicked.connect(self._load_song_choices)
        layout.addWidget(self.refresh_button, 1, 4)

        self.split_button = QPushButton("Split MIDI")
        self.split_button.setToolTip(
            "Create a separate monophonic voice track for each overlapping MIDI note lane. The original MIDI is never modified."
        )
        self.split_button.clicked.connect(self.split_midi)
        layout.addWidget(self.split_button, 1, 5)

        self.visuals_check = QCheckBox("Spectrogram")
        visuals_help = (
            "After audio rendering, run the spectrogram generator for the selected song. "
            "This creates visual output and can add substantial render time; it does not alter the WAV mix."
        )
        self.visuals_check.setToolTip(visuals_help)
        layout.addWidget(self._checkbox_with_help(self.visuals_check, visuals_help), 0, 6)
        self.plots_check = QCheckBox("Phoneme plots")
        plots_help = (
            "Write a per-role image showing the emitted phoneme symbols, note pitches, and durations. "
            "Use it to inspect lyric-to-note allocation; it does not change the generated audio."
        )
        self.plots_check.setToolTip(plots_help)
        layout.addWidget(self._checkbox_with_help(self.plots_check, plots_help), 1, 6)

        self.render_button = QPushButton("Render")
        self.render_button.setObjectName("renderButton")
        self.render_button.setToolTip(
            "Run the selected song through the existing choir.py command contract. The live console on the right is the renderer's direct output."
        )
        self.render_button.clicked.connect(self.render_song)
        layout.addWidget(self.render_button, 0, 7, 2, 1)
        layout.setColumnStretch(2, 1)
        return bar

    def _build_overview(self) -> QFrame:
        overview = QFrame()
        overview.setObjectName("overview")
        layout = QGridLayout(overview)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setHorizontalSpacing(22)
        self.overview_values: dict[str, QLabel] = {}
        metrics = (
            ("Settings", "settings"),
            ("MIDI", "midi"),
            ("Duration", "duration"),
            ("Roles", "roles"),
            ("Ready", "ready"),
            ("Final mix", "mix"),
        )
        for column, (label_text, key) in enumerate(metrics):
            label = QLabel(label_text.upper())
            label.setObjectName("metricLabel")
            value = QLabel("--")
            value.setObjectName("metricValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, 0, column)
            layout.addWidget(value, 1, column)
            self.overview_values[key] = value
        layout.setColumnStretch(len(metrics), 1)
        return overview

    def _build_role_table(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading = QLabel("Roles and Sources")
        heading.setObjectName("metricValue")
        layout.addWidget(heading)

        self.role_table = QTableWidget(0, 10)
        self.role_table.setHorizontalHeaderLabels(
            [
                "Role",
                "MIDI source",
                "Lyric input",
                "Notes",
                "MIDI range",
                "Render pitch",
                "Audible pitch",
                "Poly",
                "Stem loudness dBFS",
                "Status",
            ]
        )
        self.role_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.role_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.role_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.role_table.setAlternatingRowColors(False)
        self.role_table.verticalHeader().setVisible(False)
        header = self.role_table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(4, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        column_help = {
            0: "The output role name: the key under Tracks in settings.yaml. It names the stem and output folders.",
            1: "The title of the source track inside the MIDI file, selected by TRACK_FILENAME.",
            2: "The lyric file stem under lyrics/, selected by LYRICS_FILENAME.",
            3: "Count of playable note spans found in the selected MIDI source track.",
            4: "Raw scientific-pitch range in the MIDI file before DECTALK mapping.",
            5: "Bounded pitch range sent to DECTALK after note offset, shifts, wrapping, and render-time OCTAVE_BOOST adjustment.",
            6: "Expected musical range after OCTAVE_BOOST resampling restores the intended octave.",
            7: "Maximum simultaneous MIDI notes. Choir input should be 1; higher values need the MIDI splitter.",
            8: "Active 100 ms RMS windows: min / median / average / max, followed by peak dBFS. Windows below -70 dBFS are excluded.",
            9: "Ready means the configured MIDI source and lyric input exist and the source is monophonic.",
        }
        for column, tooltip in column_help.items():
            self.role_table.horizontalHeaderItem(column).setToolTip(tooltip)
        self.role_table.itemSelectionChanged.connect(self._select_role)
        layout.addWidget(self.role_table, 1)
        return container

    def _build_side_panel(self) -> QWidget:
        side = QWidget()
        side.setMinimumWidth(340)
        side.setMaximumWidth(520)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        detail = QFrame()
        detail.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(8)
        self.detail_title = QLabel("Selected Role")
        self.detail_title.setObjectName("metricValue")
        detail_layout.addWidget(self.detail_title)
        self.detail_fields: dict[str, QLabel] = {}
        for label_text, key in (
            ("MIDI track", "midi"),
            ("Lyrics", "lyrics"),
            ("Pitch wrap", "wrap"),
            ("Stem", "stem"),
            ("Details", "details"),
        ):
            label = QLabel(label_text.upper())
            label.setObjectName("metricLabel")
            value = QLabel("--")
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_fields[key] = value
            detail_layout.addWidget(label)
            detail_layout.addWidget(value)

        detail_actions = QHBoxLayout()
        self.open_lyrics_button = QPushButton("Open lyrics")
        self.open_lyrics_button.clicked.connect(lambda: self._open_role_path("lyrics"))
        self.open_midi_button = QPushButton("Open MIDI")
        self.open_midi_button.clicked.connect(lambda: self._open_role_path("midi"))
        self.open_stem_button = QPushButton("Open stem")
        self.open_stem_button.clicked.connect(lambda: self._open_role_path("stem"))
        for button in (self.open_lyrics_button, self.open_midi_button, self.open_stem_button):
            detail_actions.addWidget(button)
        detail_layout.addLayout(detail_actions)

        output_actions = QHBoxLayout()
        self.open_song_button = QPushButton("Song folder")
        self.open_song_button.clicked.connect(lambda: self._open_path(self.repo_root / "songs" / self.song_combo.currentText()))
        self.open_output_button = QPushButton("Output folder")
        self.open_output_button.clicked.connect(lambda: self._open_output("folder"))
        self.open_mix_button = QPushButton("Final mix")
        self.open_mix_button.clicked.connect(lambda: self._open_output("mix"))
        for button in (self.open_song_button, self.open_output_button, self.open_mix_button):
            output_actions.addWidget(button)
        detail_layout.addLayout(output_actions)
        layout.addWidget(detail)

        log_frame = QFrame()
        log_frame.setObjectName("logPanel")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_heading = QHBoxLayout()
        log_label = QLabel("CLI Output")
        log_label.setObjectName("metricValue")
        clear_log = QToolButton()
        clear_log.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        clear_log.setToolTip("Clear CLI output")
        clear_log.clicked.connect(lambda: self.log.clear())
        log_heading.addWidget(log_label)
        log_heading.addStretch(1)
        log_heading.addWidget(clear_log)
        log_layout.addLayout(log_heading)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(8000)
        log_layout.addWidget(self.log, 1)
        layout.addWidget(log_frame, 1)
        self._set_detail_actions_enabled(False)
        return side

    def _tool_button(self, icon: QStyle.StandardPixmap, tooltip: str, action) -> QToolButton:
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(tooltip)
        button.clicked.connect(action)
        return button

    def _checkbox_with_help(self, checkbox: QCheckBox, tooltip: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        help_button = QToolButton()
        help_button.setText("?")
        help_button.setToolTip(tooltip)
        help_button.setAutoRaise(True)
        help_button.setAccessibleName(f"Help for {checkbox.text()}")
        self.help_buttons.append(help_button)
        layout.addWidget(checkbox)
        layout.addWidget(help_button)
        layout.addStretch(1)
        return container

    def _change_ui_scale(self, index: int) -> None:
        value = self.scale_combo.itemData(index)
        if value is not None:
            self._apply_ui_scale(float(value))
            self.settings.setValue("ui_scale", self.ui_scale)

    def _apply_ui_scale(self, scale: float) -> None:
        self.ui_scale = max(0.85, min(1.6, scale))
        self.setStyleSheet(style_for_scale(self.ui_scale))
        self.side_panel.setMinimumWidth(round(320 * self.ui_scale))
        self.side_panel.setMaximumWidth(round(520 * self.ui_scale))
        self.role_table.verticalHeader().setDefaultSectionSize(round(31 * self.ui_scale))
        help_size = max(20, round(22 * self.ui_scale))
        for button in self.help_buttons:
            button.setFixedSize(help_size, help_size)
            button.setStyleSheet("font-weight: 700;")

    def _load_song_choices(self) -> None:
        previous = self.song_combo.currentText() or str(self.settings.value("song_name", ""))
        songs_dir = self.repo_root / "songs"
        self.repo_edit.setText(str(self.repo_root))
        self.song_combo.blockSignals(True)
        self.song_combo.clear()
        if songs_dir.is_dir():
            names = sorted(path.name for path in songs_dir.iterdir() if path.is_dir())
            self.song_combo.addItems(names)
        self.song_combo.blockSignals(False)
        if previous:
            index = self.song_combo.findText(previous)
            if index >= 0:
                self.song_combo.setCurrentIndex(index)
        if self.song_combo.count():
            self.inspect_current_song()
        else:
            self.status_label.setText(f"No song folders found in {songs_dir}")

    def choose_project(self) -> None:
        start_dir = self._last_dialog_dir(self.repo_root)
        chosen = QFileDialog.getExistingDirectory(self, "Choose DECTALK Choir project", str(start_dir))
        if not chosen:
            return
        candidate = Path(chosen).resolve()
        if not (candidate / "choir.py").is_file() or not (candidate / "songs").is_dir():
            QMessageBox.warning(
                self,
                "Not a Choir project",
                "Choose the folder containing choir.py and the songs directory.",
            )
            return
        self.repo_root = candidate
        self.settings.setValue("repo_root", str(candidate))
        self._remember_dialog_dir(candidate)
        self._load_song_choices()

    def choose_song_folder(self) -> None:
        songs_dir = self.repo_root / "songs"
        chosen = QFileDialog.getExistingDirectory(self, "Choose song folder", str(songs_dir))
        if not chosen:
            return
        candidate = Path(chosen).resolve()
        if candidate.parent != songs_dir.resolve():
            QMessageBox.warning(
                self,
                "Song must be inside this project",
                f"Choose a direct child folder of {songs_dir} so choir.py can render it by name.",
            )
            return
        index = self.song_combo.findText(candidate.name)
        if index < 0:
            self._load_song_choices()
            index = self.song_combo.findText(candidate.name)
        if index >= 0:
            self.song_combo.setCurrentIndex(index)
        self._remember_dialog_dir(candidate)

    def _last_dialog_dir(self, fallback: Path) -> Path:
        stored = self.settings.value("last_dialog_dir", "")
        candidate = Path(str(stored)) if stored else fallback
        return candidate if candidate.is_dir() else fallback

    def _remember_dialog_dir(self, path: Path) -> None:
        self.settings.setValue("last_dialog_dir", str(path if path.is_dir() else path.parent))

    def inspect_current_song(self) -> None:
        song_name = self.song_combo.currentText()
        if not song_name:
            return
        self.inspect_token += 1
        token = self.inspect_token
        self.status_label.setText(f"Inspecting {song_name}: MIDI, lyrics, and output loudness...")
        self.role_table.setRowCount(0)
        task = InspectTask(token, self.repo_root, song_name)
        task.signals.completed.connect(self._inspection_completed)
        task.signals.failed.connect(self._inspection_failed)
        self.thread_pool.start(task)

    def _inspection_completed(self, token: int, inspection: SongInspection) -> None:
        if token != self.inspect_token:
            return
        self.inspection = inspection
        self._populate_inspection(inspection)

    def _inspection_failed(self, token: int, error: str) -> None:
        if token != self.inspect_token:
            return
        self.inspection = None
        self.status_label.setText(f"Inspection failed: {error}")
        self.log.appendPlainText(f"Inspection failed: {error}")

    def _populate_inspection(self, inspection: SongInspection) -> None:
        self.overview_values["settings"].setText(
            "Ready" if inspection.settings_path.is_file() else "Missing"
        )
        self.overview_values["midi"].setText(
            inspection.midi_path.name if inspection.midi_path else "Missing"
        )
        self.overview_values["duration"].setText(
            human_duration(inspection.midi.duration_seconds if inspection.midi else None)
        )
        self.overview_values["roles"].setText(str(len(inspection.roles)))
        ready = sum(1 for role in inspection.roles if role.status == "Ready")
        self.overview_values["ready"].setText(f"{ready} / {len(inspection.roles)}")
        self.overview_values["mix"].setText(
            inspection.final_loudness.display if inspection.final_loudness else "Not rendered"
        )

        self.role_table.setRowCount(len(inspection.roles))
        for row, role in enumerate(inspection.roles):
            loudness = role.loudness.display if role.loudness else "Not rendered"
            values = (
                role.role,
                role.midi_source_name,
                role.lyric_stem,
                str(role.note_count) if role.midi_track else "--",
                role.midi_range,
                role.render_range,
                role.audible_range,
                str(role.polyphony) if role.polyphony is not None else "--",
                loudness,
                role.status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 9:
                    item.setForeground(self._status_color(role.status))
                self.role_table.setItem(row, column, item)
        self.role_table.resizeRowsToContents()
        self.current_role = None
        self._set_detail_actions_enabled(False)
        if inspection.roles:
            self.role_table.selectRow(0)

        messages = [*inspection.errors, *inspection.warnings]
        if messages:
            self.status_label.setText(" | ".join(messages[:2]))
            self.log.appendPlainText("Inspection notes:\n" + "\n".join(f"- {item}" for item in messages))
        else:
            self.status_label.setText(
                f"{inspection.song_name} inspected: {len(inspection.roles)} role(s), "
                f"{ready} ready to render."
            )

    def _status_color(self, status: str) -> QColor:
        if status == "Ready":
            return QColor("#67d7a7")
        if "Polyphonic" in status:
            return QColor("#f0bf65")
        return QColor("#ef8b83")

    def _select_role(self) -> None:
        if not self.inspection:
            return
        row = self.role_table.currentRow()
        if row < 0 or row >= len(self.inspection.roles):
            return
        role = self.inspection.roles[row]
        self.current_role = role
        self.detail_title.setText(role.role)
        midi_description = (
            f"{role.midi_source_name} ({role.note_count} notes)"
            if role.midi_track
            else role.midi_source_name
        )
        self.detail_fields["midi"].setText(midi_description)
        self.detail_fields["lyrics"].setText(self._short_path(role.lyric_path))
        wrap = "--" if role.pitch_wrap_shift is None else f"{role.pitch_wrap_shift:+} semitones"
        self.detail_fields["wrap"].setText(wrap)
        self.detail_fields["stem"].setText(self._short_path(role.stem_path))
        self.detail_fields["details"].setText("\n".join(role.details) if role.details else role.status)
        self._set_detail_actions_enabled(True)

    def _short_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    def _set_detail_actions_enabled(self, enabled: bool) -> None:
        for button in (self.open_lyrics_button, self.open_midi_button, self.open_stem_button):
            button.setEnabled(enabled)

    def _open_role_path(self, kind: str) -> None:
        if not self.current_role or not self.inspection:
            return
        if kind == "lyrics":
            self._open_path(self.current_role.lyric_path)
        elif kind == "midi":
            self._open_path(self.inspection.midi_path or self.inspection.song_dir)
        elif kind == "stem":
            self._open_path(self.current_role.stem_path)

    def _open_output(self, kind: str) -> None:
        if not self.inspection:
            return
        self._open_path(self.inspection.final_mix if kind == "mix" else self.inspection.output_dir)

    def _open_path(self, path: Path) -> None:
        target = path if path.exists() else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.status_label.setText(f"Could not open {target}")

    def render_song(self) -> None:
        song_name = self.song_combo.currentText()
        if not song_name:
            return
        if not (self.repo_root / "choir.py").is_file():
            QMessageBox.warning(self, "Cannot render", "choir.py was not found in the selected project.")
            return
        arguments = ["choir.py"]
        if self.visuals_check.isChecked():
            arguments.append("-vis")
        if self.plots_check.isChecked():
            arguments.append("-plt")
        arguments.append(song_name)
        self._start_process(f"Render {song_name}", arguments)

    def split_midi(self) -> None:
        start_dir = self._last_dialog_dir(self.repo_root / "songs")
        source_text, _ = QFileDialog.getOpenFileName(
            self,
            "Choose polyphonic MIDI to split",
            str(start_dir),
            "MIDI files (*.mid *.midi)",
        )
        if not source_text:
            return
        source = Path(source_text).resolve()
        default_output = source.with_name(f"{source.stem}_monophonic.mid")
        output_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save monophonic MIDI",
            str(default_output),
            "MIDI files (*.mid *.midi)",
        )
        if not output_text:
            return
        output = Path(output_text).resolve()
        if output.suffix.lower() not in {".mid", ".midi"}:
            output = output.with_suffix(".mid")
        if output.exists() and QMessageBox.question(
            self,
            "Replace output MIDI?",
            f"{output.name} already exists. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._remember_dialog_dir(source.parent)
        self._start_process(
            f"Split {source.name}",
            ["tools/split_polyphonic_midi.py", str(source), "--output", str(output)],
        )

    def _python_executable(self) -> Path:
        project_python = self.repo_root / ".venv" / "Scripts" / "python.exe"
        return project_python if project_python.is_file() else Path(sys.executable)

    def _start_process(self, task_name: str, arguments: list[str]) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "Task already running", "Wait for the current task to finish.")
            return
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.repo_root))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._process_finished)
        self.active_task_name = task_name
        self.log.appendPlainText(f"\n$ {self._python_executable()} {' '.join(arguments)}\n")
        self._set_task_running(True)
        self.status_label.setText(f"{task_name} is running...")
        self.process.start(str(self._python_executable()), arguments)

    def _read_process_output(self) -> None:
        if not self.process:
            return
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if output:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(output)
            self.log.ensureCursorVisible()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if self.process:
            self.log.appendPlainText(f"\nProcess error: {self.process.errorString()} ({error.name})")

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        successful = exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        state = "completed" if successful else f"failed (exit {exit_code})"
        self.log.appendPlainText(f"\n[{self.active_task_name} {state}]\n")
        self.status_label.setText(f"{self.active_task_name} {state}.")
        self._set_task_running(False)
        self.process = None
        if successful:
            self.inspect_current_song()

    def _set_task_running(self, running: bool) -> None:
        for widget in (self.render_button, self.split_button, self.refresh_button, self.song_combo):
            widget.setEnabled(not running)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the native DECTALK Choir operator GUI.")
    parser.add_argument("--repo", type=Path, help="Choir project root. Defaults to this checkout.")
    parser.add_argument(
        "--inspect",
        metavar="SONG",
        help="Print a non-GUI song inspection summary, useful for smoke tests.",
    )
    return parser.parse_args()


def print_inspection(inspection: SongInspection) -> None:
    print(f"Song: {inspection.song_name}")
    print(f"Settings: {inspection.settings_path}")
    print(f"MIDI: {inspection.midi_path or 'missing'}")
    print(f"Roles: {len(inspection.roles)}")
    for role in inspection.roles:
        print(
            f"- {role.role}: notes={role.note_count}, midi={role.midi_range}, "
            f"render={role.render_range}, status={role.status}"
        )
    for message in (*inspection.errors, *inspection.warnings):
        print(f"! {message}")


def main() -> int:
    args = parse_args()
    repo_root = (args.repo or default_repo_root()).expanduser().resolve()
    if args.inspect:
        print_inspection(inspect_song(repo_root, args.inspect, include_audio=True))
        return 0
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    window = ChoirWindow(repo_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
