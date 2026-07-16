import { Component, useEffect, useMemo, useRef, useState, type CSSProperties, type ErrorInfo, type PointerEvent, type ReactNode, type WheelEvent } from "react";
import { ChevronLeft, ChevronRight, Crosshair, ZoomIn, ZoomOut } from "lucide-react";
import type { AlignmentEntry, MidiTrack } from "./types";

type SelectedWord = { line: number; wordIndex: number } | null;

type Props = {
  track: MidiTrack | null;
  durationSeconds: number;
  durationTicks?: number;
  alignment?: AlignmentEntry[];
  selectedPhrase?: number | null;
  selectedWord?: SelectedWord;
  invalidPhraseLines?: number[];
  playheadMs?: number | null;
  playbackPaused?: boolean;
  onSelectPhrase?: (line: number) => void;
  onPlaybackPhraseChange?: (line: number) => void;
  onSelectWord?: (line: number, wordIndex: number) => void;
  onResizeWord?: (edge: "start" | "end", movement: number) => void;
  onResizePhrase?: (edge: "start" | "end", movement: number) => void;
  onAddVirtualSplit?: (noteIndex: number, fraction: number) => void;
  onCursorChange?: (milliseconds: number) => void;
};

type ErrorBoundaryProps = { children: ReactNode };
type ErrorBoundaryState = { error: Error | null };

class PianoRollErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {}

  render() {
    if (this.state.error) {
      return <div className="roll-error" role="alert"><strong>Unable to draw this alignment.</strong><span>{this.state.error.message}</span></div>;
    }
    return this.props.children;
  }
}

const PLOT_LEFT = 44;
const PLOT_WIDTH = 948;
const WORD_COLORS = ["#f29a4b", "#70a8ff", "#e87098", "#a5c95d", "#c08ae8", "#52bfd6"];

const pitchName = (pitch: number) => {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  if (!Number.isFinite(pitch) || !Number.isInteger(pitch) || pitch < 0 || pitch > 127) return "Invalid";
  return `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`;
};

const formatTime = (milliseconds: number, precise = false) => {
  const total = Math.max(0, milliseconds);
  const minutes = Math.floor(total / 60_000);
  const seconds = (total % 60_000) / 1_000;
  return `${minutes}:${seconds.toFixed(precise ? 2 : 0).padStart(precise ? 5 : 2, "0")}`;
};

function colorFor(line: number | null, wordIndex: number | null) {
  return WORD_COLORS[Math.abs((line ?? 0) * 7 + (wordIndex ?? 0)) % WORD_COLORS.length];
}

function phrasePreview(text: string) {
  const words = text.split(/\s+/).filter(Boolean);
  const preview = words.slice(0, 3).join(" ");
  const shortened = preview.length > 26 ? preview.slice(0, 25).trimEnd() : preview;
  return shortened + (preview.length > 26 || words.length > 3 ? "..." : "");
}

