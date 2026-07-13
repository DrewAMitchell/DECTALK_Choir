"""PySide6 operator interface that invokes the existing DECTALK Choir CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

from PySide6.QtCore import (
    QObject,
    QProcess,
    QRunnable,
    QSettings,
    QSignalBlocker,
    QTimer,
    Qt,
    QThreadPool,
    QUrl,
    QRect,
    QSize,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFontMetrics,
    QPainter,
    QPen,
    QTextCursor,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QInputDialog,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from choir_gui.inspector import (
    RoleInspection,
    SongInspection,
    _lyric_conversion_issue,
    inspect_song,
)
from choir_gui.import_workflow import MidiImportError, import_midi_song
from choir_gui.midi_workflow import (
    MidiPreviewError,
    MidiTimelineWidget,
    WindowsMidiPlayer,
    write_single_track_preview,
)
from choir_gui.split_workflow import (
    MidiSplitError,
    analyze_midi_source,
    split_track_preview,
    split_view_tracks,
)

ALIGNMENT_TOOL_DIR = Path(__file__).resolve().parents[1] / "tools" / "lyric_sync_assistant"
if str(ALIGNMENT_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(ALIGNMENT_TOOL_DIR))
from alignment import insert_alignment_token, resize_alignment_token


APP_STYLE_TEMPLATE = """
QWidget {
    background: #171a1b;
    color: #e8eded;
    font-size: __NORMAL_PT__pt;
}
QFrame#topBar {
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
QLabel#sectionTitle { color: #eef5f2; font-size: __METRIC_PT__pt; font-weight: 600; }
QLabel#fieldLabel { color: #8e9b99; font-size: __SMALL_PT__pt; font-weight: 600; }
QLabel#fieldValue { color: #dfe8e5; }
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
QCheckBox { color: #cfd9d6; spacing: 8px; padding: 3px 0; }
QCheckBox::indicator { width: 16px; height: 16px; margin: 1px; }
QCheckBox::indicator:unchecked { border: 1px solid #647270; background: #151819; border-radius: 3px; }
QCheckBox::indicator:checked { border: 1px solid #72e0b4; background: #2c9b71; border-radius: 3px; }
QTableWidget { gridline-color: #303839; border-radius: 5px; }
QTableWidget::item { padding: __TABLE_PADDING__px __TABLE_HORIZONTAL_PADDING__px; border-bottom: 1px solid #293031; }
QTableWidget::item:selected { background: #245f4d; }
QHeaderView::section {
    background: #242b2c;
    color: #aebdb9;
    border: 0;
    border-bottom: 1px solid #46504e;
    padding: __TABLE_PADDING__px;
    font-size: __SMALL_PT__pt;
    font-weight: 600;
}
QPlainTextEdit { font-size: __MONO_PT__pt; padding: __LOG_PADDING__px; }
QTabWidget::pane { border: 1px solid #384143; border-radius: 5px; top: -1px; }
QTabBar::tab {
    background: #202526;
    border: 1px solid #384143;
    border-bottom: 0;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    color: #aebdb9;
    padding: __TABLE_PADDING__px 12px;
    margin-right: 3px;
}
QWidget#overview { background: transparent; }
QWidget#inspectorPanel, QWidget#logPanel { background: #202526; }
QTabBar::tab:selected { background: #276d58; color: #f4fbf7; border-color: #56b990; }
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
        "__TABLE_PADDING__": str(max(4, round(5 * scale))),
        "__TABLE_HORIZONTAL_PADDING__": str(round(6 * scale)),
    }
    style = APP_STYLE_TEMPLATE
    for token, value in replacements.items():
        style = style.replace(token, value)
    return style


# Kept for callers that apply the GUI theme at the QApplication level.
APP_STYLE = style_for_scale(1.2)


class VisibleCheckBox(QCheckBox):
    """Paint a reliable checked mark instead of relying on a themed glyph."""

    def sizeHint(self) -> QSize:  # type: ignore[override]
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self.text()) + 30, max(24, metrics.height() + 8))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        box = QRect(1, max(1, (self.height() - 16) // 2), 16, 16)
        border = QColor("#72e0b4" if enabled else "#53605e")
        fill = QColor("#2c9b71" if enabled else "#283130")
        text = QColor("#cfd9d6" if enabled else "#74807f")
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill if self.isChecked() else QColor("#151819"))
        painter.drawRoundedRect(box, 3, 3)
        if self.isChecked():
            painter.setPen(QPen(QColor("#f4fbf7"), 2))
            painter.drawLine(4, box.center().y(), 8, box.bottom() - 4)
            painter.drawLine(8, box.bottom() - 4, box.right() - 3, box.top() + 4)
        painter.setPen(text)
        painter.drawText(
            QRect(25, 0, max(0, self.width() - 25), self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )


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
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        if self.cancelled:
            return
        try:
            inspection = inspect_song(self.repo_root, self.song_name, include_audio=True)
        except Exception as error:  # Keep a malformed user file from killing the GUI.
            if self.cancelled:
                return
            self.signals.failed.emit(self.token, str(error))
            return
        if self.cancelled:
            return
        self.signals.completed.emit(self.token, inspection)


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = max(0, round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def human_milliseconds(milliseconds: int | float | None) -> str:
    total = max(0, round(float(milliseconds or 0) / 1000))
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
        self.inspect_task: InspectTask | None = None
        self.inspection: SongInspection | None = None
        self.process: QProcess | None = None
        self.process_success_callback: Callable[[], None] | None = None
        self.active_task_name = ""
        self.current_role: RoleInspection | None = None
        self.latest_draft_path: Path | None = None
        self.latest_transcript_path: Path | None = None
        self.latest_alignment_path: Path | None = None
        self.latest_alignment_report_path: Path | None = None
        self.alignment_report: dict | None = None
        self.selected_alignment_key: tuple[int, int] | None = None
        self.split_source_path: Path | None = None
        self.split_analyses = ()
        self.split_selected_analysis = None
        self.split_lanes = []
        self.pending_split_replace: tuple[Path, Path, Path] | None = None
        self.midi_preview_player = WindowsMidiPlayer()
        self.midi_preview_duration_ms = 0
        self.midi_preview_timer = QTimer(self)
        self.midi_preview_timer.setInterval(100)
        self.midi_preview_timer.timeout.connect(self._update_midi_preview_position)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_player.playbackStateChanged.connect(self._update_audio_state)
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
        self.inspect_token += 1
        if self.inspect_task:
            self.inspect_task.cancel()
        self.midi_preview_timer.stop()
        self.midi_preview_player.close()
        self.audio_player.stop()
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

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.addTab(self._build_song_workspace(), "Song")
        self.workspace_tabs.addTab(self._build_midi_workflow(), "MIDI")
        self.workspace_tabs.addTab(self._build_split_workflow(), "Split")
        self.workspace_tabs.addTab(self._build_draft_workflow(), "Draft")
        self.workspace_tabs.addTab(self._build_alignment_workflow(), "Align")
        root_layout.addWidget(self.workspace_tabs, 1)

        self.status_label = QLabel("Choose a song to inspect.")
        self.status_label.setObjectName("eyebrow")
        root_layout.addWidget(self.status_label)

    def _build_song_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_role_table())
        self.side_panel = self._build_side_panel()
        splitter.addWidget(self.side_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([1040, 400])
        layout.addWidget(splitter)
        return workspace

    def _build_midi_workflow(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("MIDI preview and playback")
        heading.setObjectName("metricValue")
        layout.addWidget(heading)

        visual_panel = QFrame()
        visual_panel.setObjectName("detailPanel")
        visual_layout = QVBoxLayout(visual_panel)
        visual_layout.setContentsMargins(12, 10, 12, 10)
        visual_layout.setSpacing(8)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("MIDI source"))
        self.midi_source_combo = QComboBox()
        self.midi_source_combo.setToolTip(
            "Choose one source track for a focused piano-roll view and source-MIDI preview, or show all note tracks together."
        )
        self.midi_source_combo.currentIndexChanged.connect(self._midi_source_changed)
        source_row.addWidget(self.midi_source_combo, 1)
        self.play_midi_button = QPushButton("Play MIDI")
        self.play_midi_button.setToolTip(
            "Create a temporary preview containing timing metadata and only the selected MIDI track, then play it through Windows' system MIDI sequencer."
        )
        self.play_midi_button.clicked.connect(self.play_selected_midi)
        self.pause_midi_button = QPushButton("Pause")
        self.pause_midi_button.setToolTip("Pause or resume the source-MIDI preview.")
        self.pause_midi_button.clicked.connect(self.pause_or_resume_midi)
        self.stop_midi_button = QPushButton("Stop")
        self.stop_midi_button.setToolTip("Stop the source-MIDI preview and return its playhead to the start.")
        self.stop_midi_button.clicked.connect(self.stop_midi)
        self.midi_import_button = QPushButton("Import MIDI")
        self.midi_import_button.setToolTip(
            "Choose a MIDI file from Downloads or another folder and create a new songs/<Song>/ scaffold."
        )
        self.midi_import_button.clicked.connect(self.choose_import_midi)
        for button in (
            self.play_midi_button,
            self.pause_midi_button,
            self.stop_midi_button,
            self.midi_import_button,
        ):
            source_row.addWidget(button)
        visual_layout.addLayout(source_row)

        pitch_view_row = QHBoxLayout()
        pitch_view_row.addWidget(QLabel("Pitch view"))
        self.midi_pitch_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.midi_pitch_zoom_slider.setRange(0, 100)
        self.midi_pitch_zoom_slider.setValue(0)
        self.midi_pitch_zoom_slider.setToolTip(
            "Zoom the vertical pitch range. Use the mouse wheel over the timeline to pan when zoomed."
        )
        pitch_view_row.addWidget(self.midi_pitch_zoom_slider, 1)
        self.midi_fit_pitch_button = QPushButton("Fit pitches")
        self.midi_fit_pitch_button.setToolTip(
            "Show the full pitch range of the selected MIDI source and clear out-of-view markers."
        )
        pitch_view_row.addWidget(self.midi_fit_pitch_button)
        self.midi_pitch_range_label = QLabel("No MIDI notes")
        self.midi_pitch_range_label.setObjectName("eyebrow")
        pitch_view_row.addWidget(self.midi_pitch_range_label)
        visual_layout.addLayout(pitch_view_row)

        midi_time_view_row = QHBoxLayout()
        midi_time_view_row.addWidget(QLabel("Time zoom"))
        self.midi_time_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.midi_time_zoom_slider.setRange(0, 100)
        self.midi_time_zoom_slider.setValue(0)
        self.midi_time_zoom_slider.setToolTip(
            "Zoom the horizontal time axis. Ctrl+wheel over the timeline also zooms around the pointer."
        )
        midi_time_view_row.addWidget(self.midi_time_zoom_slider, 1)
        self.midi_fit_time_button = QPushButton("Fit time")
        self.midi_fit_time_button.setToolTip("Show the complete MIDI timeline.")
        midi_time_view_row.addWidget(self.midi_fit_time_button)
        visual_layout.addLayout(midi_time_view_row)

        self.midi_timeline = MidiTimelineWidget()
        self.midi_timeline.setToolTip(
            "A piano-roll overview of the inspected source MIDI. Click to seek a playing MIDI preview.")
        self.midi_timeline.seekRequested.connect(self._seek_midi_to_tick)
        self.midi_timeline.viewChanged.connect(self.midi_pitch_range_label.setText)
        self.midi_pitch_zoom_slider.valueChanged.connect(self.midi_timeline.set_pitch_zoom)
        self.midi_fit_pitch_button.clicked.connect(self._fit_midi_pitch_range)
        self.midi_time_zoom_slider.valueChanged.connect(self.midi_timeline.set_time_zoom)
        self.midi_fit_time_button.clicked.connect(self._fit_midi_time_range)
        visual_layout.addWidget(self.midi_timeline, 1)

        self.midi_time_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.midi_time_scrollbar.setToolTip(
            "Pan the horizontal MIDI window. The handle represents the visible portion of the song."
        )
        self.midi_time_scrollbar.valueChanged.connect(self.midi_timeline.set_time_scroll)
        self.midi_timeline.viewChanged.connect(
            lambda _summary: self._sync_timeline_scrollbar(
                self.midi_timeline, self.midi_time_scrollbar
            )
        )
        visual_layout.addWidget(self.midi_time_scrollbar)

        seek_row = QHBoxLayout()
        self.midi_seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.midi_seek_slider.setRange(0, 0)
        self.midi_seek_slider.setToolTip("MIDI preview position in milliseconds.")
        self.midi_seek_slider.sliderMoved.connect(self._seek_midi_to_milliseconds)
        seek_row.addWidget(self.midi_seek_slider, 1)
        self.midi_duration_label = QLabel("0:00 / 0:00")
        self.midi_duration_label.setObjectName("eyebrow")
        self.midi_duration_label.setMinimumWidth(92)
        seek_row.addWidget(self.midi_duration_label)
        visual_layout.addLayout(seek_row)
        self.midi_preview_state = QLabel("Choose a MIDI source to prepare a local preview.")
        self.midi_preview_state.setObjectName("eyebrow")
        visual_layout.addWidget(self.midi_preview_state)

        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Rendered audio"))
        self.play_stem_button = QPushButton("Play stem")
        self.play_stem_button.setToolTip("Play the selected role's rendered WAV stem, when it exists.")
        self.play_stem_button.clicked.connect(self.play_selected_stem)
        self.play_mix_button = QPushButton("Play final mix")
        self.play_mix_button.setToolTip("Play the selected song's final rendered WAV mix, when it exists.")
        self.play_mix_button.clicked.connect(self.play_final_mix)
        self.stop_audio_button = QPushButton("Stop audio")
        self.stop_audio_button.setToolTip("Stop rendered audio playback.")
        self.stop_audio_button.clicked.connect(self.stop_audio)
        audio_row.addWidget(self.play_stem_button)
        audio_row.addWidget(self.play_mix_button)
        audio_row.addWidget(self.stop_audio_button)
        audio_row.addWidget(QLabel("Volume"))
        self.audio_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.audio_volume_slider.setRange(0, 100)
        self.audio_volume_slider.setValue(80)
        self.audio_volume_slider.setMaximumWidth(130)
        self.audio_volume_slider.setToolTip("Rendered-audio playback volume only; it does not change exported stem or mix files.")
        self.audio_volume_slider.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        audio_row.addWidget(self.audio_volume_slider)
        visual_layout.addLayout(audio_row)
        self.audio_state = QLabel("Rendered audio is stopped.")
        self.audio_state.setObjectName("eyebrow")
        visual_layout.addWidget(self.audio_state)

        layout.addWidget(visual_panel, 1)
        self._set_midi_actions_enabled(False)
        return workspace

    def _build_split_workflow(self) -> QWidget:
        """Build the non-destructive MIDI split preview and export surface."""

        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("Split MIDI track")
        heading.setObjectName("metricValue")
        layout.addWidget(heading)
        description = QLabel(
            "Target one track from the current song MIDI to preview tentative monophonic voice lanes. The source stays untouched until export."
        )
        description.setObjectName("eyebrow")
        description.setWordWrap(True)
        layout.addWidget(description)

        panel = QFrame()
        panel.setObjectName("detailPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Current song MIDI"))
        self.split_source_edit = QLineEdit()
        self.split_source_edit.setReadOnly(True)
        self.split_source_edit.setToolTip(
            "The MIDI source belonging to the currently selected song. It is only read during split analysis."
        )
        source_row.addWidget(self.split_source_edit, 1)
        self.split_source_reload_button = QPushButton("Current")
        self.split_source_reload_button.setToolTip(
            "Use the MIDI belonging to the currently selected song. The splitter is intentionally track-targeted."
        )
        self.split_source_reload_button.clicked.connect(self.load_split_source)
        source_row.addWidget(self.split_source_reload_button)
        self.split_load_button = QPushButton("Analyze")
        self.split_load_button.setToolTip(
            "Read the current working MIDI and prepare a dry-run split preview without writing output."
        )
        self.split_load_button.clicked.connect(self.load_split_source)
        source_row.addWidget(self.split_load_button)
        panel_layout.addLayout(source_row)

        track_row = QHBoxLayout()
        track_row.addWidget(QLabel("Source track"))
        self.split_track_combo = QComboBox()
        self.split_track_combo.setToolTip(
            "Choose the note-bearing track to split. The overlay shows the original notes under tentative voice lanes."
        )
        self.split_track_combo.currentIndexChanged.connect(self._split_track_changed)
        track_row.addWidget(self.split_track_combo, 1)
        self.split_summary_label = QLabel("Select a song with a MIDI source to analyze.")
        self.split_summary_label.setObjectName("eyebrow")
        track_row.addWidget(self.split_summary_label, 2)
        panel_layout.addLayout(track_row)

        pitch_row = QHBoxLayout()
        pitch_row.addWidget(QLabel("Pitch view"))
        self.split_pitch_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.split_pitch_zoom_slider.setRange(0, 100)
        self.split_pitch_zoom_slider.setToolTip(
            "Zoom the dry-run pitch view. Use the mouse wheel over the plot to pan when zoomed."
        )
        pitch_row.addWidget(self.split_pitch_zoom_slider, 1)
        self.split_fit_pitch_button = QPushButton("Fit pitches")
        self.split_fit_pitch_button.setToolTip("Show the complete selected track pitch range.")
        self.split_fit_pitch_button.clicked.connect(self._fit_split_pitch_range)
        pitch_row.addWidget(self.split_fit_pitch_button)
        self.split_pitch_range_label = QLabel("No MIDI notes")
        self.split_pitch_range_label.setObjectName("eyebrow")
        pitch_row.addWidget(self.split_pitch_range_label)
        panel_layout.addLayout(pitch_row)

        self.split_timeline = MidiTimelineWidget()
        self.split_timeline.setMinimumHeight(130)
        self.split_timeline.set_split_overlay(True, "Source plus tentative voice lanes")
        self.split_timeline.setToolTip(
            "The translucent source is underneath tentative monophonic lanes. This is a dry run; no MIDI is edited here."
        )
        self.split_timeline.viewChanged.connect(self.split_pitch_range_label.setText)
        self.split_pitch_zoom_slider.valueChanged.connect(self.split_timeline.set_pitch_zoom)
        panel_layout.addWidget(self.split_timeline, 1)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Export MIDI"))
        self.split_output_edit = QLineEdit()
        self.split_output_edit.setToolTip(
            "A distinct output MIDI path. The source MIDI is never overwritten."
        )
        output_row.addWidget(self.split_output_edit, 1)
        self.split_output_browse_button = self._tool_button(
            QStyle.StandardPixmap.SP_DialogSaveButton,
            "Choose where to save the verified split MIDI.",
            self.choose_split_output,
        )
        output_row.addWidget(self.split_output_browse_button)
        self.split_export_button = QPushButton("Export split")
        self.split_export_button.setToolTip(
            "Run the verified splitter and write the tentative lanes to the selected output path."
        )
        self.split_export_button.clicked.connect(self.export_split_midi)
        output_row.addWidget(self.split_export_button)
        panel_layout.addLayout(output_row)

        action_row = QHBoxLayout()
        self.split_state_label = QLabel("No split analysis loaded.")
        self.split_state_label.setObjectName("eyebrow")
        action_row.addWidget(self.split_state_label, 1)
        panel_layout.addLayout(action_row)

        layout.addWidget(panel, 1)
        self._set_split_actions_enabled(True)
        return workspace

    def _build_draft_workflow(self) -> QWidget:
        """Build the transcript and lyricless note-skeleton drafting surface."""

        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("Lyric drafter")
        heading.setObjectName("metricValue")
        layout.addWidget(heading)
        description = QLabel(
            "Draft from a transcript, or create a note-level placeholder skeleton for a track with no lyrics yet."
        )
        description.setWordWrap(True)
        description.setObjectName("eyebrow")
        layout.addWidget(description)

        panel = QFrame()
        panel.setObjectName("detailPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)

        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Role"))
        self.workflow_role_combo = QComboBox()
        self.workflow_role_combo.setToolTip(
            "The configured output role. Its TRACK_FILENAME drives MIDI alignment and its LYRICS_FILENAME identifies the lyric target."
        )
        self.workflow_role_combo.currentIndexChanged.connect(self._workflow_role_changed)
        role_row.addWidget(self.workflow_role_combo, 1)
        panel_layout.addLayout(role_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Input mode"))
        self.draft_mode_combo = QComboBox()
        self.draft_mode_combo.addItem("Transcript", "transcript")
        self.draft_mode_combo.addItem("Note skeleton", "placeholder")
        self.draft_mode_combo.setToolTip(
            "Transcript allocates known words. Note skeleton creates one direct-phoneme placeholder per MIDI note for later editing."
        )
        self.draft_mode_combo.currentIndexChanged.connect(self._draft_mode_changed)
        mode_row.addWidget(self.draft_mode_combo, 1)
        mode_row.addWidget(QLabel("Placeholder"))
        self.placeholder_edit = QLineEdit("daa")
        self.placeholder_edit.setMaximumWidth(180)
        self.placeholder_edit.setToolTip(
            "Direct phoneme used for a lyricless note skeleton. Commas and invalid characters are removed before drafting."
        )
        mode_row.addWidget(self.placeholder_edit)
        panel_layout.addLayout(mode_row)

        source_label = QLabel("Transcript file (optional)")
        source_label.setObjectName("metricLabel")
        panel_layout.addWidget(source_label)
        source_row = QHBoxLayout()
        self.draft_source_edit = QLineEdit()
        self.draft_source_edit.setToolTip(
            "Optional file source. Pasted transcript text below takes precedence when it contains lyric lines."
        )
        source_row.addWidget(self.draft_source_edit, 1)
        self.draft_browse_button = self._tool_button(
            QStyle.StandardPixmap.SP_DialogOpenButton,
            "Choose a transcript or raw lyric text file for drafting.",
            self.choose_draft_source,
        )
        source_row.addWidget(self.draft_browse_button)
        panel_layout.addLayout(source_row)

        transcript_label = QLabel("Transcript text")
        transcript_label.setObjectName("metricLabel")
        panel_layout.addWidget(transcript_label)
        self.draft_input_editor = QPlainTextEdit()
        self.draft_input_editor.setPlaceholderText(
            "Paste lyrics here, one line per phrase. Timestamped lines such as [00:40] Earth Angel are supported."
        )
        self.draft_input_editor.setToolTip(
            "Paste or edit the lyric transcript directly. Existing timestamps and optional durations are preserved; commas and unsupported punctuation are normalized before drafting."
        )
        self.draft_input_editor.setMaximumHeight(150)
        panel_layout.addWidget(self.draft_input_editor)

        self.auto_lines_check = VisibleCheckBox("Auto phrase lines")
        self.auto_lines_check.setToolTip(
            "Split the draft at detected MIDI rest phrases. A single untimestamped bulk paste is split automatically; enable this for any other untimestamped input you want grouped by MIDI phrases."
        )
        panel_layout.addWidget(self.auto_lines_check)

        draft_actions = QHBoxLayout()
        self.draft_button = QPushButton("Draft lyrics")
        self.draft_button.setToolTip(
            "Run the lyric drafter and load its safe output into the editor below."
        )
        self.draft_button.clicked.connect(self.draft_lyrics)
        self.reload_draft_button = QPushButton("Reload")
        self.reload_draft_button.setToolTip(
            "Discard unsaved draft edits and reload the safe draft from disk."
        )
        self.reload_draft_button.clicked.connect(self.reload_draft)
        self.save_draft_button = QPushButton("Save draft")
        self.save_draft_button.setToolTip(
            "Save the edited draft inside outputs/<Song>/lyrics_drafts/."
        )
        self.save_draft_button.clicked.connect(self.save_draft)
        self.apply_draft_button = QPushButton("Apply draft")
        self.apply_draft_button.setToolTip(
            "Write the edited draft to the configured lyric input only after a confirmation prompt."
        )
        self.apply_draft_button.clicked.connect(self.apply_draft)
        for button in (
            self.draft_button,
            self.reload_draft_button,
            self.save_draft_button,
            self.apply_draft_button,
        ):
            draft_actions.addWidget(button)
        draft_actions.addStretch(1)
        panel_layout.addLayout(draft_actions)

        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setPlaceholderText(
            "Generated draft appears here. Edit it directly, then save or align it."
        )
        self.draft_preview.setToolTip(
            "In-app lyric editor. The text uses the existing choir.py lyric syntax."
        )
        panel_layout.addWidget(self.draft_preview, 1)
        layout.addWidget(panel, 1)
        self._set_draft_actions_enabled(False)
        self._draft_mode_changed(self.draft_mode_combo.currentIndex())
        return workspace

    def _build_alignment_workflow(self) -> QWidget:
        """Build the in-app note assignment review and lyric editor."""

        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("Align lyrics to notes")
        heading.setObjectName("metricValue")
        layout.addWidget(heading)
        description = QLabel(
            "Review words and durations over the static MIDI notes. Select a word, then drag either edge or use the arrow keys to re-fit its neighboring lyric units."
        )
        description.setObjectName("eyebrow")
        description.setWordWrap(True)
        layout.addWidget(description)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("Role"))
        self.alignment_role_combo = QComboBox()
        self.alignment_role_combo.setToolTip(
            "Choose the configured role whose draft and MIDI source should be aligned."
        )
        self.alignment_role_combo.currentIndexChanged.connect(self._alignment_role_changed)
        control_row.addWidget(self.alignment_role_combo, 1)
        self.alignment_draft_state = QLabel("Draft follows the in-app editor on the Draft tab.")
        self.alignment_draft_state.setObjectName("eyebrow")
        control_row.addWidget(self.alignment_draft_state, 2)
        self.alignment_analyze_button = QPushButton("Analyze current draft")
        self.alignment_analyze_button.setToolTip(
            "Save the current draft editor and map its lyric units to the selected role's MIDI notes."
        )
        self.alignment_analyze_button.clicked.connect(self.analyze_alignment)
        control_row.addWidget(self.alignment_analyze_button)
        layout.addLayout(control_row)

        action_row = QHBoxLayout()
        self.alignment_reload_button = QPushButton("Reload aligned")
        self.alignment_reload_button.setToolTip(
            "Discard unsaved alignment edits and reload the latest aligned lyric file."
        )
        self.alignment_reload_button.clicked.connect(self.reload_alignment)
        self.alignment_save_button = QPushButton("Save aligned")
        self.alignment_save_button.setToolTip(
            "Save the edited renderer-ready lyric file under outputs/<Song>/lyrics_aligned/."
        )
        self.alignment_save_button.clicked.connect(self.save_alignment)
        self.alignment_apply_button = QPushButton("Apply aligned")
        self.alignment_apply_button.setToolTip(
            "Write the edited aligned lyric text to the configured song input after confirmation."
        )
        self.alignment_apply_button.clicked.connect(self.apply_alignment)
        for button in (
            self.alignment_reload_button,
            self.alignment_save_button,
            self.alignment_apply_button,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        self.alignment_summary_label = QLabel("No alignment analyzed yet.")
        self.alignment_summary_label.setObjectName("eyebrow")
        action_row.addWidget(self.alignment_summary_label)
        layout.addLayout(action_row)

        alignment_pitch_row = QHBoxLayout()
        alignment_pitch_row.addWidget(QLabel("Pitch view"))
        self.alignment_pitch_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.alignment_pitch_zoom_slider.setRange(0, 100)
        self.alignment_pitch_zoom_slider.setValue(0)
        self.alignment_pitch_zoom_slider.setToolTip(
            "Zoom the alignment view vertically. Use the mouse wheel over the timeline to pan when zoomed."
        )
        alignment_pitch_row.addWidget(self.alignment_pitch_zoom_slider, 1)
        self.alignment_fit_pitch_button = QPushButton("Fit pitches")
        self.alignment_fit_pitch_button.setToolTip(
            "Show the complete pitch range for the selected static MIDI track."
        )
        alignment_pitch_row.addWidget(self.alignment_fit_pitch_button)
        self.alignment_pitch_range_label = QLabel("No MIDI notes")
        self.alignment_pitch_range_label.setObjectName("eyebrow")
        alignment_pitch_row.addWidget(self.alignment_pitch_range_label)
        alignment_pitch_row.addWidget(QLabel("Time window"))
        self.alignment_time_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.alignment_time_zoom_slider.setRange(0, 100)
        self.alignment_time_zoom_slider.setValue(0)
        self.alignment_time_zoom_slider.setToolTip(
            "Zoom the horizontal lyric window. Ctrl+wheel zooms around the pointer; Shift+wheel and the scrollbar pan."
        )
        alignment_pitch_row.addWidget(self.alignment_time_zoom_slider, 1)
        self.alignment_fit_time_button = QPushButton("Fit time")
        self.alignment_fit_time_button.setToolTip("Show the complete MIDI timeline.")
        alignment_pitch_row.addWidget(self.alignment_fit_time_button)
        layout.addLayout(alignment_pitch_row)

        alignment_view_row = QHBoxLayout()
        self.alignment_timeline = MidiTimelineWidget()
        self.alignment_timeline.setMinimumHeight(300)
        self.alignment_timeline.setToolTip(
            "Static MIDI notes with lyric units overlaid above their note spans. Click a word to select it, then drag its edge to change duration. Green is confident, yellow needs review, and red is unassigned/error."
        )
        self.alignment_timeline.set_alignment_overlay(True)
        self.alignment_timeline.viewChanged.connect(self.alignment_pitch_range_label.setText)
        self.alignment_timeline.alignmentUnitSelected.connect(self._alignment_unit_clicked)
        self.alignment_timeline.alignmentBoundaryNudgeRequested.connect(
            self.nudge_alignment_boundary
        )
        self.alignment_pitch_zoom_slider.valueChanged.connect(
            self.alignment_timeline.set_pitch_zoom
        )
        self.alignment_fit_pitch_button.clicked.connect(self._fit_alignment_pitch_range)
        self.alignment_time_zoom_slider.valueChanged.connect(
            self.alignment_timeline.set_time_zoom
        )
        self.alignment_fit_time_button.clicked.connect(self._fit_alignment_time_range)
        alignment_view_row.addWidget(self.alignment_timeline, 1)
        legend = QLabel("Green: confident   Yellow: review   Red: error")
        legend.setObjectName("eyebrow")
        legend.setToolTip(
            "Alignment does not edit MIDI. Change note structure with the separate MIDI splitter workflow."
        )
        alignment_view_row.addWidget(legend)
        layout.addLayout(alignment_view_row)

        self.alignment_time_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.alignment_time_scrollbar.setToolTip(
            "Pan the horizontal lyric window. The handle represents the visible portion of the song."
        )
        self.alignment_time_scrollbar.valueChanged.connect(
            self.alignment_timeline.set_time_scroll
        )
        self.alignment_timeline.viewChanged.connect(
            lambda _summary: self._sync_timeline_scrollbar(
                self.alignment_timeline, self.alignment_time_scrollbar
            )
        )
        layout.addWidget(self.alignment_time_scrollbar)

        self.alignment_selection_label = QLabel(
            "Click a lyric block, then drag an edge or use Left/Right. Shift+Left/Right edits the ending edge."
        )
        self.alignment_selection_label.setObjectName("eyebrow")
        layout.addWidget(self.alignment_selection_label)

        insert_row = QHBoxLayout()
        insert_row.addWidget(QLabel("Insert lyric"))
        self.alignment_insert_edit = QLineEdit()
        self.alignment_insert_edit.setPlaceholderText("word or direct phoneme")
        self.alignment_insert_edit.setToolTip(
            "Enter one lyric word or direct phoneme unit. It will consume an unassigned note or compact the nearest spare allocation."
        )
        insert_row.addWidget(self.alignment_insert_edit, 1)
        self.alignment_insert_before_button = QPushButton("Before")
        self.alignment_insert_before_button.setToolTip(
            "Insert the typed lyric immediately before the selected word and re-fit the later lyric units."
        )
        self.alignment_insert_before_button.clicked.connect(
            lambda: self.insert_alignment_word("before")
        )
        insert_row.addWidget(self.alignment_insert_before_button)
        self.alignment_insert_after_button = QPushButton("After")
        self.alignment_insert_after_button.setToolTip(
            "Insert the typed lyric immediately after the selected word and re-fit the later lyric units."
        )
        self.alignment_insert_after_button.clicked.connect(
            lambda: self.insert_alignment_word("after")
        )
        insert_row.addWidget(self.alignment_insert_after_button)
        layout.addLayout(insert_row)

        text_row = QHBoxLayout()
        self.alignment_text_toggle = QToolButton()
        self.alignment_text_toggle.setText("Advanced lyric text")
        self.alignment_text_toggle.setCheckable(True)
        self.alignment_text_toggle.setToolTip(
            "Open the raw renderer-ready lyric buffer. Most timing changes should be made by clicking the visual lyric blocks above."
        )
        text_row.addWidget(self.alignment_text_toggle)
        text_row.addStretch(1)
        layout.addLayout(text_row)

        self.alignment_editor_panel = QFrame()
        self.alignment_editor_panel.setObjectName("detailPanel")
        editor_layout = QVBoxLayout(self.alignment_editor_panel)
        editor_layout.setContentsMargins(8, 8, 8, 8)
        editor_layout.setSpacing(6)
        editor_label = QLabel("Advanced renderer-ready lyric text")
        editor_label.setObjectName("sectionTitle")
        editor_layout.addWidget(editor_label)
        self.alignment_editor = QPlainTextEdit()
        self.alignment_editor.setPlaceholderText(
            "Analyze a draft to load the renderer-ready lyric text. Visual boundary controls are preferred for timing edits."
        )
        self.alignment_editor.setToolTip(
            "In-app editor for the final lyric syntax consumed by choir.py."
        )
        editor_layout.addWidget(self.alignment_editor, 1)
        self.alignment_editor_panel.setVisible(False)
        self.alignment_text_toggle.toggled.connect(self.alignment_editor_panel.setVisible)
        layout.addWidget(self.alignment_editor_panel, 1)
        self._set_alignment_actions_enabled(False)
        return workspace

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QGridLayout(bar)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)

        title = QLabel("DECTALK Choir")
        title.setObjectName("title")
        layout.addWidget(title, 0, 0)

        song_label = QLabel("Song")
        song_label.setObjectName("fieldLabel")
        layout.addWidget(song_label, 0, 1)
        self.song_combo = QComboBox()
        self.song_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.song_combo.currentTextChanged.connect(self.inspect_current_song)
        layout.addWidget(self.song_combo, 0, 2, 1, 2)
        song_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "Choose an existing song folder under this project's songs directory",
            self.choose_song_folder,
        )
        layout.addWidget(song_button, 0, 4)

        self.render_options_button = QToolButton()
        self.render_options_button.setText("Options")
        self.render_options_button.setToolTip("Optional visual outputs for the next render.")
        self.render_options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        options_menu = QMenu(self.render_options_button)
        self.visuals_action = QAction("Generate spectrogram", self.render_options_button)
        self.visuals_action.setCheckable(True)
        self.visuals_action.setToolTip(
            "Run the spectrogram generator after audio rendering. This adds render time but does not change the WAV mix."
        )
        self.plots_action = QAction("Generate phoneme plots", self.render_options_button)
        self.plots_action.setCheckable(True)
        self.plots_action.setToolTip(
            "Write per-role diagrams of emitted phonemes, pitches, and durations. This does not change audio."
        )
        options_menu.addAction(self.visuals_action)
        options_menu.addAction(self.plots_action)
        options_menu.addSeparator()
        scale_menu = options_menu.addMenu("UI scale")
        scale_group = QActionGroup(scale_menu)
        scale_group.setExclusive(True)
        self.scale_actions: list[QAction] = []
        for percent in (90, 100, 110, 120, 135, 150):
            scale_action = QAction(f"{percent}%", scale_menu)
            scale_action.setCheckable(True)
            scale_action.setData(percent / 100)
            scale_action.setToolTip(
                f"Use {percent}% visual scale for typography, controls, and table density."
            )
            scale_group.addAction(scale_action)
            scale_menu.addAction(scale_action)
            self.scale_actions.append(scale_action)
            scale_action.triggered.connect(
                lambda checked=False, scale=percent / 100: self._change_ui_scale(scale)
            )
        self.render_options_button.setMenu(options_menu)
        layout.addWidget(self.render_options_button, 0, 5)

        self.render_button = QPushButton("Render")
        self.render_button.setObjectName("renderButton")
        self.render_button.setToolTip(
            "Run the selected song through choir.py. Empty or invalid lyric roles are skipped; the Render log tab receives direct compiler output."
        )
        self.render_button.clicked.connect(self.render_song)
        layout.addWidget(self.render_button, 0, 6)

        self.repo_edit = QLineEdit(str(self.repo_root))
        self.repo_edit.setReadOnly(True)
        self.repo_edit.setToolTip("Choir project root containing choir.py, songs, and outputs. Use the folder button to change it.")
        layout.addWidget(self.repo_edit, 1, 0, 1, 4)
        project_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "Choose Choir project folder",
            self.choose_project,
        )
        layout.addWidget(project_button, 1, 4)

        self.refresh_button = self._tool_button(
            QStyle.StandardPixmap.SP_BrowserReload,
            "Refresh songs, settings, MIDI inspection, output files, and loudness measurements.",
            self._load_song_choices,
        )
        layout.addWidget(self.refresh_button, 1, 6)
        layout.setColumnStretch(2, 1)
        return bar

    def _build_overview(self) -> QWidget:
        overview = QWidget()
        overview.setObjectName("overview")
        layout = QHBoxLayout(overview)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(22)
        self.overview_values: dict[str, QLabel] = {}
        metrics = (
            ("MIDI", "midi"),
            ("Duration", "duration"),
            ("Roles", "roles"),
            ("Ready", "ready"),
            ("Mix", "mix"),
        )
        for label_text, key in metrics:
            metric = QWidget()
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(0, 0, 0, 0)
            metric_layout.setSpacing(1)
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            value = QLabel("--")
            value.setObjectName("fieldValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            metric_layout.addWidget(label)
            metric_layout.addWidget(value)
            layout.addWidget(metric)
            self.overview_values[key] = value
        layout.addStretch(1)
        return overview

    def _build_role_table(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading_row = QHBoxLayout()
        heading = QLabel("Roles")
        heading.setObjectName("sectionTitle")
        self.role_summary_label = QLabel("--")
        self.role_summary_label.setObjectName("fieldLabel")
        heading_row.addWidget(heading)
        heading_row.addWidget(self.role_summary_label)
        heading_row.addStretch(1)
        layout.addLayout(heading_row)

        self.role_table = QTableWidget(0, 7)
        self.role_table.setHorizontalHeaderLabels(
            [
                "Role",
                "Inputs",
                "Notes / MIDI",
                "Final pitch",
                "Overlap",
                "Loudness",
                "State",
            ]
        )
        self.role_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.role_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.role_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.role_table.setAlternatingRowColors(False)
        self.role_table.setWordWrap(False)
        self.role_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.role_table.verticalHeader().setVisible(False)
        header = self.role_table.horizontalHeader()
        for column in (0, 2, 3, 4, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        column_help = {
            0: "The output role name: the key under Tracks in settings.yaml. It names the stem and output folders.",
            1: "Configured MIDI source and lyric input. Full paths appear in the Selection inspector.",
            2: "Playable note count followed by the raw scientific-pitch range in the source MIDI.",
            3: "Final audible DECTALK pitch range after mapping and OCTAVE_BOOST resampling.",
            4: "Maximum simultaneous MIDI notes and longest overlap. Exact duplicates are ignored; short two-note handoffs are accepted.",
            5: "Existing stem loudness: active-window median followed by peak dBFS. Full loudness statistics appear in the Selection inspector.",
            6: "Ready means MIDI exists and the lyric input contains convertible content. Polyphonic source is a warning; missing or invalid lyrics are skipped.",
        }
        for column, tooltip in column_help.items():
            self.role_table.horizontalHeaderItem(column).setToolTip(tooltip)
        self.role_table.itemSelectionChanged.connect(self._select_role)
        layout.addWidget(self.role_table, 1)
        return container

    def _build_side_panel(self) -> QWidget:
        side = QTabWidget()
        side.setMinimumWidth(340)
        side.setMaximumWidth(520)
        self.side_tabs = side

        detail = QWidget()
        detail.setObjectName("inspectorPanel")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(10)
        self.detail_title = QLabel("Selected Role")
        self.detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(self.detail_title)
        self.detail_fields: dict[str, QLabel] = {}
        for label_text, key in (
            ("Source", "source"),
            ("Pitch", "pitch"),
            ("Audio", "audio"),
            ("Notes", "details"),
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            value = QLabel("--")
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setObjectName("fieldValue")
            self.detail_fields[key] = value
            detail_layout.addWidget(label)
            detail_layout.addWidget(value)

        detail_actions = QHBoxLayout()
        detail_actions.addWidget(QLabel("Open"))
        self.open_lyrics_button = self._tool_button(
            QStyle.StandardPixmap.SP_FileIcon,
            "Open the selected role's lyric input in the system editor.",
            lambda: self._open_role_path("lyrics"),
        )
        self.open_lyrics_button.setText("Lyrics")
        self.open_midi_button = self._tool_button(
            QStyle.StandardPixmap.SP_MediaPlay,
            "Open the source MIDI file in the system MIDI editor/player.",
            lambda: self._open_role_path("midi"),
        )
        self.open_midi_button.setText("MIDI")
        self.open_stem_button = self._tool_button(
            QStyle.StandardPixmap.SP_MediaVolume,
            "Open the selected role's rendered WAV stem, or its output folder when not rendered.",
            lambda: self._open_role_path("stem"),
        )
        self.open_stem_button.setText("Stem")
        for button in (self.open_lyrics_button, self.open_midi_button, self.open_stem_button):
            detail_actions.addWidget(button)
        detail_actions.addStretch(1)
        detail_layout.addLayout(detail_actions)

        output_actions = QHBoxLayout()
        output_actions.addWidget(QLabel("Folders"))
        self.open_song_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirIcon,
            "Open the selected song source folder.",
            lambda: self._open_path(self.repo_root / "songs" / self.song_combo.currentText()),
        )
        self.open_song_button.setText("Song")
        self.open_output_button = self._tool_button(
            QStyle.StandardPixmap.SP_DirOpenIcon,
            "Open the selected song's generated output folder.",
            lambda: self._open_output("folder"),
        )
        self.open_output_button.setText("Outputs")
        self.open_mix_button = self._tool_button(
            QStyle.StandardPixmap.SP_MediaPlay,
            "Open the final mix WAV, or its folder when the mix has not been rendered.",
            lambda: self._open_output("mix"),
        )
        self.open_mix_button.setText("Mix")
        for button in (self.open_song_button, self.open_output_button, self.open_mix_button):
            output_actions.addWidget(button)
        output_actions.addStretch(1)
        detail_layout.addLayout(output_actions)
        detail_layout.addStretch(1)

        log_frame = QWidget()
        log_frame.setObjectName("logPanel")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_heading = QHBoxLayout()
        log_label = QLabel("Renderer output")
        log_label.setObjectName("sectionTitle")
        clear_log = QToolButton()
        clear_log.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        clear_log.setToolTip("Clear renderer output")
        clear_log.clicked.connect(lambda: self.log.clear())
        log_heading.addWidget(log_label)
        log_heading.addStretch(1)
        log_heading.addWidget(clear_log)
        log_layout.addLayout(log_heading)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(8000)
        log_layout.addWidget(self.log, 1)

        side.addTab(detail, "Selection")
        side.addTab(log_frame, "Render log")
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

    def _change_ui_scale(self, scale: float) -> None:
        self._apply_ui_scale(float(scale))
        self.settings.setValue("ui_scale", self.ui_scale)

    def _apply_ui_scale(self, scale: float) -> None:
        self.ui_scale = max(0.85, min(1.6, scale))
        self.setStyleSheet(style_for_scale(self.ui_scale))
        for action in self.scale_actions:
            action.setChecked(abs(float(action.data()) - self.ui_scale) < 0.001)
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
        if self.inspect_task:
            self.inspect_task.cancel()
        task = InspectTask(token, self.repo_root, song_name)
        self.inspect_task = task
        task.signals.completed.connect(self._inspection_completed)
        task.signals.failed.connect(self._inspection_failed)
        self.thread_pool.start(task)

    def _inspection_completed(self, token: int, inspection: SongInspection) -> None:
        if token != self.inspect_token:
            return
        self.inspect_task = None
        self.inspection = inspection
        self._populate_inspection(inspection)

    def _inspection_failed(self, token: int, error: str) -> None:
        if token != self.inspect_token:
            return
        self.inspect_task = None
        self.inspection = None
        self.status_label.setText(f"Inspection failed: {error}")
        self.log.appendPlainText(f"Inspection failed: {error}")

    def _populate_inspection(self, inspection: SongInspection) -> None:
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
            self._compact_loudness(inspection.final_loudness)
        )
        self.role_summary_label.setText(f"{ready} ready of {len(inspection.roles)}")

        self.role_table.setRowCount(len(inspection.roles))
        for row, role in enumerate(inspection.roles):
            input_summary = f"{role.midi_source_name}  |  {role.lyric_stem}.txt"
            values = (
                role.role,
                input_summary,
                f"{role.note_count}  |  {role.midi_range}" if role.midi_track else "--",
                role.audible_range,
                self._polyphony_display(role),
                self._compact_loudness(role.loudness),
                role.status,
            )
            tooltips = (
                role.role,
                f"MIDI track: {role.midi_source_name}\n"
                f"Lyrics: {role.lyric_path}\n"
                f"Stem: {role.stem_path}",
                f"Notes: {role.note_count}\nMIDI range: {role.midi_range}",
                f"Render pitch: {role.render_range}\nAudible pitch: {role.audible_range}",
                self._overlap_tooltip(role),
                role.loudness.display if role.loudness else "No rendered stem found.",
                "\n".join(role.details) if role.details else role.status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltips[column])
                if column == 6:
                    item.setForeground(self._status_color(role.status))
                self.role_table.setItem(row, column, item)
        self.role_table.resizeRowsToContents()
        self.current_role = None
        self._set_detail_actions_enabled(False)
        self._populate_workflow(inspection)
        if inspection.roles:
            self.role_table.selectRow(0)

        lyric_role_issues = []
        visible_role_statuses = {
            "Missing MIDI source",
            "Ambiguous MIDI source",
            "Missing lyric input",
            "Missing lyric content",
            "Invalid lyric content",
        }
        for role in inspection.roles:
            if role.status not in visible_role_statuses:
                continue
            detail = role.details[0] if role.details else role.status
            lyric_role_issues.append(f"{role.role}: {role.status} ({detail})")

        messages = [*inspection.errors, *inspection.warnings, *lyric_role_issues]
        if messages:
            self.status_label.setText(" | ".join(messages[:2]))
            self.status_label.setToolTip("\n".join(messages))
            self.log.appendPlainText("Inspection notes:\n" + "\n".join(f"- {item}" for item in messages))
        else:
            self.status_label.setToolTip("")
            self.status_label.setText(
                f"{inspection.song_name} inspected: {len(inspection.roles)} role(s), "
                f"{ready} ready to render."
            )

    def _populate_workflow(self, inspection: SongInspection) -> None:
        self.midi_source_combo.blockSignals(True)
        self.midi_source_combo.clear()
        self.midi_source_combo.addItem("All note tracks", None)
        if inspection.midi:
            for track in inspection.midi.tracks:
                if track.notes:
                    self.midi_source_combo.addItem(
                        f"{track.name} ({track.note_count} notes)", track.index
                    )
        self.midi_source_combo.blockSignals(False)

        self.workflow_role_combo.blockSignals(True)
        self.workflow_role_combo.clear()
        for role_index, role in enumerate(inspection.roles):
            self.workflow_role_combo.addItem(role.role, role_index)
        self.workflow_role_combo.blockSignals(False)
        self.alignment_role_combo.blockSignals(True)
        self.alignment_role_combo.clear()
        for role_index, role in enumerate(inspection.roles):
            self.alignment_role_combo.addItem(role.role, role_index)
        self.alignment_role_combo.blockSignals(False)
        self.latest_draft_path = None
        self.latest_transcript_path = None
        self.latest_alignment_path = None
        self.latest_alignment_report_path = None
        self.alignment_report = None
        self.selected_alignment_key = None
        self.draft_preview.clear()
        self.draft_input_editor.clear()
        self.alignment_editor.clear()
        self.alignment_insert_edit.clear()
        self.alignment_selection_label.setText(
            "Click a lyric block, then drag an edge or use Left/Right. Shift+Left/Right edits the ending edge."
        )
        self.alignment_text_toggle.setChecked(False)
        self._set_workflow_actions_enabled(bool(inspection.roles and inspection.midi))
        self.load_split_source()
        self._workflow_role_changed(self.workflow_role_combo.currentIndex())

    def _workflow_role(self) -> RoleInspection | None:
        if not self.inspection:
            return None
        role_index = self.workflow_role_combo.currentData()
        if role_index is None:
            return None
        role_index = int(role_index)
        if role_index < 0 or role_index >= len(self.inspection.roles):
            return None
        return self.inspection.roles[role_index]

    def _workflow_role_changed(self, combo_index: int) -> None:
        if (
            hasattr(self, "draft_preview")
            and self.latest_draft_path
            and self.draft_preview.document().isModified()
        ):
            self._write_editor_text(self.draft_preview, self.latest_draft_path, "draft")
        if (
            hasattr(self, "draft_input_editor")
            and self.latest_transcript_path
            and self.draft_input_editor.document().isModified()
            and self.draft_input_editor.toPlainText().strip()
        ):
            self._write_editor_text(
                self.draft_input_editor,
                self.latest_transcript_path,
                "transcript input",
            )
        if (
            hasattr(self, "alignment_editor")
            and self.latest_alignment_path
            and self.alignment_editor.document().isModified()
        ):
            self._write_editor_text(
                self.alignment_editor,
                self.latest_alignment_path,
                "aligned lyrics",
            )
        role = self._workflow_role()
        if not role:
            return
        raw_source = role.lyric_path.with_name(f"{role.lyric_stem}.raw.txt")
        source_path = raw_source if raw_source.is_file() else role.lyric_path
        self.latest_draft_path = (
            self.inspection.output_dir / "lyrics_drafts" / f"{role.role}.txt"
            if self.inspection
            else None
        )
        self.latest_transcript_path = (
            self.inspection.output_dir / "lyrics_drafts" / f"{role.role}.transcript.txt"
            if self.inspection
            else None
        )
        transcript_source = (
            self.latest_transcript_path
            if self.latest_transcript_path and self.latest_transcript_path.is_file()
            else source_path
        )
        self.draft_source_edit.setText(str(transcript_source))
        try:
            self.draft_input_editor.setPlainText(
                transcript_source.read_text(encoding="utf-8")
                if transcript_source.is_file()
                else ""
            )
            self.draft_input_editor.document().setModified(False)
        except OSError as error:
            self.draft_input_editor.setPlainText(f"Could not read transcript: {error}")
        self.latest_alignment_path = (
            self.inspection.output_dir / "lyrics_aligned" / f"{role.role}.txt"
            if self.inspection
            else None
        )
        self.latest_alignment_report_path = (
            self.inspection.output_dir / "lyrics_aligned" / f"{role.role}.json"
            if self.inspection
            else None
        )
        alignment_index = self.alignment_role_combo.findData(self.workflow_role_combo.currentData())
        if alignment_index >= 0 and alignment_index != self.alignment_role_combo.currentIndex():
            with QSignalBlocker(self.alignment_role_combo):
                self.alignment_role_combo.setCurrentIndex(alignment_index)
        self.alignment_draft_state.setText(
            f"Draft: {self._short_path(self.latest_draft_path)}"
            if self.latest_draft_path
            else "Draft follows the in-app editor on the Draft tab."
        )
        if not self._has_lyric_content(role.lyric_path):
            placeholder_index = self.draft_mode_combo.findData("placeholder")
            if placeholder_index >= 0:
                with QSignalBlocker(self.draft_mode_combo):
                    self.draft_mode_combo.setCurrentIndex(placeholder_index)
                self._draft_mode_changed(placeholder_index)
        self._load_draft_preview()
        self._load_alignment_preview()
        if role.midi_track:
            source_index = self.midi_source_combo.findData(role.midi_track.index)
            if source_index >= 0:
                with QSignalBlocker(self.midi_source_combo):
                    self.midi_source_combo.setCurrentIndex(source_index)
                self._midi_source_changed(source_index)
            if hasattr(self, "alignment_timeline") and self.inspection.midi:
                self.alignment_timeline.set_tracks(
                    self.inspection.midi.tracks,
                    role.midi_track.index,
                )
                self.alignment_timeline.set_duration_ms(
                    round(self.inspection.midi.duration_seconds * 1000)
                )
                with QSignalBlocker(self.alignment_pitch_zoom_slider):
                    self.alignment_pitch_zoom_slider.setValue(0)
                with QSignalBlocker(self.alignment_time_zoom_slider):
                    self.alignment_time_zoom_slider.setValue(0)
        elif hasattr(self, "alignment_timeline"):
            self.alignment_timeline.set_tracks(())
            self.alignment_timeline.set_duration_ms(0)

    def _sync_workflow_role(self, role_index: int) -> None:
        combo_index = self.workflow_role_combo.findData(role_index)
        if combo_index >= 0 and combo_index != self.workflow_role_combo.currentIndex():
            self.workflow_role_combo.setCurrentIndex(combo_index)

    def _alignment_role(self) -> RoleInspection | None:
        if not self.inspection:
            return None
        role_index = self.alignment_role_combo.currentData()
        if role_index is None:
            return None
        role_index = int(role_index)
        if role_index < 0 or role_index >= len(self.inspection.roles):
            return None
        return self.inspection.roles[role_index]

    def _alignment_role_changed(self, combo_index: int) -> None:
        role_index = self.alignment_role_combo.itemData(combo_index)
        if role_index is None:
            return
        self._sync_workflow_role(int(role_index))
        self._load_alignment_preview()

    def _midi_source_changed(self, combo_index: int) -> None:
        if not self.inspection or not self.inspection.midi:
            self.midi_timeline.set_tracks(())
            self.midi_timeline.set_duration_ms(0)
            self.midi_preview_duration_ms = 0
            self._set_midi_duration_label(0)
            return
        source_index = self.midi_source_combo.itemData(combo_index)
        focus_index = int(source_index) if source_index is not None else None
        self.midi_timeline.set_tracks(self.inspection.midi.tracks, focus_index)
        self.midi_timeline.set_duration_ms(
            round(self.inspection.midi.duration_seconds * 1000)
        )
        self.midi_preview_duration_ms = round(self.inspection.midi.duration_seconds * 1000)
        with QSignalBlocker(self.midi_pitch_zoom_slider):
            self.midi_pitch_zoom_slider.setValue(0)
        with QSignalBlocker(self.midi_time_zoom_slider):
            self.midi_time_zoom_slider.setValue(0)
        self._set_midi_duration_label(0)
        self.midi_preview_state.setText(
            "Choose Play MIDI to hear the selected source track through the Windows MIDI device."
            if focus_index is not None
            else "Choose one MIDI source track to prepare a preview."
        )

    def _fit_midi_pitch_range(self) -> None:
        self.midi_timeline.fit_pitch_range()
        with QSignalBlocker(self.midi_pitch_zoom_slider):
            self.midi_pitch_zoom_slider.setValue(0)

    def _fit_midi_time_range(self) -> None:
        self.midi_timeline.fit_time_range()
        with QSignalBlocker(self.midi_time_zoom_slider):
            self.midi_time_zoom_slider.setValue(0)

    def _fit_alignment_time_range(self) -> None:
        self.alignment_timeline.fit_time_range()
        with QSignalBlocker(self.alignment_time_zoom_slider):
            self.alignment_time_zoom_slider.setValue(0)

    @staticmethod
    def _sync_timeline_scrollbar(
        timeline: MidiTimelineWidget,
        scrollbar: QScrollBar,
    ) -> None:
        value, maximum, page_step = timeline.time_scroll_state()
        with QSignalBlocker(scrollbar):
            scrollbar.setRange(0, maximum)
            scrollbar.setPageStep(page_step)
            scrollbar.setSingleStep(max(1, page_step // 8))
            scrollbar.setValue(value)

    def _set_midi_duration_label(self, position_ms: int) -> None:
        if not hasattr(self, "midi_duration_label"):
            return
        self.midi_duration_label.setText(
            f"{human_milliseconds(position_ms)} / "
            f"{human_milliseconds(self.midi_preview_duration_ms)}"
        )

    def _fit_alignment_pitch_range(self) -> None:
        self.alignment_timeline.fit_pitch_range()
        with QSignalBlocker(self.alignment_pitch_zoom_slider):
            self.alignment_pitch_zoom_slider.setValue(0)

    def _draft_mode_changed(self, combo_index: int) -> None:
        if not hasattr(self, "draft_mode_combo"):
            return
        placeholder_mode = self.draft_mode_combo.itemData(combo_index) == "placeholder"
        self.draft_source_edit.setEnabled(not placeholder_mode)
        self.draft_browse_button.setEnabled(not placeholder_mode)
        self.draft_input_editor.setEnabled(not placeholder_mode)
        self.auto_lines_check.setEnabled(not placeholder_mode)
        self.placeholder_edit.setEnabled(placeholder_mode)

    def _set_workflow_actions_enabled(self, enabled: bool) -> None:
        self._set_midi_actions_enabled(enabled)
        self._set_draft_actions_enabled(enabled)
        self._set_alignment_actions_enabled(enabled)

    def _set_split_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.split_source_edit,
            self.split_source_reload_button,
            self.split_load_button,
            self.split_track_combo,
            self.split_pitch_zoom_slider,
            self.split_fit_pitch_button,
            self.split_output_edit,
            self.split_output_browse_button,
            self.split_export_button,
        ):
            widget.setEnabled(enabled)

    def _set_midi_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.midi_source_combo,
            self.play_midi_button,
            self.pause_midi_button,
            self.stop_midi_button,
            self.midi_import_button,
            self.midi_seek_slider,
            self.midi_pitch_zoom_slider,
            self.midi_fit_pitch_button,
            self.midi_time_zoom_slider,
            self.midi_fit_time_button,
            self.midi_time_scrollbar,
            self.midi_timeline,
            self.play_stem_button,
            self.play_mix_button,
            self.stop_audio_button,
            self.audio_volume_slider,
        ):
            widget.setEnabled(enabled)

    def _set_draft_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.workflow_role_combo,
            self.draft_mode_combo,
            self.draft_source_edit,
            self.draft_browse_button,
            self.draft_input_editor,
            self.auto_lines_check,
            self.placeholder_edit,
            self.draft_button,
            self.reload_draft_button,
            self.save_draft_button,
            self.apply_draft_button,
            self.draft_preview,
        ):
            widget.setEnabled(enabled)
        self._draft_mode_changed(self.draft_mode_combo.currentIndex())

    def _set_alignment_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.alignment_role_combo,
            self.alignment_analyze_button,
            self.alignment_reload_button,
            self.alignment_save_button,
            self.alignment_apply_button,
            self.alignment_pitch_zoom_slider,
            self.alignment_fit_pitch_button,
            self.alignment_time_zoom_slider,
            self.alignment_fit_time_button,
            self.alignment_time_scrollbar,
            self.alignment_insert_edit,
            self.alignment_insert_before_button,
            self.alignment_insert_after_button,
            self.alignment_text_toggle,
            self.alignment_timeline,
            self.alignment_editor,
        ):
            widget.setEnabled(enabled)

    def _load_draft_preview(self) -> None:
        if self.latest_draft_path and self.latest_draft_path.is_file():
            try:
                self.draft_preview.setPlainText(
                    self.latest_draft_path.read_text(encoding="utf-8")
                )
                self.draft_preview.document().setModified(False)
            except OSError as error:
                self.draft_preview.setPlainText(f"Could not read draft: {error}")
        else:
            self.draft_preview.clear()
            self.draft_preview.document().setModified(False)

    def _write_editor_text(self, editor: QPlainTextEdit, path: Path, label: str) -> bool:
        text = editor.toPlainText().rstrip()
        if not text:
            QMessageBox.warning(self, f"Empty {label}", f"Enter lyric text before saving {label.lower()}.")
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, f"Could not save {label}", str(error))
            return False
        editor.document().setModified(False)
        return True

    def _editor_conversion_issue(self, editor: QPlainTextEdit) -> str | None:
        text = editor.toPlainText().rstrip()
        if not text:
            return "aligned lyric text is empty"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                encoding="utf-8",
            ) as temporary:
                temporary.write(text + "\n")
                temporary_path = Path(temporary.name)
            return _lyric_conversion_issue(temporary_path)
        except OSError as error:
            return f"could not validate aligned lyric text: {error}"
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    def _alignment_conversion_issue(self) -> str | None:
        return self._editor_conversion_issue(self.alignment_editor)

    def _validate_alignment_editor(self) -> bool:
        issue = self._alignment_conversion_issue()
        if not issue:
            return True
        QMessageBox.warning(
            self,
            "Invalid aligned lyrics",
            f"The aligned lyric buffer was not applied:\n{issue}\n\n"
            "Fix the word or phoneme in the visual editor, then try again.",
        )
        self.status_label.setText(f"Aligned lyrics blocked: {issue}")
        return False

    def reload_draft(self) -> None:
        self._load_draft_preview()
        self.status_label.setText("Draft editor reloaded from its safe output file.")

    def save_draft(self) -> None:
        if self.latest_draft_path and self._write_editor_text(
            self.draft_preview,
            self.latest_draft_path,
            "draft",
        ):
            self.status_label.setText(f"Draft saved: {self._short_path(self.latest_draft_path)}")

    def _load_alignment_preview(self) -> None:
        self.alignment_report = None
        self.selected_alignment_key = None
        self.alignment_summary_label.setText(
            "Click a lyric block, then drag an edge or use Left/Right."
        )
        self.alignment_selection_label.setText(
            "Click a lyric block, then drag an edge or use Left/Right. Shift+Left/Right edits the ending edge."
        )
        if hasattr(self, "alignment_timeline"):
            self.alignment_timeline.set_alignment_annotations(None)
            self.alignment_timeline.set_alignment_selection(None, None)
        if self.latest_alignment_path and self.latest_alignment_path.is_file():
            try:
                self.alignment_editor.setPlainText(
                    self.latest_alignment_path.read_text(encoding="utf-8")
                )
                self.alignment_editor.document().setModified(False)
            except OSError as error:
                self.alignment_editor.setPlainText(f"Could not read aligned lyrics: {error}")
        else:
            self.alignment_editor.clear()
            self.alignment_editor.document().setModified(False)

        if not self.latest_alignment_report_path or not self.latest_alignment_report_path.is_file():
            return
        try:
            report = json.loads(self.latest_alignment_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.alignment_selection_label.setText(f"Could not read alignment report: {error}")
            return
        self.alignment_report = report
        summary = report.get("summary", {})
        self.alignment_summary_label.setText(
            f"{summary.get('status', 'Unknown')}. Click a lyric block, then drag an edge or use Left/Right."
        )
        if hasattr(self, "alignment_timeline"):
            self.alignment_timeline.set_alignment_annotations(report.get("notes", []))

    def _alignment_note(self, key: tuple[int, int] | None) -> dict | None:
        if not key or not self.alignment_report:
            return None
        line, word_index = key
        return next(
            (
                note
                for note in self.alignment_report.get("notes", [])
                if note.get("line") == line and note.get("word_index") == word_index
            ),
            None,
        )

    def _select_alignment_unit(self, line: int, word_index: int) -> None:
        note = self._alignment_note((line, word_index))
        if not note:
            self.alignment_selection_label.setText("That lyric block is not assigned to a MIDI note.")
            return
        self.selected_alignment_key = (line, word_index)
        self.alignment_timeline.set_alignment_selection(line, word_index)
        self.alignment_selection_label.setText(
            f"Selected '{note.get('lyric') or '--'}'. Drag either edge to change its duration; arrow keys nudge the boundary."
        )

    def _alignment_unit_clicked(self, line: int, word_index: int) -> None:
        self._select_alignment_unit(line, word_index)

    def nudge_alignment_boundary(
        self,
        line: int,
        word_index: int,
        edge: str,
        movement: int,
    ) -> bool:
        """Apply one snapped boundary nudge from the visual timeline."""

        if not self.alignment_report or not self.selected_alignment_key:
            self.alignment_selection_label.setText("Select a lyric block above its notes first.")
            return False
        self.selected_alignment_key = (line, word_index)
        selected = self._alignment_note(self.selected_alignment_key)
        if not selected:
            self.alignment_selection_label.setText("Select an assigned lyric block, not an unassigned note.")
            return False
        try:
            report, aligned_text = resize_alignment_token(
                self.alignment_report,
                self.alignment_editor.toPlainText(),
                line,
                word_index,
                edge,
                movement,
            )
        except (ValueError, KeyError, TypeError) as error:
            self.alignment_selection_label.setText(str(error))
            return False
        self.alignment_report = report
        self.alignment_editor.setPlainText(aligned_text)
        self.alignment_editor.document().setModified(True)
        self.alignment_timeline.set_alignment_annotations(report.get("notes", []))
        self._select_alignment_unit(line, word_index)
        edge_name = "start" if edge == "start" else "end"
        movement_name = "earlier" if movement < 0 else "later"
        self.alignment_selection_label.setText(
            f"Moved the {edge_name} of '{selected.get('lyric') or '--'}' one note {movement_name}; later lyric units re-fit automatically."
        )
        return True

    def insert_alignment_word(self, position: str) -> bool:
        if not self.alignment_report or not self.selected_alignment_key:
            self.alignment_selection_label.setText("Select a lyric block before inserting a word.")
            return False
        raw_word = self.alignment_insert_edit.text().strip()
        if not raw_word:
            self.alignment_selection_label.setText("Enter a lyric word or direct phoneme unit first.")
            return False
        line, word_index = self.selected_alignment_key
        try:
            report, aligned_text, inserted_key = insert_alignment_token(
                self.alignment_report,
                self.alignment_editor.toPlainText(),
                line,
                word_index,
                raw_word,
                position,
            )
        except (ValueError, KeyError, TypeError) as error:
            self.alignment_selection_label.setText(str(error))
            return False
        self.alignment_report = report
        self.alignment_editor.setPlainText(aligned_text)
        self.alignment_editor.document().setModified(True)
        self.alignment_timeline.set_alignment_annotations(report.get("notes", []))
        self.alignment_insert_edit.clear()
        self._select_alignment_unit(*inserted_key)
        self.alignment_selection_label.setText(
            f"Inserted '{raw_word}' {position} the selected word and re-fit later lyric units."
        )
        return True

    def _save_alignment_report(self) -> bool:
        if not self.alignment_report or not self.latest_alignment_report_path:
            return True
        try:
            self.latest_alignment_report_path.parent.mkdir(parents=True, exist_ok=True)
            self.latest_alignment_report_path.write_text(
                json.dumps(self.alignment_report, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            QMessageBox.warning(self, "Could not save alignment report", str(error))
            return False
        return True

    def analyze_alignment(self) -> None:
        role = self._alignment_role()
        if not role or not self.inspection or not self.latest_draft_path:
            return
        if not self._write_editor_text(self.draft_preview, self.latest_draft_path, "draft"):
            return
        if not self.latest_alignment_path or not self.latest_alignment_report_path:
            return
        arguments = [
            "tools/lyric_sync_assistant/alignment.py",
            self.inspection.song_name,
            role.role,
            "--draft",
            str(self.latest_draft_path),
            "--output",
            str(self.latest_alignment_path),
            "--report",
            str(self.latest_alignment_report_path),
            "--overwrite",
        ]
        self._start_process(
            f"Align lyrics for {role.role}",
            arguments,
            on_success=self._after_alignment_written,
        )

    def _after_alignment_written(self) -> None:
        self._load_alignment_preview()
        self.status_label.setText(
            "Alignment analyzed. Click a lyric block, then drag an edge or use arrow keys before applying it."
        )

    def reload_alignment(self) -> None:
        self._load_alignment_preview()
        self.status_label.setText("Aligned lyric editor reloaded from its safe output file.")

    def save_alignment(self) -> None:
        if not self._validate_alignment_editor():
            return
        if self.latest_alignment_path and self._write_editor_text(
            self.alignment_editor,
            self.latest_alignment_path,
            "aligned lyrics",
        ):
            if not self._save_alignment_report():
                return
            self.status_label.setText(
                f"Aligned lyrics saved: {self._short_path(self.latest_alignment_path)}"
            )

    def apply_alignment(self) -> None:
        role = self._alignment_role()
        if not role or not self.latest_alignment_path:
            return
        if not self._validate_alignment_editor():
            return
        if not self._write_editor_text(
            self.alignment_editor,
            self.latest_alignment_path,
            "aligned lyrics",
        ):
            return
        if not self._save_alignment_report():
            return
        confirmation = QMessageBox.question(
            self,
            "Replace lyric input?",
            f"This replaces the configured lyric input for {role.role}:\n{role.lyric_path}\n\n"
            "The aligned lyric editor contents will be written there. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            role.lyric_path.parent.mkdir(parents=True, exist_ok=True)
            role.lyric_path.write_text(
                self.alignment_editor.toPlainText().rstrip() + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            QMessageBox.warning(self, "Could not apply aligned lyrics", str(error))
            return
        self.status_label.setText(
            "Aligned lyrics applied to the configured input. Re-inspecting the song..."
        )
        self.inspect_current_song()

    def _status_color(self, status: str) -> QColor:
        if status == "Ready":
            return QColor("#67d7a7")
        if "Polyphonic" in status:
            return QColor("#f0bf65")
        return QColor("#ef8b83")

    def _compact_loudness(self, loudness) -> str:
        if not loudness:
            return "Not rendered"
        if loudness.error:
            return "Unavailable"
        if loudness.median_dbfs is None:
            return "Silent"
        peak = "--" if loudness.peak_dbfs is None else f"{loudness.peak_dbfs:.1f}"
        return f"med {loudness.median_dbfs:.1f}  |  pk {peak}"

    def _polyphony_display(self, role: RoleInspection) -> str:
        if not role.midi_track:
            return "--"
        if role.midi_track.max_polyphony <= 1:
            return "1"
        return f"{role.midi_track.max_polyphony} / {role.midi_track.longest_overlap_ms:.1f} ms"

    def _overlap_tooltip(self, role: RoleInspection) -> str:
        if not role.midi_track:
            return "No MIDI source track found."
        track = role.midi_track
        text = (
            f"Maximum simultaneous notes: {track.max_polyphony}\n"
            f"Overlap regions: {track.overlap_regions}\n"
            f"Total overlap: {track.total_overlap_ms:.1f} ms\n"
            f"Longest overlap: {track.longest_overlap_ms:.1f} ms"
        )
        if track.duplicate_note_spans:
            text += f"\nExact duplicate spans ignored: {track.duplicate_note_spans}"
        return text

    def _select_role(self) -> None:
        if not self.inspection:
            return
        row = self.role_table.currentRow()
        if row < 0 or row >= len(self.inspection.roles):
            return
        role = self.inspection.roles[row]
        self.current_role = role
        self.detail_title.setText(role.role)
        source_description = (
            f"MIDI  {role.midi_source_name} ({role.note_count} notes)\n"
            f"Lyrics  {self._short_path(role.lyric_path)}"
        )
        self.detail_fields["source"].setText(source_description)
        wrap = "--" if role.pitch_wrap_shift is None else f"{role.pitch_wrap_shift:+} semitones"
        self.detail_fields["pitch"].setText(
            f"MIDI  {role.midi_range}\nRender  {role.render_range}\nAudible  {role.audible_range}\nWrap  {wrap}"
        )
        audio_text = f"Stem  {self._short_path(role.stem_path)}"
        if role.loudness:
            audio_text += f"\n{role.loudness.display}"
        self.detail_fields["audio"].setText(audio_text)
        self.detail_fields["details"].setText("\n".join(role.details) if role.details else role.status)
        self._set_detail_actions_enabled(True)
        self._sync_workflow_role(row)

    def _short_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    @staticmethod
    def _has_lyric_content(path: Path) -> bool:
        try:
            return any(
                line.strip() and not line.lstrip().startswith("#")
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        except OSError:
            return False

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

    def choose_draft_source(self) -> None:
        role = self._workflow_role()
        fallback = role.lyric_path.parent if role else self.repo_root
        source_text, _ = QFileDialog.getOpenFileName(
            self,
            "Choose lyric transcript",
            str(self._last_dialog_dir(fallback)),
            "Text files (*.txt);;All files (*.*)",
        )
        if not source_text:
            return
        source_path = Path(source_text).resolve()
        self.draft_source_edit.setText(str(source_path))
        try:
            self.draft_input_editor.setPlainText(source_path.read_text(encoding="utf-8"))
            self.draft_input_editor.document().setModified(False)
        except OSError as error:
            QMessageBox.warning(self, "Could not read transcript", str(error))
            return
        self._remember_dialog_dir(source_path.parent)

    def draft_lyrics(self) -> None:
        self._run_lyric_drafter(apply=False)

    def apply_draft(self) -> None:
        role = self._workflow_role()
        if not role or not self.latest_draft_path:
            return
        draft_issue = self._editor_conversion_issue(self.draft_preview)
        if draft_issue:
            QMessageBox.warning(
                self,
                "Invalid lyric draft",
                f"The draft was not applied:\n{draft_issue}\n\n"
                "Fix the lyric or phoneme before replacing the song input.",
            )
            self.status_label.setText(f"Draft blocked: {draft_issue}")
            return
        confirmation = QMessageBox.question(
            self,
            "Replace lyric input?",
            f"This replaces the configured lyric input for {role.role}:\n{role.lyric_path}\n\n"
            "The edited draft buffer will be written there. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        if not self._write_editor_text(self.draft_preview, self.latest_draft_path, "draft"):
            return
        try:
            role.lyric_path.parent.mkdir(parents=True, exist_ok=True)
            role.lyric_path.write_text(
                self.draft_preview.toPlainText().rstrip() + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            QMessageBox.warning(self, "Could not apply draft", str(error))
            return
        self.status_label.setText(
            "Edited draft applied to the configured input. Re-inspecting the song..."
        )
        self.inspect_current_song()

    def _run_lyric_drafter(self, apply: bool) -> None:
        role = self._workflow_role()
        if not role or not self.inspection:
            return
        drafter = self.repo_root / "tools" / "lyric_sync_assistant" / "assistant.py"
        if not drafter.is_file():
            QMessageBox.warning(
                self,
                "Lyric drafter unavailable",
                f"The lyric drafting tool was not found:\n{drafter}",
            )
            return
        arguments = [
            "tools/lyric_sync_assistant/assistant.py",
            self.inspection.song_name,
            role.role,
            "--overwrite",
        ]
        placeholder_mode = self.draft_mode_combo.currentData() == "placeholder"
        if placeholder_mode:
            placeholder = self.placeholder_edit.text().strip() or "daa"
            arguments.extend(("--placeholder", placeholder))
        else:
            pasted_text = self.draft_input_editor.toPlainText().strip()
            has_lyric_text = any(
                line.strip() and not line.lstrip().startswith("#")
                for line in pasted_text.splitlines()
            )
            if has_lyric_text:
                source_path = self.latest_transcript_path or (
                    self.inspection.output_dir / "lyrics_drafts" / f"{role.role}.transcript.txt"
                )
                if not self._write_editor_text(
                    self.draft_input_editor,
                    source_path,
                    "transcript input",
                ):
                    return
                self.latest_transcript_path = source_path
                self.draft_source_edit.setText(str(source_path))
            else:
                source_path = Path(self.draft_source_edit.text()).expanduser().resolve()
                if not source_path.is_file():
                    QMessageBox.warning(
                        self,
                        "Transcript source missing",
                        "Paste lyric lines into Transcript text or choose an existing transcript file.",
                    )
                    return
            arguments.extend(("--text-file", str(source_path)))
        if not placeholder_mode and self.auto_lines_check.isChecked():
            arguments.append("--auto-lines")
        if apply:
            arguments.append("--apply")
            task_name = f"Apply lyric draft for {role.role}"
            on_success = self._after_draft_applied
        else:
            self.latest_draft_path = (
                self.inspection.output_dir / "lyrics_drafts" / f"{role.role}.txt"
            )
            arguments.extend(("--output", str(self.latest_draft_path)))
            task_name = f"Draft lyrics for {role.role}"
            on_success = self._after_draft_written
        self._start_process(task_name, arguments, on_success=on_success)

    def _after_draft_written(self) -> None:
        self._load_draft_preview()
        self.status_label.setText(
            "Lyric draft loaded into the in-app editor. Edit, save, align, or apply it from the GUI."
        )

    def _after_draft_applied(self) -> None:
        self.status_label.setText("Lyric draft applied to the configured input. Re-inspecting the song...")
        self.inspect_current_song()

    def _selected_midi_source_index(self) -> int | None:
        value = self.midi_source_combo.currentData()
        return int(value) if value is not None else None

    def _midi_preview_path(self, track_index: int) -> Path:
        if not self.inspection or not self.inspection.midi:
            raise MidiPreviewError("No MIDI source is selected.")
        track = next(
            (track for track in self.inspection.midi.tracks if track.index == track_index),
            None,
        )
        if not track:
            raise MidiPreviewError("The selected MIDI track no longer exists.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", track.name).strip("_") or "track"
        # Windows' sequencer can keep the previous preview file locked briefly
        # after playback stops. A fresh path makes replay independent of that
        # handle and avoids turning a stale MCI lock into a failed preview.
        token = uuid.uuid4().hex[:8]
        return self.inspection.output_dir / "_midi_preview" / f"{track.index:02d}_{safe_name}_{token}.mid"

    def play_selected_midi(self) -> None:
        if not self.inspection or not self.inspection.midi_path or not self.inspection.midi:
            return
        track_index = self._selected_midi_source_index()
        if track_index is None:
            QMessageBox.information(
                self,
                "Choose a MIDI source",
                "Choose one source track instead of All note tracks before playing a MIDI preview.",
            )
            return
        try:
            preview_path = write_single_track_preview(
                self.inspection.midi_path,
                track_index,
                self._midi_preview_path(track_index),
            )
            self.midi_preview_player.play(preview_path)
            self.midi_preview_duration_ms = max(
                self.midi_preview_player.duration_ms(),
                round(self.inspection.midi.duration_seconds * 1000),
            )
        except MidiPreviewError as error:
            QMessageBox.warning(self, "MIDI preview unavailable", str(error))
            self.midi_preview_state.setText(f"MIDI preview unavailable: {error}")
            return
        self.midi_seek_slider.setRange(0, self.midi_preview_duration_ms)
        self._set_midi_duration_label(0)
        self.midi_preview_timer.start()
        self.midi_preview_state.setText(
            f"Playing {preview_path.name} through the Windows MIDI device."
        )

    def pause_or_resume_midi(self) -> None:
        if not self.midi_preview_player.is_open:
            return
        try:
            self.midi_preview_player.pause_or_resume()
        except MidiPreviewError as error:
            self.midi_preview_state.setText(f"MIDI preview error: {error}")
            return
        self.pause_midi_button.setText(
            "Resume" if self.midi_preview_player.is_paused else "Pause"
        )
        self.midi_preview_state.setText(
            "MIDI preview paused." if self.midi_preview_player.is_paused else "MIDI preview resumed."
        )

    def stop_midi(self) -> None:
        try:
            self.midi_preview_player.stop()
        except MidiPreviewError as error:
            self.midi_preview_state.setText(f"MIDI preview error: {error}")
        self.midi_preview_timer.stop()
        self.pause_midi_button.setText("Pause")
        with QSignalBlocker(self.midi_seek_slider):
            self.midi_seek_slider.setValue(0)
        self.midi_timeline.set_playhead_tick(0)
        self._set_midi_duration_label(0)
        self.midi_preview_state.setText("MIDI preview stopped.")

    def _update_midi_preview_position(self) -> None:
        if not self.midi_preview_player.is_open:
            self.midi_preview_timer.stop()
            return
        try:
            position_ms = self.midi_preview_player.position_ms()
            mode = self.midi_preview_player.mode()
        except MidiPreviewError as error:
            self.midi_preview_timer.stop()
            self.midi_preview_state.setText(f"MIDI preview error: {error}")
            return
        with QSignalBlocker(self.midi_seek_slider):
            self.midi_seek_slider.setValue(position_ms)
        self._set_midi_duration_label(position_ms)
        if self.midi_preview_duration_ms:
            self.midi_timeline.set_playhead_tick(
                round(position_ms / self.midi_preview_duration_ms * self.midi_timeline.max_tick)
            )
        if mode in {"stopped", "not ready"}:
            self.midi_preview_timer.stop()
            self.pause_midi_button.setText("Pause")
            self.midi_preview_state.setText("MIDI preview finished.")

    def _seek_midi_to_tick(self, tick: int) -> None:
        if not self.midi_timeline.max_tick:
            return
        position_ms = round(tick / self.midi_timeline.max_tick * self.midi_preview_duration_ms)
        with QSignalBlocker(self.midi_seek_slider):
            self.midi_seek_slider.setValue(position_ms)
        self._seek_midi_to_milliseconds(position_ms)

    def _seek_midi_to_milliseconds(self, position_ms: int) -> None:
        self._set_midi_duration_label(position_ms)
        if self.midi_preview_duration_ms:
            self.midi_timeline.set_playhead_tick(
                round(position_ms / self.midi_preview_duration_ms * self.midi_timeline.max_tick)
            )
        if self.midi_preview_player.is_open:
            try:
                self.midi_preview_player.seek(position_ms)
            except MidiPreviewError as error:
                self.midi_preview_state.setText(f"MIDI preview error: {error}")

    def play_selected_stem(self) -> None:
        role = self._workflow_role()
        if not role:
            return
        self._play_audio_path(role.stem_path, f"{role.role} stem")

    def play_final_mix(self) -> None:
        if self.inspection:
            self._play_audio_path(self.inspection.final_mix, "final mix")

    def _play_audio_path(self, path: Path, label: str) -> None:
        if not path.is_file():
            QMessageBox.information(
                self,
                "Rendered audio unavailable",
                f"Render the song first. The expected {label} file was not found:\n{path}",
            )
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_player.play()
        self.audio_state.setText(f"Playing {label}: {path.name}")

    def stop_audio(self) -> None:
        self.audio_player.stop()
        self.audio_state.setText("Rendered audio is stopped.")

    def _update_audio_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.audio_state.setText("Rendered audio is stopped.")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.audio_state.setText("Rendered audio is paused.")

    def render_song(self) -> None:
        song_name = self.song_combo.currentText()
        if not song_name:
            return
        if not (self.repo_root / "choir.py").is_file():
            QMessageBox.warning(self, "Cannot render", "choir.py was not found in the selected project.")
            return
        if not self.inspection or self.inspection.song_name != song_name:
            QMessageBox.warning(
                self,
                "Cannot render",
                "Wait for the selected song inspection to finish before rendering.",
            )
            return
        blocking_statuses = {"Missing MIDI source", "Ambiguous MIDI source"}
        blocked_roles = [
            role for role in self.inspection.roles if role.status in blocking_statuses
        ]
        if blocked_roles:
            details = "\n".join(
                f"- {role.role}: {role.status}"
                for role in blocked_roles
            )
            QMessageBox.warning(
                self,
                "Cannot render incomplete song",
                "Every configured role needs a resolvable MIDI source before rendering.\n\n"
                + details
                + "\n\nUse Draft to create or paste lyrics, then refresh the inspection.",
            )
            self.status_label.setText(
                f"Render blocked: {len(blocked_roles)} role(s) need MIDI input."
            )
            return
        renderable_roles = [
            role
            for role in self.inspection.roles
            if role.midi_track and role.status not in blocking_statuses
            and role.status
            not in {"Missing lyric input", "Missing lyric content", "Invalid lyric content"}
        ]
        if not renderable_roles:
            QMessageBox.warning(
                self,
                "No renderable roles",
                "No configured role currently has both MIDI and lyric content.\n\n"
                "Use Draft to create or paste lyrics, then refresh the inspection.",
            )
            self.status_label.setText("Render blocked: no role has usable lyric content.")
            return
        arguments = ["choir.py"]
        if self.visuals_action.isChecked():
            arguments.append("-vis")
        if self.plots_action.isChecked():
            arguments.append("-plt")
        arguments.append(song_name)
        self._start_process(f"Render {song_name}", arguments)

    def load_split_source(self) -> None:
        source = self.inspection.midi_path if self.inspection else None
        if source is None or not source.is_file():
            self.split_source_path = None
            self.split_source_edit.clear()
            self.split_track_combo.clear()
            self.split_selected_analysis = None
            self.split_lanes = []
            self.split_state_label.setText("Select a song with a MIDI source first.")
            return
        try:
            _, analyses = analyze_midi_source(source)
        except (OSError, ValueError, EOFError, MidiSplitError) as error:
            QMessageBox.warning(self, "MIDI analysis failed", str(error))
            self.split_state_label.setText(f"Analysis failed: {error}")
            return

        note_tracks = [analysis for analysis in analyses if analysis.notes]
        if not note_tracks:
            QMessageBox.warning(self, "No note tracks", "The selected MIDI contains no note-bearing tracks.")
            return
        self.split_source_path = source
        self.split_analyses = tuple(note_tracks)
        self.split_track_combo.blockSignals(True)
        self.split_track_combo.clear()
        for analysis in note_tracks:
            self.split_track_combo.addItem(
                f"{analysis.source_name} ({len(analysis.notes)} notes)",
                analysis.source_index,
            )
        self.split_track_combo.blockSignals(False)
        self.split_output_edit.setText(str(source.with_name(f"{source.stem}_monophonic.mid")))
        self.split_state_label.setText(
            f"Loaded {source.name}: {len(note_tracks)} note-bearing track(s)."
        )
        self._split_track_changed(self.split_track_combo.currentIndex())

    def _split_track_changed(self, combo_index: int) -> None:
        if not self.split_source_path:
            return
        source_index = self.split_track_combo.itemData(combo_index)
        if source_index is None:
            return
        try:
            _, analysis, lanes = split_track_preview(self.split_source_path, int(source_index))
        except (OSError, ValueError, EOFError, MidiSplitError) as error:
            self.split_state_label.setText(f"Preview failed: {error}")
            return
        self.split_selected_analysis = analysis
        self.split_lanes = lanes
        self.split_timeline.set_tracks(split_view_tracks(analysis, lanes))
        self.split_timeline.set_split_overlay(
            True,
            f"{analysis.source_name}: source + {len(lanes)} tentative voice lane(s)",
        )
        with QSignalBlocker(self.split_pitch_zoom_slider):
            self.split_pitch_zoom_slider.setValue(0)
        self.split_summary_label.setText(
            f"{len(analysis.notes)} notes | max polyphony {max(1, len(lanes))} | "
            f"{len(lanes)} tentative lane(s)"
        )
        self.split_state_label.setText(
            "Dry run only. Translucent notes are source material; colored notes are tentative lanes."
        )

    def _fit_split_pitch_range(self) -> None:
        self.split_timeline.fit_pitch_range()
        with QSignalBlocker(self.split_pitch_zoom_slider):
            self.split_pitch_zoom_slider.setValue(0)

    def choose_split_output(self) -> None:
        source = self.split_source_path or Path(self.repo_root / "songs")
        default_output = (
            source.with_name(f"{source.stem}_monophonic.mid")
            if source.is_file()
            else source / "split_monophonic.mid"
        )
        output_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save split MIDI",
            str(default_output),
            "MIDI files (*.mid *.midi)",
        )
        if output_text:
            output = Path(output_text).resolve()
            if output.suffix.lower() not in {".mid", ".midi"}:
                output = output.with_suffix(".mid")
            self.split_output_edit.setText(str(output))

    def export_split_midi(self) -> None:
        if not self.split_source_path or not self.split_selected_analysis:
            QMessageBox.information(
                self,
                "Choose source track",
                "Load the current song MIDI and select a track before exporting a split.",
            )
            return
        output = Path(self.split_output_edit.text()).expanduser().resolve()
        if output.suffix.lower() not in {".mid", ".midi"}:
            output = output.with_suffix(".mid")
            self.split_output_edit.setText(str(output))
        cli_output = output
        self.pending_split_replace = None
        if output == self.split_source_path:
            backup = output.with_suffix(output.suffix + ".bak")
            if QMessageBox.question(
                self,
                "Replace working MIDI?",
                f"This replaces the current working MIDI after verification.\n"
                f"A backup will be written to:\n{backup}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            with tempfile.NamedTemporaryFile(prefix="dectalk_split_", suffix=".mid", delete=False) as handle:
                cli_output = Path(handle.name)
            cli_output.unlink(missing_ok=True)
            self.pending_split_replace = (cli_output, output, backup)
        elif output.exists() and QMessageBox.question(
            self,
            "Replace split output?",
            f"{output.name} already exists. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_process(
            f"Split {self.split_source_path.name}",
            [
                "tools/split_polyphonic_midi.py",
                str(self.split_source_path),
                "--output",
                str(cli_output),
                "--track-index",
                str(self.split_selected_analysis.source_index),
            ],
            on_success=self._after_split_export,
        )

    def _after_split_export(self) -> None:
        pending = self.pending_split_replace
        self.pending_split_replace = None
        if pending:
            temporary, source, backup = pending
            try:
                shutil.copy2(source, backup)
                shutil.copy2(temporary, source)
                temporary.unlink(missing_ok=True)
            except OSError as error:
                QMessageBox.warning(self, "Could not replace working MIDI", str(error))
                return
            self.split_state_label.setText(
                f"Replaced {source.name}; backup saved as {backup.name}. Re-inspecting the song..."
            )
            self.inspect_current_song()
            return
        self.split_state_label.setText("Verified targeted MIDI split exported.")

    def choose_import_midi(self) -> None:
        downloads = Path.home() / "Downloads"
        start_dir = self._last_dialog_dir(
            downloads if downloads.is_dir() else self.repo_root / "songs"
        )
        source_text, _ = QFileDialog.getOpenFileName(
            self,
            "Import MIDI as new song",
            str(start_dir),
            "MIDI files (*.mid *.midi)",
        )
        if not source_text:
            return
        source = Path(source_text).resolve()
        self._remember_dialog_dir(source.parent)
        self.import_midi_as_song(source)

    def import_midi_as_song(self, source: Path) -> None:
        default_name = re.sub(r"[^A-Za-z0-9]+", "", source.stem) or "ImportedSong"
        song_name, accepted = QInputDialog.getText(
            self,
            "Import MIDI as new song",
            "Song folder name:",
            text=default_name,
        )
        if not accepted:
            return
        try:
            imported = import_midi_song(source, self.repo_root, song_name)
        except MidiImportError as error:
            QMessageBox.warning(self, "MIDI import failed", str(error))
            return

        self._load_song_choices()
        song_index = self.song_combo.findText(imported.song_name)
        if song_index >= 0:
            self.song_combo.setCurrentIndex(song_index)
        if self.split_source_path == source:
            self.split_state_label.setText(
                f"Imported {source.name} as songs/{imported.song_name}/ "
                f"with {len(imported.role_names)} role(s)."
            )

    def _python_executable(self) -> Path:
        project_python = self.repo_root / ".venv" / "Scripts" / "python.exe"
        return project_python if project_python.is_file() else Path(sys.executable)

    def _start_process(
        self,
        task_name: str,
        arguments: list[str],
        on_success: Callable[[], None] | None = None,
    ) -> None:
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
        self.process_success_callback = on_success
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
            callback = self.process_success_callback
            self.process_success_callback = None
            if callback:
                callback()
            else:
                self.inspect_current_song()
        else:
            if self.pending_split_replace:
                temporary, _, _ = self.pending_split_replace
                temporary.unlink(missing_ok=True)
                self.pending_split_replace = None
            self.process_success_callback = None

    def _set_task_running(self, running: bool) -> None:
        for widget in (
            self.render_button,
            self.refresh_button,
            self.song_combo,
            self.split_source_edit,
            self.split_source_reload_button,
            self.split_load_button,
            self.split_track_combo,
            self.split_pitch_zoom_slider,
            self.split_fit_pitch_button,
            self.split_output_edit,
            self.split_output_browse_button,
            self.split_export_button,
            self.midi_source_combo,
            self.midi_import_button,
            self.midi_pitch_zoom_slider,
            self.midi_fit_pitch_button,
            self.midi_time_zoom_slider,
            self.midi_fit_time_button,
            self.midi_time_scrollbar,
            self.draft_button,
            self.draft_mode_combo,
            self.draft_source_edit,
            self.draft_browse_button,
            self.draft_input_editor,
            self.auto_lines_check,
            self.placeholder_edit,
            self.reload_draft_button,
            self.save_draft_button,
            self.apply_draft_button,
            self.alignment_analyze_button,
            self.alignment_role_combo,
            self.alignment_reload_button,
            self.alignment_save_button,
            self.alignment_apply_button,
            self.alignment_pitch_zoom_slider,
            self.alignment_fit_pitch_button,
            self.alignment_time_zoom_slider,
            self.alignment_fit_time_button,
            self.alignment_time_scrollbar,
            self.alignment_insert_edit,
            self.alignment_insert_before_button,
            self.alignment_insert_after_button,
            self.alignment_text_toggle,
            self.draft_preview,
            self.alignment_timeline,
            self.alignment_editor,
        ):
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
