import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronRight, CircleAlert, FileAudio, FolderOpen, Moon, Music2, PanelLeft, Pause, PenLine, Play, Sparkles, Square, Sun, Volume2, WandSparkles } from "lucide-react";
import { bridge, media, openSongFolder, type MediaStatus } from "./bridge";
import { PianoRoll } from "./PianoRoll";
import type { AlignmentReport, Role, SongInspection } from "./types";

type Stage = "midi" | "lyrics" | "align" | "render";
const stages: Array<[Stage, string, typeof Music2]> = [["midi", "MIDI", Music2], ["lyrics", "Lyrics", PenLine], ["align", "Align", WandSparkles], ["render", "Render", FileAudio]];
type ReviewSegment = { line: number; word_index: number; word: string; note_count: number; start_ms: number; end_ms: number; largest_internal_gap_ms: number };
type DraftState = { text: string; path: string; warnings: string[]; review_segments: ReviewSegment[]; tight_gap_ms: number };
const wordColor = (line: number, wordIndex: number) => ["#64d4ad", "#7faee9", "#f0b96c", "#d99dca", "#a5ca7a", "#76cbc8"][Math.abs(line * 7 + wordIndex) % 6];

export default function App() {
  const [songs, setSongs] = useState<string[]>([]);
  const [song, setSong] = useState("");
  const [inspection, setInspection] = useState<SongInspection | null>(null);
  const [roleName, setRoleName] = useState("");
  const [stage, setStage] = useState<Stage>("midi");
  const [theme, setTheme] = useState<"dark" | "light">(() => window.localStorage.getItem("dectalk-choir-studio.theme") === "light" ? "light" : "dark");
  const [transcript, setTranscript] = useState("");
  const [savedTranscript, setSavedTranscript] = useState("");
  const [validation, setValidation] = useState<{ invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null>(null);
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const [alignment, setAlignment] = useState<{ report: AlignmentReport; text: string } | null>(null);
  const [selectedPhrase, setSelectedPhrase] = useState<number | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const role = useMemo(() => inspection?.roles.find((item) => item.role === roleName) ?? null, [inspection, roleName]);
  const loadSong = useCallback(async (nextSong: string) => {
    if (!nextSong) return;
    setBusy("Loading song"); setError("");
    try {
      const next = await bridge<SongInspection>({ command: "inspect_song", song: nextSong });
      setInspection(next); setSong(nextSong); setRoleName(next.roles[0]?.role ?? ""); setDraftState(null); setAlignment(null); setSelectedPhrase(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); }
  }, []);
  useEffect(() => { bridge<string[]>({ command: "list_songs" }).then((items) => { setSongs(items); if (items[0]) void loadSong(items[0]); }).catch((cause) => setError(String(cause))); }, [loadSong]);
  useEffect(() => { document.documentElement.dataset.theme = theme; window.localStorage.setItem("dectalk-choir-studio.theme", theme); }, [theme]);
  useEffect(() => {
    if (!song || !roleName) return;
    bridge<{ text: string }>({ command: "read_transcript", song, role: roleName }).then((value) => { setTranscript(value.text); setSavedTranscript(value.text); setValidation(null); }).catch((cause) => setError(String(cause)));
  }, [song, roleName]);
  useEffect(() => {
    if (!song || !roleName) return;
    const timer = window.setTimeout(() => bridge<typeof validation>({ command: "validate_transcript", song, role: roleName, text: transcript }).then(setValidation).catch(() => undefined), 350);
    return () => window.clearTimeout(timer);
  }, [song, roleName, transcript]);
  const runDraft = async () => {
    if (!song || !roleName) return;
    setBusy("Drafting lyrics"); setError("");
    try {
      const result = await bridge<DraftState>({ command: "draft", song, role: roleName, text: transcript, mode: "transcript", auto_lines: false });
      setDraftState(result); setSavedTranscript(transcript);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const saveTranscript = async () => {
    if (!song || !roleName) return;
    setBusy("Saving transcript"); setError("");
    try { await bridge({ command: "save_transcript", song, role: roleName, text: transcript }); setSavedTranscript(transcript); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const runAlign = async () => {
    if (!song || !roleName) return;
    setBusy("Building alignment"); setError("");
    try { const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "align", song, role: roleName }); setAlignment(result); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const openOutputs = async () => { try { await openSongFolder(song, "output"); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const playRender = async () => { if (!inspection?.final_mix) return; try { await media<MediaStatus>("media_play", { path: inspection.final_mix, kind: "audio", fromMs: 0 }); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };

  return <main className="studio-shell">
    <header className="app-header">
      <div className="brand"><Sparkles size={18} /><span>DECTALK Choir</span><strong>Studio</strong></div>
      <label className="song-select"><span>Song</span><select value={song} onChange={(event) => void loadSong(event.target.value)}>{songs.map((item) => <option key={item}>{item}</option>)}</select></label>
      <div className="selection-actions"><button className="header-command" type="button" onClick={() => void playRender()} disabled={!inspection?.final_mix} title="Play the completed song mix" aria-label="Play render"><Play size={15} /></button><button className="header-command" type="button" onClick={() => void openOutputs()} disabled={!song} title="Open this song's generated output folder" aria-label="Open output folder"><FolderOpen size={16} /></button></div>
      <div className="theme-switch" role="group" aria-label="Color theme"><button className={theme === "dark" ? "active" : ""} type="button" title="Use dark theme" aria-label="Use dark theme" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}><Moon size={15} /></button><button className={theme === "light" ? "active" : ""} type="button" title="Use light theme" aria-label="Use light theme" aria-pressed={theme === "light"} onClick={() => setTheme("light")}><Sun size={16} /></button></div>
      <div className="header-state">{busy || (inspection ? `${inspection.roles.length} roles · ${inspection.midi ? Math.round(inspection.midi.duration_seconds) : 0}s` : "No song loaded")}</div>
    </header>
    <nav className="lifecycle" aria-label="Track design phases">
      {stages.map(([id, label, Icon], index) => <button key={id} className={stage === id ? "active" : ""} onClick={() => setStage(id)}><span className="stage-index">{index + 1}</span><Icon size={16} />{label}</button>)}
    </nav>
    {error && <div className="error-banner"><CircleAlert size={17} />{error}</div>}
    <section className="workspace">
      <aside className="track-rail"><div className="rail-heading"><PanelLeft size={16} /> Tracks</div>{inspection?.roles.map((item) => <button key={item.role} className={item.role === roleName ? "track active" : "track"} onClick={() => setRoleName(item.role)}><strong>{item.role}</strong><span>{item.midi_source_name}</span><small>{item.note_count} notes · {item.midi_range}</small>{item.polyphony && item.polyphony > 1 && <i>Needs split</i>}</button>)}</aside>
      <section className="surface">
        {stage === "midi" && <MidiStage song={song} role={role} inspection={inspection} splitOpen={splitOpen} setSplitOpen={setSplitOpen} setError={setError} />}
        {stage === "lyrics" && <LyricsStage transcript={transcript} setTranscript={setTranscript} validation={validation} onDraft={runDraft} onSave={saveTranscript} busy={busy} draftState={draftState} dirty={transcript !== savedTranscript} />}
        {stage === "align" && <AlignStage role={role} inspection={inspection} song={song} alignment={alignment} setAlignment={setAlignment} selectedPhrase={selectedPhrase} setSelectedPhrase={setSelectedPhrase} onAlign={runAlign} busy={busy} setBusy={setBusy} setError={setError} />}
        {stage === "render" && <RenderStage role={role} song={song} draftState={draftState} alignment={alignment} busy={busy} setBusy={setBusy} setError={setError} />}
      </section>
    </section>
  </main>;
}

function MidiStage({ song, role, inspection, splitOpen, setSplitOpen, setError }: { song: string; role: Role | null; inspection: SongInspection | null; splitOpen: boolean; setSplitOpen(value: boolean): void; setError(value: string): void }) {
  const track = role?.midi_track ?? null;
  const [cursorMs, setCursorMs] = useState(0);
  const [mediaState, setMediaState] = useState<MediaStatus | null>(null);
  const [mediaLabel, setMediaLabel] = useState("Select Play MIDI to preview this source track.");
  const active = Boolean(mediaState && !["stopped", "not ready"].includes(mediaState.mode));

  useEffect(() => {
    if (!active) return;
    const poll = window.setInterval(() => {
      media<MediaStatus>("media_status").then(setMediaState).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
    }, 250);
    return () => window.clearInterval(poll);
  }, [active, setError]);
  useEffect(() => {
    setCursorMs(0); setMediaState(null); setMediaLabel("Select Play MIDI to preview this source track.");
    return () => { void media<MediaStatus>("media_stop").catch(() => undefined); };
  }, [role?.role]);
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
  const playAudio = async (path: string, label: string) => { try { const next = await media<MediaStatus>("media_play", { path, kind: "audio", fromMs: 0 }); setMediaState(next); setMediaLabel(`Playing ${label}.`); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const seek = async (milliseconds: number) => { setCursorMs(milliseconds); if (!active) return; try { setMediaState(await media<MediaStatus>("media_seek", { positionMs: Math.round(milliseconds) })); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };

  return <><section className="surface-header"><div><p className="eyebrow">Source material</p><h1>{track?.name ?? "Select a track"}</h1><p>{track ? `${track.note_count} notes · ${role?.midi_range} · source remains read-only` : "Choose a role to inspect its source MIDI."}</p></div></section><section className="midi-transport"><div className="transport-group"><button className="primary" onClick={() => void playMidi()} disabled={!track}><Play size={15} /> Play MIDI</button><button className="secondary icon-command" title={mediaState?.paused ? "Resume preview" : "Pause preview"} onClick={() => void togglePause()} disabled={!active}>{mediaState?.paused ? <Play size={15} /> : <Pause size={15} />}</button><button className="secondary icon-command" title="Stop playback" onClick={() => void stop()} disabled={!mediaState}><Square size={14} /></button><span>{mediaLabel}</span></div><div className="transport-group rendered"><Volume2 size={15} /><button className="secondary" onClick={() => role && void playAudio(role.stem_path, `${role.role} stem`)} disabled={!role}>Stem</button><button className="secondary" onClick={() => inspection && void playAudio(inspection.final_mix, "final mix")} disabled={!inspection}>Final mix</button></div></section><PianoRoll track={track} durationSeconds={inspection?.midi?.duration_seconds ?? 0} playheadMs={active ? mediaState?.position_ms : null} onCursorChange={(milliseconds) => void seek(milliseconds)} /><button className="disclosure" onClick={() => setSplitOpen(!splitOpen)}>{splitOpen ? <ChevronDown size={17} /> : <ChevronRight size={17} />} Split this MIDI source</button>{splitOpen && <section className="split-panel"><div><strong>Non-destructive split</strong><p>Lane preview and explicit export will move here from the existing splitter. The source stays untouched until export.</p></div></section>}</>;
}

function LyricsStage({ transcript, setTranscript, validation, onDraft, onSave, busy, draftState, dirty }: { transcript: string; setTranscript(value: string): void; validation: { invalid_words: string[]; normalized_lines: string[]; ok: boolean } | null; onDraft(): void; onSave(): void; busy: string; draftState: DraftState | null; dirty: boolean }) {
  return <><section className="surface-header"><div><p className="eyebrow">Durable lyric source</p><h1>Transcript</h1><p>Keep the human transcript here. Drafted timing is a generated artifact, not the next thing to maintain.</p></div><div className="header-actions"><span className={dirty ? "save-state dirty" : "save-state"}>{dirty ? "Unsaved changes" : "Saved"}</span><button className="secondary" onClick={onSave} disabled={!!busy || !dirty}>Save source</button><button className="primary" onClick={onDraft} disabled={!!busy}><WandSparkles size={16} /> Draft timing</button></div></section><textarea className="transcript" value={transcript} onChange={(event) => setTranscript(event.target.value)} placeholder="Paste plain lyrics. Line breaks are phrase hints; commas and unsupported punctuation are normalized." />{validation && (!validation.ok || validation.normalized_lines.length > 0) && <div className={validation.ok ? "notice" : "warning"}><CircleAlert size={17} /><div>{validation.invalid_words.length > 0 && <><strong>Check these words:</strong> {validation.invalid_words.join(", ")}</>}{validation.normalized_lines.length > 0 && <span> Punctuation will be normalized before drafting.</span>}</div></div>}{draftState?.review_segments.length ? <details className="draft-review" open><summary>{draftState.review_segments.length} rapid multi-note word {draftState.review_segments.length === 1 ? "span needs" : "spans need"} verification <span>gaps at or below {draftState.tight_gap_ms} ms</span></summary><div>{draftState.review_segments.map((segment) => <div key={`${segment.line}-${segment.word_index}`} style={{ "--word-color": wordColor(segment.line, segment.word_index) } as CSSProperties}><strong>{segment.word}</strong><span>{segment.note_count} notes · {Math.round(segment.start_ms / 1000)}s-{Math.round(segment.end_ms / 1000)}s</span></div>)}</div></details> : null}{draftState && <details className="generated"><summary>Generated draft ready for alignment <span>{draftState.path}</span></summary><pre>{draftState.text}</pre></details>}</>;
}

function AlignStage({ role, inspection, song, alignment, setAlignment, selectedPhrase, setSelectedPhrase, onAlign, busy, setBusy, setError }: { role: Role | null; inspection: SongInspection | null; song: string; alignment: { report: AlignmentReport; text: string } | null; setAlignment(value: { report: AlignmentReport; text: string } | null): void; selectedPhrase: number | null; setSelectedPhrase(value: number): void; onAlign(): void; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [selectedWord, setSelectedWord] = useState<{ line: number; wordIndex: number } | null>(null);
  const [insertWord, setInsertWord] = useState("");
  const [applyArmed, setApplyArmed] = useState(false);
  const [applied, setApplied] = useState<{ path: string; backup_path: string | null } | null>(null);
  const [cursorMs, setCursorMs] = useState(0);
  const [mediaState, setMediaState] = useState<MediaStatus | null>(null);
  const [mediaLabel, setMediaLabel] = useState("Preview this role while you align its lyrics.");
  const active = Boolean(mediaState && !["stopped", "not ready"].includes(mediaState.mode));
  const phraseEntries = alignment?.report.notes.filter((entry) => entry.line === selectedPhrase) ?? [];
  const words = phraseEntries.filter((entry, index, items) => index === 0 || entry.word_index !== items[index - 1].word_index);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => media<MediaStatus>("media_status").then(setMediaState).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))), 250);
    return () => window.clearInterval(timer);
  }, [active, setError]);
  useEffect(() => {
    setCursorMs(0); setMediaState(null); setMediaLabel("Preview this role while you align its lyrics.");
    return () => { void media<MediaStatus>("media_stop").catch(() => undefined); };
  }, [role?.role]);
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
  const seek = async (milliseconds: number) => { setCursorMs(milliseconds); if (!active) return; try { setMediaState(await media<MediaStatus>("media_seek", { positionMs: Math.round(milliseconds) })); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const adjust = async (edge: "start" | "end", movement: -1 | 1) => {
    if (!alignment || !selectedWord || !role) return;
    setBusy("Adjusting phrase"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "resize_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, edge, movement });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const adjustPhrase = async (edge: "start" | "end", movement: -1 | 1) => {
    if (!alignment || !role || selectedPhrase === null) return;
    setBusy("Adjusting phrase range"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string }>({ command: "resize_phrase", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedPhrase, edge, movement });
      setAlignment(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const insert = async () => {
    if (!alignment || !selectedWord || !role || !insertWord.trim()) return;
    setBusy("Inserting lyric"); setError("");
    try {
      const result = await bridge<{ report: AlignmentReport; text: string; selected: { line: number; word_index: number } }>({ command: "insert_alignment", song, role: role.role, report: alignment.report, text: alignment.text, line: selectedWord.line, word_index: selectedWord.wordIndex, word: insertWord.trim(), position: "after" });
      setAlignment(result); setSelectedWord({ line: result.selected.line, wordIndex: result.selected.word_index }); setInsertWord("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const apply = async () => {
    if (!alignment || !role) return;
    setBusy("Applying aligned lyrics"); setError("");
    try {
      const result = await bridge<{ path: string; backup_path: string | null }>({ command: "apply_alignment", song, role: role.role, text: alignment.text });
      setApplied(result); setApplyArmed(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  const selectedLabel = words.find((item) => item.word_index === selectedWord?.wordIndex)?.lyric;
  const overlay = alignment ? <div className="phrase-workbench">
    <div className="phrase-workbench-heading"><p className="eyebrow">{selectedPhrase ? `Phrase ${selectedPhrase}` : "Select a phrase above the notes"}</p><strong>{selectedPhrase ? "Drag the phrase bar edges to change phrase range" : "Phrase blocks stay compact until selected."}</strong></div>
    {selectedPhrase && <div className="word-strip">{words.map((item) => <button className={selectedWord?.line === item.line && selectedWord.wordIndex === item.word_index ? "selected" : ""} style={{ "--word-color": wordColor(item.line ?? 0, item.word_index ?? 0) } as CSSProperties} key={item.note_index} onClick={() => item.line !== null && item.word_index !== null && setSelectedWord({ line: item.line, wordIndex: item.word_index })}>{item.lyric}<small>{item.duration_ms} ms</small></button>)}</div>}
    {selectedWord && <div className="phrase-adjust"><strong>{selectedLabel}</strong><span>Drag either highlighted edge note left or right. The boundary stays inside this phrase.</span><label><input value={insertWord} onChange={(event) => setInsertWord(event.target.value)} placeholder="Insert after" /><button type="button" onClick={() => void insert()} disabled={!!busy || !insertWord.trim()}>Insert</button></label></div>}
  </div> : null;
  return <section className="align-workspace"><section className="surface-header"><div><p className="eyebrow">Phrase-level review</p><h1>Align {role?.role ?? "lyrics"}</h1><p>Click a phrase to expose its words directly above the notes. The selected word drives its matching colored note span.</p></div><div className="header-actions"><span className="save-state">{alignment ? "Safe alignment saved" : "Not built"}</span>{alignment && <button className="secondary" title="Validate and replace the configured lyrics file used by choir.py" onClick={() => setApplyArmed(true)} disabled={!!busy}>Apply to source</button>}<button className="primary" onClick={onAlign} disabled={!!busy}><WandSparkles size={16} /> {alignment ? "Rebuild alignment" : "Build alignment"}</button></div></section>{applyArmed && alignment && <section className="apply-confirm"><div><strong>Replace configured lyric input?</strong><span>`choir.py` will use this alignment on the next render. A backup is created beside the lyric file.</span></div><button className="secondary" onClick={() => setApplyArmed(false)} disabled={!!busy}>Cancel</button><button className="primary" onClick={() => void apply()} disabled={!!busy}>Apply alignment</button></section>}{applied && <div className="notice alignment-applied"><CircleAlert size={17} /><div><strong>Configured lyric input updated.</strong> {applied.path}{applied.backup_path && <> Backup: {applied.backup_path}</>}</div></div>}<section className="midi-transport align-transport"><div className="transport-group"><button className="primary" onClick={() => void playMidi()} disabled={!role?.midi_track}><Play size={15} /> Play MIDI</button><button className="secondary icon-command" title={mediaState?.paused ? "Resume preview" : "Pause preview"} onClick={() => void togglePause()} disabled={!active}>{mediaState?.paused ? <Play size={15} /> : <Pause size={15} />}</button><button className="secondary icon-command" title="Stop playback" onClick={() => void stop()} disabled={!mediaState}><Square size={14} /></button><span>{mediaLabel}</span></div></section><PianoRoll track={role?.midi_track ?? null} durationSeconds={inspection?.midi?.duration_seconds ?? 0} alignment={alignment?.report.notes} selectedPhrase={selectedPhrase} selectedWord={selectedWord} playheadMs={active ? mediaState?.position_ms : null} onCursorChange={(milliseconds) => void seek(milliseconds)} onSelectPhrase={(line) => { setSelectedPhrase(line); setSelectedWord(null); }} onSelectWord={(line, wordIndex) => setSelectedWord({ line, wordIndex })} onResizeWord={(edge, movement) => void adjust(edge, movement)} onResizePhrase={(edge, movement) => void adjustPhrase(edge, movement)}>{overlay}</PianoRoll></section>;
}

function RenderStage({ role, song, draftState, alignment, busy, setBusy, setError }: { role: Role | null; song: string; draftState: { text: string } | null; alignment: { report: AlignmentReport; text: string } | null; busy: string; setBusy(value: string): void; setError(value: string): void }) {
  const [result, setResult] = useState<{ ok: boolean; returncode: number; stdout: string; stderr: string } | null>(null);
  const render = async () => {
    if (!song) return;
    setBusy("Rendering ready roles"); setError("");
    try {
      const next = await bridge<{ ok: boolean; returncode: number; stdout: string; stderr: string }>({ command: "render", song });
      setResult(next);
      if (!next.ok) setError(`choir.py exited ${next.returncode}. Review the compiler log below.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(""); }
  };
  return <section className="render-stage"><p className="eyebrow">Compile only what is ready</p><h1>{role?.role ?? "Select a role"}</h1><dl><div><dt>Transcript</dt><dd>Saved separately</dd></div><div><dt>Draft</dt><dd>{draftState ? "Ready" : "Not drafted"}</dd></div><div><dt>Alignment</dt><dd>{alignment ? "Reviewed" : "Not started"}</dd></div></dl><button className="primary" onClick={() => void render()} disabled={!!busy}><FileAudio size={16} /> Render ready roles</button><p className="muted">This invokes the existing `choir.py {song}` contract. Empty or invalid roles remain skipped by the compiler.</p>{result && <details className="generated" open={!result.ok}><summary>{result.ok ? "Render complete" : "Render failed"} <span>exit {result.returncode}</span></summary><pre>{result.stdout}{result.stderr}</pre></details>}</section>;
}
