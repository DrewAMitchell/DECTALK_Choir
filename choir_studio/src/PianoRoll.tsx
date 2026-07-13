import { useEffect, useMemo, useState, type CSSProperties, type PointerEvent, type ReactNode, type WheelEvent } from "react";
import { ChevronLeft, ChevronRight, Crosshair, Search, ZoomIn, ZoomOut } from "lucide-react";
import type { AlignmentEntry, MidiTrack } from "./types";

type SelectedWord = { line: number; wordIndex: number } | null;

type Props = {
  track: MidiTrack | null;
  durationSeconds: number;
  alignment?: AlignmentEntry[];
  selectedPhrase?: number | null;
  selectedWord?: SelectedWord;
  playheadMs?: number | null;
  onSelectPhrase?: (line: number) => void;
  onSelectWord?: (line: number, wordIndex: number) => void;
  onResizeWord?: (edge: "start" | "end", movement: -1 | 1) => void;
  onResizePhrase?: (edge: "start" | "end", movement: -1 | 1) => void;
  onCursorChange?: (milliseconds: number) => void;
  children?: ReactNode;
};

const PLOT_LEFT = 56;
const PLOT_WIDTH = 910;
const WORD_COLORS = ["#64d4ad", "#7faee9", "#f0b96c", "#d99dca", "#a5ca7a", "#76cbc8"];

const pitchName = (pitch: number) => {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
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
  return words.slice(0, 3).join(" ") + (words.length > 3 ? "..." : "");
}

