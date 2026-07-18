import { useCallback, useEffect, useMemo, useRef, useState, useTransition, type CSSProperties, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { ArrowLeft, BarChart3, Check, ChevronsLeft, ChevronsRight, CircleAlert, CircleCheck, FileAudio, FileInput, FolderOpen, GitBranch, Inbox, Info, LoaderCircle, MessageSquareText, Minus, Moon, Music2, Music3, PanelLeft, Pause, PenLine, Play, Plus, Settings2, Sparkles, Square, Sun, Trash2, WandSparkles, X } from "lucide-react";
import { bridge, deleteSong, media, openFfmpegDownload, openMedia, openSongFolder, renderJobStatus, spectrogramJobStatus, startRenderJob, startSpectrogramJob, type MediaStatus, type RenderJobStatus, type SpectrogramJobStatus } from "./bridge";
import { PianoRoll } from "./PianoRoll";
import type { AlignmentReport, Role, SongInspection } from "./types";
import choirStudioMark from "./assets/choir-studio-mark.svg";

type Stage = "align" | "review";
const stages: Array<[Stage, string, typeof Music2]> = [["align", "Align", WandSparkles], ["review", "Render Audio", BarChart3]];
const lyricEditorTips = [
  { lead: "Use near-homonyms.", message: <>Sound spelling can improve pronunciation: try <code>frir</code> for <q>for</q>, or <code>uh</code> when <q>a</q> sounds too sharp.</> },
  { lead: "Split difficult words.", message: <>Break complex words into simpler sound-alike chunks; for example, try <code>a quaint ants</code> when <code>acquaintance</code> is unclear.</> },
  { lead: "Fix MIDI structure externally.", message: <>If two sung syllables need distinct notes but the MIDI has one sustained note, split it in a MIDI editor. Align only assigns lyrics to existing notes.</> },
  { lead: "Avoid extremely short notes.", message: <>Tiny MIDI notes leave too little time for DECTALK to pronounce a phoneme cleanly. Lengthen or merge those notes in a MIDI editor before aligning lyrics.</> },
];
const lyricTipIntervals = [10000, 6500, 4000];
const FFMPEG_WINGET_COMMAND = "winget install --id Gyan.FFmpeg.Shared --exact";
const UI_STATE_KEY = "dectalk-choir-studio.ui-state";
type StoredUiState = { song?: string; role?: string; stage?: Stage; theme?: "dark" | "light"; render_roles?: Record<string, string[]> };

function readStoredUiState(): StoredUiState {
  try {
    const value = JSON.parse(window.localStorage.getItem(UI_STATE_KEY) ?? "{}") as Record<string, unknown>;
    return {
      song: typeof value.song === "string" ? value.song : undefined,
      role: typeof value.role === "string" ? value.role : undefined,
      stage: stages.some(([id]) => id === value.stage) ? value.stage as Stage : undefined,
      theme: value.theme === "light" || value.theme === "dark" ? value.theme : undefined,
      render_roles: typeof value.render_roles === "object" && value.render_roles !== null
        ? Object.fromEntries(Object.entries(value.render_roles).filter(([, roles]) => Array.isArray(roles) && roles.every((role) => typeof role === "string"))) as Record<string, string[]>
        : undefined,
    };
  } catch {
    return {};
  }
}

const storedUiState = readStoredUiState();
type ReviewSegment = { line: number; word_index: number; word: string; note_count: number; start_ms: number; end_ms: number; largest_internal_gap_ms: number };
type DraftState = { text: string; path: string; warnings: string[]; review_segments: ReviewSegment[]; tight_gap_ms: number };
type TranscriptState = { text: string; kind: "alignment" | "transcript"; transcript_exists: boolean };
type AlignmentState = { report: AlignmentReport; text: string; source_in_sync?: boolean };
type CandidateState = { exists: boolean; text?: string; path?: string; report?: AlignmentReport; source_in_sync?: boolean };
type AlignmentWorkspace = { candidate: CandidateState; templates: AlignmentTemplate[] };
type NoteSkeleton = { text: string; note_count: number };
type AlignmentTemplate = { role: string; path: string };
type SplitLane = { number: number; name: string; note_count: number; minimum_pitch: number | null; maximum_pitch: number | null };
type SplitPreview = { source_path: string; source_name: string; track_index: number; note_count: number; max_polyphony: number; default_filename: string; lanes: SplitLane[]; splittable: boolean };
type SplitResult = { path: string; summary_path: string; backup_path: string | null; replaced_source: boolean; lanes: SplitLane[]; warning: string | null };
type DectalkImportResult = { role: string; note_count: number; duration_ms: number; midi_path: string; source_path: string; alignment_path: string };
type MidiSongImportResult = { song: string; roles: string[]; midi_path: string };
type TrackTuning = {
  VOICE: string | null;
  HEAD_SIZE: number | null;
  PITCH_SHIFT: number;
  OCTAVE_BOOST: number;
  PITCH_WRAP_SHIFT: number | null;
  VOLUME_ADJUST_DB: number;
  IGNORE_MIDI_VELOCITY: boolean;
  VELOCITY_VOLUME_SCALE_DB: number;
  STEM_PEAK_CEILING_DBFS: number;
  GAP_MEND_MS: number;
  MINIMUM_NOTE_DURATION_MS: number;
  CODA_MAX_MS: number;
};
const trackTuningMatches = (left: TrackTuning | null, right: TrackTuning | null) =>
  left !== null && right !== null && JSON.stringify(left) === JSON.stringify(right);
type VisualTextOptions = {
  label: string;
  label_enabled: boolean;
  label_position: string;
  label_show_voice: boolean;
  label_show_head_size: boolean;
  label_font: string;
  label_font_size_percent: number;
  current_word_enabled: boolean;
  current_word_position: string;
  current_word_font: string;
  current_word_font_size_percent: number;
  current_word_use_track_color: boolean;
};
type VisualDraft = { position: [number, number, number]; hsb: [number, number, number]; options: VisualTextOptions };
type IntermediateAnimationMode = "delete" | "compress" | "keep";
type SpectrogramVideoSettings = { intermediate_animation_mode: IntermediateAnimationMode };
type SpectrogramStageTiming = { stage: string; seconds: number; details: string };
const visualTextPositions = [
  ["top-left", "Top left"], ["top-center", "Top center"], ["top-right", "Top right"],
  ["center-left", "Center left"], ["center", "Center"], ["center-right", "Center right"],
  ["bottom-left", "Bottom left"], ["bottom-center", "Bottom center"], ["bottom-right", "Bottom right"],
];
const visualFonts = [["choir", "Choir"], ["sans", "Sans"], ["serif", "Serif"], ["mono", "Monospace"]];
const visualFontFamily = (font: string) => font === "serif" ? "Georgia, serif" : font === "mono" ? "Consolas, monospace" : font === "sans" ? "Segoe UI, sans-serif" : "Segoe UI, sans-serif";
const roleVisualOptions = (role: Role): VisualTextOptions => ({
  label: role.visual_label,
  label_enabled: role.visual_label_enabled,
  label_position: role.visual_label_position,
  label_show_voice: role.visual_label_show_voice,
  label_show_head_size: role.visual_label_show_head_size,
  label_font: role.visual_label_font,
  label_font_size_percent: role.visual_label_font_size_percent,
  current_word_enabled: role.visual_current_word_enabled,
  current_word_position: role.visual_current_word_position,
  current_word_font: role.visual_current_word_font,
  current_word_font_size_percent: role.visual_current_word_font_size_percent,
  current_word_use_track_color: role.visual_current_word_use_track_color,
});
const wordColor = (line: number, wordIndex: number) => ["#f29a4b", "#70a8ff", "#e87098", "#a5c95d", "#c08ae8", "#52bfd6"][Math.abs(line * 7 + wordIndex) % 6];

const NOTE_CLASS: Record<string, number> = { C: 0, "C#": 1, Db: 1, D: 2, "D#": 3, Eb: 3, E: 4, F: 5, "F#": 6, Gb: 6, G: 7, "G#": 8, Ab: 8, A: 9, "A#": 10, Bb: 10, B: 11 };

function parseRangeNotes(value: string) {
  const notes = [...value.matchAll(/([A-G](?:#|b)?)(-?\d+)/g)].map((match) => ({ label: match[0], octave: Number(match[2]), midi: (Number(match[2]) + 1) * 12 + NOTE_CLASS[match[1]] }));
  return notes.length >= 2 ? [notes[0], notes[notes.length - 1]] as const : null;
}

function formatPitchSpan(semitones: number) {
  const octaves = Math.floor(semitones / 12);
  const remainder = semitones % 12;
  if (!octaves) return `${remainder} semitone${remainder === 1 ? "" : "s"}`;
  return `${octaves} octave${octaves === 1 ? "" : "s"}${remainder ? ` + ${remainder}` : ""}`;
}

function octaveRangeColor(octave: number) {
  if (octave <= 1) return "#3e73c4";
  if (octave === 2) return "#4d8ee6";
  if (octave === 3) return "#42a96d";
  if (octave === 4) return "#d6b438";
  if (octave === 5) return "#e66a4f";
  return "#c9404d";
}

function PitchRange({ value, label }: { value: string; label?: string }) {
  const notes = parseRangeNotes(value);
  if (!notes) return <span>{value}</span>;
  const [low, high] = notes;
  return <span className="pitch-range" title={`${value} · ${formatPitchSpan(Math.abs(high.midi - low.midi))}`}>
    {label && <small>{label}</small>}
    <b style={{ "--octave-color": octaveRangeColor(low.octave) } as CSSProperties}>{low.label}</b>
    <i>→</i>
    <b style={{ "--octave-color": octaveRangeColor(high.octave) } as CSSProperties}>{high.label}</b>
    <em>{formatPitchSpan(Math.abs(high.midi - low.midi))}</em>
  </span>;
}

function RailPitchRange({ value }: { value: string }) {
  const notes = parseRangeNotes(value);
  if (!notes) return <span className="track-pitch-endpoints">{value}</span>;
  const [low, high] = notes;
  return <span className="track-pitch-endpoints" title={`${value} · ${formatPitchSpan(Math.abs(high.midi - low.midi))}`}>
    <b style={{ "--octave-color": octaveRangeColor(low.octave) } as CSSProperties}>{low.label}</b>
    <b style={{ "--octave-color": octaveRangeColor(high.octave) } as CSSProperties}>{high.label}</b>
  </span>;
}

function SourceSyncMarker({ state }: { state: Role["source_sync_state"] }) {
  if (state === "absent") return null;
  const synced = state === "synced";
  const label = synced ? "Published lyrics match the current alignment" : "Current alignment has not been applied to source";
  return <span className={`source-sync-marker ${state}`} title={label} aria-label={label}>{synced ? <Check size={13} strokeWidth={3} /> : <X size={13} strokeWidth={3} />}</span>;
}

function overlapTooltip(role: Role, action: string) {
  const track = role.midi_track;
  if (!track || !role.polyphony || role.polyphony <= 1) return "No simultaneous notes detected";
  const details = track.overlap_totals.map((region) =>
    `(!) ${region.note_count} ♩: ${Math.round(region.duration_ms).toLocaleString()} ms total overlap`
  );
  return [
    `${role.polyphony}-note overlap detected. ${action}`,
    ...details,
  ].join("\n");
}

function TrackOverlapBadge({ role, onSplit }: { role: Role; onSplit(): void }) {
  if (role.polyphony && role.polyphony > 1) {
    return <button type="button" className="track-overlap-badge has-overlap" onClick={onSplit} title={overlapTooltip(role, "Open the monophonic track splitter.")} aria-label={`Split overlapping MIDI track for ${role.role}`}><Info size={12} /><span>{role.polyphony}-note overlap</span></button>;
  }
  return <span className="track-overlap-badge" title={role.polyphony === 1 ? "No simultaneous notes detected" : "Note overlap unavailable"}><Check size={12} /><span>{role.polyphony === 1 ? "No note overlap" : "Overlap unavailable"}</span></span>;
}

function midiPitchLabel(value: number | null) {
  if (value === null) return "--";
  const pitchClasses = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  return `${pitchClasses[((value % 12) + 12) % 12]}${Math.floor(value / 12) - 1}`;
}

function firstPhraseLine(report: AlignmentReport): number | null {
  return report.notes.find((entry) => entry.line !== null && Boolean(entry.lyric))?.line ?? null;
}

function suggestedSongName(path: string) {
  const filename = path.split(/[\\/]/).pop() ?? "NewSong.mid";
  const stem = filename.replace(/\.(?:mid|midi)$/i, "");
  return stem.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^[_-]+|[_-]+$/g, "") || "NewSong";
}

export default function App() {
  const [songs, setSongs] = useState<string[]>([]);
  const [song, setSong] = useState(storedUiState.song ?? "");
  const [inspection, setInspection] = useState<SongInspection | null>(null);
  const [roleName, setRoleName] = useState(storedUiState.role ?? "");
  const [stage, setStage] = useState<Stage>(storedUiState.stage ?? "align");
  const [theme, setTheme] = useState<"dark" | "light">(storedUiState.theme ?? (window.localStorage.getItem("dectalk-choir-studio.theme") === "light" ? "light" : "dark"));
  const [renderRolesBySong, setRenderRolesBySong] = useState<Record<string, string[]>>(storedUiState.render_roles ?? {});
  const [transcript, setTranscript] = useState("");
  const [transcriptLocked, setTranscriptLocked] = useState(false);
  const [savedTranscript, setSavedTranscript] = useState("");
  const [transcriptLoadedKey, setTranscriptLoadedKey] = useState("");
  const [validation, setValidation] = useState<{ invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null>(null);
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const [draftRole, setDraftRole] = useState("");
  const [alignment, setAlignment] = useState<AlignmentState | null>(null);
  const [alignmentRole, setAlignmentRole] = useState("");
  const [lyricsPrompt, setLyricsPrompt] = useState("");
  const [templateSources, setTemplateSources] = useState<AlignmentTemplate[]>([]);
  const [selectedPhrase, setSelectedPhrase] = useState<number | null>(null);
  const [deleteSongArmed, setDeleteSongArmed] = useState(false);
  const [midiImportSource, setMidiImportSource] = useState("");
  const [dectalkImportOpen, setDectalkImportOpen] = useState(false);
  const [lyricsModalOpen, setLyricsModalOpen] = useState(false);
  const [splitRoleName, setSplitRoleName] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [alignmentLoading, setAlignmentLoading] = useState(false);
  const [alignmentTransitionPending, startAlignmentTransition] = useTransition();
  const alignmentRequestRef = useRef(0);
  const invalidateAlignmentLoad = useCallback(() => {
    alignmentRequestRef.current += 1;
    setAlignmentLoading(false);
  }, []);

  const role = useMemo(() => inspection?.roles.find((item) => item.role === roleName) ?? null, [inspection, roleName]);
  const splitRole = useMemo(() => inspection?.roles.find((item) => item.role === splitRoleName) ?? null, [inspection, splitRoleName]);
  const transcriptKey = `${song}:${roleName}`;
  const loadSong = useCallback(async (nextSong: string, preferredRole = "") => {
    if (!nextSong) return;
    invalidateAlignmentLoad();
    setBusy("Loading song"); setError("");
    try {
      const next = await bridge<SongInspection>({ command: "inspect_song", song: nextSong });
      const nextRole = next.roles.some((item) => item.role === preferredRole) ? preferredRole : next.roles[0]?.role ?? "";
      setInspection(next); setSong(nextSong); setRoleName(nextRole); setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); }
  }, [invalidateAlignmentLoad]);
  useEffect(() => { bridge<string[]>({ command: "list_songs" }).then((items) => { setSongs(items); const restoredSong = storedUiState.song && items.includes(storedUiState.song) ? storedUiState.song : items[0]; if (restoredSong) void loadSong(restoredSong, restoredSong === storedUiState.song ? storedUiState.role : ""); }).catch((cause) => setError(String(cause))); }, [loadSong]);
  useEffect(() => { document.documentElement.dataset.theme = theme; window.localStorage.setItem("dectalk-choir-studio.theme", theme); }, [theme]);
  useEffect(() => {
    if (!song || !inspection) return;
    window.localStorage.setItem(UI_STATE_KEY, JSON.stringify({ song, role: roleName, stage, theme, render_roles: renderRolesBySong }));
  }, [song, roleName, stage, theme, inspection, renderRolesBySong]);
  useEffect(() => {
    if (!song || !inspection) return;
    const configuredRoles = inspection.roles.filter((item) => item.render_enabled && item.render_eligible).map((item) => item.role);
    setRenderRolesBySong((current) => {
      const stored = current[song];
      if (stored && stored.length === configuredRoles.length && stored.every((role, index) => role === configuredRoles[index])) return current;
      return { ...current, [song]: configuredRoles };
    });
  }, [song, inspection]);
  useEffect(() => {
    if (!lyricsModalOpen || !song || !roleName) {
      setTranscriptLoadedKey("");
      return;
    }
    let cancelled = false;
    setTranscriptLoadedKey("");
    setTranscript(""); setSavedTranscript(""); setTranscriptLocked(false); setValidation(null);
    bridge<TranscriptState>({ command: "read_transcript", song, role: roleName }).then((value) => {
      if (cancelled) return;
      setTranscript(value.text); setSavedTranscript(value.text); setTranscriptLocked(value.transcript_exists); setTranscriptLoadedKey(`${song}:${roleName}`); setValidation(null);
    }).catch((cause) => { if (!cancelled) setError(String(cause)); });
    return () => { cancelled = true; };
  }, [song, roleName, lyricsModalOpen]);
  useEffect(() => {
    if (stage !== "align" || !song || !roleName) {
      setAlignmentLoading(false);
      return;
    }
    const requestId = ++alignmentRequestRef.current;
    let cancelled = false;
    setAlignmentLoading(true);
    bridge<AlignmentWorkspace>({ command: "load_alignment_workspace", song, role: roleName }).then((workspace) => {
      if (cancelled || requestId !== alignmentRequestRef.current) return;
      startAlignmentTransition(() => {
        setTemplateSources(workspace.templates);
        const candidate = workspace.candidate;
        if (!candidate.exists || !candidate.text || !candidate.path || !candidate.report) {
          setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null);
          setAlignmentLoading(false);
          return;
        }
        setDraftState({ text: candidate.text, path: candidate.path, warnings: [], review_segments: [], tight_gap_ms: 0 });
        setDraftRole(roleName); setAlignment({ text: candidate.text, report: candidate.report, source_in_sync: candidate.source_in_sync }); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(candidate.report));
        setAlignmentLoading(false);
      });
    }).catch((cause) => { if (!cancelled && requestId === alignmentRequestRef.current) { setTemplateSources([]); setAlignmentLoading(false); setError(String(cause)); } });
    return () => { cancelled = true; };
  }, [song, roleName, stage, startAlignmentTransition]);
  useEffect(() => {
    if (!lyricsModalOpen || !song || !roleName) return;
    const timer = window.setTimeout(() => bridge<typeof validation>({ command: "validate_transcript", song, role: roleName, text: transcript }).then(setValidation).catch(() => undefined), 350);
    return () => window.clearTimeout(timer);
  }, [song, roleName, transcript, lyricsModalOpen]);
  useEffect(() => {
    if (!lyricsModalOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setLyricsModalOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [lyricsModalOpen]);
  const runDraft = async () => {
    if (!song || !roleName) return;
    invalidateAlignmentLoad();
    setBusy("Drafting and aligning lyrics"); setError("");
    try {
      const draft = await bridge<DraftState>({ command: "draft", song, role: roleName, text: transcript, auto_lines: false });
      setDraftState(draft); setDraftRole(roleName); setTranscript(draft.text); setSavedTranscript(draft.text); setTranscriptLocked(true); setLyricsPrompt("");
      const pending = await bridge<AlignmentState & { path: string }>({ command: "align", song, role: roleName });
      setDraftState({ ...draft, text: pending.text, path: pending.path }); setTranscript(pending.text); setSavedTranscript(pending.text); setAlignment(pending); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(pending.report)); setLyricsModalOpen(false); setStage("align");
      setInspection(await bridge<SongInspection>({ command: "inspect_song", song }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const runNoteSkeleton = async (placeholder: string) => {
    if (!song || !roleName) return;
    if (transcriptLoadedKey !== transcriptKey) {
      setError("Wait for this role's lyric source to finish loading before creating a note skeleton.");
      return;
    }
    invalidateAlignmentLoad();
    setBusy("Creating note skeleton"); setError("");
    try {
      const skeleton = await bridge<NoteSkeleton>({ command: "create_note_skeleton", song, role: roleName, placeholder });
      setTranscript(skeleton.text); setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null); setLyricsPrompt("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const saveTranscript = async () => {
    if (!song || !roleName) return;
    setBusy("Saving transcript"); setError("");
    try { await bridge({ command: "save_transcript", song, role: roleName, text: transcript }); setSavedTranscript(transcript); setTranscriptLocked(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const selectStage = (nextStage: Stage) => {
    setLyricsPrompt("");
    setStage(nextStage);
  };
  const openOutputs = async () => { try { await openSongFolder(song, "output"); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const removeSong = async () => {
    if (!song) return;
    setBusy(`Deleting ${song}`); setError("");
    try {
      await deleteSong(song);
      const remaining = await bridge<string[]>({ command: "list_songs" });
      setSongs(remaining); setDeleteSongArmed(false); setInspection(null); setRoleName(""); setDraftState(null); setAlignment(null);
      if (remaining[0]) await loadSong(remaining[0]); else setSong("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const chooseMidiSong = async () => {
    setError("");
    try {
      const selected = await open({ multiple: false, directory: false, filters: [{ name: "MIDI files", extensions: ["mid", "midi"] }] });
      if (typeof selected === "string") setMidiImportSource(selected);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const finishMidiSongImport = useCallback(async (result: MidiSongImportResult) => {
    const refreshedSongs = await bridge<string[]>({ command: "list_songs" });
    setSongs(refreshedSongs); setMidiImportSource(""); setStage("align");
    await loadSong(result.song, result.roles[0] ?? "");
  }, [loadSong]);
  const playRender = async () => { if (!inspection?.final_mix) return; try { await openMedia(inspection.final_mix); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const adoptTemplate = async (sourceRole: string) => {
    if (!song || !roleName || !sourceRole) return;
    invalidateAlignmentLoad();
    setBusy(`Copying ${sourceRole} alignment`); setError("");
    try {
      const result = await bridge<AlignmentState & { path: string }>({ command: "copy_alignment_template", song, role: roleName, source_role: sourceRole });
      setDraftState({ text: result.text, path: result.path, warnings: [], review_segments: [], tight_gap_ms: 0 }); setDraftRole(roleName);
      setAlignment(result); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(result.report)); setStage("align");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const hasDraft = draftState !== null && draftRole === roleName;
  const activeAlignment = alignmentRole === roleName ? alignment : null;
  const reviewEnabledRoles = renderRolesBySong[song] ?? inspection?.roles.filter((item) => item.render_enabled && item.render_eligible).map((item) => item.role) ?? [];
  const sourceSyncStateFor = (item: Role): Role["source_sync_state"] => item.role === roleName && activeAlignment && item.source_sync_state !== "absent"
    ? activeAlignment.source_in_sync === true ? "synced" : "pending"
    : item.source_sync_state;
  const transcriptBackedRoles = inspection?.roles.filter((item) => item.source_sync_state !== "absent") ?? [];
  const allTranscriptTracksSynced = transcriptBackedRoles.length > 0 && transcriptBackedRoles.every((item) => sourceSyncStateFor(item) === "synced");
  const selectRole = (nextRole: string) => {
    invalidateAlignmentLoad();
    setRoleName(nextRole); setSelectedPhrase(null); setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setTemplateSources([]);
    setLyricsPrompt("");
  };
  const updateRenderRoles = async (roles: string[]) => {
    if (!song || !inspection) return;
    const previous = reviewEnabledRoles;
    setRenderRolesBySong((current) => ({ ...current, [song]: roles }));
    try {
      await bridge({ command: "update_render_enabled_roles", song, roles });
      const refreshed = await bridge<SongInspection>({ command: "inspect_song", song });
      setInspection(refreshed);
    } catch (cause) {
      setRenderRolesBySong((current) => ({ ...current, [song]: previous }));
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  const closeSplitModal = useCallback(() => setSplitRoleName(""), []);
  const refreshAfterMidiSplit = useCallback(async () => {
    if (!song) return;
    invalidateAlignmentLoad();
    const refreshed = await bridge<SongInspection>({ command: "inspect_song", song });
    setInspection(refreshed); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null);
  }, [song, invalidateAlignmentLoad]);
  const finishDectalkImport = useCallback(async (result: DectalkImportResult) => {
    invalidateAlignmentLoad();
    const refreshed = await bridge<SongInspection>({ command: "inspect_song", song });
    setInspection(refreshed); setRoleName(result.role); setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null); setDectalkImportOpen(false); setStage("align");
  }, [song, invalidateAlignmentLoad]);

  return <main className="studio-shell">
    <header className="app-header">
      <div className="brand"><img className="brand-mark" src={choirStudioMark} alt="" /><span>DECTALK Choir</span><strong>Studio</strong></div>
      <div className="header-song-cluster"><label className="song-select"><select aria-label="Select song" value={song} onChange={(event) => void loadSong(event.target.value)}>{songs.map((item) => <option key={item}>{item}</option>)}</select></label><div className="selection-actions"><button className="header-command" type="button" onClick={() => void chooseMidiSong()} title="Import a MIDI file as a new Choir song" aria-label="Import MIDI as new song"><Inbox size={16} /></button><button className="header-command" type="button" onClick={() => setDectalkImportOpen(true)} disabled={!song} title="Import a timed DECTalk phoneme string as a new MIDI track with an applied lyric alignment" aria-label="Import timed DECTalk phonemes"><FileInput size={16} /></button><button className="header-command" type="button" onClick={() => void openOutputs()} disabled={!song} title="Open this song's generated output folder" aria-label="Open output folder"><FolderOpen size={16} /></button><button className="header-command" type="button" onClick={() => void playRender()} disabled={!inspection?.final_mix} title="Open the completed song mix in your default media player" aria-label="Open render in default media player"><Play size={15} /></button><button className="header-command destructive-command" type="button" onClick={() => setDeleteSongArmed(true)} disabled={!song} title="Delete this song and all of its outputs" aria-label="Delete selected song"><Trash2 size={15} /></button></div></div>
      <nav className="lifecycle" aria-label="Track design phases">
        {stages.map(([id, label, Icon], index) => <button key={id} className={`${stage === id ? "active" : ""}${id === "review" && stage !== "review" && allTranscriptTracksSynced ? " ready-next" : ""}`} onClick={() => selectStage(id)}><span className="stage-index">{index + 1}</span><Icon size={16} />{label}</button>)}
      </nav>
      <div className="header-state">{busy}</div>
      <div className="theme-switch" role="group" aria-label="Color theme"><button className={theme === "dark" ? "active" : ""} type="button" title="Use dark theme" aria-label="Use dark theme" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}><Moon size={15} /></button><button className={theme === "light" ? "active" : ""} type="button" title="Use light theme" aria-label="Use light theme" aria-pressed={theme === "light"} onClick={() => setTheme("light")}><Sun size={16} /></button></div>
    </header>
    {deleteSongArmed && <section className="song-delete-confirm" role="alertdialog" aria-label={`Delete ${song}`}><div><strong>Delete {song}?</strong><span>Its inputs, settings, and generated outputs will be removed.</span></div><button className="secondary" type="button" onClick={() => setDeleteSongArmed(false)} disabled={!!busy}>Cancel</button><button className="danger" type="button" onClick={() => void removeSong()} disabled={!!busy}>Delete song</button></section>}
    {error && <div className="error-toast" role="alert" aria-live="assertive"><CircleAlert size={17} /><span>{error}</span><div className="error-actions">{/ffmpeg/i.test(error) && <><button type="button" className="error-action" onClick={() => void openFfmpegDownload().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Open FFmpeg's official Windows download guidance">Get FFmpeg</button><button type="button" className="error-action" onClick={() => void navigator.clipboard.writeText(FFMPEG_WINGET_COMMAND).then(() => setError(`Copied: ${FFMPEG_WINGET_COMMAND}`)).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Copy the Windows Package Manager install command">Copy winget</button></>}<button type="button" onClick={() => setError("")} title="Dismiss error" aria-label="Dismiss error"><X size={16} /></button></div></div>}
    <section className={`workspace ${stage === "review" ? "review-workspace" : ""}`}>
      <aside className="track-rail"><h2 className="rail-song-title" title={song}>{song || "Song"}</h2><div className="rail-heading"><PanelLeft size={16} /> Tracks</div><div className="track-list">{inspection?.roles.map((item) => {
        const syncState = sourceSyncStateFor(item);
        return <div key={item.role} className={`track-entry${item.role === roleName ? " active" : ""}`}><div className="track"><button className="track-select-hitbox" type="button" onClick={() => selectRole(item.role)} aria-pressed={item.role === roleName} aria-label={`Select ${item.role}`} /><span className="track-copy"><span className="track-name-row"><strong title={`${item.role} · ${item.midi_source_name}`}>{item.role}</strong><SourceSyncMarker state={syncState} /></span><TrackOverlapBadge role={item} onSplit={() => { if (item.role !== roleName) selectRole(item.role); setSplitRoleName(item.role); }} /><RailPitchRange value={item.midi_range} /></span><span className="track-note-total" title={`${item.note_count} MIDI notes`} aria-label={`${item.note_count} MIDI notes`}><b>{item.note_count}</b><Music3 size={13} aria-hidden="true" /></span></div></div>;
      })}</div></aside>
      <section className={`surface${stage === "align" ? " align-surface" : ""}`}>
        {stage === "align" && <AlignStage role={role} inspection={inspection} song={song} alignment={activeAlignment} loading={alignmentLoading || alignmentTransitionPending} templateSources={templateSources} onAdoptTemplate={adoptTemplate} onOpenLyrics={() => setLyricsModalOpen(true)} onOpenSplitter={() => role && setSplitRoleName(role.role)} onApplied={async () => { const refreshed = await bridge<SongInspection>({ command: "inspect_song", song }); setInspection(refreshed); }} setAlignment={setAlignment} selectedPhrase={selectedPhrase} setSelectedPhrase={setSelectedPhrase} busy={busy} setBusy={setBusy} setError={setError} />}
        {stage === "review" && <ReviewStage song={song} role={role} inspection={inspection} enabledRoles={reviewEnabledRoles} onEnabledRolesChange={(roles) => void updateRenderRoles(roles)} onSelectRole={selectRole} setInspection={setInspection} busy={busy} setBusy={setBusy} setError={setError} />}
      </section>
    </section>
    {lyricsModalOpen && <section className="lyrics-modal-backdrop" role="presentation" onMouseDown={() => setLyricsModalOpen(false)}><section className="lyrics-modal" role="dialog" aria-modal="true" aria-label={`Edit ${roleName} lyrics`} onMouseDown={(event) => event.stopPropagation()}><button className="lyrics-modal-close" type="button" title="Close lyric editor" aria-label="Close lyric editor" onClick={() => setLyricsModalOpen(false)}><X size={17} /></button><LyricsStage transcript={transcript} transcriptLoaded={transcriptLoadedKey === transcriptKey} transcriptLocked={transcriptLocked} setTranscript={setTranscript} validation={validation} onDraft={runDraft} onNoteSkeleton={runNoteSkeleton} onSave={saveTranscript} busy={busy} draftState={hasDraft ? draftState : null} dirty={transcript !== savedTranscript} prompt={lyricsPrompt} /></section></section>}
    {midiImportSource && <MidiSongImportModal sourcePath={midiImportSource} onClose={() => setMidiImportSource("")} onImported={finishMidiSongImport} setError={setError} />}
    {dectalkImportOpen && <DectalkImportModal song={song} onClose={() => setDectalkImportOpen(false)} onImported={finishDectalkImport} setError={setError} />}
    {splitRole && <PolyphonicSplitModal song={song} role={splitRole} onClose={closeSplitModal} onSourceChanged={refreshAfterMidiSplit} setError={setError} />}
  </main>;
}

function MidiSongImportModal({ sourcePath, onClose, onImported, setError }: { sourcePath: string; onClose(): void; onImported(result: MidiSongImportResult): Promise<void>; setError(value: string): void }) {
  const [songName, setSongName] = useState(() => suggestedSongName(sourcePath));
  const [working, setWorking] = useState(false);
  const sourceName = sourcePath.split(/[\\/]/).pop() ?? sourcePath;
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !working) onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [working, onClose]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!songName.trim()) return;
    setWorking(true); setError("");
    try {
      const result = await bridge<MidiSongImportResult>({ command: "import_midi_song", source_path: sourcePath, song: songName.trim() });
      await onImported(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };
  return <section className="split-modal-backdrop" role="presentation" onMouseDown={() => { if (!working) onClose(); }}><form className="split-modal midi-song-import-modal" role="dialog" aria-modal="true" aria-labelledby="midi-song-import-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => void submit(event)}>
    <button className="split-modal-close" type="button" onClick={onClose} disabled={working} title="Close MIDI import" aria-label="Close MIDI import"><X size={17} /></button>
    <header><p className="eyebrow">New Choir song</p><h2 id="midi-song-import-title">Import MIDI song</h2><p>Each note-bearing MIDI track becomes an editable Choir role.</p></header>
    <div className="midi-import-source"><Inbox size={18} /><span><small>Selected MIDI</small><strong title={sourcePath}>{sourceName}</strong></span></div>
    <label><span>Song name</span><input autoFocus value={songName} onChange={(event) => setSongName(event.target.value)} pattern="[A-Za-z0-9_-]+" title="Use letters, numbers, underscores, or hyphens" placeholder="NewSong" disabled={working} /></label>
    <p className="dectalk-import-note">Studio copies the MIDI into a new song workspace, creates settings and lyric placeholders, then opens the first track in Align. Existing song folders are never overwritten.</p>
    <div className="split-actions"><button type="button" className="secondary" onClick={onClose} disabled={working}>Cancel</button><button type="submit" className="primary" disabled={working || !songName.trim()}>{working ? <><LoaderCircle size={15} /> Creating song...</> : <><Inbox size={15} /> Create and open Align</>}</button></div>
  </form></section>;
}

function DectalkImportModal({ song, onClose, onImported, setError }: { song: string; onClose(): void; onImported(result: DectalkImportResult): Promise<void>; setError(value: string): void }) {
  const [role, setRole] = useState("Imported Voice");
  const [text, setText] = useState("");
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !working) onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [working, onClose]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!role.trim() || !text.trim()) return;
    setWorking(true); setError("");
    try {
      const result = await bridge<DectalkImportResult>({ command: "import_dectalk_track", song, role: role.trim(), text });
      await onImported(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };
  return <section className="split-modal-backdrop" role="presentation" onMouseDown={() => { if (!working) onClose(); }}><form className="split-modal dectalk-import-modal" role="dialog" aria-modal="true" aria-labelledby="dectalk-import-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => void submit(event)}>
    <button className="split-modal-close" type="button" onClick={onClose} disabled={working} title="Close DECTalk import" aria-label="Close DECTalk import"><X size={17} /></button>
    <header><p className="eyebrow">Current song: {song}</p><h2 id="dectalk-import-title">Import timed DECTalk phonemes</h2><p>Create a new MIDI role and publish its direct-phoneme alignment in one operation.</p></header>
    <label><span>Track name</span><input autoFocus value={role} onChange={(event) => setRole(event.target.value)} placeholder="Imported Voice" disabled={working} /></label>
    <label className="dectalk-import-source"><span>DECTalk command string</span><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="[:np][d&lt;80,12&gt;ao&lt;500,12&gt;ng&lt;80,12&gt;][:tone 440,500]" disabled={working} /></label>
    <p className="dectalk-import-note">Contiguous phonemes at one pitch become one MIDI note. Timed underscores become rests; rests of 250 ms or longer start a new phrase. Tone events use <code>[:tone frequency_hz,duration_ms]</code>; Studio retains that exact command and maps its frequency to the nearest MIDI note for alignment.</p>
    <div className="split-actions"><button type="button" className="secondary" onClick={onClose} disabled={working}>Cancel</button><button type="submit" className="primary" disabled={working || !role.trim() || !text.trim()}>{working ? <><LoaderCircle size={15} /> Importing...</> : <><FileInput size={15} /> Import and open Align</>}</button></div>
  </form></section>;
}

function PolyphonicSplitModal({ song, role, onClose, onSourceChanged, setError }: { song: string; role: Role; onClose(): void; onSourceChanged(): Promise<void>; setError(value: string): void }) {
  const [preview, setPreview] = useState<SplitPreview | null>(null);
  const [result, setResult] = useState<SplitResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [replaceSource, setReplaceSource] = useState(true);
  const [filename, setFilename] = useState("");
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setPreview(null); setResult(null);
    bridge<SplitPreview>({ command: "preview_polyphonic_split", song, role: role.role }).then((value) => {
      if (cancelled) return;
      setPreview(value); setFilename(value.default_filename); setLoading(false);
    }).catch((cause) => { if (!cancelled) { setLoading(false); setError(cause instanceof Error ? cause.message : String(cause)); onClose(); } });
    return () => { cancelled = true; };
  }, [song, role.role, onClose, setError]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !working) { event.preventDefault(); onClose(); } };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, working]);

  const runSplit = async () => {
    if (!preview) return;
    setWorking(true); setError("");
    try {
      const next = await bridge<SplitResult>({ command: "export_polyphonic_split", song, role: role.role, filename, replace_source: replaceSource, confirm_overwrite: confirmOverwrite });
      setResult(next);
      if (next.replaced_source) await onSourceChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };

  return <section className="split-modal-backdrop" role="presentation" onMouseDown={() => { if (!working) onClose(); }}><section className="split-modal" role="dialog" aria-modal="true" aria-labelledby="split-modal-title" onMouseDown={(event) => event.stopPropagation()}><button className="split-modal-close" type="button" onClick={onClose} disabled={working} title="Close MIDI splitter" aria-label="Close MIDI splitter"><X size={17} /></button>
    <header><p className="eyebrow">Selected MIDI track</p><h2 id="split-modal-title">Split {role.role} into voices</h2><p>{role.midi_source_name} · {role.note_count} notes</p></header>
    {loading && <div className="split-loading"><LoaderCircle size={18} /> Analyzing note overlap...</div>}
    {preview && !result && <>
      <section className="split-analysis"><div><span>Maximum overlap</span><strong>{preview.max_polyphony}</strong></div><div><span>Output voices</span><strong>{preview.lanes.length}</strong></div><div><span>Preserved notes</span><strong>{preview.note_count}</strong></div></section>
      <div className="split-lanes" aria-label="Tentative split voices">{preview.lanes.map((lane) => <div key={lane.number}><span className="split-lane-index">{lane.number}</span><strong title={lane.name}>{lane.name}</strong><span>{midiPitchLabel(lane.minimum_pitch)}-{midiPitchLabel(lane.maximum_pitch)}</span><b>{lane.note_count} notes</b></div>)}</div>
      {!preview.splittable ? <div className="notice"><CircleCheck size={17} /><div><strong>Already monophonic.</strong> This track has no simultaneous notes to separate.</div></div> : <>
        <fieldset className="split-destination"><legend>Output</legend><label className={replaceSource ? "selected" : ""}><input type="radio" name="split-output" checked={replaceSource} onChange={() => setReplaceSource(true)} /><span><strong>Replace working MIDI</strong><small>Recommended. Keeps a one-time <code>.bak</code> copy and preserves this role on voice one.</small></span></label><label className={!replaceSource ? "selected" : ""}><input type="radio" name="split-output" checked={!replaceSource} onChange={() => setReplaceSource(false)} /><span><strong>Export a new MIDI</strong><small>Writes a separate file beside the source without activating it.</small></span></label></fieldset>
        {!replaceSource && <div className="split-filename"><label><span>Filename</span><input value={filename} onChange={(event) => setFilename(event.target.value)} /></label><label className="split-overwrite"><input type="checkbox" checked={confirmOverwrite} onChange={(event) => setConfirmOverwrite(event.target.checked)} /> Allow replacing an existing export</label></div>}
        <footer><span>Only <strong>{preview.source_name}</strong> is split. Every other MIDI track passes through unchanged.</span><button type="button" className="primary" onClick={() => void runSplit()} disabled={working || (!replaceSource && !filename.trim())}>{working ? <LoaderCircle size={16} /> : <GitBranch size={16} />}{working ? "Splitting MIDI" : replaceSource ? "Replace working MIDI" : "Export split MIDI"}</button></footer>
      </>}
    </>}
    {result && <section className="split-complete"><CircleCheck size={24} /><div><strong>MIDI split complete</strong><span>{result.path}</span>{result.backup_path && <small>Backup: {result.backup_path}</small>}{result.warning && <small className="split-warning">{result.warning}</small>}</div><button className="primary" type="button" onClick={onClose}>Done</button></section>}
  </section></section>;
}

function LyricsStage({ transcript, transcriptLoaded, transcriptLocked, setTranscript, validation, onDraft, onNoteSkeleton, onSave, busy, draftState, dirty, prompt }: { transcript: string; transcriptLoaded: boolean; transcriptLocked: boolean; setTranscript(value: string): void; validation: { invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null; onDraft(): void; onNoteSkeleton(placeholder: string): void; onSave(): void; busy: string; draftState: DraftState | null; dirty: boolean; prompt: string }) {
  const [skeletonPhoneme, setSkeletonPhoneme] = useState("duw");
  const [replaceArmed, setReplaceArmed] = useState(false);
  const hasLyrics = Boolean(transcript.trim());
  const skeletonDisabled = Boolean(busy) || !transcriptLoaded || !skeletonPhoneme.trim();
  const createSkeleton = () => {
    if (!replaceArmed) {
      setReplaceArmed(true);
      return;
    }
    setReplaceArmed(false);
    onNoteSkeleton(skeletonPhoneme);
  };
  useEffect(() => {
    if (prompt) document.querySelector<HTMLTextAreaElement>(".transcript")?.focus();
  }, [prompt]);
  useEffect(() => { setReplaceArmed(false); }, [transcript, skeletonPhoneme]);
  const skeletonTitle = !transcriptLoaded
    ? "Wait for this role's lyric source to finish loading before creating a note skeleton."
    : replaceArmed
      ? `Confirm creation of a ${skeletonPhoneme} note skeleton${hasLyrics ? " and replacement of the current lyrics" : ""}.`
      : "Create one direct DECTALK phoneme per MIDI note, grouped at MIDI rests.";
  return <><section className="surface-header lyrics-header"><div className="lyrics-title"><p className="eyebrow">Working lyric draft</p><h1>Lyrics</h1><p>Paste lyrics or create a note skeleton here. Draft timing turns this same text into the editable aligned draft.</p></div><div className="header-actions lyrics-actions"><span className={dirty ? "save-state dirty" : "save-state"}>{!transcriptLoaded ? "Loading lyrics" : replaceArmed ? "Confirm note skeleton" : dirty ? "Working changes" : transcriptLocked ? "Transcript preserved" : "Transcript not captured"}</span><button className="secondary" title={transcriptLocked ? "The original transcript is immutable. Delete its .transcript.txt file manually to replace it." : "Preserve this text once as the original transcript"} onClick={onSave} disabled={!!busy || !transcriptLoaded || transcriptLocked || !transcript.trim()}>Preserve transcript</button><label className="skeleton-control" title={skeletonTitle}><input value={skeletonPhoneme} onChange={(event) => setSkeletonPhoneme(event.target.value)} aria-label="Note skeleton phoneme" placeholder="duw" /><button className="secondary" onClick={createSkeleton} disabled={skeletonDisabled}><Music2 size={16} /> {replaceArmed ? "Confirm skeleton" : "Note skeleton"}</button></label><button className="primary" onClick={onDraft} title={!transcriptLoaded ? "Wait for this role's lyrics to finish loading" : "Draft timing against this role's MIDI notes"} disabled={!!busy || !transcriptLoaded || !transcript.trim()}><WandSparkles size={16} /> Draft timing</button></div></section><LyricsTipBoard /><textarea className="transcript" value={transcript} onChange={(event) => setTranscript(event.target.value)} disabled={!transcriptLoaded} placeholder={transcriptLoaded ? "Paste plain lyrics, or create one direct phoneme per MIDI note. Line breaks are phrase hints; commas and unsupported punctuation are normalized." : "Loading this track's lyric source..."} />{validation && (!validation.ok || validation.normalized_lines.length > 0) && <div className={validation.ok ? "notice" : "warning"}><CircleAlert size={17} /><div>{validation.invalid_words.length > 0 && <><strong>Check these words:</strong> {validation.invalid_words.join(", ")}</>}{validation.normalized_lines.length > 0 && <span> Punctuation will be normalized before drafting.</span>}</div></div>}{draftState?.review_segments.length ? <details className="draft-review" open><summary>{draftState.review_segments.length} rapid multi-note word {draftState.review_segments.length === 1 ? "span needs" : "spans need"} verification <span>gaps at or below {draftState.tight_gap_ms} ms</span></summary><div>{draftState.review_segments.map((segment) => <div key={`${segment.line}-${segment.word_index}`} style={{ "--word-color": wordColor(segment.line, segment.word_index) } as CSSProperties}><strong>{segment.word}</strong><span>{segment.note_count} notes · {Math.round(segment.start_ms / 1000)}s-{Math.round(segment.end_ms / 1000)}s</span></div>)}</div></details> : null}{draftState && <details className="generated"><summary>Generated draft ready for alignment <span>{draftState.path}</span></summary><pre>{draftState.text}</pre></details>}</>;
}

function LyricsTipBoard() {
  const [tipIndex, setTipIndex] = useState(0);
  const [speedIndex, setSpeedIndex] = useState(1);
  const [paused, setPaused] = useState(false);
  const intervalMs = lyricTipIntervals[speedIndex];
  useEffect(() => {
    if (paused) return;
    const timer = window.setInterval(() => setTipIndex((current) => (current + 1) % lyricEditorTips.length), intervalMs);
    return () => window.clearInterval(timer);
  }, [paused, intervalMs]);
  const tip = lyricEditorTips[tipIndex];
  return <aside className={`lyrics-tip-board${paused ? " paused" : ""}`} role="note" aria-label="Lyric drafting tip" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocusCapture={() => setPaused(true)} onBlurCapture={() => setPaused(false)}>
    <span className="lyrics-tip-label"><Sparkles size={14} /> Choir tip</span>
    <p key={tipIndex}><strong>{tip.lead}</strong> {tip.message}</p>
    <span className="lyrics-tip-timing">
      <button type="button" onClick={() => setSpeedIndex((current) => Math.max(0, current - 1))} disabled={speedIndex === 0} title="Rotate tips more slowly" aria-label="Rotate lyric tips more slowly"><ChevronsLeft size={13} /></button>
      <span className="lyrics-tip-progress" title={`${intervalMs / 1000} seconds per tip`}><i key={`${tipIndex}-${speedIndex}-${paused}`} style={{ "--tip-duration": `${intervalMs}ms` } as CSSProperties} /></span>
      <button type="button" onClick={() => setSpeedIndex((current) => Math.min(lyricTipIntervals.length - 1, current + 1))} disabled={speedIndex === lyricTipIntervals.length - 1} title="Rotate tips more quickly" aria-label="Rotate lyric tips more quickly"><ChevronsRight size={13} /></button>
      <span className="lyrics-tip-count">{tipIndex + 1}/{lyricEditorTips.length}</span>
    </span>
  </aside>;
}

function AlignStage({ role, inspection, song, alignment, loading, templateSources, onAdoptTemplate, onOpenLyrics, onOpenSplitter, onApplied, setAlignment, selectedPhrase, setSelectedPhrase, busy, setBusy, setError }: { role: Role | null; inspection: SongInspection | null; song: string; alignment: AlignmentState | null; loading: boolean; templateSources: AlignmentTemplate[]; onAdoptTemplate(sourceRole: string): void; onOpenLyrics(): void; onOpenSplitter(): void; onApplied(): Promise<void>; setAlignment(value: AlignmentState | null): void; selectedPhrase: number | null; setSelectedPhrase(value: number): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [selectedWord, setSelectedWord] = useState<{ line: number; wordIndex: number } | null>(null);
  const [insertWord, setInsertWord] = useState("");
  const [insertOpen, setInsertOpen] = useState(false);
  const [draggedWord, setDraggedWord] = useState<number | null>(null);
  const [dragTargetWord, setDragTargetWord] = useState<number | null>(null);
  const suppressWordClickRef = useRef(false);
  const [showAllWords, setShowAllWords] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [applyArmed, setApplyArmed] = useState(false);
  const [templateRole, setTemplateRole] = useState("");
  const [cursorMs, setCursorMs] = useState(0);
  const [mediaState, setMediaState] = useState<MediaStatus | null>(null);
  const [mediaLabel, setMediaLabel] = useState("Preview this role while you align its lyrics.");
  const active = Boolean(mediaState && !["stopped", "not ready"].includes(mediaState.mode));
  const missingNoteWords = Number(alignment?.report.summary.zero_note_tokens ?? 0);
  const sourceInSync = alignment?.source_in_sync === true;
  const invalidPhraseLines = alignment?.report.token_counts?.filter((item) => item.note_count === 0).map((item) => item.line) ?? [];
  const phraseEntries = alignment?.report.notes.filter((entry) => entry.line === selectedPhrase) ?? [];
  const wordDurations = new Map<string, number>();
  phraseEntries.forEach((entry) => {
    if (entry.word_index !== null) {
      const key = `${entry.line}:${entry.word_index}`;
      wordDurations.set(key, (wordDurations.get(key) ?? 0) + entry.duration_ms);
    }
  });
  const words = alignment?.report.token_counts
    ?.filter((item) => item.line === selectedPhrase)
    .map((item) => ({ line: item.line, word_index: item.word_index, lyric: item.word, note_count: item.note_count, mode: item.mode ?? "sing", duration_ms: wordDurations.get(`${item.line}:${item.word_index}`) ?? 0 }))
    ?? phraseEntries.filter((entry, index, items) => index === 0 || entry.word_index !== items[index - 1].word_index).map((entry) => ({ ...entry, mode: "sing", note_count: entry.word_note_count ?? 1 }));
  const visibleWords = showAllWords ? words : words.slice(0, 10);
  const hiddenWordCount = words.length - visibleWords.length;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => media<MediaStatus>("media_status").then(setMediaState).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))), 250);
    return () => window.clearInterval(timer);
  }, [active, setError]);
  useEffect(() => {
    setCursorMs(0); setMediaState(null); setMediaLabel("Preview this role while you align its lyrics."); setSelectedWord(null); setInsertWord(""); setInsertOpen(false); setApplyArmed(false); setTemplateRole(""); setShowAllWords(false);
    return () => { void media<MediaStatus>("media_stop").catch(() => undefined); };
  }, [role?.role]);
  useEffect(() => { setApplyArmed(false); }, [alignment?.text]);
  useEffect(() => {
    const armDelete = (event: KeyboardEvent) => setDeleteArmed(event.ctrlKey || event.key === "Control");
    const disarmDelete = (event: KeyboardEvent) => setDeleteArmed(event.ctrlKey);
    const clearDeleteArmed = () => setDeleteArmed(false);
    window.addEventListener("keydown", armDelete);
    window.addEventListener("keyup", disarmDelete);
    window.addEventListener("blur", clearDeleteArmed);
    return () => {
      window.removeEventListener("keydown", armDelete);
      window.removeEventListener("keyup", disarmDelete);
      window.removeEventListener("blur", clearDeleteArmed);
    };
  }, []);
  const playMidi = async () => {
    if (!role || !song) return;
    setError(""); setMediaLabel("Preparing selected MIDI track...");
    try {
      const preview = await bridge<{ path: string; duration_ms: number; track: string }>({ command: "prepare_midi_preview", song, role: role.role });
      const next = await media<MediaStatus>("media_play", { path: preview.path, kind: "midi", fromMs: cursorMs });
      setMediaState({ ...next, duration_ms: Math.max(next.duration_ms, preview.duration_ms) }); setMediaLabel(`Playing ${preview.track}.`);
    } catch (cause) { const message = cause instanceof Error ? cause.message : String(cause); setError(message); setMediaLabel("MIDI preview unavailable."); }
  };
  const togglePause = async () => { try { const next = await media<MediaStatus>("media_toggle_pause"); setMediaState(next); setMediaLabel(next.paused ? "MIDI preview paused." : "MIDI preview resumed."); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const stop = async () => { try { const next = await media<MediaStatus>("media_stop"); setMediaState(next); setMediaLabel("Playback stopped."); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const seek = async (milliseconds: number) => { setCursorMs(milliseconds); if (!active || mediaState?.paused) return; try { setMediaState(await media<MediaStatus>("media_seek", { positionMs: Math.round(milliseconds) })); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const adjust = async (edge: "start" | "end", movement: number) => {
    if (!alignment || !selectedWord || !role) return;
    setBusy("Adjusting phrase"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "resize_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, edge, movement });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const adjustWordNoteCount = async (delta: -1 | 1) => {
    if (!alignment || !selectedWord || !role) return;
    setBusy("Adjusting word notes"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "adjust_word_note_count", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, delta });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const toggleWordMode = async () => {
    if (!alignment || !selectedWord || !role) return;
    setBusy("Changing word voice mode"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "toggle_word_mode", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const adjustPhrase = async (edge: "start" | "end", movement: number) => {
    if (!alignment || !role || selectedPhrase === null) return;
    setBusy("Adjusting phrase range"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "resize_phrase", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedPhrase, edge, movement });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const addVirtualSplit = async (noteIndex: number, fraction: number) => {
    if (!alignment || !role) return;
    setBusy("Splitting virtual note"); setError("");
    try {
      const selected = words.find((item) => item.line === selectedWord?.line && item.word_index === selectedWord?.wordIndex);
      const target = selected?.note_count === 0 ? selectedWord : null;
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "add_virtual_split", song, role: role.role, report: alignment.report, note_index: noteIndex, fraction, target_line: target?.line, target_word_index: target?.wordIndex });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const insert = async () => {
    if (!alignment || !selectedWord || !role || !insertWord.trim()) return;
    setBusy("Inserting lyric"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; selected: { line: number; word_index: number } }>({ command: "insert_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, word: insertWord.trim(), position: "after" });
      setAlignment(result); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index }); setInsertWord(""); setInsertOpen(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const removeWord = async (line: number, wordIndex: number) => {
    if (!alignment || !role) return;
    setBusy("Removing lyric"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; selected: { line: number; word_index: number } }>({ command: "delete_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line, word_index: wordIndex, confirm_delete: true });
      setAlignment(result); setSelectedPhrase(result.selected.line); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const reorderWord = async (sourceWordIndex: number, targetWordIndex: number) => {
    if (!alignment || !role || selectedPhrase === null || sourceWordIndex === targetWordIndex) return;
    setBusy("Reordering lyric"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; selected: { line: number; word_index: number } }>({ command: "reorder_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedPhrase, word_index: sourceWordIndex, target_word_index: targetWordIndex });
      setAlignment(result); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); setDraggedWord(null); setDragTargetWord(null); }
  };
  const beginWordPointerDrag = (event: ReactPointerEvent<HTMLDivElement>, line: number, sourceWordIndex: number) => {
    if (busy || event.button !== 0) return;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startY = event.clientY;
    let dragging = false;
    let targetWordIndex: number | null = null;
    suppressWordClickRef.current = false;

    const cleanup = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
    };
    const move = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId !== pointerId) return;
      const horizontalDelta = pointerEvent.clientX - startX;
      if (!dragging && Math.abs(horizontalDelta) < 4) return;
      if (!dragging) {
        dragging = true;
        suppressWordClickRef.current = true;
        setDraggedWord(sourceWordIndex);
      }
      pointerEvent.preventDefault();
      const direction = horizontalDelta < 0 ? -1 : 1;
      const candidates = [...document.querySelectorAll<HTMLElement>(`.word-token[data-line="${line}"][data-word-index]`)]
        .map((element) => ({ element, index: Number.parseInt(element.dataset.wordIndex ?? "", 10) }))
        .filter(({ index }) => Number.isInteger(index) && (direction < 0 ? index < sourceWordIndex : index > sourceWordIndex))
        .map(({ element, index }) => {
          const bounds = element.getBoundingClientRect();
          return { index, distance: Math.abs(pointerEvent.clientX - (bounds.left + bounds.width / 2)) + Math.abs(pointerEvent.clientY - (bounds.top + bounds.height / 2)) * 0.5 };
        })
        .sort((left, right) => left.distance - right.distance);
      targetWordIndex = candidates[0]?.index ?? null;
      setDragTargetWord(targetWordIndex);
    };
    const finish = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId !== pointerId) return;
      cleanup();
      if (dragging && targetWordIndex !== null) void reorderWord(sourceWordIndex, targetWordIndex);
      else { setDraggedWord(null); setDragTargetWord(null); }
      window.setTimeout(() => { suppressWordClickRef.current = false; }, 0);
    };
    const cancel = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId !== pointerId) return;
      cleanup(); setDraggedWord(null); setDragTargetWord(null); suppressWordClickRef.current = false;
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", cancel);
  };
  const apply = async () => {
    if (!alignment || !role) return;
    setBusy("Applying aligned lyrics"); setError("");
    try {
      const result = await bridge<{ source_in_sync: boolean }>({ command: "apply_alignment", song, role: role.role, text: alignment.text });
      setAlignment({ ...alignment, source_in_sync: result.source_in_sync }); setApplyArmed(false); await onApplied();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const selectedWordPosition = words.findIndex((item) => item.line === selectedWord?.line && item.word_index === selectedWord?.wordIndex);
  const selectedWordNoteCount = selectedWordPosition >= 0 ? words[selectedWordPosition].note_count : 0;
  const canDecreaseWordNotes = words.length > 1 && selectedWordNoteCount > 1;
  const canIncreaseWordNotes = selectedWordPosition >= 0 && words.some((item, index) => index !== selectedWordPosition && item.note_count > 1);
  const selectedPhraseInvalid = selectedPhrase !== null && invalidPhraseLines.includes(selectedPhrase);
  const hasSelectedPhrase = selectedPhrase !== null;
  const phraseInstruction = hasSelectedPhrase ? "Drag either full-height edge guide to snap across any available note boundary." : "Phrase blocks stay compact until selected.";
  const overlay = alignment && selectedPhrase !== null ? <div className={`phrase-workbench ${selectedPhraseInvalid ? "invalid" : ""}`}>
    <div className="phrase-workbench-heading" title={phraseInstruction}><p className="eyebrow">{hasSelectedPhrase ? `Phrase ${selectedPhrase + 1}` : "Select a phrase above the notes"}</p><strong>{phraseInstruction}</strong></div>
    {hasSelectedPhrase && <div className="word-strip">{visibleWords.map((item) => {
      if (item.line === null || item.word_index === null) return null;
      const isSelected = selectedWord?.line === item.line && selectedWord.wordIndex === item.word_index;
      const wordTooltip = `${item.lyric ?? "Word"}\n${item.note_count} ♩ | ${item.note_count === 0 ? "Needs a note" : `${item.duration_ms} ms`}\nDrag to reorder within this phrase`;
      return <div className={`word-token ${draggedWord === item.word_index ? "dragging" : ""} ${dragTargetWord === item.word_index && draggedWord !== item.word_index ? "drop-target" : ""} ${item.note_count === 0 ? "invalid" : ""}`} style={{ "--word-color": wordColor(item.line, item.word_index) } as CSSProperties} key={`${item.line}-${item.word_index}`} data-line={item.line} data-word-index={item.word_index} title={wordTooltip} onPointerDown={(event) => beginWordPointerDrag(event, item.line!, item.word_index!)}>
        <button className={isSelected ? "selected" : ""} onClick={() => { if (suppressWordClickRef.current) return; setSelectedWord({ line: item.line!, wordIndex: item.word_index! }); setInsertOpen(false); }}>{item.lyric}<small>{item.note_count === 0 ? "Needs note" : `${item.duration_ms} ms`}</small></button>
        {isSelected && <span className="word-quick-controls"><button type="button" className={item.mode === "speak" ? "active" : ""} title={item.mode === "speak" ? "Use pitched singing for this word" : "Speak this word normally within its claimed MIDI time"} aria-label={item.mode === "speak" ? "Switch this word to singing" : "Switch this word to normal speech"} disabled={!!busy} onPointerDown={(event) => event.stopPropagation()} onClick={() => void toggleWordMode()}><MessageSquareText size={12} /></button><button type="button" title="Give one note back to this phrase" aria-label="Decrease this word's note count" disabled={!!busy || !canDecreaseWordNotes} onPointerDown={(event) => event.stopPropagation()} onClick={() => void adjustWordNoteCount(-1)}><Minus size={12} /></button><button type="button" title="Assign one more note from this phrase" aria-label="Increase this word's note count" disabled={!!busy || !canIncreaseWordNotes} onPointerDown={(event) => event.stopPropagation()} onClick={() => void adjustWordNoteCount(1)}><Plus size={12} /></button></span>}
        <span className="word-bottom-controls"><span className="word-note-count" aria-label={`${item.note_count} owned note${item.note_count === 1 ? "" : "s"}`}>{item.note_count}♩</span><button className={deleteArmed ? "word-delete armed" : "word-delete"} type="button" title={deleteArmed ? `Ctrl-click to remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} aria-label={deleteArmed ? `Remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} draggable={false} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { if (!event.ctrlKey) return; void removeWord(item.line!, item.word_index!); }} disabled={!!busy || !deleteArmed}><Minus size={11} /></button></span>
      </div>;
    })}{hiddenWordCount > 0 && <button className="word-more" type="button" onClick={() => setShowAllWords(true)}>{hiddenWordCount} more</button>}{showAllWords && words.length > 10 && <button className="word-more" type="button" onClick={() => setShowAllWords(false)}>Collapse</button>}<div className="word-insert-anchor"><button className="add-word" type="button" title="Insert a word after the selected word" aria-label="Insert a word" onClick={() => { const anchor = selectedWord ?? (words.length ? { line: words[words.length - 1].line!, wordIndex: words[words.length - 1].word_index! } : null); if (anchor) { setSelectedWord(anchor); setInsertOpen(true); } }} disabled={!words.length || !!busy}><Plus size={15} /></button></div></div>}
    {insertOpen && <form className="word-insert-popover" onSubmit={(event) => { event.preventDefault(); void insert(); }}><input autoFocus value={insertWord} onChange={(event) => setInsertWord(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { setInsertOpen(false); setInsertWord(""); } }} placeholder="New word" /><button className="primary" type="submit" disabled={!!busy || !insertWord.trim()}>Insert</button><button className="secondary" type="button" aria-label="Cancel insert" title="Cancel insert" onClick={() => { setInsertOpen(false); setInsertWord(""); }}><X size={14} /></button></form>}
  </div> : null;
  return (
    <section className="align-workspace">
      <section className="midi-transport align-transport">
        <div className="transport-group"><button className="primary" onClick={() => void playMidi()} disabled={!role?.midi_track}><Play size={15} /> Play MIDI</button><button className="secondary icon-command" title={mediaState?.paused ? "Resume preview" : "Pause preview"} onClick={() => void togglePause()} disabled={!active}>{mediaState?.paused ? <Play size={15} /> : <Pause size={15} />}</button><button className="secondary icon-command" title="Stop playback" onClick={() => void stop()} disabled={!mediaState}><Square size={14} /></button><button className="secondary align-lyrics-command" type="button" title="Edit the working lyric draft without leaving Align" onClick={onOpenLyrics}><PenLine size={15} /> Edit track lyrics</button><span>{mediaLabel}</span></div>
        <div className="transport-group align-actions"><span className={missingNoteWords ? "save-state dirty" : "save-state"}>{loading ? "Loading alignment..." : alignment ? missingNoteWords ? `${missingNoteWords} word${missingNoteWords === 1 ? "" : "s"} need a note` : sourceInSync ? "Source in sync" : alignment.report.template?.source_role ? `Template: ${alignment.report.template.source_role}` : "Pending source update" : "Not drafted"}</span>{templateSources.length > 0 && <label className="template-picker" title="Copy a saved same-lyrics alignment and remap it to this track by time"><select value={templateRole} onChange={(event) => setTemplateRole(event.target.value)}><option value="">Aligned template</option>{templateSources.map((source) => <option key={source.role} value={source.role}>{source.role}</option>)}</select><button type="button" className="secondary" onClick={() => onAdoptTemplate(templateRole)} disabled={!!busy || !templateRole}>Use</button></label>}{role?.polyphony && role.polyphony > 1 ? <button type="button" className="polyphony-warning-control" onClick={onOpenSplitter} title={overlapTooltip(role, "Rendering sequentializes overlaps; split it to preserve each chord voice.")}><CircleAlert size={14} /><span>{role.polyphony}-note overlap</span><GitBranch size={14} /></button> : null}{alignment && <button className={`secondary apply-source-control${applyArmed ? " apply-alignment-confirm" : ""}${sourceInSync ? " apply-alignment-complete" : ""}`} title={sourceInSync ? "The configured lyric source matches this alignment" : missingNoteWords ? "Resolve words without MIDI notes before applying" : applyArmed ? "Confirm publishing this alignment to the configured lyric source" : "Validate and replace the configured lyric input used by choir.py"} onClick={() => { if (applyArmed) void apply(); else setApplyArmed(true); }} disabled={!!busy || missingNoteWords > 0 || sourceInSync}>{sourceInSync ? <><Check size={14} /> Applied</> : applyArmed ? "Apply alignment" : "Apply to source"}</button>}</div>
      </section>
      <div className="align-roll-shell">{overlay && <div className="align-phrase-toolbar">{overlay}</div>}<PianoRoll track={role?.midi_track ?? null} durationSeconds={inspection?.midi?.duration_seconds ?? 0} durationTicks={inspection?.midi?.duration_ticks} alignment={alignment?.report.notes} virtualSplits={alignment?.report.virtual_splits} selectedPhrase={selectedPhrase} selectedWord={selectedWord} invalidPhraseLines={invalidPhraseLines} playheadMs={active ? mediaState?.position_ms : null} playbackPaused={Boolean(mediaState?.paused)} onCursorChange={(milliseconds) => void seek(milliseconds)} onSelectPhrase={(line) => { setSelectedPhrase(line); setSelectedWord(null); setShowAllWords(false); }} onPlaybackPhraseChange={(line) => { setSelectedPhrase(line); setSelectedWord(null); setShowAllWords(false); }} onSelectWord={(line, wordIndex) => { setSelectedPhrase(line); setSelectedWord({ line, wordIndex }); setShowAllWords(wordIndex >= 10); }} onResizeWord={(edge, movement) => void adjust(edge, movement)} onResizePhrase={(edge, movement) => void adjustPhrase(edge, movement)} onAddVirtualSplit={(noteIndex, fraction) => void addVirtualSplit(noteIndex, fraction)} />{loading && <div className="align-loading" role="status" aria-live="polite"><LoaderCircle size={17} /><span>Loading alignment and phrase map...</span></div>}</div>
    </section>
  );
}

function formatDb(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} dBFS` : "--";
}

function formatDuration(seconds: number | undefined) {
  const total = Math.max(0, Math.round(seconds ?? 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function parseSpectrogramStageTimings(log: string | undefined): SpectrogramStageTiming[] {
  if (!log) return [];
  const timings = new Map<string, SpectrogramStageTiming>();
  for (const line of log.split(/\r?\n/)) {
    const match = line.match(/^TIMING stage=([a-z_]+) seconds=([0-9.]+)(?:\s+(.*))?$/);
    if (!match) continue;
    const seconds = Number(match[2]);
    if (Number.isFinite(seconds)) timings.set(match[1], { stage: match[1], seconds, details: match[3] ?? "" });
  }
  return [...timings.values()];
}

function parseActiveSpectrogramStage(log: string | undefined): string | null {
  if (!log) return null;
  let active: string | null = null;
  for (const line of log.split(/\r?\n/)) {
    const started = line.match(/^PROGRESS stage=([a-z_]+) state=started$/);
    if (started) active = started[1];
    const completed = line.match(/^TIMING stage=([a-z_]+) seconds=/);
    if (completed?.[1] === active) active = null;
  }
  return active;
}

function formatStageDuration(seconds: number) {
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}m ${String(rounded % 60).padStart(2, "0")}s`;
}

function hsbToHex([hue, saturation, brightness]: [number, number, number]) {
  const saturationUnit = Math.max(0, Math.min(100, saturation)) / 100;
  const brightnessUnit = Math.max(0, Math.min(100, brightness)) / 100;
  const chroma = brightnessUnit * saturationUnit;
  const segment = ((hue % 360) + 360) % 360 / 60;
  const secondary = chroma * (1 - Math.abs(segment % 2 - 1));
  const offset = brightnessUnit - chroma;
  const [red, green, blue] = segment < 1 ? [chroma, secondary, 0] : segment < 2 ? [secondary, chroma, 0] : segment < 3 ? [0, chroma, secondary] : segment < 4 ? [0, secondary, chroma] : segment < 5 ? [secondary, 0, chroma] : [chroma, 0, secondary];
  return `#${[red, green, blue].map((value) => Math.round((value + offset) * 255).toString(16).padStart(2, "0")).join("")}`;
}

function hexToHsb(value: string): [number, number, number] {
  const hex = value.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(hex)) return [0, 0, 0];
  const [red, green, blue] = [0, 2, 4].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
  const highest = Math.max(red, green, blue);
  const lowest = Math.min(red, green, blue);
  const delta = highest - lowest;
  let hue = 0;
  if (delta) hue = highest === red ? 60 * (((green - blue) / delta) % 6) : highest === green ? 60 * ((blue - red) / delta + 2) : 60 * ((red - green) / delta + 4);
  return [Math.round((hue + 360) % 360), Math.round((highest ? delta / highest : 0) * 100), Math.round(highest * 100)];
}

function LoudnessValues({ loudness }: { loudness: Role["loudness"] }) {
  if (!loudness) return <>No stem</>;
  if (loudness.error) return <>{loudness.error}</>;
  const minimumWarning = loudness.minimum_dbfs !== null && loudness.minimum_dbfs > -40;
  return <><span className={minimumWarning ? "loudness-minimum is-high" : "loudness-minimum"} title={minimumWarning ? "Minimum active loudness is above -40 dBFS" : undefined}>{formatDb(loudness.minimum_dbfs)}</span> / {formatDb(loudness.median_dbfs)} / {formatDb(loudness.average_dbfs)} / {formatDb(loudness.maximum_dbfs)}</>;
}

function ReviewTrackTable({ roles, selectedRole, enabledRoles, onToggleRole, onSelectRole, onTuneRole, final, setError }: { roles: Role[]; selectedRole: string | undefined; enabledRoles: string[]; onToggleRole(role: string): void; onSelectRole(role: string): void; onTuneRole(role: string): void; final: SongInspection["final_loudness"] | undefined; setError(value: string): void }) {
  return <section className="review-stats review-track-table" aria-label="Track review statistics"><table><colgroup><col className="review-col-controls" /><col className="review-col-role" /><col className="review-col-status" /><col className="review-col-midi" /><col className="review-col-pitch" /><col className="review-col-short" /><col /><col className="review-col-peak" /></colgroup><thead><tr><th>Render</th><th>Role</th><th>Status</th><th>MIDI</th><th>DECtalk / audible</th><th className="review-short-note-count" title="Raw MIDI notes shorter than 150 milliseconds compared with all notes in the track">&lt;150 ms / total</th><th>Active loudness min / median / avg / max</th><th>Peak</th></tr></thead><tbody>{roles.map((item) => {
    const enabled = enabledRoles.includes(item.role);
    const eligibilityMessage = item.render_eligible
      ? enabled ? `Exclude ${item.role} from rendering` : `Include ${item.role} in rendering`
      : item.details[0] ?? `${item.role} needs valid MIDI plus lyrics before it can render`;
    return <tr key={item.role} className={item.role === selectedRole ? "selected" : ""} onClick={() => onSelectRole(item.role)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectRole(item.role); } }} tabIndex={0} role="button" aria-pressed={item.role === selectedRole}>
      <td className="review-row-controls" onClick={(event) => event.stopPropagation()}><label title={eligibilityMessage}><input type="checkbox" checked={enabled} disabled={!item.render_eligible} onChange={() => onToggleRole(item.role)} /><span className="sr-only">{enabled ? "Enabled" : "Disabled"}</span></label><button className="review-stem-play" type="button" disabled={!item.stem_exists} title={item.stem_exists ? `Open ${item.role} stem in the default media player` : `${item.role} has no rendered stem yet`} aria-label={item.stem_exists ? `Open ${item.role} stem in the default media player` : `${item.role} has no rendered stem yet`} onClick={() => void openMedia(item.stem_path).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))}><Play size={12} /></button><button className="review-tune-control" type="button" title={`Tune ${item.role}`} aria-label={`Tune ${item.role}`} onClick={() => onTuneRole(item.role)}><Settings2 size={13} /></button></td>
      <th>{item.role}</th><td>{item.status}</td><td><PitchRange value={item.midi_range} /></td><td><div className="pitch-range-stack"><PitchRange label="render" value={item.render_range} /><PitchRange label="heard" value={item.audible_range} /></div></td><td className="review-short-note-count" title={`${item.notes_below_150ms} of ${item.note_count} raw MIDI notes are shorter than 150 ms`}><strong>{item.notes_below_150ms}</strong><span> / {item.note_count}</span></td><td><LoudnessValues loudness={item.loudness} /></td><td>{formatDb(item.loudness?.peak_dbfs)}</td>
    </tr>;
  })}</tbody></table><div className="mix-loudness"><strong>Final mix</strong><span>{final ? final.error ?? <><span className={final.minimum_dbfs !== null && final.minimum_dbfs > -40 ? "loudness-minimum is-high" : "loudness-minimum"} title={final.minimum_dbfs !== null && final.minimum_dbfs > -40 ? "Minimum active loudness is above -40 dBFS" : undefined}>{formatDb(final.minimum_dbfs)} min</span> / {formatDb(final.median_dbfs)} median / {formatDb(final.average_dbfs)} average / {formatDb(final.maximum_dbfs)} max / {formatDb(final.peak_dbfs)} peak</> : "No completed mix available"}</span></div></section>;
}

function ReviewStage({ song, role, inspection, enabledRoles, onEnabledRolesChange, onSelectRole, setInspection, busy, setBusy, setError }: { song: string; role: Role | null; inspection: SongInspection | null; enabledRoles: string[]; onEnabledRolesChange(roles: string[]): void; onSelectRole(role: string): void; setInspection(value: SongInspection | null): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [panel, setPanel] = useState<"overview" | "tune" | "visuals">("overview");
  const [visualRoleName, setVisualRoleName] = useState("");
  const [position, setPosition] = useState<[number, number, number]>([0.5, 0.25, 0.25]);
  const [hsb, setHsb] = useState<[number, number, number]>([0, 100, 100]);
  const [visualDrafts, setVisualDrafts] = useState<Record<string, VisualDraft>>({});
  const [visualDirtyRoles, setVisualDirtyRoles] = useState<string[]>([]);
  const [visualSavedRoles, setVisualSavedRoles] = useState<string[]>([]);
  const [visualSaving, setVisualSaving] = useState(false);
  const [spectrogramVideoSettings, setSpectrogramVideoSettings] = useState<SpectrogramVideoSettings | null>(null);
  const [spectrogramVideoSettingsSaving, setSpectrogramVideoSettingsSaving] = useState(false);
  const visualDraftsRef = useRef(visualDrafts);
  const visualSaveTimers = useRef(new Map<string, number>());
  const visualSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const [job, setJob] = useState<SpectrogramJobStatus | null>(null);
  const [spectrogramStageClock, setSpectrogramStageClock] = useState({ stage: "", startedAt: 0, now: 0 });
  const [renderJob, setRenderJob] = useState<RenderJobStatus | null>(null);
  const [tuning, setTuning] = useState<TrackTuning | null>(null);
  const [savedTuning, setSavedTuning] = useState<TrackTuning | null>(null);
  const tuningCache = useRef(new Map<string, TrackTuning>());
  const visualDrag = useRef<{ pointerId: number; role: string; mode: "move" | "resize"; startX: number; startY: number; bounds: DOMRect; position: [number, number, number] } | null>(null);
  const visualPreviewFrameRef = useRef<HTMLDivElement>(null);
  const [visualDragging, setVisualDragging] = useState(false);
  const [visualPreviewSize, setVisualPreviewSize] = useState<{ width: number; height: number } | null>(null);
  const monitorWidth = Math.max(1, window.screen.width);
  const monitorHeight = Math.max(1, window.screen.height);
  const monitorAspect = monitorWidth / monitorHeight;
  const monitorAspectRatio = `${monitorWidth} / ${monitorHeight}`;
  const enabledVisualRoles = inspection?.roles.filter((item) => enabledRoles.includes(item.role)) ?? [];
  const visualRole = enabledVisualRoles.find((item) => item.role === visualRoleName) ?? null;
  const visualOptions = visualRole
    ? visualDrafts[visualRole.role]?.options ?? roleVisualOptions(visualRole)
    : { label: "", label_enabled: false, label_position: "top-left", label_show_voice: false, label_show_head_size: false, label_font: "choir", label_font_size_percent: 7, current_word_enabled: false, current_word_position: "bottom-center", current_word_font: "choir", current_word_font_size_percent: 10, current_word_use_track_color: false };
  useEffect(() => {
    visualSaveTimers.current.forEach((timer) => window.clearTimeout(timer));
    visualSaveTimers.current.clear();
    setPanel("overview");
    setVisualRoleName("");
    setVisualDrafts({});
    setVisualDirtyRoles([]);
    setVisualSavedRoles([]);
    setJob(null);
    setSpectrogramVideoSettings(null);
    visualDraftsRef.current = {};
  }, [song]);
  useEffect(() => {
    if (panel !== "tune") return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPanel("overview");
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [panel]);
  useEffect(() => {
    if (!song) return;
    let cancelled = false;
    bridge<SpectrogramVideoSettings>({ command: "get_spectrogram_video_settings", song }).then((next) => {
      if (!cancelled) setSpectrogramVideoSettings(next);
    }).catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { cancelled = true; };
  }, [song, setError]);
  useEffect(() => { setRenderJob(null); }, [inspection?.song_name]);
  useEffect(() => {
    const frame = visualPreviewFrameRef.current;
    if (panel !== "visuals" || !frame) return;
    const fitPreview = () => {
      const bounds = frame.getBoundingClientRect();
      let width = Math.max(1, bounds.width);
      let height = width / monitorAspect;
      if (height > bounds.height) {
        height = Math.max(1, bounds.height);
        width = height * monitorAspect;
      }
      setVisualPreviewSize({ width: Math.floor(width), height: Math.floor(height) });
    };
    fitPreview();
    const observer = new ResizeObserver(fitPreview);
    observer.observe(frame);
    return () => observer.disconnect();
  }, [panel, monitorAspect]);
  useEffect(() => {
    if (!visualRole) return;
    const draft = visualDraftsRef.current[visualRole.role];
    setPosition(draft?.position ?? visualRole.visual_position);
    setHsb(draft?.hsb ?? visualRole.visual_hsb);
  }, [visualRoleName, visualRole?.visual_position, visualRole?.visual_hsb]);
  useEffect(() => {
    if (!song || !role) {
      setTuning(null);
      setSavedTuning(null);
      return;
    }
    const cacheKey = `${song}:${role.role}`;
    const cached = tuningCache.current.get(cacheKey);
    if (cached) {
      setTuning(cached);
      setSavedTuning(cached);
      return;
    }
    let cancelled = false;
    setTuning(null);
    setSavedTuning(null);
    // Let the selected table row paint before requesting role-specific settings.
    const timer = window.setTimeout(() => bridge<TrackTuning>({ command: "get_track_tuning", song, role: role.role }).then((next) => {
      tuningCache.current.set(cacheKey, next);
      if (!cancelled) {
        setTuning(next);
        setSavedTuning(next);
      }
    }).catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); }), 80);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [song, role?.role, setError]);
  const changeTriplet = (setValue: (value: [number, number, number]) => void, source: [number, number, number], index: number, value: string) => {
    const next = [...source] as [number, number, number];
    next[index] = Number(value);
    setValue(next);
  };
  const persistVisualRole = (targetRole: string, targetSong = song) => {
    const existingTimer = visualSaveTimers.current.get(targetRole);
    if (existingTimer !== undefined) window.clearTimeout(existingTimer);
    visualSaveTimers.current.delete(targetRole);
    visualSaveQueue.current = visualSaveQueue.current.then(async () => {
      const draft = visualDraftsRef.current[targetRole];
      if (!draft) return;
      setVisualSaving(true);
      try {
        await bridge({ command: "save_visual_layout", song: targetSong, role: targetRole, position: draft.position, hsb: draft.hsb, options: draft.options });
        if (visualDraftsRef.current[targetRole] === draft) {
          setVisualDirtyRoles((current) => current.filter((item) => item !== targetRole));
          setVisualSavedRoles((current) => current.includes(targetRole) ? current : [...current, targetRole]);
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setVisualSaving(false);
      }
    });
  };
  const queueVisualAutosave = (targetRole: string) => {
    const existingTimer = visualSaveTimers.current.get(targetRole);
    if (existingTimer !== undefined) window.clearTimeout(existingTimer);
    setVisualDirtyRoles((current) => current.includes(targetRole) ? current : [...current, targetRole]);
    const targetSong = song;
    const timer = window.setTimeout(() => persistVisualRole(targetRole, targetSong), 450);
    visualSaveTimers.current.set(targetRole, timer);
  };
  const updateVisualPosition = (next: [number, number, number], targetRole = visualRoleName) => {
    if (targetRole === visualRoleName) setPosition(next);
    if (!targetRole) return;
    const target = inspection?.roles.find((item) => item.role === targetRole);
    const updated = { ...visualDraftsRef.current, [targetRole]: { position: next, hsb: visualDraftsRef.current[targetRole]?.hsb ?? (target?.visual_hsb ?? hsb), options: visualDraftsRef.current[targetRole]?.options ?? (target ? roleVisualOptions(target) : visualOptions) } };
    visualDraftsRef.current = updated;
    setVisualDrafts(updated);
    queueVisualAutosave(targetRole);
  };
  const updateVisualHsb = (next: [number, number, number], targetRole = visualRoleName) => {
    if (targetRole === visualRoleName) setHsb(next);
    if (!targetRole) return;
    const target = inspection?.roles.find((item) => item.role === targetRole);
    const updated = { ...visualDraftsRef.current, [targetRole]: { position: visualDraftsRef.current[targetRole]?.position ?? (target?.visual_position ?? position), hsb: next, options: visualDraftsRef.current[targetRole]?.options ?? (target ? roleVisualOptions(target) : visualOptions) } };
    visualDraftsRef.current = updated;
    setVisualDrafts(updated);
    queueVisualAutosave(targetRole);
  };
  const updateVisualOptions = (next: VisualTextOptions, targetRole = visualRoleName) => {
    if (!targetRole) return;
    const target = inspection?.roles.find((item) => item.role === targetRole);
    const updated = { ...visualDraftsRef.current, [targetRole]: { position: visualDraftsRef.current[targetRole]?.position ?? (target?.visual_position ?? position), hsb: visualDraftsRef.current[targetRole]?.hsb ?? (target?.visual_hsb ?? hsb), options: next } };
    visualDraftsRef.current = updated;
    setVisualDrafts(updated);
    queueVisualAutosave(targetRole);
  };
  const beginVisualDrag = (event: ReactPointerEvent<HTMLButtonElement | HTMLSpanElement>, item: Role, mode: "move" | "resize") => {
    const preview = event.currentTarget.closest(".visual-layout-preview");
    if (!(preview instanceof HTMLElement)) return;
    event.preventDefault();
    event.stopPropagation();
    setVisualRoleName(item.role);
    const draft = visualDraftsRef.current[item.role];
    const startPosition = draft?.position ?? item.visual_position;
    const startHsb = draft?.hsb ?? item.visual_hsb;
    setPosition(startPosition);
    setHsb(startHsb);
    event.currentTarget.setPointerCapture(event.pointerId);
    visualDrag.current = { pointerId: event.pointerId, role: item.role, mode, startX: event.clientX, startY: event.clientY, bounds: preview.getBoundingClientRect(), position: startPosition };
    setVisualDragging(true);
  };
  const updateVisualDrag = (event: ReactPointerEvent<HTMLButtonElement | HTMLSpanElement>) => {
    const drag = visualDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    const dx = (event.clientX - drag.startX) / Math.max(1, drag.bounds.width);
    const dy = (event.clientY - drag.startY) / Math.max(1, drag.bounds.height);
    const [size, left, top] = drag.position;
    if (drag.mode === "move") {
      updateVisualPosition([size, Math.max(0, Math.min(1 - size, left + dx)), Math.max(0, Math.min(1 - size, top + dy))], drag.role);
      return;
    }
    const delta = Math.abs(dx) >= Math.abs(dy) ? dx : dy;
    updateVisualPosition([Math.max(0.05, Math.min(1 - left, 1 - top, size + delta)), left, top], drag.role);
  };
  const finishVisualDrag = (event: ReactPointerEvent<HTMLButtonElement | HTMLSpanElement>) => {
    const drag = visualDrag.current;
    if (drag?.pointerId !== event.pointerId) return;
    visualDrag.current = null;
    setVisualDragging(false);
    persistVisualRole(drag.role);
  };
  const refresh = async () => {
    const next = await bridge<SongInspection>({ command: "inspect_song", song });
    setInspection(next);
  };
  useEffect(() => {
    if (job?.state !== "running") return;
    const timer = window.setInterval(() => {
      void spectrogramJobStatus().then((next) => setJob(next)).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
    }, 750);
    return () => window.clearInterval(timer);
  }, [job?.state, setError]);
  const activeSpectrogramStage = job?.state === "running" ? parseActiveSpectrogramStage(job.log) : null;
  useEffect(() => {
    if (!activeSpectrogramStage) {
      setSpectrogramStageClock({ stage: "", startedAt: 0, now: 0 });
      return;
    }
    setSpectrogramStageClock((current) => current.stage === activeSpectrogramStage
      ? current
      : { stage: activeSpectrogramStage, startedAt: performance.now(), now: performance.now() });
  }, [activeSpectrogramStage]);
  useEffect(() => {
    if (!activeSpectrogramStage) return;
    const timer = window.setInterval(() => setSpectrogramStageClock((current) => ({ ...current, now: performance.now() })), 250);
    return () => window.clearInterval(timer);
  }, [activeSpectrogramStage]);
  useEffect(() => {
    if (renderJob?.state !== "running") return;
    const timer = window.setInterval(() => {
      void renderJobStatus().then((next) => setRenderJob(next)).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
    }, 500);
    return () => window.clearInterval(timer);
  }, [renderJob?.state, setError]);
  useEffect(() => {
    if (job?.state === "completed") void refresh();
    if (job?.state === "failed") setError(job.message);
  }, [job?.state]);
  useEffect(() => {
    if (renderJob?.state === "completed") void refresh();
    if (renderJob?.state === "failed") setError(renderJob.message);
  }, [renderJob?.state]);
  const generate = async () => {
    if (!enabledRoles.length) {
      setError("Enable at least one renderable track before generating a spectrogram video.");
      return;
    }
    setBusy("Saving spectrogram layout");
    setError("");
    try {
      visualSaveTimers.current.forEach((timer) => window.clearTimeout(timer));
      visualSaveTimers.current.clear();
      await visualSaveQueue.current;
      const pendingLayouts = enabledVisualRoles.map((item) => ({ role: item.role, layout: visualDraftsRef.current[item.role] ?? { position: item.visual_position, hsb: item.visual_hsb, options: roleVisualOptions(item) } }));
      for (const { role: draftRole, layout } of pendingLayouts) {
        await bridge({ command: "save_visual_layout", song, role: draftRole, position: layout.position, hsb: layout.hsb, options: layout.options });
      }
      if (pendingLayouts.length) {
        setVisualDirtyRoles([]);
        setVisualSavedRoles(pendingLayouts.map((item) => item.role));
        await refresh();
      }
      setJob(await startSpectrogramJob(song, enabledRoles));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); }
  };
  const updateIntermediateAnimationMode = async (mode: IntermediateAnimationMode) => {
    if (!song || !spectrogramVideoSettings) return;
    const previous = spectrogramVideoSettings;
    setSpectrogramVideoSettings({ intermediate_animation_mode: mode });
    setSpectrogramVideoSettingsSaving(true);
    setError("");
    try {
      const saved = await bridge<SpectrogramVideoSettings>({
        command: "update_spectrogram_video_settings",
        song,
        intermediate_animation_mode: mode,
      });
      setSpectrogramVideoSettings(saved);
    } catch (cause) {
      setSpectrogramVideoSettings(previous);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSpectrogramVideoSettingsSaving(false);
    }
  };
  const toggleRenderRole = (targetRole: string) => {
    onEnabledRolesChange(enabledRoles.includes(targetRole) ? enabledRoles.filter((item) => item !== targetRole) : [...enabledRoles, targetRole]);
  };
  const changeTuning = (key: keyof TrackTuning, value: TrackTuning[keyof TrackTuning]) => {
    setTuning((current) => current ? { ...current, [key]: value } : current);
  };
  const tuningDirty = !trackTuningMatches(tuning, savedTuning);
  const saveTuning = async () => {
    if (!song || !role || !tuning || !tuningDirty) return;
    setBusy(`Saving ${role.role} tuning`); setError("");
    try {
      const result = await bridge<{ values: TrackTuning }>({ command: "update_track_tuning", song, role: role.role, values: tuning });
      tuningCache.current.set(`${song}:${role.role}`, result.values);
      setTuning(result.values);
      setSavedTuning(result.values);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const resetTuning = async () => {
    if (!song || !role) return;
    setBusy(`Reloading ${role.role} tuning`); setError("");
    try {
      const cacheKey = `${song}:${role.role}`;
      tuningCache.current.delete(cacheKey);
      const next = await bridge<TrackTuning>({ command: "get_track_tuning", song, role: role.role });
      tuningCache.current.set(cacheKey, next);
      setTuning(next);
      setSavedTuning(next);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const render = async () => {
    if (!song || !enabledRoles.length) {
      setError("Select at least one track to render.");
      return;
    }
    setError("");
    try {
      setRenderJob(await startRenderJob(song, enabledRoles));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const final = inspection?.final_loudness;
  const hasFinishedRender = Boolean(final);
  const renderState = renderJob?.state ?? "idle";
  const renderStatusLabel = renderState === "completed" ? "Render complete" : renderState === "running" ? "Rendering" : renderState === "failed" ? "Render failed" : "Ready to render";
  const renderStatusMessage = renderJob?.message ?? "Settings are saved in settings.yaml";
  const renderStatusIcon = renderState === "completed" ? <CircleCheck size={17} /> : renderState === "failed" ? <CircleAlert size={17} /> : renderState === "running" ? <LoaderCircle size={17} /> : <FileAudio size={17} />;
  const spectrogramTimings = parseSpectrogramStageTimings(job?.log);
  const spectrogramStageLabels: Record<string, string> = { setup: "Setup", parallel_tracks: "Track clips", composition: "Composite", cleanup: "Cleanup", total: "Total" };
  const activeSpectrogramElapsed = spectrogramStageClock.stage === activeSpectrogramStage
    ? Math.max(0, (spectrogramStageClock.now - spectrogramStageClock.startedAt) / 1000)
    : 0;
  return <section className={`review-stage ${panel}-panel`}>
    <header className="surface-header review-header"><div className="review-identity"><p className="eyebrow">Output review</p><h1>{song || "Select a song"}<span>{role?.role ?? "Select a role"}</span></h1><p>Enable renderable tracks in the table; tune an individual role from its cog.</p></div><section className={`review-render ${renderState}`} aria-label="Render selected tracks"><div className="render-status"><div className="render-status-state">{renderStatusIcon}<span>{renderStatusLabel}</span></div><strong>{enabledRoles.length} tracks enabled</strong><span className="render-status-message" title={renderStatusMessage}>{renderStatusMessage}</span></div><div className="review-render-actions"><button className="primary" type="button" onClick={() => void render()} disabled={renderState === "running" || !enabledRoles.length}>{renderState === "running" ? "Rendering in background..." : <><FileAudio size={16} /> Render enabled tracks <span className="render-duration">{formatDuration(inspection?.midi?.duration_seconds)}</span></>}</button><button className="secondary spectrogram-layout-command" type="button" onClick={() => { const initialRole = enabledVisualRoles.some((item) => item.role === visualRoleName) ? visualRoleName : enabledVisualRoles[0]?.role ?? ""; setVisualRoleName(initialRole); setPanel("visuals"); }} disabled={!hasFinishedRender || !enabledVisualRoles.length}><BarChart3 size={15} /> Spectrogram layout</button></div></section></header>
    {panel !== "overview" && <nav className="review-panel-nav" aria-label="Render audio workspace">
      <button className="secondary review-panel-back" type="button" onClick={() => setPanel("overview")}><ArrowLeft size={15} /><BarChart3 size={15} /> Output overview</button>
      {panel === "tune" && <span>Editing {role?.role ?? "the selected role"} tuning profile</span>}
      {panel === "visuals" && <><span>{enabledVisualRoles.length} enabled render region{enabledVisualRoles.length === 1 ? "" : "s"}. Select regions directly on the canvas.</span><div className="visual-header-actions"><div className="spectrogram-stage-progress" role="status" aria-live="polite">{spectrogramTimings.map((timing) => <span key={timing.stage} className={timing.stage === "total" ? "total" : "complete"} title={timing.details || undefined}><small>{spectrogramStageLabels[timing.stage] ?? timing.stage.replace(/_/g, " ")}</small>{formatStageDuration(timing.seconds)}</span>)}{activeSpectrogramStage && <span className="running"><LoaderCircle size={12} /><small>{spectrogramStageLabels[activeSpectrogramStage] ?? activeSpectrogramStage.replace(/_/g, " ")}</small>{formatStageDuration(activeSpectrogramElapsed)}</span>}</div><button className="primary" type="button" onClick={() => void generate()} disabled={!!busy || visualSaving || !enabledRoles.length || job?.state === "running"}>{job?.state === "running" ? "Generating video..." : <><BarChart3 size={16} /> Generate spectrograms</>}</button><button className="secondary" type="button" onClick={() => void openMedia(inspection!.animation_path!)} disabled={!!busy || !inspection?.animation_exists || !inspection.animation_path}><Play size={15} /> Open video</button></div></>}
    </nav>}
    {panel === "tune" && <button className="tuning-modal-backdrop" type="button" onClick={() => setPanel("overview")} aria-label="Close track tuning" />}
    <section className="range-legend" aria-label="Register color legend"><strong>Register color</strong><span className="range-legend-blue">C2 low</span><span className="range-legend-green">C3 mid</span><span className="range-legend-yellow">C4 mid-high</span><span className="range-legend-orange">C5 weak</span><span className="range-legend-red">C6+ weakest</span></section>
    <ReviewTrackTable roles={inspection?.roles ?? []} selectedRole={role?.role} enabledRoles={enabledRoles} onToggleRole={toggleRenderRole} onSelectRole={onSelectRole} onTuneRole={(nextRole) => { onSelectRole(nextRole); setPanel("tune"); }} final={final} setError={setError} />
    <section className="review-stats" aria-label="Track review statistics"><table><thead><tr><th>Role</th><th>Status</th><th>Notes</th><th>MIDI</th><th>DECtalk / audible</th><th>Poly</th><th>Active loudness min / median / avg / max</th><th>Peak</th></tr></thead><tbody>{inspection?.roles.map((item) => <tr key={item.role} className={item.role === role?.role ? "selected" : ""} onClick={() => onSelectRole(item.role)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectRole(item.role); } }} tabIndex={0} role="button" aria-pressed={item.role === role?.role}><th>{item.role}</th><td>{item.status}</td><td>{item.note_count}</td><td><PitchRange value={item.midi_range} /></td><td><div className="pitch-range-stack"><PitchRange label="render" value={item.render_range} /><PitchRange label="heard" value={item.audible_range} /></div></td><td>{item.polyphony ?? "--"}</td><td>{item.loudness ? item.loudness.error ?? `${formatDb(item.loudness.minimum_dbfs)} / ${formatDb(item.loudness.median_dbfs)} / ${formatDb(item.loudness.average_dbfs)} / ${formatDb(item.loudness.maximum_dbfs)}` : "No stem"}</td><td>{formatDb(item.loudness?.peak_dbfs)}</td></tr>)}</tbody></table><div className="mix-loudness"><strong>Final mix</strong><span>{final ? final.error ?? `${formatDb(final.minimum_dbfs)} min · ${formatDb(final.median_dbfs)} median · ${formatDb(final.average_dbfs)} average · ${formatDb(final.maximum_dbfs)} max · ${formatDb(final.peak_dbfs)} peak` : "No completed mix available"}</span></div></section>
    {renderJob && renderJob.state !== "idle" && <details className="generated review-log" open={renderJob.state !== "completed"}><summary>{renderJob.message} <span>{renderJob.selected_roles.join(", ")} · {renderJob.state === "running" ? "background job" : `exit ${renderJob.returncode ?? "--"}`}</span></summary><pre>{renderJob.log || "The renderer is starting; live compiler output will appear here."}</pre></details>}
    <section className="track-tuning" role="dialog" aria-modal="true" aria-label={`Tune ${role?.role ?? "selected role"}`}>
      <header className="tuning-modal-header"><span>Track tuning</span><strong>{role?.role ?? "Select a role"}</strong><small>Saved to this role in `settings.yaml`</small></header>
      <button className="tuning-modal-close" type="button" onClick={() => setPanel("overview")} title="Close track tuning" aria-label="Close track tuning"><X size={17} /></button>
      {tuning && <div className="tuning-body">
        <p className="tuning-guide"><strong>Value reference</strong><span>0 means no adjustment. Pitch values are semitones; +12 / -12 equals one octave. DECtalk pitch index: C3 = 0, C5 = 24, C6 = 36.</span></p>
        <section><h2>Pitch</h2>
          <label className="tuning-field" title="Transpose the final musical output after MIDI-to-DECTALK mapping."><span>Pitch shift <small>semitones</small></span><div className="tuning-input"><input type="number" min="-24" max="24" step="1" value={tuning.PITCH_SHIFT} onChange={(event) => changeTuning("PITCH_SHIFT", Number(event.target.value))} /><output>st</output></div><em>0 keeps the MIDI pitch. +12 raises the final voice one octave.</em></label>
          <label className="tuning-field" title="Moves the temporary DECTALK render register, then speed-corrects the WAV back to the MIDI pitch."><span>Octave boost <small>semitones</small></span><div className="tuning-input"><input type="number" min="-48" max="48" step="12" value={tuning.OCTAVE_BOOST} onChange={(event) => changeTuning("OCTAVE_BOOST", Number(event.target.value))} /><output>st</output></div><em>0 is direct. +12 renders one octave lower, then restores the intended final octave.</em></label>
          <label className="tuning-field" title="Overrides automatic whole-octave wrapping into the configured DECTALK pitch bounds."><span>Pitch wrap <small>whole octaves</small></span><div className="tuning-input"><select value={tuning.PITCH_WRAP_SHIFT ?? "auto"} onChange={(event) => changeTuning("PITCH_WRAP_SHIFT", event.target.value === "auto" ? null : Number(event.target.value))}><option value="auto">Auto (recommended)</option><option value="-24">-24 st (2 octaves)</option><option value="-12">-12 st (1 octave)</option><option value="0">0 st (do not wrap)</option><option value="12">+12 st (1 octave)</option><option value="24">+24 st (2 octaves)</option></select></div><em>Leave on Auto unless you are intentionally overriding the safe pitch wrap.</em></label>
        </section>
        <section><h2>Level</h2>
          <label className="tuning-field" title="Beta: replaces only the [:n?] voice command in DEC_SETUP and preserves head size plus other DECtalk directives."><span>Voice <small><b className="beta-badge">Beta</b> DECtalk command</small></span><div className="tuning-input"><select value={tuning.VOICE ?? ""} onChange={(event) => changeTuning("VOICE", event.target.value || null)}><option value="">DECtalk default</option><option value="np">[:np] Perfect Paul</option><option value="nb">[:nb] DECtalk voice</option><option value="nh">[:nh] DECtalk voice</option><option value="nd">[:nd] DECtalk voice</option><option value="nf">[:nf] DECtalk voice</option><option value="nu">[:nu] DECtalk voice</option><option value="nr">[:nr] DECtalk voice</option><option value="nw">[:nw] DECtalk voice</option><option value="nk">[:nk] DECtalk voice</option></select></div><em>Changes only the voice command. Note loudness is compensated automatically for every voice.</em></label>
          <label className="tuning-field" title="Writes [:dv hs N] into DEC_SETUP. Head size affects voice timbre and loudness; it is not a gain control."><span>Head size <small>DECTALK hs</small></span><div className="tuning-input"><input type="number" min="65" max="200" step="1" value={tuning.HEAD_SIZE ?? ""} placeholder="Set head size" onChange={(event) => changeTuning("HEAD_SIZE", event.target.value === "" ? null : Number(event.target.value))} /><output>hs</output></div><em>This engine clamps lower values to hs 65. Calibration covers hs 65 through 140.</em></label>
          <label className="tuning-field" title="Applies a constant gain to the complete rendered stem."><span>Stem gain <small>decibels</small></span><div className="tuning-input"><input type="number" min="-24" max="24" step="0.5" value={tuning.VOLUME_ADJUST_DB} onChange={(event) => changeTuning("VOLUME_ADJUST_DB", Number(event.target.value))} /><output>dB</output></div><em>0 dB leaves the stem level unchanged. Positive is louder.</em></label>
        </section>
        <section><h2>Note guard</h2>
          <label className="tuning-toggle" title="Leave checked to keep all MIDI velocities from changing rendered loudness."><input type="checkbox" checked={tuning.IGNORE_MIDI_VELOCITY} onChange={(event) => changeTuning("IGNORE_MIDI_VELOCITY", event.target.checked)} /> Ignore MIDI velocity <small>default: on; no hidden dynamic gain</small></label>
          <label className="tuning-field" title="Dynamic range derived from average MIDI velocity when Ignore MIDI velocity is off."><span>Velocity dynamic range <small>opt-in</small></span><div className="tuning-input"><input type="number" min="0" max="24" step="0.5" disabled={tuning.IGNORE_MIDI_VELOCITY} value={tuning.VELOCITY_VOLUME_SCALE_DB} onChange={(event) => changeTuning("VELOCITY_VOLUME_SCALE_DB", Number(event.target.value))} /><output>dB</output></div><em>0 adds no velocity response. Increase only after unchecking Ignore MIDI velocity.</em></label>
          <p className="tuning-guide"><strong>Automatic note leveling</strong><span>Each sung MIDI note is adjusted to a -5 dBFS peak after pitch correction. Stem gain remains the manual loudness control.</span></p>
          <label className="tuning-field" title="Final peak ceiling for the completed role stem after all phrases are assembled."><span>Stem peak ceiling <small>final role guard</small></span><div className="tuning-input"><input type="number" min="-60" max="0" step="0.5" value={tuning.STEM_PEAK_CEILING_DBFS} onChange={(event) => changeTuning("STEM_PEAK_CEILING_DBFS", Number(event.target.value))} /><output>dBFS</output></div><em>Last safety pass for this output track. -1 dBFS is the default.</em></label>
          <label className="tuning-field" title="Folds MIDI gaps at or below this duration into the preceding note."><span>Mend gaps <small>timing threshold</small></span><div className="tuning-input"><input type="number" min="0" max="100" step="1" value={tuning.GAP_MEND_MS} onChange={(event) => changeTuning("GAP_MEND_MS", Number(event.target.value))} /><output>ms</output></div><em>0 preserves every MIDI gap. Positive values close only short gaps.</em></label>
          <label className="tuning-field" title="Extends short notes into available silence without moving the following note onset."><span>Minimum note <small>rest-only floor</small></span><div className="tuning-input"><input type="number" min="0" max="1000" step="5" value={tuning.MINIMUM_NOTE_DURATION_MS} onChange={(event) => changeTuning("MINIMUM_NOTE_DURATION_MS", Number(event.target.value))} /><output>ms</output></div><em>0 preserves MIDI durations. A positive floor consumes only the following rest and never shifts later notes.</em></label>
          <label className="tuning-field" title="Caps the total time spent on ending consonants when one-vowel words span multiple notes."><span>Ending consonant cap <small>whole coda</small></span><div className="tuning-input"><input type="number" min="0" max="1000" step="5" value={tuning.CODA_MAX_MS} onChange={(event) => changeTuning("CODA_MAX_MS", Number(event.target.value))} /><output>ms</output></div><em>200 ms limits the complete ending cluster. Short notes naturally use less and return the remaining time to the vowel.</em></label>
        </section>
        <div className="tuning-actions"><button className="secondary" type="button" title="Discard unsaved fields and reload this role's settings.yaml profile" onClick={() => void resetTuning()} disabled={!!busy || !tuningDirty}>Reset</button><button className="primary" type="button" title={tuningDirty ? "Save these tuning changes to settings.yaml" : "No tuning changes to save"} onClick={() => void saveTuning()} disabled={!!busy || !tuningDirty}><Sparkles size={15} /> Save track tuning</button></div>
      </div>}
    </section>
    <section className="review-visualizer">
      <div className="visual-preview-column"><div className="visual-output-policy"><strong>After final MP4, working clips</strong><div className="visual-output-modes" role="group" aria-label="Intermediate animation mode">{([['delete', 'Delete', 'Remove the lossless per-track animation files after the final song MP4 is created.'], ['compress', 'Compress', 'Convert each lossless per-track animation to a smaller H.264 MP4, then remove its lossless source.'], ['keep', 'Keep lossless', 'Preserve the original lossless per-track animation files for later editing or recompositing.']] as [IntermediateAnimationMode, string, string][]).map(([mode, label, description]) => <button key={mode} type="button" title={description} aria-label={`${label}: ${description}`} className={spectrogramVideoSettings?.intermediate_animation_mode === mode ? "active" : ""} aria-pressed={spectrogramVideoSettings?.intermediate_animation_mode === mode} onClick={() => void updateIntermediateAnimationMode(mode)} disabled={!spectrogramVideoSettings || spectrogramVideoSettingsSaving || job?.state === "running"}>{label}</button>)}</div>{spectrogramVideoSettingsSaving && <LoaderCircle size={14} />}</div><div className="visual-preview-frame" ref={visualPreviewFrameRef}><div className={visualDragging ? "visual-layout-preview dragging" : "visual-layout-preview"} style={{ "--video-aspect": monitorAspectRatio, ...(visualPreviewSize ? { width: `${visualPreviewSize.width}px`, height: `${visualPreviewSize.height}px` } : {}) } as CSSProperties} aria-label={`Enabled spectrogram render regions at ${monitorWidth} by ${monitorHeight}`}>{enabledVisualRoles.map((item) => {
        const selected = item.role === visualRoleName;
        const draft = visualDrafts[item.role];
        const visualPosition = draft?.position ?? item.visual_position;
        const visualHsb = draft?.hsb ?? item.visual_hsb;
        const options = draft?.options ?? roleVisualOptions(item);
        const previewRegionHeight = (visualPreviewSize?.height ?? 360) * visualPosition[0];
        const labelParts = [options.label || item.role];
        if (options.label_show_voice) labelParts.push(item.dectalk_voice === "np" ? "Perfect Paul [:np]" : item.dectalk_voice ? `[:${item.dectalk_voice}]` : "default voice");
        if (options.label_show_head_size) labelParts.push(item.head_size ? `hs ${item.head_size}` : "head size default");
        const unsaved = visualDirtyRoles.includes(item.role) || (!item.visual_configured && !visualSavedRoles.includes(item.role));
        return <button key={item.role} type="button" className={`visual-region${selected ? " selected" : ""}${unsaved ? " unsaved" : ""}`} title={unsaved ? `Drag ${item.role} to position it; this layout is not saved yet` : `Drag ${item.role} to position it`} onPointerDown={(event) => beginVisualDrag(event, item, "move")} onPointerMove={updateVisualDrag} onPointerUp={finishVisualDrag} onPointerCancel={finishVisualDrag} onClick={() => setVisualRoleName(item.role)} style={{ left: `${visualPosition[1] * 100}%`, top: `${visualPosition[2] * 100}%`, width: `${visualPosition[0] * 100}%`, height: `${visualPosition[0] * 100}%`, background: `hsl(${visualHsb[0]} ${visualHsb[1]}% ${Math.max(18, visualHsb[2] / 2)}%)`, borderColor: `hsl(${visualHsb[0]} ${visualHsb[1]}% ${visualHsb[2]}%)` }}>
          <span className="visual-region-name">{item.role}</span>
          {options.label_enabled && <span className={`visual-text-preview ${options.label_position}`} style={{ fontFamily: visualFontFamily(options.label_font), fontSize: `${Math.max(7, previewRegionHeight * options.label_font_size_percent / 100)}px` }}>{labelParts.join(" | ")}</span>}
          {options.current_word_enabled && <span className={`visual-text-preview current-word ${options.current_word_position}`} style={{ color: options.current_word_use_track_color ? `hsl(${visualHsb[0]} ${visualHsb[1]}% ${visualHsb[2]}%)` : "#ffffff", fontFamily: visualFontFamily(options.current_word_font), fontSize: `${Math.max(7, previewRegionHeight * options.current_word_font_size_percent / 100)}px` }}>Current word</span>}
          {unsaved && <small className="visual-unsaved-badge">Not saved</small>}
          {selected && <span className="visual-resize-handle" title={`Drag to resize ${item.role}`} onPointerDown={(event) => beginVisualDrag(event, item, "resize")} onPointerMove={updateVisualDrag} onPointerUp={finishVisualDrag} onPointerCancel={finishVisualDrag} />}
        </button>;
      })}</div></div></div>
      <div className="visual-layout-controls">
        <div><p className="eyebrow">Spectrogram render layout</p><strong>{visualRole?.role ?? "Select an enabled region"}</strong><p>Drag a region to move it; drag its lower-right handle to resize. Numeric fields provide precise positioning.</p></div>
        <div className="visual-fields"><label>Color<input type="color" value={hsbToHex(hsb)} onChange={(event) => updateVisualHsb(hexToHsb(event.target.value))} disabled={!visualRole} /></label><label>Size<input type="number" min="0.05" max={Math.min(1 - position[1], 1 - position[2])} step="0.01" value={position[0]} onChange={(event) => changeTriplet(updateVisualPosition, position, 0, event.target.value)} disabled={!visualRole} /></label><label>Left<input type="number" min="0" max={1 - position[0]} step="0.01" value={position[1]} onChange={(event) => changeTriplet(updateVisualPosition, position, 1, event.target.value)} disabled={!visualRole} /></label><label>Top<input type="number" min="0" max={1 - position[0]} step="0.01" value={position[2]} onChange={(event) => changeTriplet(updateVisualPosition, position, 2, event.target.value)} disabled={!visualRole} /></label><label>Hue<input type="number" min="0" max="360" step="1" value={hsb[0]} onChange={(event) => changeTriplet(updateVisualHsb, hsb, 0, event.target.value)} disabled={!visualRole} /></label><label>Sat<input type="number" min="0" max="100" step="1" value={hsb[1]} onChange={(event) => changeTriplet(updateVisualHsb, hsb, 1, event.target.value)} disabled={!visualRole} /></label><label>Bright<input type="number" min="0" max="100" step="1" value={hsb[2]} onChange={(event) => changeTriplet(updateVisualHsb, hsb, 2, event.target.value)} disabled={!visualRole} /></label></div>
        <section className="visual-overlay-controls" aria-label="Spectrogram text overlays">
          <div className="visual-overlay-row"><label className="visual-overlay-toggle"><input type="checkbox" checked={visualOptions.label_enabled} onChange={(event) => updateVisualOptions({ ...visualOptions, label_enabled: event.target.checked })} disabled={!visualRole} /><span>Track label</span></label><input className="visual-label-input" value={visualOptions.label} onChange={(event) => updateVisualOptions({ ...visualOptions, label: event.target.value })} disabled={!visualRole || !visualOptions.label_enabled} aria-label="Track label text" maxLength={80} /><select value={visualOptions.label_position} onChange={(event) => updateVisualOptions({ ...visualOptions, label_position: event.target.value })} disabled={!visualRole || !visualOptions.label_enabled} aria-label="Track label position">{visualTextPositions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
          <div className="visual-overlay-row text-style"><span>Label style</span><select value={visualOptions.label_font} onChange={(event) => updateVisualOptions({ ...visualOptions, label_font: event.target.value })} disabled={!visualRole || !visualOptions.label_enabled} aria-label="Track label font">{visualFonts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><label className="visual-percent-input"><input type="number" min="2" max="25" step="0.5" value={visualOptions.label_font_size_percent} onChange={(event) => updateVisualOptions({ ...visualOptions, label_font_size_percent: Number(event.target.value) })} disabled={!visualRole || !visualOptions.label_enabled} aria-label="Track label font size percentage" /><span>% height</span></label></div>
          <div className="visual-overlay-row metadata"><span>Include</span><label><input type="checkbox" checked={visualOptions.label_show_voice} onChange={(event) => updateVisualOptions({ ...visualOptions, label_show_voice: event.target.checked })} disabled={!visualRole || !visualOptions.label_enabled} /> Voice</label><label><input type="checkbox" checked={visualOptions.label_show_head_size} onChange={(event) => updateVisualOptions({ ...visualOptions, label_show_head_size: event.target.checked })} disabled={!visualRole || !visualOptions.label_enabled} /> Head size</label></div>
          <div className="visual-overlay-row"><label className="visual-overlay-toggle"><input type="checkbox" checked={visualOptions.current_word_enabled} onChange={(event) => updateVisualOptions({ ...visualOptions, current_word_enabled: event.target.checked })} disabled={!visualRole} /><span>Current word</span></label><span className="visual-overlay-note">Uses applied alignment timing</span><select value={visualOptions.current_word_position} onChange={(event) => updateVisualOptions({ ...visualOptions, current_word_position: event.target.value })} disabled={!visualRole || !visualOptions.current_word_enabled} aria-label="Current word position">{visualTextPositions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
          <div className="visual-overlay-row text-style"><label className="visual-overlay-toggle"><input type="checkbox" checked={visualOptions.current_word_use_track_color} onChange={(event) => updateVisualOptions({ ...visualOptions, current_word_use_track_color: event.target.checked })} disabled={!visualRole || !visualOptions.current_word_enabled} /><span>Track color</span></label><select value={visualOptions.current_word_font} onChange={(event) => updateVisualOptions({ ...visualOptions, current_word_font: event.target.value })} disabled={!visualRole || !visualOptions.current_word_enabled} aria-label="Current word font">{visualFonts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><label className="visual-percent-input"><input type="number" min="2" max="25" step="0.5" value={visualOptions.current_word_font_size_percent} onChange={(event) => updateVisualOptions({ ...visualOptions, current_word_font_size_percent: Number(event.target.value) })} disabled={!visualRole || !visualOptions.current_word_enabled} aria-label="Current word font size percentage" /><span>% height</span></label></div>
        </section>
        <div className="visual-save-state" role="status" aria-live="polite">{visualSaving || visualDirtyRoles.length ? <><LoaderCircle size={14} /> Saving layout...</> : <><CircleCheck size={14} /> Layout saved automatically</>}</div>
      </div>
    </section>
    {job && job.state !== "idle" && <details className="generated review-log spectrogram-log" open><summary>{job.message} <span>{job.state === "running" ? "background job" : `exit ${job.returncode ?? "--"}`} - process log</span></summary><pre>{job.log || "The generator is starting; live process output will appear here."}</pre></details>}
  </section>;
}
