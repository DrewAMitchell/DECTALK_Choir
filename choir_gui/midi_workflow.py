"""MIDI visualization and Windows source-track preview helpers for the GUI."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import uuid

import mido
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from choir_gui.inspector import MidiTrackInfo
from pyFuncs.PitchMapping import midi_pitch_name


def _format_duration_ms(milliseconds: int) -> str:
    total_seconds = max(0, round(milliseconds / 1000))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


class MidiPreviewError(RuntimeError):
    """The selected MIDI source cannot be prepared or played locally."""


class MidiTimelineWidget(QWidget):
    """Compact piano-roll overview driven by the already-inspected MIDI notes."""

    seekRequested = Signal(int)
    viewChanged = Signal(str)
    alignmentUnitSelected = Signal(int, int)
    alignmentBoundaryNudgeRequested = Signal(int, int, str, int)

    _TRACK_COLORS = (
        QColor("#54c99a"),
        QColor("#e6b35f"),
        QColor("#76b7e5"),
        QColor("#d98eac"),
        QColor("#9cbe72"),
        QColor("#c59ae0"),
    )
    _PITCH_COLORS = (
        QColor("#54c99a"),
        QColor("#65a9df"),
        QColor("#c7a1e4"),
        QColor("#d98eac"),
        QColor("#e6b35f"),
        QColor("#d98572"),
        QColor("#9cbe72"),
        QColor("#65c8c0"),
        QColor("#c7a46b"),
        QColor("#8fc18d"),
        QColor("#d39bb2"),
        QColor("#8ba6d9"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tracks: tuple[MidiTrackInfo, ...] = ()
        self._focus_track_index: int | None = None
        self._max_tick = 1
        self._playhead_tick = 0
        self._data_min_pitch = 0
        self._data_max_pitch = 127
        self._pitch_center = 63.5
        self._pitch_zoom = 0
        self._time_center_tick = 0.5
        self._time_zoom = 0
        self._duration_ms = 0
        self._alignment_annotations: dict[int, dict] = {}
        self._alignment_selected_key: tuple[int, int] | None = None
        self._alignment_label_regions: list[tuple[QRect, int, int]] = []
        self._alignment_drag: tuple[int, int, str, int] | None = None
        self._alignment_overlay = False
        self._split_overlay = False
        self._scope_override: str | None = None
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def max_tick(self) -> int:
        return self._max_tick

    def set_tracks(
        self,
        tracks: tuple[MidiTrackInfo, ...],
        focus_track_index: int | None = None,
    ) -> None:
        self._tracks = tracks
        self._focus_track_index = focus_track_index
        pitch_tracks = (
            tuple(track for track in tracks if track.index == focus_track_index)
            if focus_track_index is not None
            else tracks
        )
        self._max_tick = max(
            (note.end_tick for track in pitch_tracks for note in track.notes), default=1
        )
        notes = [note for track in pitch_tracks for note in track.notes]
        self._data_min_pitch = min((note.pitch for note in notes), default=0)
        self._data_max_pitch = max((note.pitch for note in notes), default=127)
        self._time_center_tick = self._max_tick / 2
        self._time_zoom = 0
        self.fit_pitch_range(emit=False)
        self._playhead_tick = min(self._playhead_tick, self._max_tick)
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def set_alignment_annotations(self, entries: list[dict] | None) -> None:
        self._alignment_annotations = {
            int(entry["note_index"]): entry
            for entry in (entries or [])
            if entry.get("note_index") is not None
        }
        self.update()

    def set_alignment_selection(self, line: int | None, word_index: int | None) -> None:
        self._alignment_selected_key = (
            (int(line), int(word_index))
            if line is not None and word_index is not None
            else None
        )
        self.update()

    def set_alignment_overlay(self, enabled: bool) -> None:
        self._alignment_overlay = enabled
        self.update()

    def set_duration_ms(self, duration_ms: int | None) -> None:
        self._duration_ms = max(0, int(duration_ms or 0))
        self.update()

    def _time_span_for_zoom(self, zoom: int | None = None) -> float:
        value = self._time_zoom if zoom is None else max(0, min(100, int(zoom)))
        if value == 0:
            return float(self._max_tick)
        return max(1.0, self._max_tick * (1.0 - value * 0.008))

    def set_time_zoom(self, value: int) -> None:
        self._time_zoom = max(0, min(100, int(value)))
        if self._time_zoom == 0:
            self._time_center_tick = self._max_tick / 2
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def zoom_time_by(self, steps: int, anchor_fraction: float = 0.5) -> None:
        """Zoom the time axis around a point in the visible window."""

        if not self._tracks or not steps:
            return
        time_min, time_max = self._visible_time_bounds()
        fraction = max(0.0, min(1.0, float(anchor_fraction)))
        anchor_tick = time_min + fraction * (time_max - time_min)
        next_zoom = max(0, min(100, self._time_zoom + int(steps) * 10))
        if next_zoom == self._time_zoom:
            return
        self._time_zoom = next_zoom
        next_span = self._time_span_for_zoom()
        lower = anchor_tick - fraction * next_span
        lower = max(0.0, min(max(0.0, self._max_tick - next_span), lower))
        self._time_center_tick = lower + next_span / 2
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def pan_time(self, steps: int) -> None:
        if not self._tracks or self._time_zoom == 0 or not steps:
            return
        time_min, time_max = self._visible_time_bounds()
        span = max(1.0, time_max - time_min)
        lower = time_min - steps * span * 0.2
        lower = max(0.0, min(max(0.0, self._max_tick - span), lower))
        self._time_center_tick = lower + span / 2
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def fit_time_range(self) -> None:
        self._time_zoom = 0
        self._time_center_tick = self._max_tick / 2
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def set_split_overlay(self, enabled: bool, scope: str | None = None) -> None:
        """Render the first track as a translucent source under tentative lanes."""

        self._split_overlay = enabled
        self._scope_override = scope
        self.update()

    def set_pitch_zoom(self, value: int) -> None:
        self._pitch_zoom = max(0, min(100, int(value)))
        if self._pitch_zoom == 0:
            self.fit_pitch_range(emit=False)
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def fit_pitch_range(self, emit: bool = True) -> None:
        lower = max(0, self._data_min_pitch - 1)
        upper = min(127, self._data_max_pitch + 1)
        self._pitch_center = (lower + upper) / 2
        self._pitch_zoom = 0
        if emit:
            self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def _visible_pitch_bounds(self) -> tuple[int, int]:
        data_span = max(1, self._data_max_pitch - self._data_min_pitch + 3)
        if self._pitch_zoom == 0:
            span = data_span
        else:
            minimum_span = min(12, data_span)
            span = max(
                minimum_span,
                round(data_span * (1.0 - self._pitch_zoom * 0.0075)),
            )
        lower = round(self._pitch_center - (span - 1) / 2)
        upper = lower + span - 1
        if lower < 0:
            upper -= lower
            lower = 0
        if upper > 127:
            lower -= upper - 127
            upper = 127
        return max(0, lower), min(127, max(lower, upper))

    def _visible_time_bounds(self) -> tuple[float, float]:
        if self._time_zoom == 0:
            return 0.0, float(self._max_tick)
        span = self._time_span_for_zoom()
        lower = self._time_center_tick - span / 2
        upper = lower + span
        if lower < 0:
            upper -= lower
            lower = 0
        if upper > self._max_tick:
            lower -= upper - self._max_tick
            upper = self._max_tick
        return max(0.0, lower), min(float(self._max_tick), max(lower + 1.0, upper))

    def time_scroll_state(self) -> tuple[int, int, int]:
        """Return scrollbar value, maximum, and page step for the current window."""

        if self._time_zoom == 0 or self._max_tick <= 1:
            return 0, 0, 1000
        time_min, time_max = self._visible_time_bounds()
        span = max(1.0, time_max - time_min)
        page = max(1, min(1000, round(span / self._max_tick * 1000)))
        maximum = max(0, 1000 - page)
        available = max(1.0, self._max_tick - span)
        value = round(time_min / available * maximum) if maximum else 0
        return value, maximum, page

    def set_time_scroll(self, value: int) -> None:
        if self._time_zoom == 0:
            return
        _, maximum, _ = self.time_scroll_state()
        if maximum <= 0:
            return
        span = self._time_span_for_zoom()
        available = max(0.0, self._max_tick - span)
        lower = max(0.0, min(available, int(value) / maximum * available))
        self._time_center_tick = lower + span / 2
        self.viewChanged.emit(self.pitch_view_summary())
        self.update()

    def pitch_view_summary(self) -> str:
        if not self._tracks:
            return "No MIDI notes"
        visible_min, visible_max = self._visible_pitch_bounds()
        time_min, time_max = self._visible_time_bounds()
        notes = [note for track in self._visible_tracks() for note in track.notes]
        above = sum(note.pitch > visible_max for note in notes)
        below = sum(note.pitch < visible_min for note in notes)
        out_of_view = f"; out of view: {above} above, {below} below" if above or below else ""
        before = sum(note.end_tick <= time_min for note in notes)
        after = sum(note.start_tick >= time_max for note in notes)
        time_out_of_view = (
            f"; time out of view: {before} before, {after} after"
            if before or after
            else ""
        )
        return (
            f"Data {midi_pitch_name(self._data_min_pitch)}-{midi_pitch_name(self._data_max_pitch)}  "
            f"View {midi_pitch_name(visible_min)}-{midi_pitch_name(visible_max)}{out_of_view}  "
            f"Window {time_min / self._max_tick:.0%}-{time_max / self._max_tick:.0%}"
            f"{time_out_of_view}"
        )

    def set_playhead_tick(self, tick: int) -> None:
        bounded = max(0, min(int(tick), self._max_tick))
        if bounded != self._playhead_tick:
            self._playhead_tick = bounded
            self.update()

    def _visible_tracks(self) -> tuple[MidiTrackInfo, ...]:
        if self._focus_track_index is None:
            return tuple(track for track in self._tracks if track.notes)
        return tuple(
            track
            for track in self._tracks
            if track.index == self._focus_track_index and track.notes
        )

    def _timeline_rect(self) -> tuple[int, int, int, int]:
        left = 46
        top = 58 if self._alignment_overlay else 28
        width = max(1, self.width() - left - 14)
        height = max(1, self.height() - top - 28)
        return left, top, width, height

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151819"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        tracks = self._visible_tracks()
        if not tracks:
            painter.setPen(QColor("#93a29f"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Select a MIDI source track to inspect its note timeline.",
            )
            return

        notes = [note for track in tracks for note in track.notes]
        minimum_pitch, maximum_pitch = self._visible_pitch_bounds()
        pitch_span = max(1, maximum_pitch - minimum_pitch + 1)
        time_min, time_max = self._visible_time_bounds()
        time_span = max(1.0, time_max - time_min)
        left, top, width, height = self._timeline_rect()

        painter.setPen(QPen(QColor("#3a4444"), 1))
        for pitch in range(minimum_pitch, maximum_pitch + 1):
            y = top + (maximum_pitch - pitch + 1) / pitch_span * height
            if pitch % 2:
                painter.fillRect(
                    QRect(left, round(y), width, max(1, round(height / pitch_span))),
                    QColor("#182122"),
                )
            if pitch % 12 == 0:
                painter.setPen(QPen(QColor("#455352"), 1))
                painter.drawLine(left, round(y), left + width, round(y))
                painter.setPen(QColor("#98aaa6"))
                painter.drawText(4, round(y) + 4, midi_pitch_name(pitch))
            else:
                painter.setPen(QPen(QColor("#2b3333"), 1))
                painter.drawLine(left, round(y), left + width, round(y))

        for track_position, track in enumerate(tracks):
            color = self._TRACK_COLORS[track_position % len(self._TRACK_COLORS)]
            for note_position, note in enumerate(track.notes, start=1):
                annotation = (
                    self._alignment_annotations.get(note_position)
                    if self._focus_track_index == track.index
                    else None
                )
                if self._split_overlay:
                    note_color = QColor("#d9e3df") if track_position == 0 else color
                    note_color.setAlpha(70 if track_position == 0 else 175)
                elif annotation:
                    note_color = self._annotation_color(annotation)
                elif self._focus_track_index is not None:
                    note_color = QColor(self._PITCH_COLORS[note.pitch % len(self._PITCH_COLORS)])
                else:
                    note_color = color
                painter.setBrush(note_color)
                painter.setPen(QPen(note_color.darker(125), 1))
                if note.pitch < minimum_pitch or note.pitch > maximum_pitch:
                    continue
                note_start = max(time_min, note.start_tick)
                note_end = min(time_max, note.end_tick)
                if note_end <= note_start:
                    continue
                x = left + (note_start - time_min) / time_span * width
                right = left + (note_end - time_min) / time_span * width
                y = top + (maximum_pitch - note.pitch) / pitch_span * height
                note_height = max(2, height / pitch_span - 1)
                painter.drawRoundedRect(
                    round(x),
                    round(y),
                    max(3, round(right - x)),
                    round(note_height),
                    2,
                    2,
                )

        if self._alignment_overlay and self._focus_track_index is not None:
            self._alignment_label_regions = []
            focused_track = next(
                (track for track in tracks if track.index == self._focus_track_index),
                None,
            )
            if focused_track:
                groups: dict[tuple[object, object], dict] = {}
                for annotation in self._alignment_annotations.values():
                    note_index = annotation.get("note_index")
                    if not note_index or note_index > len(focused_track.notes):
                        continue
                    if annotation.get("line") is None or annotation.get("word_index") is None:
                        # Unassigned notes are already visible in red; do not merge
                        # every gap in the song into one misleading placeholder label.
                        continue
                    key = (annotation.get("line"), annotation.get("word_index"))
                    note = focused_track.notes[int(note_index) - 1]
                    group = groups.setdefault(
                        key,
                        {
                            "start": note.start_tick,
                            "end": note.end_tick,
                            "start_ms": annotation.get("start_ms", 0),
                            "end_ms": annotation.get("end_ms", 0),
                            "lyric": annotation.get("lyric") or "--",
                            "confidence": annotation.get("confidence", "Review"),
                            "key": key,
                        },
                    )
                    group["start"] = min(group["start"], note.start_tick)
                    group["end"] = max(group["end"], note.end_tick)
                    group["start_ms"] = min(
                        group["start_ms"], annotation.get("start_ms", group["start_ms"])
                    )
                    group["end_ms"] = max(
                        group["end_ms"], annotation.get("end_ms", group["end_ms"])
                    )
                metrics = QFontMetrics(self.font())
                for group in sorted(groups.values(), key=lambda item: item["start"]):
                    start = max(time_min, group["start"])
                    end = min(time_max, group["end"])
                    if end <= start:
                        continue
                    x = left + (start - time_min) / time_span * width
                    right = left + (end - time_min) / time_span * width
                    duration_ms = max(0, round(group["end_ms"] - group["start_ms"]))
                    label_text = f"{group['lyric']} {duration_ms}ms"
                    full_width = metrics.horizontalAdvance(label_text) + 16
                    label_width = max(22, round(right - x), full_width)
                    label_left = min(round(x), left + width - label_width)
                    label_rect = QRect(label_left, 24, label_width, 26)
                    label_color = self._annotation_color(group)
                    selected = group["key"] == self._alignment_selected_key
                    label_color.setAlpha(120 if selected else 80)
                    painter.setBrush(label_color)
                    painter.setPen(
                        QPen(
                            QColor("#f1d26e") if selected else label_color.lighter(135),
                            2 if selected else 1,
                        )
                    )
                    painter.drawRoundedRect(label_rect, 3, 3)
                    if selected:
                        painter.setPen(QPen(QColor("#f1d26e"), 2))
                        painter.drawLine(
                            label_rect.left() + 2,
                            label_rect.top() + 4,
                            label_rect.left() + 2,
                            label_rect.bottom() - 4,
                        )
                        painter.drawLine(
                            label_rect.right() - 2,
                            label_rect.top() + 4,
                            label_rect.right() - 2,
                            label_rect.bottom() - 4,
                        )
                    painter.setPen(QColor("#edf5f1"))
                    painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)
                    self._alignment_label_regions.append(
                        (label_rect, int(group["key"][0]), int(group["key"][1]))
                    )

        notes_above = sum(note.pitch > maximum_pitch for note in notes)
        notes_below = sum(note.pitch < minimum_pitch for note in notes)
        painter.setPen(QColor("#f0bf65"))
        if notes_above:
            painter.drawText(left + width - 120, top + 14, f"{notes_above} notes above")
        if notes_below:
            painter.drawText(left + width - 120, top + height - 4, f"{notes_below} notes below")

        if time_min <= self._playhead_tick <= time_max:
            playhead_x = left + (self._playhead_tick - time_min) / time_span * width
            painter.setPen(QPen(QColor("#f1d26e"), 2))
            painter.drawLine(round(playhead_x), top, round(playhead_x), top + height)
        else:
            edge_x = left if self._playhead_tick < time_min else left + width
            painter.setPen(QPen(QColor("#f1d26e"), 2))
            painter.drawLine(round(edge_x), top, round(edge_x), top + height)
            painter.setPen(QColor("#f1d26e"))
            painter.drawText(
                max(left, round(edge_x) - 68),
                top + 14,
                "playhead out of view",
            )
            playhead_x = edge_x
        if self._duration_ms:
            elapsed_ms = round(self._playhead_tick / max(1, self._max_tick) * self._duration_ms)
            marker_rect = QRect(
                max(left, min(left + width - 104, round(playhead_x) - 52)),
                2,
                104,
                20,
            )
            painter.setBrush(QColor("#263532"))
            painter.setPen(QPen(QColor("#f1d26e"), 1))
            painter.drawRoundedRect(marker_rect, 4, 4)
            painter.setPen(QColor("#f7e7a5"))
            painter.drawText(
                marker_rect,
                Qt.AlignmentFlag.AlignCenter,
                f"{_format_duration_ms(elapsed_ms)} / {_format_duration_ms(self._duration_ms)}",
            )
        painter.setPen(QColor("#98aaa6"))
        scope = self._scope_override or (
            tracks[0].name if len(tracks) == 1 else f"{len(tracks)} MIDI tracks"
        )
        painter.drawText(
            left,
            18,
            f"{scope}  |  {len(notes)} notes  |  "
            f"{midi_pitch_name(minimum_pitch)}-{midi_pitch_name(maximum_pitch)}",
        )
        hint = (
            "Click a word; drag its edges; Left/Right nudges start; Shift+Left/Right nudges end"
            if self._alignment_overlay
            else "Click to seek; wheel or scrollbar pans time; Ctrl+wheel zooms; Alt+wheel pans pitch"
        )
        painter.drawText(left, top + height + 20, hint)

    @staticmethod
    def _annotation_color(annotation: dict | None) -> QColor:
        confidence = str((annotation or {}).get("confidence", "Review"))
        if confidence == "Confident":
            return QColor("#54c99a")
        if confidence == "Error":
            return QColor("#ef8b83")
        return QColor("#e6b35f")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        left, _, width, _ = self._timeline_rect()
        if self._alignment_overlay:
            position = event.position().toPoint()
            for region, line, word_index in self._alignment_label_regions:
                if region.contains(position):
                    self.alignmentUnitSelected.emit(line, word_index)
                    self.setFocus()
                    edge = None
                    if (line, word_index) == self._alignment_selected_key:
                        if abs(position.x() - region.left()) <= 8:
                            edge = "start"
                        elif abs(position.x() - region.right()) <= 8:
                            edge = "end"
                    if edge:
                        self._alignment_drag = (line, word_index, edge, position.x())
                    event.accept()
                    return
        if left <= event.position().x() <= left + width:
            fraction = (event.position().x() - left) / width
            time_min, time_max = self._visible_time_bounds()
            tick = round(time_min + max(0.0, min(1.0, fraction)) * (time_max - time_min))
            self.set_playhead_tick(tick)
            self.seekRequested.emit(tick)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint()
        if self._alignment_drag:
            line, word_index, edge, last_x = self._alignment_drag
            delta = position.x() - last_x
            step_pixels = 14
            steps = int(delta / step_pixels)
            if steps:
                movement = 1 if steps > 0 else -1
                for _ in range(abs(steps)):
                    self.alignmentBoundaryNudgeRequested.emit(
                        line,
                        word_index,
                        edge,
                        movement,
                    )
                self._alignment_drag = (
                    line,
                    word_index,
                    edge,
                    last_x + steps * step_pixels,
                )
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
            return
        if self._alignment_overlay:
            for region, line, word_index in self._alignment_label_regions:
                if not region.contains(position):
                    continue
                annotation = next(
                    (
                        item
                        for item in self._alignment_annotations.values()
                        if item.get("line") == line and item.get("word_index") == word_index
                    ),
                    {},
                )
                near_edge = (line, word_index) == self._alignment_selected_key and (
                    abs(position.x() - region.left()) <= 8
                    or abs(position.x() - region.right()) <= 8
                )
                self.setCursor(
                    Qt.CursorShape.SizeHorCursor
                    if near_edge
                    else Qt.CursorShape.PointingHandCursor
                )
                self.setToolTip(
                    f"{annotation.get('lyric') or '--'}\n"
                    f"{annotation.get('word_note_count') or 1} MIDI note(s)\n"
                    + (
                        "Drag the edge to change duration."
                        if near_edge
                        else "Click to select; drag an edge to change duration."
                    )
                )
                super().mouseMoveEvent(event)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if self._alignment_overlay:
            self.setToolTip(
                "Words are shown above their MIDI notes. Click a word block to adjust its boundary."
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._alignment_drag = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._alignment_overlay and self._alignment_selected_key:
            line, word_index = self._alignment_selected_key
            movement = 0
            if event.key() == Qt.Key.Key_Left:
                movement = -1
            elif event.key() == Qt.Key.Key_Right:
                movement = 1
            if movement:
                edge = (
                    "end"
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    else "start"
                )
                self.alignmentBoundaryNudgeRequested.emit(
                    line,
                    word_index,
                    edge,
                    movement,
                )
                event.accept()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if not self._tracks:
            return super().wheelEvent(event)
        steps = event.angleDelta().y() // 120
        if not steps:
            return
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            left, _, width, _ = self._timeline_rect()
            fraction = (event.position().x() - left) / max(1, width)
            self.zoom_time_by(steps, fraction)
            event.accept()
            return
        if modifiers & Qt.KeyboardModifier.AltModifier:
            if self._pitch_zoom == 0:
                return super().wheelEvent(event)
            visible_min, visible_max = self._visible_pitch_bounds()
            shift = max(1, (visible_max - visible_min + 1) // 6)
            self._pitch_center = max(0, min(127, self._pitch_center - steps * shift))
            self.viewChanged.emit(self.pitch_view_summary())
            self.update()
            event.accept()
            return
        self.pan_time(steps)
        event.accept()
        event.accept()


def write_single_track_preview(
    source_path: Path,
    track_index: int,
    output_path: Path,
) -> Path:
    """Write tempo metadata plus exactly one source track for local preview playback."""

    source = mido.MidiFile(source_path)
    if track_index < 0 or track_index >= len(source.tracks):
        raise MidiPreviewError(f"MIDI track index {track_index} is not present in {source_path.name}.")

    preview = mido.MidiFile(type=1, ticks_per_beat=source.ticks_per_beat)
    # Keep timing metadata from every source track, but never copy conductor
    # note events. Some MIDI files put drums on track zero; copying that track
    # made the Windows sequencer play every source layer in a single-track
    # preview and caused percussion to be interpreted as tonal notes.
    metadata_events: list[tuple[int, int, int, mido.Message]] = []
    source_end_tick = 0
    for source_track_index, track in enumerate(source.tracks):
        absolute_tick = 0
        for event_index, message in enumerate(track):
            absolute_tick += message.time
            if message.is_meta and message.type not in {"track_name", "end_of_track"}:
                metadata_events.append(
                    (absolute_tick, source_track_index, event_index, message.copy(time=0))
                )
        source_end_tick = max(source_end_tick, absolute_tick)

    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Preview timing", time=0))
    previous_tick = 0
    for tick, source_track_index, event_index, message in sorted(metadata_events):
        conductor.append(message.copy(time=max(0, tick - previous_tick)))
        previous_tick = tick
    conductor.append(mido.MetaMessage("end_of_track", time=max(0, source_end_tick - previous_tick)))
    preview.tracks.append(conductor)
    preview.tracks.append(
        mido.MidiTrack(message.copy() for message in source.tracks[track_index])
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)
    return output_path


class WindowsMidiPlayer:
    """Small adapter around the Windows sequencer so no synth dependency is bundled."""

    def __init__(self) -> None:
        self._alias = f"dectalkchoir_{uuid.uuid4().hex[:12]}"
        self._opened = False
        self._paused = False
        self._winmm = ctypes.windll.winmm if os.name == "nt" else None

    @property
    def is_supported(self) -> bool:
        return self._winmm is not None

    @property
    def is_open(self) -> bool:
        return self._opened

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _send(self, command: str, result_length: int = 0) -> str:
        if not self._winmm:
            raise MidiPreviewError("MIDI source preview is available only on Windows.")
        # MCI may return a device string for commands such as ``open``. Passing
        # a one-character buffer for a command whose result we ignore causes
        # Windows to fail with "output string was too large". Query commands
        # opt into a real return buffer; control commands pass NULL/0.
        buffer = ctypes.create_unicode_buffer(result_length) if result_length > 0 else None
        result = self._winmm.mciSendStringW(
            command,
            buffer,
            result_length if buffer is not None else 0,
            0,
        )
        if result:
            error_buffer = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(result, error_buffer, len(error_buffer))
            raise MidiPreviewError(error_buffer.value or f"Windows MCI error {result}")
        return buffer.value if buffer is not None else ""

    def play(self, path: Path) -> None:
        self.close()
        normalized = str(path.resolve()).replace('"', "")
        self._send(f'open "{normalized}" type sequencer alias {self._alias}')
        self._opened = True
        self._paused = False
        self._send(f"set {self._alias} time format milliseconds")
        self._send(f"play {self._alias} from 0")

    def pause_or_resume(self) -> None:
        if not self._opened:
            return
        if self._paused:
            # The Windows MIDI sequencer accepts ``pause`` but commonly does
            # not implement MCI's generic ``resume`` command. Restart from
            # the reported position instead.
            self._send(f"play {self._alias} from {self.position_ms()}")
        else:
            self._send(f"pause {self._alias}")
        self._paused = not self._paused

    def stop(self) -> None:
        if self._opened:
            self._send(f"stop {self._alias}")
            self._paused = False

    def seek(self, position_ms: int) -> None:
        if not self._opened:
            return
        position_ms = max(0, min(position_ms, self.duration_ms()))
        self._send(f"play {self._alias} from {position_ms}")
        self._paused = False

    def position_ms(self) -> int:
        if not self._opened:
            return 0
        value = self._send(f"status {self._alias} position", 32)
        return int(value or 0)

    def duration_ms(self) -> int:
        if not self._opened:
            return 0
        value = self._send(f"status {self._alias} length", 32)
        return int(value or 0)

    def mode(self) -> str:
        if not self._opened:
            return "not ready"
        return self._send(f"status {self._alias} mode", 64).lower()

    def close(self) -> None:
        if self._opened:
            try:
                self._send(f"close {self._alias}")
            except MidiPreviewError:
                pass
        self._opened = False
        self._paused = False