export function PianoRoll({
  track,
  durationSeconds,
  alignment = [],
  selectedPhrase,
  selectedWord,
  playheadMs,
  onSelectPhrase,
  onSelectWord,
  onResizeWord,
  onResizePhrase,
  onCursorChange,
  children,
}: Props) {
  const notes = track?.notes ?? [];
  const durationMs = Math.max(1, durationSeconds * 1000);
  const [timeZoom, setTimeZoom] = useState(0);
  const [timeCenterMs, setTimeCenterMs] = useState(durationMs / 2);
  const [cursorMs, setCursorMs] = useState<number | null>(null);
  const [boundaryDrag, setBoundaryDrag] = useState<{ edge: "start" | "end"; pointerId: number; startX: number; direction: -1 | 1 | null } | null>(null);
  const [phraseDrag, setPhraseDrag] = useState<{ edge: "start" | "end"; pointerId: number; startX: number } | null>(null);
  const bounds = useMemo(() => {
    const pitches = notes.map((note) => note.pitch);
    return { min: Math.min(...pitches, 48) - 1, max: Math.max(...pitches, 60) + 1 };
  }, [notes]);
  const span = Math.max(1, bounds.max - bounds.min + 1);
  const maxTick = Math.max(1, ...notes.map((note) => note.end_tick));
  const entriesByNote = useMemo(() => new Map(alignment.map((entry) => [entry.note_index, entry])), [alignment]);
  const phrases = useMemo(() => {
    const grouped = new Map<number, AlignmentEntry[]>();
    alignment.forEach((entry) => {
      if (entry.line !== null && entry.lyric) grouped.set(entry.line, [...(grouped.get(entry.line) ?? []), entry]);
    });
    return [...grouped.entries()].map(([line, entries]) => ({
      line,
      start: Math.min(...entries.map((entry) => entry.start_ms)),
      end: Math.max(...entries.map((entry) => entry.end_ms)),
      text: entries.filter((entry, index, all) => index === 0 || entry.word_index !== all[index - 1].word_index).map((entry) => entry.lyric).join(" "),
    }));
  }, [alignment]);
  const selectedWordRange = useMemo(() => {
    if (selectedPhrase === null || selectedPhrase === undefined || !selectedWord) return null;
    const current = alignment.filter((entry) => entry.line === selectedPhrase && entry.word_index === selectedWord.wordIndex);
    if (!current.length) return null;
    const previous = alignment.filter((entry) => entry.line === selectedPhrase && entry.word_index === selectedWord.wordIndex - 1);
    const next = alignment.filter((entry) => entry.line === selectedPhrase && entry.word_index === selectedWord.wordIndex + 1);
    return {
      first: Math.min(...current.map((entry) => entry.note_index)),
      last: Math.max(...current.map((entry) => entry.note_index)),
      count: current.length,
      previousCount: previous.length,
      nextCount: next.length,
    };
  }, [alignment, selectedPhrase, selectedWord]);

  const visibleDurationMs = Math.max(1_500, durationMs * (1 - timeZoom * 0.009));
  const lowerBound = Math.max(0, durationMs - visibleDurationMs);
  const viewStartMs = Math.max(0, Math.min(lowerBound, timeCenterMs - visibleDurationMs / 2));
  const viewEndMs = viewStartMs + visibleDurationMs;
  const timelineCursorMs = Math.max(viewStartMs, Math.min(viewEndMs, cursorMs ?? viewStartMs));
  const scaleX = (milliseconds: number) => PLOT_LEFT + ((milliseconds - viewStartMs) / visibleDurationMs) * PLOT_WIDTH;
  const moveCursor = (milliseconds: number) => {
    const next = Math.max(0, Math.min(durationMs, milliseconds));
    setCursorMs(next);
    onCursorChange?.(next);
  };
  const noteTiming = (index: number) => {
    const entry = entriesByNote.get(index + 1);
    if (entry) return { start: entry.start_ms, end: entry.end_ms, entry };
    const note = notes[index];
    return {
      start: (note.start_tick / maxTick) * durationMs,
      end: (note.end_tick / maxTick) * durationMs,
      entry: undefined,
    };
  };

  useEffect(() => {
    setTimeCenterMs(durationMs / 2);
    setTimeZoom(0);
    setCursorMs(null);
  }, [durationMs, track?.index]);
  useEffect(() => {
    const phrase = phrases.find((item) => item.line === selectedPhrase);
    if (phrase) {
      moveCursor(phrase.start);
      setTimeCenterMs((phrase.start + phrase.end) / 2);
    }
  }, [selectedPhrase, phrases]); // The cursor follows deliberate phrase selection.

  const setZoom = (next: number) => setTimeZoom(Math.max(0, Math.min(90, next)));
  const pan = (direction: -1 | 1) => setTimeCenterMs((current) => Math.max(0, Math.min(durationMs, current + direction * visibleDurationMs * 0.55)));
  const beginBoundaryDrag = (event: PointerEvent<SVGRectElement>, noteInWord: number | null, wordNoteCount: number | null) => {
    if (!onResizeWord || noteInWord === null || wordNoteCount === null) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const edge = noteInWord === 1 && noteInWord === wordNoteCount
      ? event.clientX < rect.left + rect.width / 2 ? "start" : "end"
      : noteInWord === 1 ? "start" : noteInWord === wordNoteCount ? "end" : null;
    if (!edge) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setBoundaryDrag({ edge, pointerId: event.pointerId, startX: event.clientX, direction: null });
  };
  const updateBoundaryDrag = (event: PointerEvent<SVGRectElement>) => {
    setBoundaryDrag((current) => {
      if (!current || current.pointerId !== event.pointerId) return current;
      const delta = event.clientX - current.startX;
      return { ...current, direction: Math.abs(delta) > 6 ? (delta < 0 ? -1 : 1) : null };
    });
  };
  const finishBoundaryDrag = (event: PointerEvent<SVGRectElement>) => {
    if (!boundaryDrag || boundaryDrag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const direction = boundaryDrag.direction ?? (event.clientX < boundaryDrag.startX ? -1 : 1);
    const moved = Math.abs(event.clientX - boundaryDrag.startX) > 6;
    setBoundaryDrag(null);
    if (moved) onResizeWord?.(boundaryDrag.edge, direction as -1 | 1);
  };
  const beginPhraseDrag = (event: PointerEvent<SVGGElement>, selected: boolean) => {
    if (!selected || !onResizePhrase) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const edge = event.clientX < rect.left + rect.width / 2 ? "start" : "end";
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPhraseDrag({ edge, pointerId: event.pointerId, startX: event.clientX });
  };
  const finishPhraseDrag = (event: PointerEvent<SVGGElement>) => {
    if (!phraseDrag || phraseDrag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const direction = event.clientX < phraseDrag.startX ? -1 : 1;
    const moved = Math.abs(event.clientX - phraseDrag.startX) > 6;
    setPhraseDrag(null);
    if (moved) onResizePhrase?.(phraseDrag.edge, direction as -1 | 1);
  };
  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      setZoom(timeZoom + (event.deltaY < 0 ? 8 : -8));
      return;
    }
    if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      event.preventDefault();
      pan(event.deltaY > 0 ? 1 : -1);
    }
  };

  if (!track) return <div className="empty-canvas">Choose a note-bearing role from the track rail.</div>;

  return (
    <div className="roll-wrap" onWheel={handleWheel}>
      {children && <div className="roll-phrase-overlay">{children}</div>}
      <div className="roll-controls">
        <div className="roll-window"><Crosshair size={14} /><strong>{cursorMs === null ? "Timeline seek" : formatTime(cursorMs, true)}</strong><span>{formatTime(viewStartMs, true)} - {formatTime(viewEndMs, true)}</span></div>
        <div className="roll-zoom">
          <button type="button" aria-label="Zoom out horizontally" title="Zoom out horizontally" onClick={() => setZoom(timeZoom - 8)}><ZoomOut size={15} /></button>
          <input aria-label="Horizontal time zoom" type="range" min="0" max="90" value={timeZoom} onChange={(event) => setZoom(Number(event.target.value))} />
          <button type="button" aria-label="Zoom in horizontally" title="Zoom in horizontally" onClick={() => setZoom(timeZoom + 8)}><ZoomIn size={15} /></button>
          <button type="button" aria-label="Pan earlier" title="Pan earlier" onClick={() => pan(-1)}><ChevronLeft size={15} /></button>
          <button type="button" aria-label="Pan later" title="Pan later" onClick={() => pan(1)}><ChevronRight size={15} /></button>
          <button type="button" className="fit-time" onClick={() => { setZoom(0); setTimeCenterMs(durationMs / 2); }}><Search size={14} /> Fit</button>
        </div>
      </div>
      <div className="roll-timeline"><div className="timeline-slider" style={{ "--seek-position": `${((timelineCursorMs - viewStartMs) / visibleDurationMs) * 100}%` } as CSSProperties}><output>{formatTime(timelineCursorMs, true)}</output><input type="range" aria-label="Seek within visible timeline" title="Seek within the visible timeline" min={viewStartMs} max={viewEndMs} step="1" value={timelineCursorMs} onChange={(event) => moveCursor(Number(event.target.value))} /></div></div>
      <svg className="piano-roll" viewBox="0 0 1000 500" role="img" aria-label={`${track.name} piano roll`}>
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
          return <g key={phrase.line} className={`phrase ${selected ? "selected" : ""}`} onClick={(event) => { event.stopPropagation(); onSelectPhrase?.(phrase.line); }} onPointerDown={(event) => beginPhraseDrag(event, selected)} onPointerUp={finishPhraseDrag} onPointerCancel={() => setPhraseDrag(null)}>
            <rect x={left} y="28" width={Math.max(28, right - left)} height="32" rx="5" />
            <text x={left + 8} y="49">{phrasePreview(phrase.text)}</text>
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
          const boundaryNote = selectedWordNote && (timing.entry?.note_in_word === 1 || timing.entry?.note_in_word === timing.entry?.word_note_count);
          let previewWordIndex: number | null = null;
          if (boundaryDrag?.direction && selectedWordRange && selectedPhraseNote && selectedWord) {
            const noteNumber = index + 1;
            if (boundaryDrag.edge === "start") {
              if (boundaryDrag.direction < 0 && noteNumber === selectedWordRange.first - 1 && selectedWordRange.previousCount > 1) previewWordIndex = selectedWord.wordIndex;
              if (boundaryDrag.direction > 0 && noteNumber === selectedWordRange.first && selectedWordRange.count > 1) previewWordIndex = selectedWord.wordIndex - 1;
            } else {
              if (boundaryDrag.direction > 0 && noteNumber === selectedWordRange.last + 1 && selectedWordRange.nextCount > 1) previewWordIndex = selectedWord.wordIndex;
              if (boundaryDrag.direction < 0 && noteNumber === selectedWordRange.last && selectedWordRange.count > 1) previewWordIndex = selectedWord.wordIndex + 1;
            }
          }
          const previewing = previewWordIndex !== null;
          const previewReleased = previewing && previewWordIndex !== selectedWord?.wordIndex;
          const colorLine = previewing ? selectedPhrase ?? null : timing.entry?.line ?? null;
          const colorWord = previewing ? previewWordIndex : timing.entry?.word_index ?? null;
          const coloredNote = selectedPhraseNote || previewing;
          return <rect key={`${note.start_tick}-${index}`} className={`midi-note ${coloredNote ? "in-phrase" : ""} ${selectedWordNote && !previewReleased ? "selected-word" : ""} ${boundaryNote ? "boundary-handle" : ""} ${previewing ? previewReleased ? "preview-release" : "preview-claim" : ""}`} style={coloredNote ? { "--word-color": colorFor(colorLine, colorWord) } as CSSProperties : undefined} onClick={(event) => { event.stopPropagation(); if (timing.entry?.line !== null && timing.entry?.line !== undefined && timing.entry?.word_index !== null && timing.entry?.word_index !== undefined) onSelectWord?.(timing.entry.line, timing.entry.word_index); }} onPointerDown={(event) => { if (selectedWordNote) beginBoundaryDrag(event, timing.entry?.note_in_word ?? null, timing.entry?.word_note_count ?? null); }} onPointerMove={updateBoundaryDrag} onPointerUp={finishBoundaryDrag} onPointerCancel={() => setBoundaryDrag(null)} x={x} y={y} width={Math.max(2, right - x)} height={Math.max(7, 350 / span - 3)} rx="2" />;
        })}
        {playheadMs !== null && playheadMs !== undefined && playheadMs >= viewStartMs && playheadMs <= viewEndMs && <line className="playhead-cursor" x1={scaleX(playheadMs)} x2={scaleX(playheadMs)} y1="20" y2="470" />}
        {cursorMs !== null && cursorMs >= viewStartMs && cursorMs <= viewEndMs && <g className="timing-cursor"><line x1={scaleX(cursorMs)} x2={scaleX(cursorMs)} y1="20" y2="470" /><rect x={Math.min(872, Math.max(PLOT_LEFT, scaleX(cursorMs) - 30))} y="462" width="64" height="22" rx="4" /><text x={Math.min(876, Math.max(PLOT_LEFT + 4, scaleX(cursorMs) - 26))} y="477">{formatTime(cursorMs, true)}</text></g>}
      </svg>
      <div className="roll-time"><span>0:00</span><span>{formatTime(durationMs)}</span></div>
    </div>
  );
}
