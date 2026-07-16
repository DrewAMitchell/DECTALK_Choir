import { useCallback, useEffect, useMemo, useRef, useState, useTransition, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { ArrowLeft, BarChart3, ChevronsLeft, ChevronsRight, CircleAlert, CircleCheck, FileAudio, FolderOpen, LoaderCircle, Minus, Moon, Music2, PanelLeft, Pause, PenLine, Play, Plus, Settings2, Sparkles, Square, Sun, Trash2, WandSparkles, X } from "lucide-react";
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
type TranscriptState = { text: string };
type CandidateState = { exists: boolean; text?: string; path?: string; report?: AlignmentReport };
type AlignmentWorkspace = { candidate: CandidateState; templates: AlignmentTemplate[] };
type NoteSkeleton = { text: string; note_count: number };
type AlignmentTemplate = { role: string; path: string };
type TrackTuning = {
  VOICE: string | null;
  HEAD_SIZE: number | null;
  PITCH_SHIFT: number;
  OCTAVE_BOOST: number;
  PITCH_WRAP_SHIFT: number | null;
  VOLUME_ADJUST_DB: number;
  IGNORE_MIDI_VELOCITY: boolean;
  VELOCITY_VOLUME_SCALE_DB: number;
  PITCH_VOLUME_BOOST_START: number;
  PITCH_VOLUME_BOOST_DB_PER_SEMITONE: number;
  PITCH_VOLUME_BOOST_MAX_DB: number;
  NOTE_NORMALIZE_TARGET_DBFS: number | "auto";
  NOTE_NORMALIZE_MAX_BOOST_DB: number;
  NOTE_NORMALIZE_PEAK_CEILING_DBFS: number;
  STEM_PEAK_CEILING_DBFS: number;
  GAP_MEND_MS: number;
};
type AutoNormalizeTuning = { supported: boolean; head_size: number | null; message: string; values: TrackTuning | null };
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

function firstPhraseLine(report: AlignmentReport): number | null {
  return report.notes.find((entry) => entry.line !== null && Boolean(entry.lyric))?.line ?? null;
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
  const [savedTranscript, setSavedTranscript] = useState("");
  const [transcriptLoadedKey, setTranscriptLoadedKey] = useState("");
  const [validation, setValidation] = useState<{ invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null>(null);
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const [draftRole, setDraftRole] = useState("");
  const [alignment, setAlignment] = useState<{ report: AlignmentReport; text: string } | null>(null);
  const [alignmentRole, setAlignmentRole] = useState("");
  const [lyricsPrompt, setLyricsPrompt] = useState("");
  const [templateSources, setTemplateSources] = useState<AlignmentTemplate[]>([]);
  const [selectedPhrase, setSelectedPhrase] = useState<number | null>(null);
  const [deleteSongArmed, setDeleteSongArmed] = useState(false);
  const [lyricsModalOpen, setLyricsModalOpen] = useState(false);
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
    setTranscript(""); setSavedTranscript(""); setValidation(null);
    bridge<TranscriptState>({ command: "read_transcript", song, role: roleName }).then((value) => {
      if (cancelled) return;
      setTranscript(value.text); setSavedTranscript(value.text); setTranscriptLoadedKey(`${song}:${roleName}`); setValidation(null);
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
        setDraftRole(roleName); setAlignment({ text: candidate.text, report: candidate.report }); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(candidate.report));
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
      setDraftState(draft); setDraftRole(roleName); setTranscript(draft.text); setSavedTranscript(draft.text); setLyricsPrompt("");
      const pending = await bridge<{ report: AlignmentReport; text: string; path: string }>({ command: "align", song, role: roleName });
      setDraftState({ ...draft, text: pending.text, path: pending.path }); setTranscript(pending.text); setSavedTranscript(pending.text); setAlignment(pending); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(pending.report)); setLyricsModalOpen(false); setStage("align");
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
    try { await bridge({ command: "save_transcript", song, role: roleName, text: transcript }); setSavedTranscript(transcript); }
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
  const playRender = async () => { if (!inspection?.final_mix) return; try { await openMedia(inspection.final_mix); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const adoptTemplate = async (sourceRole: string) => {
    if (!song || !roleName || !sourceRole) return;
    invalidateAlignmentLoad();
    setBusy(`Copying ${sourceRole} alignment`); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; path: string }>({ command: "copy_alignment_template", song, role: roleName, source_role: sourceRole });
      setDraftState({ text: result.text, path: result.path, warnings: [], review_segments: [], tight_gap_ms: 0 }); setDraftRole(roleName);
      setAlignment(result); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(result.report)); setStage("align");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const hasDraft = draftState !== null && draftRole === roleName;
  const activeAlignment = alignmentRole === roleName ? alignment : null;
  const reviewEnabledRoles = renderRolesBySong[song] ?? inspection?.roles.filter((item) => item.render_enabled && item.render_eligible).map((item) => item.role) ?? [];
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

  return <main className="studio-shell">
    <header className="app-header">
      <div className="brand"><img className="brand-mark" src={choirStudioMark} alt="" /><span>DECTALK Choir</span><strong>Studio</strong></div>
      <div className="header-song-cluster"><label className="song-select"><span>Song</span><select value={song} onChange={(event) => void loadSong(event.target.value)}>{songs.map((item) => <option key={item}>{item}</option>)}</select></label><div className="selection-actions"><button className="header-command" type="button" onClick={() => void playRender()} disabled={!inspection?.final_mix} title="Open the completed song mix in your default media player" aria-label="Open render in default media player"><Play size={15} /></button><button className="header-command" type="button" onClick={() => void openOutputs()} disabled={!song} title="Open this song's generated output folder" aria-label="Open output folder"><FolderOpen size={16} /></button><button className="header-command destructive-command" type="button" onClick={() => setDeleteSongArmed(true)} disabled={!song} title="Delete this song and all of its outputs" aria-label="Delete selected song"><Trash2 size={15} /></button></div></div>
      <nav className="lifecycle" aria-label="Track design phases">
        {stages.map(([id, label, Icon], index) => <button key={id} className={stage === id ? "active" : ""} onClick={() => selectStage(id)}><span className="stage-index">{index + 1}</span><Icon size={16} />{label}</button>)}
      </nav>
      <div className="header-state">{busy}</div>
      <div className="theme-switch" role="group" aria-label="Color theme"><button className={theme === "dark" ? "active" : ""} type="button" title="Use dark theme" aria-label="Use dark theme" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}><Moon size={15} /></button><button className={theme === "light" ? "active" : ""} type="button" title="Use light theme" aria-label="Use light theme" aria-pressed={theme === "light"} onClick={() => setTheme("light")}><Sun size={16} /></button></div>
    </header>
    {deleteSongArmed && <section className="song-delete-confirm" role="alertdialog" aria-label={`Delete ${song}`}><div><strong>Delete {song}?</strong><span>Its inputs, settings, and generated outputs will be removed.</span></div><button className="secondary" type="button" onClick={() => setDeleteSongArmed(false)} disabled={!!busy}>Cancel</button><button className="danger" type="button" onClick={() => void removeSong()} disabled={!!busy}>Delete song</button></section>}
    {error && <div className="error-toast" role="alert" aria-live="assertive"><CircleAlert size={17} /><span>{error}</span><div className="error-actions">{/ffmpeg/i.test(error) && <><button type="button" className="error-action" onClick={() => void openFfmpegDownload().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Open FFmpeg's official Windows download guidance">Get FFmpeg</button><button type="button" className="error-action" onClick={() => void navigator.clipboard.writeText(FFMPEG_WINGET_COMMAND).then(() => setError(`Copied: ${FFMPEG_WINGET_COMMAND}`)).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Copy the Windows Package Manager install command">Copy winget</button></>}<button type="button" onClick={() => setError("")} title="Dismiss error" aria-label="Dismiss error"><X size={16} /></button></div></div>}
    <section className={`workspace ${stage === "review" ? "review-workspace" : ""}`}>
      <aside className="track-rail"><h2 className="rail-song-title" title={song}>{song || "Song"}</h2><div className="rail-heading"><PanelLeft size={16} /> Tracks</div><div className="track-list">{inspection?.roles.map((item) => <button key={item.role} className={item.role === roleName ? "track active" : "track"} onClick={() => selectRole(item.role)}><strong title={item.role}>{item.role}</strong><span>{item.midi_source_name}</span><div className="track-range"><PitchRange value={item.midi_range} /><span className="track-note-count">{item.note_count} notes</span></div>{item.polyphony && item.polyphony > 1 && <i>Needs split</i>}</button>)}</div></aside>
      <section className={`surface${stage === "align" ? " align-surface" : ""}`}>
        {stage === "align" && <AlignStage role={role} inspection={inspection} song={song} alignment={activeAlignment} loading={alignmentLoading || alignmentTransitionPending} templateSources={templateSources} onAdoptTemplate={adoptTemplate} onOpenLyrics={() => setLyricsModalOpen(true)} setAlignment={setAlignment} selectedPhrase={selectedPhrase} setSelectedPhrase={setSelectedPhrase} busy={busy} setBusy={setBusy} setError={setError} />}
        {stage === "review" && <ReviewStage song={song} role={role} inspection={inspection} enabledRoles={reviewEnabledRoles} onEnabledRolesChange={(roles) => void updateRenderRoles(roles)} onSelectRole={selectRole} setInspection={setInspection} busy={busy} setBusy={setBusy} setError={setError} />}
      </section>
    </section>
    {lyricsModalOpen && <section className="lyrics-modal-backdrop" role="presentation" onMouseDown={() => setLyricsModalOpen(false)}><section className="lyrics-modal" role="dialog" aria-modal="true" aria-label={`Edit ${roleName} lyrics`} onMouseDown={(event) => event.stopPropagation()}><button className="lyrics-modal-close" type="button" title="Close lyric editor" aria-label="Close lyric editor" onClick={() => setLyricsModalOpen(false)}><X size={17} /></button><LyricsStage transcript={transcript} transcriptLoaded={transcriptLoadedKey === transcriptKey} setTranscript={setTranscript} validation={validation} onDraft={runDraft} onNoteSkeleton={runNoteSkeleton} onSave={saveTranscript} busy={busy} draftState={hasDraft ? draftState : null} dirty={transcript !== savedTranscript} prompt={lyricsPrompt} /></section></section>}
  </main>;
}

function LyricsStage({ transcript, transcriptLoaded, setTranscript, validation, onDraft, onNoteSkeleton, onSave, busy, draftState, dirty, prompt }: { transcript: string; transcriptLoaded: boolean; setTranscript(value: string): void; validation: { invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null; onDraft(): void; onNoteSkeleton(placeholder: string): void; onSave(): void; busy: string; draftState: DraftState | null; dirty: boolean; prompt: string }) {
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
  return <><section className="surface-header lyrics-header"><div className="lyrics-title"><p className="eyebrow">Working lyric draft</p><h1>Lyrics</h1><p>Paste lyrics or create a note skeleton here. Draft timing turns this same text into the editable aligned draft.</p></div><div className="header-actions lyrics-actions"><span className={dirty ? "save-state dirty" : "save-state"}>{!transcriptLoaded ? "Loading lyrics" : replaceArmed ? "Confirm note skeleton" : dirty ? "Unsaved changes" : "Saved"}</span><button className="secondary" onClick={onSave} disabled={!!busy || !transcriptLoaded || !dirty}>Save draft</button><label className="skeleton-control" title={skeletonTitle}><input value={skeletonPhoneme} onChange={(event) => setSkeletonPhoneme(event.target.value)} aria-label="Note skeleton phoneme" placeholder="duw" /><button className="secondary" onClick={createSkeleton} disabled={skeletonDisabled}><Music2 size={16} /> {replaceArmed ? "Confirm skeleton" : "Note skeleton"}</button></label><button className="primary" onClick={onDraft} title={!transcriptLoaded ? "Wait for this role's lyrics to finish loading" : "Draft timing against this role's MIDI notes"} disabled={!!busy || !transcriptLoaded || !transcript.trim()}><WandSparkles size={16} /> Draft timing</button></div></section><LyricsTipBoard /><textarea className="transcript" value={transcript} onChange={(event) => setTranscript(event.target.value)} disabled={!transcriptLoaded} placeholder={transcriptLoaded ? "Paste plain lyrics, or create one direct phoneme per MIDI note. Line breaks are phrase hints; commas and unsupported punctuation are normalized." : "Loading this track's lyric source..."} />{validation && (!validation.ok || validation.normalized_lines.length > 0) && <div className={validation.ok ? "notice" : "warning"}><CircleAlert size={17} /><div>{validation.invalid_words.length > 0 && <><strong>Check these words:</strong> {validation.invalid_words.join(", ")}</>}{validation.normalized_lines.length > 0 && <span> Punctuation will be normalized before drafting.</span>}</div></div>}{draftState?.review_segments.length ? <details className="draft-review" open><summary>{draftState.review_segments.length} rapid multi-note word {draftState.review_segments.length === 1 ? "span needs" : "spans need"} verification <span>gaps at or below {draftState.tight_gap_ms} ms</span></summary><div>{draftState.review_segments.map((segment) => <div key={`${segment.line}-${segment.word_index}`} style={{ "--word-color": wordColor(segment.line, segment.word_index) } as CSSProperties}><strong>{segment.word}</strong><span>{segment.note_count} notes · {Math.round(segment.start_ms / 1000)}s-{Math.round(segment.end_ms / 1000)}s</span></div>)}</div></details> : null}{draftState && <details className="generated"><summary>Generated draft ready for alignment <span>{draftState.path}</span></summary><pre>{draftState.text}</pre></details>}</>;
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

function AlignStage({ role, inspection, song, alignment, loading, templateSources, onAdoptTemplate, onOpenLyrics, setAlignment, selectedPhrase, setSelectedPhrase, busy, setBusy, setError }: { role: Role | null; inspection: SongInspection | null; song: string; alignment: { report: AlignmentReport; text: string } | null; loading: boolean; templateSources: AlignmentTemplate[]; onAdoptTemplate(sourceRole: string): void; onOpenLyrics(): void; setAlignment(value: { report: AlignmentReport; text: string } | null): void; selectedPhrase: number | null; setSelectedPhrase(value: number): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [selectedWord, setSelectedWord] = useState<{ line: number; wordIndex: number } | null>(null);
  const [insertWord, setInsertWord] = useState("");
  const [insertOpen, setInsertOpen] = useState(false);
  const [draggedWord, setDraggedWord] = useState<number | null>(null);
  const [showAllWords, setShowAllWords] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [applyArmed, setApplyArmed] = useState(false);
  const [applied, setApplied] = useState<{ path: string; backup_path: string | null } | null>(null);
  const [templateRole, setTemplateRole] = useState("");
  const [cursorMs, setCursorMs] = useState(0);
  const [mediaState, setMediaState] = useState<MediaStatus | null>(null);
  const [mediaLabel, setMediaLabel] = useState("Preview this role while you align its lyrics.");
  const active = Boolean(mediaState && !["stopped", "not ready"].includes(mediaState.mode));
  const missingNoteWords = Number(alignment?.report.summary.zero_note_tokens ?? 0);
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
    .map((item) => ({ line: item.line, word_index: item.word_index, lyric: item.word, note_count: item.note_count, duration_ms: wordDurations.get(`${item.line}:${item.word_index}`) ?? 0 }))
    ?? phraseEntries.filter((entry, index, items) => index === 0 || entry.word_index !== items[index - 1].word_index).map((entry) => ({ ...entry, note_count: entry.word_note_count ?? 1 }));
  const visibleWords = showAllWords ? words : words.slice(0, 10);
  const hiddenWordCount = words.length - visibleWords.length;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => media<MediaStatus>("media_status").then(setMediaState).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))), 250);
    return () => window.clearInterval(timer);
  }, [active, setError]);
  useEffect(() => {
    setCursorMs(0); setMediaState(null); setMediaLabel("Preview this role while you align its lyrics."); setSelectedWord(null); setInsertWord(""); setInsertOpen(false); setApplyArmed(false); setApplied(null); setTemplateRole(""); setShowAllWords(false);
    return () => { void media<MediaStatus>("media_stop").catch(() => undefined); };
  }, [role?.role]);
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
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "add_virtual_split", song, role: role.role, report: alignment.report, note_index: noteIndex, fraction });
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
  const reorderWord = async (targetWordIndex: number) => {
    if (!alignment || !role || selectedPhrase === null || draggedWord === null || draggedWord === targetWordIndex) return;
    setBusy("Reordering lyric"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; selected: { line: number; word_index: number } }>({ command: "reorder_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedPhrase, word_index: draggedWord, target_word_index: targetWordIndex });
      setAlignment(result); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); setDraggedWord(null); }
  };
  const apply = async () => {
    if (!alignment || !role) return;
    setBusy("Applying aligned lyrics"); setError("");
    try {
      const result = await bridge<{ path: string; backup_path: string | null }>({ command: "apply_alignment", song, role: role.role, text: alignment.text });
      setApplied(result); setApplyArmed(false);
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
      return <div className={`word-token ${draggedWord === item.word_index ? "dragging" : ""} ${item.note_count === 0 ? "invalid" : ""}`} style={{ "--word-color": wordColor(item.line, item.word_index) } as CSSProperties} key={`${item.line}-${item.word_index}`} draggable={!busy} title="Drag this word onto another word to move it before that word" onDragStart={() => setDraggedWord(item.word_index!)} onDragEnd={() => setDraggedWord(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); void reorderWord(item.word_index!); }}>
        <span className="word-note-count" aria-label={`${item.note_count} owned note${item.note_count === 1 ? "" : "s"}`}>{item.note_count}♩</span>
        <button className={isSelected ? "selected" : ""} onClick={() => { setSelectedWord({ line: item.line!, wordIndex: item.word_index! }); setInsertOpen(false); }} title={item.lyric ?? ""}>{item.lyric}<small>{item.note_count === 0 ? "Needs note" : `${item.duration_ms} ms`}</small></button>
        {isSelected && <span className="word-quick-controls"><button type="button" title="Give one note back to this phrase" aria-label="Decrease this word's note count" disabled={!!busy || !canDecreaseWordNotes} onPointerDown={(event) => event.stopPropagation()} onClick={() => void adjustWordNoteCount(-1)}><Minus size={12} /></button><button type="button" title="Assign one more note from this phrase" aria-label="Increase this word's note count" disabled={!!busy || !canIncreaseWordNotes} onPointerDown={(event) => event.stopPropagation()} onClick={() => void adjustWordNoteCount(1)}><Plus size={12} /></button></span>}
        <button className={deleteArmed ? "word-delete armed" : "word-delete"} type="button" title={deleteArmed ? `Ctrl-click to remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} aria-label={deleteArmed ? `Remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} draggable={false} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { if (!event.ctrlKey) return; void removeWord(item.line!, item.word_index!); }} disabled={!!busy || !deleteArmed}><Minus size={11} /></button>
      </div>;
    })}{hiddenWordCount > 0 && <button className="word-more" type="button" onClick={() => setShowAllWords(true)}>{hiddenWordCount} more</button>}{showAllWords && words.length > 10 && <button className="word-more" type="button" onClick={() => setShowAllWords(false)}>Collapse</button>}<div className="word-insert-anchor"><button className="add-word" type="button" title="Insert a word after the selected word" aria-label="Insert a word" onClick={() => { const anchor = selectedWord ?? (words.length ? { line: words[words.length - 1].line!, wordIndex: words[words.length - 1].word_index! } : null); if (anchor) { setSelectedWord(anchor); setInsertOpen(true); } }} disabled={!words.length || !!busy}><Plus size={15} /></button></div></div>}
    {insertOpen && <form className="word-insert-popover" onSubmit={(event) => { event.preventDefault(); void insert(); }}><input autoFocus value={insertWord} onChange={(event) => setInsertWord(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { setInsertOpen(false); setInsertWord(""); } }} placeholder="New word" /><button className="primary" type="submit" disabled={!!busy || !insertWord.trim()}>Insert</button><button className="secondary" type="button" aria-label="Cancel insert" title="Cancel insert" onClick={() => { setInsertOpen(false); setInsertWord(""); }}><X size={14} /></button></form>}
  </div> : null;
  return (
    <section className="align-workspace">
      {applyArmed && alignment && <section className="apply-confirm"><div><strong>Replace configured lyric input?</strong><span>`choir.py` will use this alignment on the next render. A backup is created beside the lyric file.</span></div><button className="secondary" onClick={() => setApplyArmed(false)} disabled={!!busy}>Cancel</button><button className="primary" onClick={() => void apply()} disabled={!!busy}>Apply alignment</button></section>}
      {applied && <div className="notice alignment-applied"><CircleAlert size={17} /><div><strong>Configured lyric input updated.</strong> {applied.path}{applied.backup_path && <> Backup: {applied.backup_path}</>}</div></div>}
      <section className="midi-transport align-transport">
        <div className="transport-group"><button className="primary" onClick={() => void playMidi()} disabled={!role?.midi_track}><Play size={15} /> Play MIDI</button><button className="secondary icon-command" title={mediaState?.paused ? "Resume preview" : "Pause preview"} onClick={() => void togglePause()} disabled={!active}>{mediaState?.paused ? <Play size={15} /> : <Pause size={15} />}</button><button className="secondary icon-command" title="Stop playback" onClick={() => void stop()} disabled={!mediaState}><Square size={14} /></button><button className="secondary align-lyrics-command" type="button" title="Edit the working lyric draft without leaving Align" onClick={onOpenLyrics}><PenLine size={15} /> Edit track lyrics</button><span>{mediaLabel}</span></div>
        <div className="transport-group align-actions"><span className={missingNoteWords ? "save-state dirty" : "save-state"}>{alignment?.report.template?.source_role ? `Template: ${alignment.report.template.source_role}` : alignment ? missingNoteWords ? `${missingNoteWords} word${missingNoteWords === 1 ? "" : "s"} need a note` : "Pending source update" : "Not drafted"}</span>{templateSources.length > 0 && <label className="template-picker" title="Copy a saved same-lyrics alignment and remap it to this track by time"><select value={templateRole} onChange={(event) => setTemplateRole(event.target.value)}><option value="">Aligned template</option>{templateSources.map((source) => <option key={source.role} value={source.role}>{source.role}</option>)}</select><button type="button" className="secondary" onClick={() => onAdoptTemplate(templateRole)} disabled={!!busy || !templateRole}>Use</button></label>}{alignment && <button className="secondary" title={missingNoteWords ? "Resolve words without MIDI notes before applying" : "Validate and replace the configured lyric input used by choir.py"} onClick={() => setApplyArmed(true)} disabled={!!busy || missingNoteWords > 0}>Apply to source</button>}</div>
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

function ReviewTrackTable({ roles, selectedRole, enabledRoles, onToggleRole, onSelectRole, onTuneRole, final, setError }: { roles: Role[]; selectedRole: string | undefined; enabledRoles: string[]; onToggleRole(role: string): void; onSelectRole(role: string): void; onTuneRole(role: string): void; final: SongInspection["final_loudness"] | undefined; setError(value: string): void }) {
  return <section className="review-stats review-track-table" aria-label="Track review statistics"><table><colgroup><col className="review-col-controls" /><col className="review-col-role" /><col className="review-col-status" /><col className="review-col-midi" /><col className="review-col-pitch" /><col /><col className="review-col-peak" /></colgroup><thead><tr><th>Render</th><th>Role</th><th>Status</th><th>MIDI</th><th>DECtalk / audible</th><th>Active loudness min / median / avg / max</th><th>Peak</th></tr></thead><tbody>{roles.map((item) => {
    const enabled = enabledRoles.includes(item.role);
    const eligibilityMessage = item.render_eligible
      ? enabled ? `Exclude ${item.role} from rendering` : `Include ${item.role} in rendering`
      : item.details[0] ?? `${item.role} needs valid MIDI plus lyrics before it can render`;
    return <tr key={item.role} className={item.role === selectedRole ? "selected" : ""} onClick={() => onSelectRole(item.role)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectRole(item.role); } }} tabIndex={0} role="button" aria-pressed={item.role === selectedRole}>
      <td className="review-row-controls" onClick={(event) => event.stopPropagation()}><label title={eligibilityMessage}><input type="checkbox" checked={enabled} disabled={!item.render_eligible} onChange={() => onToggleRole(item.role)} /><span className="sr-only">{enabled ? "Enabled" : "Disabled"}</span></label><button className="review-stem-play" type="button" disabled={!item.stem_exists} title={item.stem_exists ? `Open ${item.role} stem in the default media player` : `${item.role} has no rendered stem yet`} aria-label={item.stem_exists ? `Open ${item.role} stem in the default media player` : `${item.role} has no rendered stem yet`} onClick={() => void openMedia(item.stem_path).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))}><Play size={12} /></button><button className="review-tune-control" type="button" title={`Tune ${item.role}`} aria-label={`Tune ${item.role}`} onClick={() => onTuneRole(item.role)}><Settings2 size={13} /></button></td>
      <th>{item.role}</th><td>{item.status}</td><td><PitchRange value={item.midi_range} /></td><td><div className="pitch-range-stack"><PitchRange label="render" value={item.render_range} /><PitchRange label="heard" value={item.audible_range} /></div></td><td>{item.loudness ? item.loudness.error ?? `${formatDb(item.loudness.minimum_dbfs)} / ${formatDb(item.loudness.median_dbfs)} / ${formatDb(item.loudness.average_dbfs)} / ${formatDb(item.loudness.maximum_dbfs)}` : "No stem"}</td><td>{formatDb(item.loudness?.peak_dbfs)}</td>
    </tr>;
  })}</tbody></table><div className="mix-loudness"><strong>Final mix</strong><span>{final ? final.error ?? `${formatDb(final.minimum_dbfs)} min / ${formatDb(final.median_dbfs)} median / ${formatDb(final.average_dbfs)} average / ${formatDb(final.maximum_dbfs)} max / ${formatDb(final.peak_dbfs)} peak` : "No completed mix available"}</span></div></section>;
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
  const visualDraftsRef = useRef(visualDrafts);
  const visualSaveTimers = useRef(new Map<string, number>());
  const visualSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const [job, setJob] = useState<SpectrogramJobStatus | null>(null);
  const [renderJob, setRenderJob] = useState<RenderJobStatus | null>(null);
  const [tuning, setTuning] = useState<TrackTuning | null>(null);
  const [autoNormalize, setAutoNormalize] = useState<AutoNormalizeTuning | null>(null);
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
    visualDraftsRef.current = {};
  }, [song]);
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
      return;
    }
    const cacheKey = `${song}:${role.role}`;
    const cached = tuningCache.current.get(cacheKey);
    if (cached) {
      setTuning(cached);
      return;
    }
    let cancelled = false;
    setTuning(null);
    // Let the selected table row paint before requesting role-specific settings.
    const timer = window.setTimeout(() => bridge<TrackTuning>({ command: "get_track_tuning", song, role: role.role }).then((next) => {
      tuningCache.current.set(cacheKey, next);
      if (!cancelled) setTuning(next);
    }).catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); }), 80);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [song, role?.role, setError]);
  useEffect(() => {
    if (!song || !role) {
      setAutoNormalize(null);
      return;
    }
    let cancelled = false;
    setAutoNormalize(null);
    // This is advisory calibration data; it should never compete with role selection.
    const timer = window.setTimeout(() => bridge<AutoNormalizeTuning>({ command: "get_auto_normalize_tuning", song, role: role.role }).then((next) => {
      if (!cancelled) setAutoNormalize(next);
    }).catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); }), 160);
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
  const toggleRenderRole = (targetRole: string) => {
    onEnabledRolesChange(enabledRoles.includes(targetRole) ? enabledRoles.filter((item) => item !== targetRole) : [...enabledRoles, targetRole]);
  };
  const changeTuning = (key: keyof TrackTuning, value: TrackTuning[keyof TrackTuning]) => {
    setTuning((current) => current ? { ...current, [key]: value } : current);
  };
  const applyAutoNormalize = async () => {
    if (!song || !role || !tuning?.HEAD_SIZE) return;
    setError("");
    try {
      const next = await bridge<AutoNormalizeTuning>({ command: "get_auto_normalize_tuning", song, role: role.role, head_size: tuning.HEAD_SIZE, voice: tuning.VOICE ?? "" });
      setAutoNormalize(next);
      if (!next.supported || !next.values) {
        setError(next.message);
        return;
      }
      setTuning(next.values);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const saveTuning = async () => {
    if (!song || !role || !tuning) return;
    setBusy(`Saving ${role.role} tuning`); setError("");
    try {
      const result = await bridge<{ values: TrackTuning }>({ command: "update_track_tuning", song, role: role.role, values: tuning });
      tuningCache.current.set(`${song}:${role.role}`, result.values); setTuning(result.values);
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
      tuningCache.current.set(cacheKey, next); setTuning(next);
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
  const spectrogramTotal = spectrogramTimings.find((timing) => timing.stage === "total");
  const spectrogramStageLabels: Record<string, string> = { setup: "Setup", parallel_tracks: "Track clips", composition: "Composite", cleanup: "Cleanup", total: "Total" };
  return <section className={`review-stage ${panel}-panel`}>
    <header className="surface-header review-header"><div className="review-identity"><p className="eyebrow">Output review</p><h1>{song || "Select a song"}<span>{role?.role ?? "Select a role"}</span></h1><p>Enable renderable tracks in the table; tune an individual role from its cog.</p></div><section className={`review-render ${renderState}`} aria-label="Render selected tracks"><div className="render-status"><div className="render-status-state">{renderStatusIcon}<span>{renderStatusLabel}</span></div><strong>{enabledRoles.length} tracks enabled</strong><span className="render-status-message" title={renderStatusMessage}>{renderStatusMessage}</span></div><div className="review-render-actions"><button className="primary" type="button" onClick={() => void render()} disabled={renderState === "running" || !enabledRoles.length}>{renderState === "running" ? "Rendering in background..." : <><FileAudio size={16} /> Render enabled tracks <span className="render-duration">{formatDuration(inspection?.midi?.duration_seconds)}</span></>}</button><button className="secondary spectrogram-layout-command" type="button" onClick={() => { const initialRole = enabledVisualRoles.some((item) => item.role === visualRoleName) ? visualRoleName : enabledVisualRoles[0]?.role ?? ""; setVisualRoleName(initialRole); setPanel("visuals"); }} disabled={!hasFinishedRender || !enabledVisualRoles.length}><BarChart3 size={15} /> Spectrogram layout</button></div></section></header>
    {panel !== "overview" && <nav className="review-panel-nav" aria-label="Render audio workspace">
      <button className="secondary review-panel-back" type="button" onClick={() => setPanel("overview")}><ArrowLeft size={15} /><BarChart3 size={15} /> Output overview</button>
      {panel === "tune" && <span>Editing {role?.role ?? "the selected role"} tuning profile</span>}
      {panel === "visuals" && <><span>{enabledVisualRoles.length} enabled render region{enabledVisualRoles.length === 1 ? "" : "s"}. Select regions directly on the canvas.</span>{job && job.state !== "idle" && <div className={`spectrogram-job-status ${job.state}`} role="status" aria-live="polite" title={job.message}>{job.state === "completed" ? <CircleCheck size={15} /> : job.state === "failed" ? <CircleAlert size={15} /> : <LoaderCircle size={15} />}<strong>{job.state === "completed" ? "Video complete" : job.state === "failed" ? "Video failed" : "Generating video"}{spectrogramTotal ? ` · ${formatStageDuration(spectrogramTotal.seconds)}` : ""}</strong></div>}<div className="visual-header-actions"><button className="primary" type="button" onClick={() => void generate()} disabled={!!busy || visualSaving || !enabledRoles.length || job?.state === "running"}>{job?.state === "running" ? "Generating video..." : <><BarChart3 size={16} /> Generate spectrograms</>}</button><button className="secondary" type="button" onClick={() => void openMedia(inspection!.animation_path!)} disabled={!!busy || !inspection?.animation_exists || !inspection.animation_path}><Play size={15} /> Open video</button></div></>}
    </nav>}
    {panel === "tune" && <button className="tuning-modal-backdrop" type="button" onClick={() => setPanel("overview")} aria-label="Close track tuning" />}
    <section className="range-legend" aria-label="Register color legend"><strong>Register color</strong><span className="range-legend-blue">C2 low</span><span className="range-legend-green">C3 mid</span><span className="range-legend-yellow">C4 mid-high</span><span className="range-legend-orange">C5 weak</span><span className="range-legend-red">C6+ weakest</span></section>
    <ReviewTrackTable roles={inspection?.roles ?? []} selectedRole={role?.role} enabledRoles={enabledRoles} onToggleRole={toggleRenderRole} onSelectRole={onSelectRole} onTuneRole={(nextRole) => { onSelectRole(nextRole); setPanel("tune"); }} final={final} setError={setError} />
    <section className="review-stats" aria-label="Track review statistics"><table><thead><tr><th>Role</th><th>Status</th><th>Notes</th><th>MIDI</th><th>DECtalk / audible</th><th>Poly</th><th>Active loudness min / median / avg / max</th><th>Peak</th></tr></thead><tbody>{inspection?.roles.map((item) => <tr key={item.role} className={item.role === role?.role ? "selected" : ""} onClick={() => onSelectRole(item.role)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectRole(item.role); } }} tabIndex={0} role="button" aria-pressed={item.role === role?.role}><th>{item.role}</th><td>{item.status}</td><td>{item.note_count}</td><td><PitchRange value={item.midi_range} /></td><td><div className="pitch-range-stack"><PitchRange label="render" value={item.render_range} /><PitchRange label="heard" value={item.audible_range} /></div></td><td>{item.polyphony ?? "--"}</td><td>{item.loudness ? item.loudness.error ?? `${formatDb(item.loudness.minimum_dbfs)} / ${formatDb(item.loudness.median_dbfs)} / ${formatDb(item.loudness.average_dbfs)} / ${formatDb(item.loudness.maximum_dbfs)}` : "No stem"}</td><td>{formatDb(item.loudness?.peak_dbfs)}</td></tr>)}</tbody></table><div className="mix-loudness"><strong>Final mix</strong><span>{final ? final.error ?? `${formatDb(final.minimum_dbfs)} min · ${formatDb(final.median_dbfs)} median · ${formatDb(final.average_dbfs)} average · ${formatDb(final.maximum_dbfs)} max · ${formatDb(final.peak_dbfs)} peak` : "No completed mix available"}</span></div></section>
    {renderJob && renderJob.state !== "idle" && <details className="generated review-log" open={renderJob.state !== "completed"}><summary>{renderJob.message} <span>{renderJob.selected_roles.join(", ")} · {renderJob.state === "running" ? "background job" : `exit ${renderJob.returncode ?? "--"}`}</span></summary><pre>{renderJob.log || "The renderer is starting; live compiler output will appear here."}</pre></details>}
    <details className="track-tuning" open>
      <summary><span>Track tuning</span><strong>{role?.role ?? "Select a role"}</strong><small>Saved to this role in `settings.yaml`</small></summary>
      <button className="tuning-modal-close" type="button" onClick={() => setPanel("overview")} title="Close track tuning" aria-label="Close track tuning"><X size={17} /></button>
      {tuning && <div className="tuning-body">
        <p className="tuning-guide"><strong>Value reference</strong><span>0 means no adjustment. Pitch values are semitones; +12 / -12 equals one octave. DECtalk pitch index: C3 = 0, C5 = 24, C6 = 36.</span></p>
        <section><h2>Pitch</h2>
          <label className="tuning-field" title="Transpose the final musical output after MIDI-to-DECTALK mapping."><span>Pitch shift <small>semitones</small></span><div className="tuning-input"><input type="number" min="-24" max="24" step="1" value={tuning.PITCH_SHIFT} onChange={(event) => changeTuning("PITCH_SHIFT", Number(event.target.value))} /><output>st</output></div><em>0 keeps the MIDI pitch. +12 raises the final voice one octave.</em></label>
          <label className="tuning-field" title="Moves the temporary DECTALK render register, then speed-corrects the WAV back to the MIDI pitch."><span>Octave boost <small>semitones</small></span><div className="tuning-input"><input type="number" min="-48" max="48" step="12" value={tuning.OCTAVE_BOOST} onChange={(event) => changeTuning("OCTAVE_BOOST", Number(event.target.value))} /><output>st</output></div><em>0 is direct. +12 renders one octave lower, then restores the intended final octave.</em></label>
          <label className="tuning-field" title="Overrides automatic whole-octave wrapping into the configured DECTALK pitch bounds."><span>Pitch wrap <small>whole octaves</small></span><div className="tuning-input"><select value={tuning.PITCH_WRAP_SHIFT ?? "auto"} onChange={(event) => changeTuning("PITCH_WRAP_SHIFT", event.target.value === "auto" ? null : Number(event.target.value))}><option value="auto">Auto (recommended)</option><option value="-24">-24 st (2 octaves)</option><option value="-12">-12 st (1 octave)</option><option value="0">0 st (do not wrap)</option><option value="12">+12 st (1 octave)</option><option value="24">+24 st (2 octaves)</option></select></div><em>Leave on Auto unless you are intentionally overriding the safe pitch wrap.</em></label>
        </section>
        <section><h2>Level</h2>
          <label className="tuning-field" title="Beta: replaces only the [:n?] voice command in DEC_SETUP and preserves head size plus other DECtalk directives."><span>Voice <small><b className="beta-badge">Beta</b> DECtalk command</small></span><div className="tuning-input"><select value={tuning.VOICE ?? ""} onChange={(event) => changeTuning("VOICE", event.target.value || null)}><option value="">DECtalk default</option><option value="np">[:np] Perfect Paul (calibrated)</option><option value="nb">[:nb] DECtalk voice</option><option value="nh">[:nh] DECtalk voice</option><option value="nd">[:nd] DECtalk voice</option><option value="nf">[:nf] DECtalk voice</option><option value="nu">[:nu] DECtalk voice</option><option value="nr">[:nr] DECtalk voice</option><option value="nw">[:nw] DECtalk voice</option><option value="nk">[:nk] DECtalk voice</option></select></div><em>Changes only the voice command. Auto-normalize is measured for Perfect Paul [:np] only.</em></label>
          <label className="tuning-field" title="Writes [:dv hs N] into DEC_SETUP. Head size affects voice timbre and loudness; it is not a gain control."><span>Head size <small>DECTALK hs</small></span><div className="tuning-input"><input type="number" min="40" max="200" step="1" value={tuning.HEAD_SIZE ?? ""} placeholder="Set head size" onChange={(event) => changeTuning("HEAD_SIZE", event.target.value === "" ? null : Number(event.target.value))} /><output>hs</output></div><em>Voice parameter. The measured [:np] calibration covers hs 80 through 140.</em></label>
          <label className="tuning-field" title="Applies a constant gain to the complete rendered stem."><span>Stem gain <small>decibels</small></span><div className="tuning-input"><input type="number" min="-24" max="24" step="0.5" value={tuning.VOLUME_ADJUST_DB} onChange={(event) => changeTuning("VOLUME_ADJUST_DB", Number(event.target.value))} /><output>dB</output></div><em>0 dB leaves the stem level unchanged. Positive is louder.</em></label>
          <label className="tuning-field" title="Final audible DECTALK pitch at which high-note gain begins."><span>Weak pitch start <small>DECTALK pitch</small></span><div className="tuning-input"><input type="number" min="0" max="36" step="1" value={tuning.PITCH_VOLUME_BOOST_START} onChange={(event) => changeTuning("PITCH_VOLUME_BOOST_START", Number(event.target.value))} /><output>pitch</output></div><em>24 is C5. 0 disables the curve when dB per semitone is also 0.</em></label>
          <label className="tuning-field" title="Extra gain applied for every semitone above Weak pitch start."><span>High-note slope <small>gain rate</small></span><div className="tuning-input"><input type="number" min="0" max="24" step="0.1" value={tuning.PITCH_VOLUME_BOOST_DB_PER_SEMITONE} onChange={(event) => changeTuning("PITCH_VOLUME_BOOST_DB_PER_SEMITONE", Number(event.target.value))} /><output>dB/st</output></div><em>0 disables pitch-dependent gain. Example: 1.7 adds 1.7 dB per semitone.</em></label>
          <label className="tuning-field" title="Maximum total gain allowed from the high-note curve."><span>High-note cap <small>maximum gain</small></span><div className="tuning-input"><input type="number" min="0" max="24" step="0.5" value={tuning.PITCH_VOLUME_BOOST_MAX_DB} onChange={(event) => changeTuning("PITCH_VOLUME_BOOST_MAX_DB", Number(event.target.value))} /><output>dB</output></div><em>Hard ceiling for the high-note curve; 0 permits no high-note boost.</em></label>
        </section>
        <section><h2>Note guard</h2>
          <label className="tuning-toggle" title="Leave checked to keep all MIDI velocities from changing rendered loudness."><input type="checkbox" checked={tuning.IGNORE_MIDI_VELOCITY} onChange={(event) => changeTuning("IGNORE_MIDI_VELOCITY", event.target.checked)} /> Ignore MIDI velocity <small>default: on; no hidden dynamic gain</small></label>
          <label className="tuning-field" title="Dynamic range derived from average MIDI velocity when Ignore MIDI velocity is off."><span>Velocity dynamic range <small>opt-in</small></span><div className="tuning-input"><input type="number" min="0" max="24" step="0.5" disabled={tuning.IGNORE_MIDI_VELOCITY} value={tuning.VELOCITY_VOLUME_SCALE_DB} onChange={(event) => changeTuning("VELOCITY_VOLUME_SCALE_DB", Number(event.target.value))} /><output>dB</output></div><em>0 adds no velocity response. Increase only after unchecking Ignore MIDI velocity.</em></label>
          <label className="tuning-toggle" title="Use the strongest reference notes to choose the note-level loudness target."><input type="checkbox" checked={tuning.NOTE_NORMALIZE_TARGET_DBFS === "auto"} onChange={(event) => changeTuning("NOTE_NORMALIZE_TARGET_DBFS", event.target.checked ? "auto" : -18)} /> Auto target <small>measures this voice's reference notes</small></label>
          <label className="tuning-field" title="Target RMS level per MIDI note when Auto target is off."><span>Target level <small>RMS dBFS</small></span><div className="tuning-input"><input type="number" min="-60" max="-1" step="1" disabled={tuning.NOTE_NORMALIZE_TARGET_DBFS === "auto"} value={tuning.NOTE_NORMALIZE_TARGET_DBFS === "auto" ? -18 : tuning.NOTE_NORMALIZE_TARGET_DBFS} onChange={(event) => changeTuning("NOTE_NORMALIZE_TARGET_DBFS", Number(event.target.value))} /><output>dBFS</output></div><em>Used only with Auto target off. Closer to 0 dBFS is louder.</em></label>
          <label className="tuning-field" title="Caps the correction applied to each grouped MIDI note."><span>Note max boost <small>per note</small></span><div className="tuning-input"><input type="number" min="0" max="24" step="0.5" value={tuning.NOTE_NORMALIZE_MAX_BOOST_DB} onChange={(event) => changeTuning("NOTE_NORMALIZE_MAX_BOOST_DB", Number(event.target.value))} /><output>dB</output></div><em>0 disables note leveling. Higher values allow stronger correction.</em></label>
          <label className="tuning-field" title="Final peak ceiling for each MIDI-note group after pitch, segment, and note-level gain."><span>Note peak ceiling <small>post-boost guard</small></span><div className="tuning-input"><input type="number" min="-60" max="0" step="0.5" value={tuning.NOTE_NORMALIZE_PEAK_CEILING_DBFS} onChange={(event) => changeTuning("NOTE_NORMALIZE_PEAK_CEILING_DBFS", Number(event.target.value))} /><output>dBFS</output></div><em>Attenuates already-hot notes too. -1 dBFS prevents a 0 dBFS note peak.</em></label>
          <label className="tuning-field" title="Final peak ceiling for the completed role stem after all phrases are assembled."><span>Stem peak ceiling <small>final role guard</small></span><div className="tuning-input"><input type="number" min="-60" max="0" step="0.5" value={tuning.STEM_PEAK_CEILING_DBFS} onChange={(event) => changeTuning("STEM_PEAK_CEILING_DBFS", Number(event.target.value))} /><output>dBFS</output></div><em>Last safety pass for this output track. -1 dBFS is the default.</em></label>
          <label className="tuning-field" title="Folds MIDI gaps at or below this duration into the preceding note."><span>Mend gaps <small>timing threshold</small></span><div className="tuning-input"><input type="number" min="0" max="100" step="1" value={tuning.GAP_MEND_MS} onChange={(event) => changeTuning("GAP_MEND_MS", Number(event.target.value))} /><output>ms</output></div><em>0 preserves every MIDI gap. Positive values close only short gaps.</em></label>
        </section>
        <div className="tuning-actions"><button className="secondary" type="button" title={autoNormalize?.message ?? "Set a calibrated [:np] head size, then load the measured baseline."} onClick={() => void applyAutoNormalize()} disabled={!tuning.HEAD_SIZE || !!busy}>Auto-normalize</button><button className="secondary" type="button" title="Discard unsaved fields and reload this role's settings.yaml profile" onClick={() => void resetTuning()} disabled={!!busy}>Reset</button><button className="primary" type="button" onClick={() => void saveTuning()} disabled={!!busy}><Sparkles size={15} /> Save track tuning</button><span>{autoNormalize?.supported ? `${autoNormalize.message} Review the staged values, then save.` : autoNormalize?.message ?? "Set head size to enable the measured baseline."}</span></div>
      </div>}
    </details>
    <section className="review-visualizer">
      <div className="visual-preview-frame" ref={visualPreviewFrameRef}><div className={visualDragging ? "visual-layout-preview dragging" : "visual-layout-preview"} style={{ "--video-aspect": monitorAspectRatio, ...(visualPreviewSize ? { width: `${visualPreviewSize.width}px`, height: `${visualPreviewSize.height}px` } : {}) } as CSSProperties} aria-label={`Enabled spectrogram render regions at ${monitorWidth} by ${monitorHeight}`}>{enabledVisualRoles.map((item) => {
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
      })}</div></div>
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
    {job && job.state !== "idle" && <section className="spectrogram-timing-summary" aria-label="Spectrogram render stage durations" aria-live="polite"><strong>Stage duration</strong><div>{spectrogramTimings.length ? spectrogramTimings.map((timing) => <span key={timing.stage} className={timing.stage === "total" ? "total" : ""} title={timing.details || undefined}><small>{spectrogramStageLabels[timing.stage] ?? timing.stage.replace(/_/g, " ")}</small>{formatStageDuration(timing.seconds)}</span>) : <span className="pending"><LoaderCircle size={13} /><small>Waiting for renderer timing...</small></span>}</div></section>}
    {job && job.state !== "idle" && <details className="generated review-log" open={job.state !== "completed"}><summary>{job.message} <span>{job.state === "running" ? "background job" : `exit ${job.returncode ?? "--"}`} · timing and process log</span></summary><pre>{job.log || "The generator is still running; live timing and process output will appear here."}</pre></details>}
  </section>;
}
