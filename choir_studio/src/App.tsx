import { useCallback, useEffect, useMemo, useRef, useState, useTransition, type CSSProperties } from "react";
import { BarChart3, ChevronLeft, ChevronRight, CircleAlert, CircleCheck, FileAudio, FolderOpen, LoaderCircle, Minus, Moon, Music2, PanelLeft, Pause, PenLine, Play, Plus, Settings2, Sparkles, Square, Sun, Trash2, WandSparkles, X } from "lucide-react";
import { bridge, deleteSong, media, openFfmpegDownload, openMedia, openSongFolder, renderJobStatus, spectrogramJobStatus, startRenderJob, startSpectrogramJob, type MediaStatus, type RenderJobStatus, type SpectrogramJobStatus } from "./bridge";
import { PianoRoll } from "./PianoRoll";
import type { AlignmentReport, Role, SongInspection } from "./types";

type Stage = "lyrics" | "align" | "review";
const stages: Array<[Stage, string, typeof Music2]> = [["lyrics", "Lyrics", PenLine], ["align", "Align", WandSparkles], ["review", "Review", BarChart3]];
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
  const [stage, setStage] = useState<Stage>(storedUiState.stage ?? "lyrics");
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
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [alignmentLoading, setAlignmentLoading] = useState(false);
  const [alignmentTransitionPending, startAlignmentTransition] = useTransition();
  const alignmentRequestRef = useRef(0);

  const role = useMemo(() => inspection?.roles.find((item) => item.role === roleName) ?? null, [inspection, roleName]);
  const transcriptKey = `${song}:${roleName}`;
  const loadSong = useCallback(async (nextSong: string, preferredRole = "") => {
    if (!nextSong) return;
    setBusy("Loading song"); setError("");
    try {
      const next = await bridge<SongInspection>({ command: "inspect_song", song: nextSong });
      const nextRole = next.roles.some((item) => item.role === preferredRole) ? preferredRole : next.roles[0]?.role ?? "";
      setInspection(next); setSong(nextSong); setRoleName(nextRole); setDraftState(null); setDraftRole(""); setAlignment(null); setAlignmentRole(""); setSelectedPhrase(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); }
  }, []);
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
    if (stage !== "lyrics" || !song || !roleName) {
      setTranscriptLoadedKey("");
      return;
    }
    let cancelled = false;
    setTranscriptLoadedKey("");
    bridge<TranscriptState>({ command: "read_transcript", song, role: roleName }).then((value) => {
      if (cancelled) return;
      setTranscript(value.text); setSavedTranscript(value.text); setTranscriptLoadedKey(`${song}:${roleName}`); setValidation(null);
    }).catch((cause) => { if (!cancelled) setError(String(cause)); });
    return () => { cancelled = true; };
  }, [song, roleName, stage]);
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
          return;
        }
        setDraftState({ text: candidate.text, path: candidate.path, warnings: [], review_segments: [], tight_gap_ms: 0 });
        setDraftRole(roleName); setAlignment({ text: candidate.text, report: candidate.report }); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(candidate.report));
      });
    }).catch((cause) => { if (!cancelled && requestId === alignmentRequestRef.current) { setTemplateSources([]); setError(String(cause)); } }).finally(() => {
      if (!cancelled && requestId === alignmentRequestRef.current) setAlignmentLoading(false);
    });
    return () => { cancelled = true; };
  }, [song, roleName, stage, startAlignmentTransition]);
  useEffect(() => {
    if (stage !== "lyrics" || !song || !roleName) return;
    const timer = window.setTimeout(() => bridge<typeof validation>({ command: "validate_transcript", song, role: roleName, text: transcript }).then(setValidation).catch(() => undefined), 350);
    return () => window.clearTimeout(timer);
  }, [song, roleName, stage, transcript]);
  const runDraft = async () => {
    if (!song || !roleName) return;
    setBusy("Drafting and aligning lyrics"); setError("");
    try {
      const draft = await bridge<DraftState>({ command: "draft", song, role: roleName, text: transcript, auto_lines: false });
      setDraftState(draft); setDraftRole(roleName); setTranscript(draft.text); setSavedTranscript(draft.text); setLyricsPrompt("");
      const pending = await bridge<{ report: AlignmentReport; text: string; path: string }>({ command: "align", song, role: roleName });
      setDraftState({ ...draft, text: pending.text, path: pending.path }); setTranscript(pending.text); setSavedTranscript(pending.text); setAlignment(pending); setAlignmentRole(roleName); setSelectedPhrase(firstPhraseLine(pending.report)); setStage("align");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const runNoteSkeleton = async (placeholder: string) => {
    if (!song || !roleName) return;
    if (transcriptLoadedKey !== transcriptKey) {
      setError("Wait for this role's lyric source to finish loading before creating a note skeleton.");
      return;
    }
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
      <div className="brand"><span className="brand-mark" aria-hidden="true"><i className="visual-bar bar-low" /><i className="visual-bar bar-mid" /><i className="visual-bar bar-high" /><b className="sharp">#</b><b className="flat">♭</b></span><span>DECTALK Choir</span><strong>Studio</strong></div>
      <label className="song-select"><span>Song</span><select value={song} onChange={(event) => void loadSong(event.target.value)}>{songs.map((item) => <option key={item}>{item}</option>)}</select></label>
      <nav className="lifecycle" aria-label="Track design phases">
        {stages.map(([id, label, Icon], index) => <button key={id} className={stage === id ? "active" : ""} onClick={() => selectStage(id)}><span className="stage-index">{index + 1}</span><Icon size={16} />{label}</button>)}
      </nav>
      <div className="selection-actions"><button className="header-command" type="button" onClick={() => void playRender()} disabled={!inspection?.final_mix} title="Open the completed song mix in your default media player" aria-label="Open render in default media player"><Play size={15} /></button><button className="header-command" type="button" onClick={() => void openOutputs()} disabled={!song} title="Open this song's generated output folder" aria-label="Open output folder"><FolderOpen size={16} /></button><button className="header-command destructive-command" type="button" onClick={() => setDeleteSongArmed(true)} disabled={!song} title="Delete this song and all of its outputs" aria-label="Delete selected song"><Trash2 size={15} /></button></div>
      <div className="theme-switch" role="group" aria-label="Color theme"><button className={theme === "dark" ? "active" : ""} type="button" title="Use dark theme" aria-label="Use dark theme" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}><Moon size={15} /></button><button className={theme === "light" ? "active" : ""} type="button" title="Use light theme" aria-label="Use light theme" aria-pressed={theme === "light"} onClick={() => setTheme("light")}><Sun size={16} /></button></div>
      <div className="header-state">{busy}</div>
    </header>
    {deleteSongArmed && <section className="song-delete-confirm" role="alertdialog" aria-label={`Delete ${song}`}><div><strong>Delete {song}?</strong><span>Its inputs, settings, and generated outputs will be removed.</span></div><button className="secondary" type="button" onClick={() => setDeleteSongArmed(false)} disabled={!!busy}>Cancel</button><button className="danger" type="button" onClick={() => void removeSong()} disabled={!!busy}>Delete song</button></section>}
    {error && <div className="error-toast" role="alert" aria-live="assertive"><CircleAlert size={17} /><span>{error}</span><div className="error-actions">{/ffmpeg/i.test(error) && <><button type="button" className="error-action" onClick={() => void openFfmpegDownload().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Open FFmpeg's official Windows download guidance">Get FFmpeg</button><button type="button" className="error-action" onClick={() => void navigator.clipboard.writeText(FFMPEG_WINGET_COMMAND).then(() => setError(`Copied: ${FFMPEG_WINGET_COMMAND}`)).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))} title="Copy the Windows Package Manager install command">Copy winget</button></>}<button type="button" onClick={() => setError("")} title="Dismiss error" aria-label="Dismiss error"><X size={16} /></button></div></div>}
    <section className={`workspace ${stage === "review" ? "review-workspace" : ""}`}>
      <aside className="track-rail"><h2 className="rail-song-title" title={song}>{song || "Song"}</h2><div className="rail-heading"><PanelLeft size={16} /> Tracks</div><div className="track-list">{inspection?.roles.map((item) => <button key={item.role} className={item.role === roleName ? "track active" : "track"} onClick={() => selectRole(item.role)}><strong title={item.role}>{item.role}</strong><span>{item.midi_source_name}</span><small>{item.note_count} notes · {item.midi_range}</small>{item.polyphony && item.polyphony > 1 && <i>Needs split</i>}</button>)}</div></aside>
      <section className={`surface${stage === "lyrics" ? " lyrics-surface" : ""}`}>
        {stage === "lyrics" && lyricsPrompt && <div className="notice draft-route"><WandSparkles size={17} /><div>{lyricsPrompt}</div></div>}
        {stage === "lyrics" && <section className="lyrics-stage"><LyricsStage transcript={transcript} transcriptLoaded={transcriptLoadedKey === transcriptKey} setTranscript={setTranscript} validation={validation} onDraft={runDraft} onNoteSkeleton={runNoteSkeleton} onSave={saveTranscript} busy={busy} draftState={hasDraft ? draftState : null} dirty={transcript !== savedTranscript} prompt={lyricsPrompt} /></section>}
        {stage === "align" && <AlignStage role={role} inspection={inspection} song={song} alignment={activeAlignment} loading={alignmentLoading || alignmentTransitionPending} templateSources={templateSources} onAdoptTemplate={adoptTemplate} setAlignment={setAlignment} selectedPhrase={selectedPhrase} setSelectedPhrase={setSelectedPhrase} busy={busy} setBusy={setBusy} setError={setError} />}
        {stage === "review" && <ReviewStage song={song} role={role} inspection={inspection} enabledRoles={reviewEnabledRoles} onEnabledRolesChange={(roles) => void updateRenderRoles(roles)} onSelectRole={selectRole} onSelectVisualRole={selectRole} setInspection={setInspection} busy={busy} setBusy={setBusy} setError={setError} />}
      </section>
    </section>
  </main>;
}

function LyricsStage({ transcript, transcriptLoaded, setTranscript, validation, onDraft, onNoteSkeleton, onSave, busy, draftState, dirty, prompt }: { transcript: string; transcriptLoaded: boolean; setTranscript(value: string): void; validation: { invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null; onDraft(): void; onNoteSkeleton(placeholder: string): void; onSave(): void; busy: string; draftState: DraftState | null; dirty: boolean; prompt: string }) {
  const [skeletonPhoneme, setSkeletonPhoneme] = useState("duw");
  const [replaceArmed, setReplaceArmed] = useState(false);
  const hasLyrics = Boolean(transcript.trim());
  const skeletonDisabled = Boolean(busy) || !transcriptLoaded || !skeletonPhoneme.trim();
  const createSkeleton = () => {
    if (hasLyrics && !replaceArmed) {
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
    : hasLyrics && !replaceArmed
      ? "Replace the current lyric text with one direct phoneme per MIDI note."
      : "Create one direct DECTALK phoneme per MIDI note, grouped at MIDI rests.";
  return <><section className="surface-header lyrics-header"><div className="lyrics-title"><p className="eyebrow">Working lyric draft</p><h1>Lyrics</h1><p>Paste lyrics or create a note skeleton here. Draft timing turns this same text into the editable aligned draft.</p></div><div className="header-actions lyrics-actions"><span className={dirty ? "save-state dirty" : "save-state"}>{replaceArmed ? "Click again to replace" : dirty ? "Unsaved changes" : "Saved"}</span><button className="secondary" onClick={onSave} disabled={!!busy || !dirty}>Save draft</button><label className="skeleton-control" title={skeletonTitle}><input value={skeletonPhoneme} onChange={(event) => setSkeletonPhoneme(event.target.value)} aria-label="Note skeleton phoneme" placeholder="duw" /><button className="secondary" onClick={createSkeleton} disabled={skeletonDisabled}><Music2 size={16} /> {replaceArmed ? "Replace lyrics" : "Note skeleton"}</button></label><button className="primary" onClick={onDraft} disabled={!!busy || !transcript.trim()}><WandSparkles size={16} /> Draft timing</button></div></section><textarea className="transcript" value={transcript} onChange={(event) => setTranscript(event.target.value)} placeholder="Paste plain lyrics, or create one direct phoneme per MIDI note. Line breaks are phrase hints; commas and unsupported punctuation are normalized." />{validation && (!validation.ok || validation.normalized_lines.length > 0) && <div className={validation.ok ? "notice" : "warning"}><CircleAlert size={17} /><div>{validation.invalid_words.length > 0 && <><strong>Check these words:</strong> {validation.invalid_words.join(", ")}</>}{validation.normalized_lines.length > 0 && <span> Punctuation will be normalized before drafting.</span>}</div></div>}{draftState?.review_segments.length ? <details className="draft-review" open><summary>{draftState.review_segments.length} rapid multi-note word {draftState.review_segments.length === 1 ? "span needs" : "spans need"} verification <span>gaps at or below {draftState.tight_gap_ms} ms</span></summary><div>{draftState.review_segments.map((segment) => <div key={`${segment.line}-${segment.word_index}`} style={{ "--word-color": wordColor(segment.line, segment.word_index) } as CSSProperties}><strong>{segment.word}</strong><span>{segment.note_count} notes · {Math.round(segment.start_ms / 1000)}s-{Math.round(segment.end_ms / 1000)}s</span></div>)}</div></details> : null}{draftState && <details className="generated"><summary>Generated draft ready for alignment <span>{draftState.path}</span></summary><pre>{draftState.text}</pre></details>}</>;
}

function AlignStage({ role, inspection, song, alignment, loading, templateSources, onAdoptTemplate, setAlignment, selectedPhrase, setSelectedPhrase, busy, setBusy, setError }: { role: Role | null; inspection: SongInspection | null; song: string; alignment: { report: AlignmentReport; text: string } | null; loading: boolean; templateSources: AlignmentTemplate[]; onAdoptTemplate(sourceRole: string): void; setAlignment(value: { report: AlignmentReport; text: string } | null): void; selectedPhrase: number | null; setSelectedPhrase(value: number): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
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
    const updateDeleteArmed = (event: KeyboardEvent) => setDeleteArmed(event.ctrlKey || event.key === "Control");
    const clearDeleteArmed = () => setDeleteArmed(false);
    window.addEventListener("keydown", updateDeleteArmed);
    window.addEventListener("keyup", updateDeleteArmed);
    window.addEventListener("blur", clearDeleteArmed);
    return () => {
      window.removeEventListener("keydown", updateDeleteArmed);
      window.removeEventListener("keyup", updateDeleteArmed);
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
  const claimAdjacentNote = async (direction: -1 | 1) => {
    if (!alignment || !selectedWord || !role) return;
    setBusy("Claiming adjacent note"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "claim_alignment_note", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, direction });
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
      setAlignment(result); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index });
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
  const canClaimPrevious = selectedWordPosition > 0 && words[selectedWordPosition - 1].note_count > 1;
  const canClaimNext = selectedWordPosition >= 0 && selectedWordPosition < words.length - 1 && words[selectedWordPosition + 1].note_count > 1;
  const selectedPhraseInvalid = selectedPhrase !== null && invalidPhraseLines.includes(selectedPhrase);
  const overlay = alignment && selectedPhrase !== null ? <div className={`phrase-workbench ${selectedPhraseInvalid ? "invalid" : ""}`}>
    <div className="phrase-workbench-heading"><p className="eyebrow">{selectedPhrase ? `Phrase ${selectedPhrase}` : "Select a phrase above the notes"}</p><strong>{selectedPhrase ? "Drag either full-height edge guide to snap across any available note boundary." : "Phrase blocks stay compact until selected."}</strong></div>
    {selectedPhrase && <div className="word-strip">{visibleWords.map((item) => {
      if (item.line === null || item.word_index === null) return null;
      const isSelected = selectedWord?.line === item.line && selectedWord.wordIndex === item.word_index;
      return <div className={`word-token ${draggedWord === item.word_index ? "dragging" : ""} ${item.note_count === 0 ? "invalid" : ""}`} style={{ "--word-color": wordColor(item.line, item.word_index) } as CSSProperties} key={`${item.line}-${item.word_index}`} draggable={!busy} title="Drag this word onto another word to move it before that word" onDragStart={() => setDraggedWord(item.word_index!)} onDragEnd={() => setDraggedWord(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); void reorderWord(item.word_index!); }}>
        <button className={isSelected ? "selected" : ""} onClick={() => { setSelectedWord({ line: item.line!, wordIndex: item.word_index! }); setInsertOpen(false); }} title={item.lyric ?? ""}>{item.lyric}<small>{item.note_count === 0 ? "Needs note" : `${item.duration_ms} ms`}</small></button>
        {isSelected && <span className="word-quick-controls"><button type="button" title="Claim one note from the preceding word" aria-label="Claim one note from the preceding word" disabled={!!busy || !canClaimPrevious} onPointerDown={(event) => event.stopPropagation()} onClick={() => void claimAdjacentNote(-1)}><ChevronLeft size={12} /></button><button type="button" title="Claim one note from the following word" aria-label="Claim one note from the following word" disabled={!!busy || !canClaimNext} onPointerDown={(event) => event.stopPropagation()} onClick={() => void claimAdjacentNote(1)}><ChevronRight size={12} /></button></span>}
        <button className={deleteArmed ? "word-delete armed" : "word-delete"} type="button" title={deleteArmed ? `Ctrl-click to remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} aria-label={deleteArmed ? `Remove ${item.lyric}` : `Hold Ctrl to remove ${item.lyric}`} draggable={false} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { if (!event.ctrlKey) return; void removeWord(item.line!, item.word_index!); }} disabled={!!busy}><Minus size={11} /></button>
      </div>;
    })}{hiddenWordCount > 0 && <button className="word-more" type="button" onClick={() => setShowAllWords(true)}>{hiddenWordCount} more</button>}{showAllWords && words.length > 10 && <button className="word-more" type="button" onClick={() => setShowAllWords(false)}>Collapse</button>}<div className="word-insert-anchor"><button className="add-word" type="button" title="Insert a word after the selected word" aria-label="Insert a word" onClick={() => { const anchor = selectedWord ?? (words.length ? { line: words[words.length - 1].line!, wordIndex: words[words.length - 1].word_index! } : null); if (anchor) { setSelectedWord(anchor); setInsertOpen(true); } }} disabled={!words.length || !!busy}><Plus size={15} /></button>{insertOpen && <form className="word-insert-popover" onSubmit={(event) => { event.preventDefault(); void insert(); }}><input autoFocus value={insertWord} onChange={(event) => setInsertWord(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { setInsertOpen(false); setInsertWord(""); } }} placeholder="New word" /><button className="primary" type="submit" disabled={!!busy || !insertWord.trim()}>Insert</button><button className="secondary" type="button" aria-label="Cancel insert" title="Cancel insert" onClick={() => { setInsertOpen(false); setInsertWord(""); }}><X size={14} /></button></form>}</div></div>}
  </div> : null;
  return (
    <section className="align-workspace">
      {applyArmed && alignment && <section className="apply-confirm"><div><strong>Replace configured lyric input?</strong><span>`choir.py` will use this alignment on the next render. A backup is created beside the lyric file.</span></div><button className="secondary" onClick={() => setApplyArmed(false)} disabled={!!busy}>Cancel</button><button className="primary" onClick={() => void apply()} disabled={!!busy}>Apply alignment</button></section>}
      {applied && <div className="notice alignment-applied"><CircleAlert size={17} /><div><strong>Configured lyric input updated.</strong> {applied.path}{applied.backup_path && <> Backup: {applied.backup_path}</>}</div></div>}
      <section className="midi-transport align-transport">
        <div className="transport-group"><button className="primary" onClick={() => void playMidi()} disabled={!role?.midi_track}><Play size={15} /> Play MIDI</button><button className="secondary icon-command" title={mediaState?.paused ? "Resume preview" : "Pause preview"} onClick={() => void togglePause()} disabled={!active}>{mediaState?.paused ? <Play size={15} /> : <Pause size={15} />}</button><button className="secondary icon-command" title="Stop playback" onClick={() => void stop()} disabled={!mediaState}><Square size={14} /></button><span>{mediaLabel}</span></div>
        <div className="transport-group align-actions"><span className={missingNoteWords ? "save-state dirty" : "save-state"}>{alignment?.report.template?.source_role ? `Template: ${alignment.report.template.source_role}` : alignment ? missingNoteWords ? `${missingNoteWords} word${missingNoteWords === 1 ? "" : "s"} need a note` : "Pending source update" : "Not drafted"}</span>{templateSources.length > 0 && <label className="template-picker" title="Copy a saved same-lyrics alignment and remap it to this track by time"><select value={templateRole} onChange={(event) => setTemplateRole(event.target.value)}><option value="">Aligned template</option>{templateSources.map((source) => <option key={source.role} value={source.role}>{source.role}</option>)}</select><button type="button" className="secondary" onClick={() => onAdoptTemplate(templateRole)} disabled={!!busy || !templateRole}>Use</button></label>}{alignment && <button className="secondary" title={missingNoteWords ? "Resolve words without MIDI notes before applying" : "Validate and replace the configured lyric input used by choir.py"} onClick={() => setApplyArmed(true)} disabled={!!busy || missingNoteWords > 0}>Apply to source</button>}</div>
      </section>
      <div className="align-roll-shell"><PianoRoll track={role?.midi_track ?? null} durationSeconds={inspection?.midi?.duration_seconds ?? 0} durationTicks={inspection?.midi?.duration_ticks} alignment={alignment?.report.notes} selectedPhrase={selectedPhrase} selectedWord={selectedWord} invalidPhraseLines={invalidPhraseLines} playheadMs={active ? mediaState?.position_ms : null} onCursorChange={(milliseconds) => void seek(milliseconds)} onSelectPhrase={(line) => { setSelectedPhrase(line); setSelectedWord(null); setShowAllWords(false); }} onPlaybackPhraseChange={(line) => { setSelectedPhrase(line); setSelectedWord(null); setShowAllWords(false); }} onSelectWord={(line, wordIndex) => { setSelectedPhrase(line); setSelectedWord({ line, wordIndex }); setShowAllWords(wordIndex >= 10); }} onResizeWord={(edge, movement) => void adjust(edge, movement)} onResizePhrase={(edge, movement) => void adjustPhrase(edge, movement)} onAddVirtualSplit={(noteIndex, fraction) => void addVirtualSplit(noteIndex, fraction)}>{overlay}</PianoRoll>{loading && <div className="align-loading" role="status" aria-live="polite"><LoaderCircle size={17} /><span>Loading alignment and phrase map...</span></div>}</div>
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
  return <section className="review-stats review-track-table" aria-label="Track review statistics"><table><thead><tr><th>Render</th><th>Role</th><th>Status</th><th>MIDI</th><th>DECtalk / audible</th><th>Active loudness min / median / avg / max</th><th>Peak</th></tr></thead><tbody>{roles.map((item) => {
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

function ReviewStage({ song, role, inspection, enabledRoles, onEnabledRolesChange, onSelectRole, onSelectVisualRole, setInspection, busy, setBusy, setError }: { song: string; role: Role | null; inspection: SongInspection | null; enabledRoles: string[]; onEnabledRolesChange(roles: string[]): void; onSelectRole(role: string): void; onSelectVisualRole(role: string): void; setInspection(value: SongInspection | null): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [panel, setPanel] = useState<"overview" | "tune" | "visuals">("overview");
  const [position, setPosition] = useState<[number, number, number]>([0.5, 0.25, 0.25]);
  const [hsb, setHsb] = useState<[number, number, number]>([0, 100, 100]);
  const [job, setJob] = useState<SpectrogramJobStatus | null>(null);
  const [renderJob, setRenderJob] = useState<RenderJobStatus | null>(null);
  const [tuning, setTuning] = useState<TrackTuning | null>(null);
  const [autoNormalize, setAutoNormalize] = useState<AutoNormalizeTuning | null>(null);
  const tuningCache = useRef(new Map<string, TrackTuning>());
  useEffect(() => { setPanel("overview"); }, [song]);
  useEffect(() => { setRenderJob(null); }, [inspection?.song_name]);
  useEffect(() => {
    if (!role) return;
    setPosition(role.visual_position);
    setHsb(role.visual_hsb);
    setJob((current) => current?.state === "running" ? current : null);
  }, [role?.role, role?.visual_position, role?.visual_hsb]);
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
  const refresh = async () => {
    const next = await bridge<SongInspection>({ command: "inspect_song", song });
    setInspection(next);
  };
  const saveLayout = async () => {
    if (!role) return;
    setBusy("Saving visualizer layout"); setError("");
    try {
      await bridge({ command: "save_visual_layout", song, role: role.role, position, hsb });
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
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
    setError("");
    try {
      setJob(await startSpectrogramJob(song, enabledRoles));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
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
  const enabledVisualRoles = inspection?.roles.filter((item) => enabledRoles.includes(item.role)) ?? [];
  const hasFinishedRender = Boolean(final);
  return <section className={`review-stage ${panel}-panel`}>
    <header className="surface-header review-header"><div className="review-identity"><p className="eyebrow">Output review</p><h1>{song || "Select a song"}<span>{role?.role ?? "Select a role"}</span></h1><p>Enable renderable tracks in the table; tune an individual role from its cog.</p></div><section className={renderJob?.state === "completed" ? "review-render completed" : "review-render"} aria-label="Render selected tracks">{renderJob?.state === "completed" ? <div className="render-complete"><CircleCheck size={19} /><div><strong>Render complete</strong><span>{renderJob.message}</span></div></div> : <div><p className="eyebrow">Render set</p><strong>{enabledRoles.length} tracks enabled</strong><span>{renderJob?.state === "running" ? renderJob.message : "Saved in settings.yaml"}</span></div>}<div className="review-render-actions"><button className="primary" type="button" onClick={() => void render()} disabled={renderJob?.state === "running" || !enabledRoles.length}>{renderJob?.state === "running" ? "Rendering in background..." : <><FileAudio size={16} /> Render enabled tracks <span className="render-duration">{formatDuration(inspection?.midi?.duration_seconds)}</span></>}</button>{hasFinishedRender && <button className="secondary spectrogram-layout-command" type="button" onClick={() => { const firstEnabledRole = enabledVisualRoles[0]?.role; if (firstEnabledRole) onSelectVisualRole(firstEnabledRole); setPanel("visuals"); }} disabled={!enabledVisualRoles.length}><BarChart3 size={15} /> Spectrogram layout</button>}</div></section></header>
    {panel !== "overview" && <nav className="review-panel-nav" aria-label="Review workspace">
      <button className="secondary" type="button" onClick={() => setPanel("overview")}><BarChart3 size={15} /> Review overview</button>
      {panel === "tune" && <span>Editing {role?.role ?? "the selected role"} tuning profile</span>}
      {panel === "visuals" && <span>{enabledVisualRoles.length} enabled render region{enabledVisualRoles.length === 1 ? "" : "s"}. Select one to edit its color and position.</span>}
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
      <div className="visual-layout-preview" aria-label="Enabled spectrogram render regions">{enabledVisualRoles.map((item) => <button key={item.role} type="button" className={item.role === role?.role ? "visual-region selected" : "visual-region"} title={`Select ${item.role} for layout editing`} onClick={() => onSelectVisualRole(item.role)} style={{ left: `${item.visual_position[1] * 100}%`, top: `${item.visual_position[2] * 100}%`, width: `${item.visual_position[0] * 100}%`, height: `${item.visual_position[0] * 100}%`, background: `hsl(${item.visual_hsb[0]} ${item.visual_hsb[1]}% ${Math.max(18, item.visual_hsb[2] / 2)}%)`, borderColor: `hsl(${item.visual_hsb[0]} ${item.visual_hsb[1]}% ${item.visual_hsb[2]}%)` }}><span>{item.role}</span></button>)}</div>
      <div className="visual-layout-controls"><div><p className="eyebrow">Spectrogram render layout</p><strong>{enabledVisualRoles.some((item) => item.role === role?.role) ? role?.role : "Select an enabled region"}</strong><p>The preview and video use only enabled stems. Position and color remain per-region settings.</p></div><div className="visual-fields"><label>Color<input type="color" value={hsbToHex(hsb)} onChange={(event) => setHsb(hexToHsb(event.target.value))} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Size<input type="number" min="0.05" max="1" step="0.01" value={position[0]} onChange={(event) => changeTriplet(setPosition, position, 0, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Left<input type="number" min="0" max="1" step="0.01" value={position[1]} onChange={(event) => changeTriplet(setPosition, position, 1, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Top<input type="number" min="0" max="1" step="0.01" value={position[2]} onChange={(event) => changeTriplet(setPosition, position, 2, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Hue<input type="number" min="0" max="360" step="1" value={hsb[0]} onChange={(event) => changeTriplet(setHsb, hsb, 0, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Sat<input type="number" min="0" max="100" step="1" value={hsb[1]} onChange={(event) => changeTriplet(setHsb, hsb, 1, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label><label>Bright<input type="number" min="0" max="100" step="1" value={hsb[2]} onChange={(event) => changeTriplet(setHsb, hsb, 2, event.target.value)} disabled={!enabledVisualRoles.some((item) => item.role === role?.role)} /></label></div><div className="review-actions"><button className="secondary" type="button" onClick={() => void saveLayout()} disabled={!enabledVisualRoles.some((item) => item.role === role?.role) || !!busy}>Save region</button><button className="primary" type="button" onClick={() => void generate()} disabled={!enabledRoles.length || job?.state === "running"}>{job?.state === "running" ? "Generating video..." : <><BarChart3 size={16} /> Generate enabled spectrograms</>}</button>{inspection?.animation_exists && inspection.animation_path && <button className="secondary" type="button" onClick={() => void openMedia(inspection.animation_path!)} disabled={!!busy}><Play size={15} /> Open video</button>}</div></div>
    </section>
    {job && job.state !== "idle" && <details className="generated review-log" open={job.state !== "completed"}><summary>{job.message} <span>{job.state === "running" ? "background job" : `exit ${job.returncode ?? "--"}`}</span></summary><pre>{job.log || "The generator is still running; its completed log will appear here."}</pre></details>}
  </section>;
}
