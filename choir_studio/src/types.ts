export type MidiNote = {
  start_tick: number;
  end_tick: number;
  pitch: number;
  velocity: number;
  channel: number;
};

export type MidiTrack = {
  index: number;
  name: string;
  notes: MidiNote[];
  note_count: number;
  min_pitch: number | null;
  max_pitch: number | null;
  max_polyphony: number;
  warnings: string[];
};

export type Role = {
  role: string;
  midi_source_name: string;
  lyric_stem: string;
  lyric_path: string;
  stem_path: string;
  stem_exists: boolean;
  loudness: Loudness | null;
  visual_hsb: [number, number, number];
  visual_position: [number, number, number];
  visual_configured: boolean;
  visual_label: string;
  visual_label_enabled: boolean;
  visual_label_position: string;
  visual_label_show_voice: boolean;
  visual_label_show_head_size: boolean;
  visual_label_font: string;
  visual_label_font_size_percent: number;
  visual_current_word_enabled: boolean;
  visual_current_word_position: string;
  visual_current_word_font: string;
  visual_current_word_font_size_percent: number;
  visual_current_word_use_track_color: boolean;
  dectalk_voice: string | null;
  head_size: number | null;
  render_enabled: boolean;
  render_eligible: boolean;
  midi_track: MidiTrack | null;
  midi_range: string;
  render_range: string;
  audible_range: string;
  note_count: number;
  polyphony: number | null;
  status: string;
  details: string[];
};

export type Loudness = {
  minimum_dbfs: number | null;
  median_dbfs: number | null;
  average_dbfs: number | null;
  maximum_dbfs: number | null;
  peak_dbfs: number | null;
  active_windows: number;
  total_windows: number;
  error: string | null;
};

export type SongInspection = {
  song_name: string;
  song_dir: string;
  midi_path: string | null;
  midi: {
    duration_ticks: number;
    duration_seconds: number;
    tracks: MidiTrack[];
    warnings: string[];
  } | null;
  final_mix: string;
  final_loudness: Loudness | null;
  animation_path: string | null;
  animation_exists: boolean;
  roles: Role[];
  warnings: string[];
  errors: string[];
};

export type AlignmentEntry = {
  note_index: number;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  midi_pitch: number;
  midi_name: string;
  velocity: number;
  lyric: string | null;
  line: number | null;
  word_index: number | null;
  note_in_word: number | null;
  word_note_count: number | null;
  confidence: "Confident" | "Review" | "Error" | string;
  status: string;
};

export type AlignmentReport = {
  summary: Record<string, string | number>;
  notes: AlignmentEntry[];
  token_counts?: Array<{ line: number; word_index: number; word: string; note_count: number }>;
  virtual_splits?: Array<{ note_index: number; fraction: number }>;
  template?: { source_role: string; mode: string; source_note_count: number; target_note_count: number };
};