function PianoRollCanvas({
  track,
  durationSeconds,
  durationTicks,
  alignment = [],
  selectedPhrase,
  selectedWord,
  invalidPhraseLines = [],
  playheadMs,
  playbackPaused = false,
  onSelectPhrase,
  onPlaybackPhraseChange,
  onSelectWord,
  onResizeWord,
  onResizePhrase,
  onAddVirtualSplit,
  onCursorChange,
}: Props) {
  const sourceNotes = track?.notes ?? [];
  const notes = alignment.length > sourceNotes.length
    ? alignment.map((entry) => ({ start_tick: entry.start_ms, end_tick: entry.end_ms, pitch: entry.midi_pitch, velocity: entry.velocity, channel: 0 }))
    : sourceNotes;
  const durationMs = Math.max(1, durationSeconds * 1000);
  // Always begin with the complete song in view. Phrase selection and playback
  // can center the cursor without unexpectedly changing the user's zoom level.
  const defaultTimeZoom = 0;
  const [timeZoom, setTimeZoom] = useState(defaultTimeZoom);
  const [timeCenterMs, setTimeCenterMs] = useState(durationMs / 2);
  const [cursorMs, setCursorMs] = useState<number | null>(null);
  const [boundaryDrag, setBoundaryDrag] = useState<{ edge: "start" | "end"; pointerId: number; movement: number; targetX: number } | null>(null);
  const [phraseDrag, setPhraseDrag] = useState<{ edge: "start" | "end"; pointerId: number; movement: number; targetX: number; invalid: boolean; missingWords: number } | null>(null);
  const [virtualSplitDrag, setVirtualSplitDrag] = useState<{ pointerId: number; displayIndex: number; fraction: number } | null>(null);
  const canvasPanRef = useRef<{ pointerId: number; startX: number; startCenterMs: number; dragging: boolean } | null>(null);
  const suppressSelectionRef = useRef(false);
  const manualViewportRef = useRef(false);
  const [canvasPanning, setCanvasPanning] = useState(false);
  const [followedPhrase, setFollowedPhrase] = useState<number | null>(null);
  const bounds = useMemo(() => {
    const pitches = notes.map((note) => note.pitch).filter((pitch) => Number.isFinite(pitch));
    return { min: Math.min(...pitches, 48) - 1, max: Math.max(...pitches, 60) + 1 };
  }, [notes]);
  const span = Math.max(1, bounds.max - bounds.min + 1);
  const maxTick = Math.max(1, durationTicks ?? 0, ...sourceNotes.map((note) => note.end_tick));
  const sourceTimeSpan = useMemo(() => {
    if (!sourceNotes.length) return null;
    const start = Math.min(...sourceNotes.map((note) => (note.start_tick / maxTick) * durationMs));
    const end = Math.max(...sourceNotes.map((note) => (note.end_tick / maxTick) * durationMs));
    return { start, end };
  }, [durationMs, maxTick, sourceNotes]);
  const entriesByNote = useMemo(() => new Map(alignment.map((entry) => [entry.note_index, entry])), [alignment]);
  const phrases = useMemo(() => {
    const grouped = new Map<number, AlignmentEntry[]>();
    alignment.forEach((entry) => {
      if (entry.line !== null && entry.lyric) grouped.set(entry.line, [...(grouped.get(entry.line) ?? []), entry]);
    });
    return [...grouped.entries()].map(([line, entries]) => {
      const ordered = [...entries].sort((left, right) => left.note_index - right.note_index);
      const wordCounts = new Map<number, number>();
      ordered.forEach((entry) => wordCounts.set(entry.word_index ?? 0, (wordCounts.get(entry.word_index ?? 0) ?? 0) + 1));
      return {
        line,
        start: Math.min(...ordered.map((entry) => entry.start_ms)),
        end: Math.max(...ordered.map((entry) => entry.end_ms)),
        first: ordered[0].note_index,
        last: ordered[ordered.length - 1].note_index,
        wordCounts: [...wordCounts.values()],
        text: ordered.filter((entry, index, all) => index === 0 || entry.word_index !== all[index - 1].word_index).map((entry) => entry.lyric).join(" "),
      };
    });
  }, [alignment]);
  const wordRanges = useMemo(() => {
    const grouped = new Map<string, AlignmentEntry[]>();
    alignment.forEach((entry) => {
      if (entry.line !== null && entry.word_index !== null && entry.lyric) {
        const key = `${entry.line}:${entry.word_index}`;
        grouped.set(key, [...(grouped.get(key) ?? []), entry]);
      }
    });
    return [...grouped.values()].map((entries) => ({
      line: entries[0].line!,
      wordIndex: entries[0].word_index!,
      first: Math.min(...entries.map((entry) => entry.note_index)),
      last: Math.max(...entries.map((entry) => entry.note_index)),
      count: entries.length,
    })).sort((left, right) => left.first - right.first);
  }, [alignment]);
  const selectedWordRange = useMemo(() => {
    if (selectedPhrase === null || selectedPhrase === undefined || !selectedWord) return null;
    const selectedIndex = wordRanges.findIndex((item) => item.line === selectedPhrase && item.wordIndex === selectedWord.wordIndex);
    if (selectedIndex < 0) return null;
    const current = wordRanges[selectedIndex];
    const previousAvailable = wordRanges.slice(0, selectedIndex).reduce((total, item) => total + Math.max(0, item.count - 1), 0);
    const followingAvailable = wordRanges.slice(selectedIndex + 1).reduce((total, item) => total + Math.max(0, item.count - 1), 0);
    const unassignedTail = alignment.filter((entry) => entry.line === null && entry.note_index > current.last).length;
    return { ...current, previousAvailable, followingAvailable, unassignedTail };
  }, [alignment, selectedPhrase, selectedWord, wordRanges]);
  const visibleDurationMs = Math.max(1_500, durationMs * (1 - timeZoom * 0.009));
  const lowerBound = Math.max(0, durationMs - visibleDurationMs);
  const viewStartMs = Math.max(0, Math.min(lowerBound, timeCenterMs - visibleDurationMs / 2));
  const viewEndMs = viewStartMs + visibleDurationMs;
  const scaleX = (milliseconds: number) => PLOT_LEFT + ((milliseconds - viewStartMs) / visibleDurationMs) * PLOT_WIDTH;
  const moveCursor = (milliseconds: number) => {
    const next = Math.max(0, Math.min(durationMs, milliseconds));
    setCursorMs(next);
    onCursorChange?.(next);
  };
  const selectPhrase = (line: number) => {
    const phrase = phrases.find((item) => item.line === line);
    if (!phrase) return;
    manualViewportRef.current = false;
    onSelectPhrase?.(line);
    setTimeCenterMs((phrase.start + phrase.end) / 2);
    moveCursor(phrase.start);
  };
  const noteTiming = (index: number) => {
    if (!notes.length) return { start: 0, end: 0, entry: undefined };
    const safeIndex = Math.max(0, Math.min(notes.length - 1, index));
    const entry = entriesByNote.get(safeIndex + 1);
    if (entry) return { start: entry.start_ms, end: entry.end_ms, entry };
    const note = notes[safeIndex];
    return {
      start: alignment.length > sourceNotes.length ? note.start_tick : (note.start_tick / maxTick) * durationMs,
      end: alignment.length > sourceNotes.length ? note.end_tick : (note.end_tick / maxTick) * durationMs,
      entry: undefined,
    };
  };
  const selectedWordAnchor = (() => {
    if (!selectedWord) return null;
    const entries = alignment.filter((entry) => entry.line === selectedWord.line && entry.word_index === selectedWord.wordIndex);
    if (!entries.length) return null;
    let left = Number.POSITIVE_INFINITY;
    let right = Number.NEGATIVE_INFINITY;
    let top = Number.POSITIVE_INFINITY;
    for (const entry of entries) {
      const note = notes[entry.note_index - 1];
      if (!note) continue;
      const timing = noteTiming(entry.note_index - 1);
      if (timing.end < viewStartMs || timing.start > viewEndMs) continue;
      left = Math.min(left, Math.max(PLOT_LEFT, scaleX(timing.start)));
      right = Math.max(right, Math.min(PLOT_LEFT + PLOT_WIDTH, scaleX(timing.end)));
      top = Math.min(top, 100 + ((bounds.max - note.pitch) / span) * 360 + 3);
    }
    if (!Number.isFinite(left) || !Number.isFinite(right) || !Number.isFinite(top)) return null;
    const label = entries[0].lyric ?? "Selected word";
    const width = Math.min(170, Math.max(36, label.length * 6.5 + 14));
    return { label, width, x: Math.max(PLOT_LEFT, Math.min(PLOT_LEFT + PLOT_WIDTH - width, (left + right - width) / 2)), y: Math.max(96, top - 8) };
  })();
  const boundaryTargets = (edge: "start" | "end") => {
    if (!selectedWordRange) return [];
    const minimum = edge === "start" ? -selectedWordRange.previousAvailable : -(selectedWordRange.count - 1);
    const maximum = edge === "start" ? selectedWordRange.previousAvailable ? selectedWordRange.count - 1 : 0 : selectedWordRange.followingAvailable + selectedWordRange.unassignedTail;
    return Array.from({ length: maximum - minimum + 1 }, (_, index) => {
      const movement = minimum + index;
      const noteIndex = edge === "start"
        ? selectedWordRange.first + movement - 1
        : selectedWordRange.last + movement - 1;
      return { movement, x: scaleX(edge === "start" ? noteTiming(noteIndex).start : noteTiming(noteIndex).end) };
    });
  };
  const snappedBoundary = (event: PointerEvent<SVGRectElement>, edge: "start" | "end") => {
    const svgRect = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!svgRect) return { movement: 0, targetX: 0 };
    const pointerX = ((event.clientX - svgRect.left) / svgRect.width) * 1000;
    const target = boundaryTargets(edge).reduce((closest, candidate) => Math.abs(candidate.x - pointerX) < Math.abs(closest.x - pointerX) ? candidate : closest);
    return { movement: target.movement, targetX: target.x };
  };
  const selectedPhraseRange = useMemo(() => phrases.find((phrase) => phrase.line === selectedPhrase) ?? null, [phrases, selectedPhrase]);
  const phraseBoundaryTargets = (edge: "start" | "end") => {
    if (!selectedPhraseRange || !notes.length || selectedPhraseRange.first < 1 || selectedPhraseRange.last > notes.length) return [];
    const previousWords = phrases.filter((phrase) => phrase.line < selectedPhraseRange.line).flatMap((phrase) => phrase.wordCounts);
    const followingWords = phrases.filter((phrase) => phrase.line > selectedPhraseRange.line).flatMap((phrase) => phrase.wordCounts);
    const unassignedTail = alignment.filter((entry) => entry.line === null && entry.note_index > selectedPhraseRange.last).length;
    const firstWordCount = selectedPhraseRange.wordCounts[0] ?? 1;
    const lastWordCount = selectedPhraseRange.wordCounts[selectedPhraseRange.wordCounts.length - 1] ?? 1;
    const validMinimum = edge === "start" ? -previousWords.reduce((total, count) => total + Math.max(0, count - 1), 0) : -(lastWordCount - 1);
    const validMaximum = edge === "start" ? firstWordCount - 1 : followingWords.reduce((total, count) => total + Math.max(0, count - 1), 0) + unassignedTail;
    // Invalid targets are preview-only. The renderer cannot encode a zero-note word.
    const minimum = edge === "start" ? -(selectedPhraseRange.first - 1) : validMinimum;
    const maximum = edge === "end" ? notes.length - selectedPhraseRange.last : validMaximum;
    return Array.from({ length: Math.max(0, maximum - minimum) + 1 }, (_, index) => {
      const movement = minimum + index;
      const noteIndex = Math.max(0, Math.min(notes.length - 1, edge === "start"
        ? selectedPhraseRange.first + movement - 1
        : selectedPhraseRange.last + movement - 1));
      const invalid = movement < validMinimum || movement > validMaximum;
      const excess = movement < validMinimum ? validMinimum - movement : Math.max(0, movement - validMaximum);
      return {
        movement,
        x: scaleX(edge === "start" ? noteTiming(noteIndex).start : noteTiming(noteIndex).end),
        invalid,
        missingWords: invalid ? excess : 0,
      };
    });
  };
  const snappedPhraseBoundary = (event: PointerEvent<SVGRectElement>, edge: "start" | "end") => {
    const svgRect = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    const targets = phraseBoundaryTargets(edge);
    if (!svgRect || !targets.length) return { movement: 0, targetX: 0, invalid: false, missingWords: 0 };
    const pointerX = ((event.clientX - svgRect.left) / svgRect.width) * 1000;
    const target = targets.reduce((closest, candidate) => Math.abs(candidate.x - pointerX) < Math.abs(closest.x - pointerX) ? candidate : closest);
    return { movement: target.movement, targetX: target.x, invalid: target.invalid, missingWords: target.missingWords };
  };

  useEffect(() => {
    setTimeCenterMs(sourceTimeSpan ? (sourceTimeSpan.start + sourceTimeSpan.end) / 2 : durationMs / 2);
    setTimeZoom(defaultTimeZoom);
    setCursorMs(null);
    setFollowedPhrase(null);
  }, [alignment.length, defaultTimeZoom, durationMs, sourceTimeSpan?.end, sourceTimeSpan?.start, track?.index]);
  useEffect(() => {
    const selected = phrases.find((phrase) => phrase.line === selectedPhrase);
    if (selected) setTimeCenterMs((selected.start + selected.end) / 2);
  }, [phrases, selectedPhrase]);
  useEffect(() => {
    if (playheadMs !== null && playheadMs !== undefined) setCursorMs(playheadMs);
  }, [playheadMs]);
  useEffect(() => {
    if (playheadMs === null || playheadMs === undefined) {
      setFollowedPhrase(null);
      return;
    }
    if (playbackPaused) {
      setFollowedPhrase(null);
      return;
    }
    const activePhrase = phrases.find((phrase) => playheadMs >= phrase.start && playheadMs <= phrase.end);
    if (activePhrase) {
      if (activePhrase.line !== followedPhrase) {
        manualViewportRef.current = false;
        setFollowedPhrase(activePhrase.line);
        onPlaybackPhraseChange?.(activePhrase.line);
      }
      if (!manualViewportRef.current && (activePhrase.start < viewStartMs || activePhrase.end > viewEndMs)) setTimeCenterMs((activePhrase.start + activePhrase.end) / 2);
      return;
    }
    if (!activePhrase) {
      setFollowedPhrase(null);
      if (!manualViewportRef.current && (playheadMs < viewStartMs || playheadMs > viewEndMs)) setTimeCenterMs(playheadMs);
    }
  }, [playheadMs, playbackPaused, phrases, followedPhrase, viewStartMs, viewEndMs, onPlaybackPhraseChange]);

  const setZoom = (next: number) => setTimeZoom(Math.max(0, Math.min(90, next)));
  const pan = (direction: -1 | 1) => {
    manualViewportRef.current = true;
    setTimeCenterMs((current) => Math.max(0, Math.min(durationMs, current + direction * visibleDurationMs * 0.55)));
  };
  const consumeSuppressedSelection = () => {
    if (!suppressSelectionRef.current) return false;
    suppressSelectionRef.current = false;
    return true;
  };
  const beginCanvasPan = (event: PointerEvent<SVGSVGElement>) => {
    const target = event.target as Element;
    // Boundary handles and Ctrl-drag splits own their gestures. Other surfaces use a
    // drag threshold so a click selects a phrase/note while a drag always pans.
    if (event.button !== 0 || target.closest(".word-boundary-region, .phrase-boundary-region")) return;
    canvasPanRef.current = { pointerId: event.pointerId, startX: event.clientX, startCenterMs: timeCenterMs, dragging: false };
  };
  const updateCanvasPan = (event: PointerEvent<SVGSVGElement>) => {
    const canvasPan = canvasPanRef.current;
    if (!canvasPan || canvasPan.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const deltaX = event.clientX - canvasPan.startX;
    if (!canvasPan.dragging && Math.abs(deltaX) < 4) return;
    if (!canvasPan.dragging) {
      canvasPan.dragging = true;
      suppressSelectionRef.current = true;
      manualViewportRef.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setCanvasPanning(true);
    }
    event.preventDefault();
    const deltaMs = -(deltaX / rect.width) * visibleDurationMs;
    setTimeCenterMs(Math.max(0, Math.min(durationMs, canvasPan.startCenterMs + deltaMs)));
  };
  const finishCanvasPan = (event: PointerEvent<SVGSVGElement>) => {
    const canvasPan = canvasPanRef.current;
    if (!canvasPan || canvasPan.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    canvasPanRef.current = null;
    setCanvasPanning(false);
    if (canvasPan.dragging) window.setTimeout(() => { suppressSelectionRef.current = false; }, 0);
  };
  const beginBoundaryDrag = (event: PointerEvent<SVGRectElement>, edge: "start" | "end") => {
    if (!onResizeWord || !selectedWordRange) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const target = snappedBoundary(event, edge);
    setBoundaryDrag({ edge, pointerId: event.pointerId, ...target });
  };
  const updateBoundaryDrag = (event: PointerEvent<SVGRectElement>) => {
    const target = snappedBoundary(event, boundaryDrag?.edge ?? "start");
    const pointerId = event.pointerId;
    setBoundaryDrag((current) => {
      if (!current || current.pointerId !== pointerId) return current;
      return { ...current, ...target };
    });
  };
  const finishBoundaryDrag = (event: PointerEvent<SVGRectElement>) => {
    if (!boundaryDrag || boundaryDrag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setBoundaryDrag(null);
    if (boundaryDrag.movement) onResizeWord?.(boundaryDrag.edge, boundaryDrag.movement);
  };
  const beginPhraseDrag = (event: PointerEvent<SVGRectElement>, edge: "start" | "end") => {
    if (!onResizePhrase || !selectedPhraseRange) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPhraseDrag({ edge, pointerId: event.pointerId, ...snappedPhraseBoundary(event, edge) });
  };
  const updatePhraseDrag = (event: PointerEvent<SVGRectElement>) => {
    const target = snappedPhraseBoundary(event, phraseDrag?.edge ?? "start");
    const pointerId = event.pointerId;
    setPhraseDrag((current) => {
      if (!current || current.pointerId !== pointerId) return current;
      return { ...current, ...target };
    });
  };
  const finishPhraseDrag = (event: PointerEvent<SVGRectElement>) => {
    if (!phraseDrag || phraseDrag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setPhraseDrag(null);
    if (phraseDrag.movement) onResizePhrase?.(phraseDrag.edge, phraseDrag.movement);
  };
  const finishVirtualSplit = (event: PointerEvent<SVGRectElement>) => {
    if (!virtualSplitDrag || virtualSplitDrag.pointerId !== event.pointerId || !onAddVirtualSplit || !sourceNotes.length) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const fraction = virtualSplitDrag.fraction;
    const timing = noteTiming(virtualSplitDrag.displayIndex);
    const sourceIndex = sourceNotes.findIndex((note) => {
      const start = (note.start_tick / maxTick) * durationMs;
      const end = (note.end_tick / maxTick) * durationMs;
      return timing.start >= start - 1 && timing.end <= end + 1;
    });
    setVirtualSplitDrag(null);
    if (sourceIndex >= 0) onAddVirtualSplit(sourceIndex + 1, fraction);
  };
  const updateVirtualSplit = (event: PointerEvent<SVGRectElement>) => {
    if (!virtualSplitDrag || virtualSplitDrag.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = Math.max(0.05, Math.min(0.95, (event.clientX - rect.left) / rect.width));
    setVirtualSplitDrag((current) => current && current.pointerId === event.pointerId ? { ...current, fraction } : current);
  };
  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      setZoom(timeZoom + (event.deltaY < 0 ? 8 : -8));
      return;
    }
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    if (!delta) return;
    event.preventDefault();
    manualViewportRef.current = true;
    const width = Math.max(1, event.currentTarget.getBoundingClientRect().width);
    setTimeCenterMs((current) => Math.max(0, Math.min(durationMs, current + (delta / width) * visibleDurationMs)));
  };

  if (!track) return <div className="empty-canvas">Choose a note-bearing role from the track rail.</div>;
  if (!notes.length) return <div className="empty-canvas">{track.name} has no paired MIDI notes to display.</div>;

  return (
    <div className="roll-wrap" onWheel={handleWheel}>
      <div className="roll-controls">
        <div className="roll-window" title="Drag empty MIDI space to pan. Ctrl + wheel changes horizontal zoom."><Crosshair size={14} /><strong>{cursorMs === null ? "No marker" : formatTime(cursorMs, true)}</strong><span>{formatTime(viewStartMs, true)} - {formatTime(viewEndMs, true)} · {formatTime(durationMs, true)} total</span><em>Drag canvas to pan</em></div>
        <div className="roll-zoom">
          <button type="button" aria-label="Zoom out horizontally" title="Zoom out horizontally" onClick={() => setZoom(timeZoom - 8)}><ZoomOut size={15} /></button>
          <input aria-label="Horizontal time zoom" type="range" min="0" max="90" value={timeZoom} onChange={(event) => setZoom(Number(event.target.value))} />
          <button type="button" aria-label="Zoom in horizontally" title="Zoom in horizontally" onClick={() => setZoom(timeZoom + 8)}><ZoomIn size={15} /></button>
        </div>
      </div>
      <div className="roll-pan-controls" aria-label="Timeline pan controls"><button type="button" aria-label="Pan MIDI view earlier" title="Pan MIDI view earlier" onClick={() => pan(-1)}><ChevronLeft size={17} /></button><button type="button" aria-label="Pan MIDI view later" title="Pan MIDI view later" onClick={() => pan(1)}><ChevronRight size={17} /></button></div>
      <svg className={canvasPanning ? "piano-roll panning" : "piano-roll"} viewBox="0 0 1000 500" preserveAspectRatio="none" overflow="hidden" role="img" aria-label={`${track.name} piano roll`} onPointerDown={beginCanvasPan} onPointerMove={updateCanvasPan} onPointerUp={finishCanvasPan} onPointerCancel={() => { canvasPanRef.current = null; setCanvasPanning(false); }}>
        <rect width="1000" height="500" className="roll-bg" />
        {Array.from({ length: span }, (_, index) => {
          const pitch = bounds.min + index;
          const y = 100 + ((bounds.max - pitch) / span) * 360;
          return <g key={pitch}><line x1="0" x2="1000" y1={y} y2={y} className={pitch % 12 === 0 ? "octave-line" : "pitch-line"} /><text x="12" y={y - 4} className="pitch-label">{pitchName(pitch)}</text></g>;
        })}
        {Array.from({ length: 9 }, (_, index) => {
          const milliseconds = viewStartMs + (visibleDurationMs / 8) * index;
          const x = PLOT_LEFT + (PLOT_WIDTH / 8) * index;
          return <g key={index}><line x1={x} x2={x} y1="80" y2="470" className="time-line" /><text x={x + 3} y="91" className="time-label">{formatTime(milliseconds, true)}</text></g>;
        })}
        {phrases.filter((phrase) => phrase.end >= viewStartMs && phrase.start <= viewEndMs).map((phrase) => {
          const selected = selectedPhrase === phrase.line;
          const left = Math.max(PLOT_LEFT, scaleX(phrase.start));
          const right = Math.min(PLOT_LEFT + PLOT_WIDTH, scaleX(phrase.end));
          const invalid = invalidPhraseLines.includes(phrase.line);
          return <g key={phrase.line} className={`phrase ${selected ? "selected" : ""} ${invalid ? "invalid" : ""}`} onClick={(event) => { event.stopPropagation(); if (!consumeSuppressedSelection()) selectPhrase(phrase.line); }}>
            <rect x={left} y="28" width={Math.max(28, right - left)} height="32" rx="5" />
            <clipPath id={`phrase-label-${phrase.line}`}><rect x={left + 5} y="30" width={Math.max(0, right - left - 10)} height="28" /></clipPath>
            <text x={left + 8} y="49" clipPath={`url(#phrase-label-${phrase.line})`}>{invalid ? `! ${phrasePreview(phrase.text)}` : phrasePreview(phrase.text)}</text>
            <title>{phrase.text}</title>
          </g>;
        })}
        {notes.map((note, index) => {
          const timing = noteTiming(index);
          if (timing.end < viewStartMs || timing.start > viewEndMs) return null;
          const x = Math.max(PLOT_LEFT, scaleX(timing.start));
          const right = Math.min(PLOT_LEFT + PLOT_WIDTH, scaleX(timing.end));
          const y = 100 + ((bounds.max - note.pitch) / span) * 360 + 3;
          const selectedPhraseNote = timing.entry?.line === selectedPhrase;
          const selectedWordNote = selectedPhraseNote && timing.entry?.word_index === selectedWord?.wordIndex;
          let previewWordIndex: number | null = null;
          let previewPhrase = false;
          let previewPhraseReleased = false;
          if (boundaryDrag && boundaryDrag.movement && selectedWordRange && selectedPhraseNote && selectedWord) {
            const noteNumber = index + 1;
            if (boundaryDrag.edge === "start") {
              if (boundaryDrag.movement < 0 && noteNumber >= selectedWordRange.first + boundaryDrag.movement && noteNumber < selectedWordRange.first) previewWordIndex = selectedWord.wordIndex;
              if (boundaryDrag.movement > 0 && noteNumber >= selectedWordRange.first && noteNumber < selectedWordRange.first + boundaryDrag.movement) previewWordIndex = selectedWord.wordIndex - 1;
            } else {
              if (boundaryDrag.movement > 0 && noteNumber > selectedWordRange.last && noteNumber <= selectedWordRange.last + boundaryDrag.movement) previewWordIndex = selectedWord.wordIndex;
              if (boundaryDrag.movement < 0 && noteNumber > selectedWordRange.last + boundaryDrag.movement && noteNumber <= selectedWordRange.last) previewWordIndex = selectedWord.wordIndex + 1;
            }
          }
          if (phraseDrag && phraseDrag.movement && selectedPhraseRange) {
            const noteNumber = index + 1;
            if (phraseDrag.edge === "start") {
              if (phraseDrag.movement < 0 && noteNumber >= selectedPhraseRange.first + phraseDrag.movement && noteNumber < selectedPhraseRange.first) previewPhrase = true;
              if (phraseDrag.movement > 0 && noteNumber >= selectedPhraseRange.first && noteNumber < selectedPhraseRange.first + phraseDrag.movement) { previewPhrase = true; previewPhraseReleased = true; }
            } else {
              if (phraseDrag.movement > 0 && noteNumber > selectedPhraseRange.last && noteNumber <= selectedPhraseRange.last + phraseDrag.movement) previewPhrase = true;
              if (phraseDrag.movement < 0 && noteNumber > selectedPhraseRange.last + phraseDrag.movement && noteNumber <= selectedPhraseRange.last) { previewPhrase = true; previewPhraseReleased = true; }
            }
          }
          const previewing = previewWordIndex !== null;
          const previewReleased = previewing && previewWordIndex !== selectedWord?.wordIndex;
          const colorLine = previewing || previewPhrase ? selectedPhrase ?? null : timing.entry?.line ?? null;
          const colorWord = previewing ? previewWordIndex : timing.entry?.word_index ?? null;
          const coloredNote = selectedPhraseNote || previewing || previewPhrase;
          const previewClass = previewing ? previewReleased ? "preview-release" : "preview-claim" : previewPhrase ? previewPhraseReleased ? "preview-release" : "preview-claim" : "";
          const splitting = virtualSplitDrag?.displayIndex === index;
          return <g key={`${note.start_tick}-${index}`}><rect className={`midi-note ${coloredNote ? "in-phrase" : ""} ${selectedWordNote && !previewReleased ? "selected-word" : ""} ${previewClass}`} style={coloredNote ? { "--word-color": colorFor(colorLine, colorWord) } as CSSProperties : undefined} onPointerDown={(event) => { if (event.ctrlKey) { event.stopPropagation(); const rect = event.currentTarget.getBoundingClientRect(); const fraction = Math.max(0.05, Math.min(0.95, (event.clientX - rect.left) / rect.width)); event.currentTarget.setPointerCapture(event.pointerId); setVirtualSplitDrag({ pointerId: event.pointerId, displayIndex: index, fraction }); } }} onPointerMove={updateVirtualSplit} onPointerUp={finishVirtualSplit} onPointerCancel={() => setVirtualSplitDrag(null)} onClick={(event) => { event.stopPropagation(); if (consumeSuppressedSelection()) return; if (timing.entry?.line !== null && timing.entry?.line !== undefined && timing.entry?.word_index !== null && timing.entry?.word_index !== undefined) onSelectWord?.(timing.entry.line, timing.entry.word_index); }} x={x} y={y} width={Math.max(2, right - x)} height={Math.max(7, 350 / span - 3)} rx="2"><title>Ctrl + drag within a note to create a virtual lyric split</title></rect>{splitting && <line className="virtual-split-preview" x1={x + Math.max(2, right - x) * virtualSplitDrag.fraction} x2={x + Math.max(2, right - x) * virtualSplitDrag.fraction} y1={y} y2={y + Math.max(7, 350 / span - 3)} />}</g>;
        })}
        {selectedWordAnchor && <g className="selected-word-label"><rect x={selectedWordAnchor.x} y={selectedWordAnchor.y - 13} width={selectedWordAnchor.width} height="17" rx="3" /><text x={selectedWordAnchor.x + 7} y={selectedWordAnchor.y}>{selectedWordAnchor.label}</text><title>{selectedWordAnchor.label}</title></g>}
        {selectedPhraseRange && (["start", "end"] as const).map((edge) => {
          const target = phraseBoundaryTargets(edge).find((item) => item.movement === 0);
          if (!target) return null;
          return <rect key={`phrase-${edge}`} className={`phrase-boundary-region ${edge}`} x={target.x - 11} y="22" width="22" height="448" onPointerDown={(event) => beginPhraseDrag(event, edge)} onPointerMove={updatePhraseDrag} onPointerUp={finishPhraseDrag} onPointerCancel={() => setPhraseDrag(null)}><title>Drag to move this phrase edge across MIDI notes. Red targets create a temporary word-without-note review state.</title></rect>;
        })}
        {selectedWordRange && (["start", "end"] as const).map((edge) => {
          const target = boundaryTargets(edge).find((item) => item.movement === 0);
          if (!target) return null;
          return <rect key={edge} className={`word-boundary-region ${edge}`} x={target.x - 10} y="78" width="20" height="392" onPointerDown={(event) => beginBoundaryDrag(event, edge)} onPointerMove={updateBoundaryDrag} onPointerUp={finishBoundaryDrag} onPointerCancel={() => setBoundaryDrag(null)}><title>Drag to snap this word boundary across MIDI notes</title></rect>;
        })}
        {boundaryDrag && boundaryDrag.movement !== 0 && <g className="boundary-target"><line x1={boundaryDrag.targetX} x2={boundaryDrag.targetX} y1="70" y2="470" /><rect x={Math.max(PLOT_LEFT, boundaryDrag.targetX - 23)} y="72" width="46" height="18" rx="3" /><text x={Math.max(PLOT_LEFT + 4, boundaryDrag.targetX - 18)} y="85">{boundaryDrag.movement > 0 ? "+" : ""}{boundaryDrag.movement}</text></g>}
        {phraseDrag && phraseDrag.movement !== 0 && <g className={`boundary-target phrase-target ${phraseDrag.invalid ? "invalid" : ""}`}><line x1={phraseDrag.targetX} x2={phraseDrag.targetX} y1="20" y2="470" /><rect x={Math.max(PLOT_LEFT, phraseDrag.targetX - 30)} y="22" width="60" height="18" rx="3" /><text x={Math.max(PLOT_LEFT + 4, phraseDrag.targetX - 25)} y="35">{phraseDrag.invalid ? `${phraseDrag.missingWords} pending` : `${phraseDrag.movement > 0 ? "+" : ""}${phraseDrag.movement} notes`}</text></g>}
        {playheadMs !== null && playheadMs !== undefined && playheadMs >= viewStartMs && playheadMs <= viewEndMs && <line className="playhead-cursor" x1={scaleX(playheadMs)} x2={scaleX(playheadMs)} y1="20" y2="470" />}
        {cursorMs !== null && cursorMs >= viewStartMs && cursorMs <= viewEndMs && <g className="timing-cursor"><line x1={scaleX(cursorMs)} x2={scaleX(cursorMs)} y1="20" y2="470" /><rect x={Math.min(872, Math.max(PLOT_LEFT, scaleX(cursorMs) - 30))} y="462" width="64" height="22" rx="4" /><text x={Math.min(876, Math.max(PLOT_LEFT + 4, scaleX(cursorMs) - 26))} y="477">{formatTime(cursorMs, true)}</text></g>}
      </svg>
    </div>
  );
}

export function PianoRoll(props: Props) {
  return <PianoRollErrorBoundary><PianoRollCanvas {...props} /></PianoRollErrorBoundary>;
}
