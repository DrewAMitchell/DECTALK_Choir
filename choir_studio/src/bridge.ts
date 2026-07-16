import { invoke } from "@tauri-apps/api/core";

type Response<T> = { ok: true; data: T } | { ok: false; error: string };

export async function bridge<T>(request: Record<string, unknown>): Promise<T> {
  const response = await invoke<Response<T>>("choir_bridge", { request });
  if (!response.ok) throw new Error(response.error);
  return response.data;
}

export type MediaStatus = {
  position_ms: number;
  duration_ms: number;
  paused: boolean;
  mode: string;
};

export type SpectrogramJobStatus = {
  state: "idle" | "running" | "completed" | "failed";
  song: string | null;
  message: string;
  returncode: number | null;
  log: string;
  path: string | null;
};

export type RenderJobStatus = {
  state: "idle" | "running" | "completed" | "failed";
  song: string | null;
  selected_roles: string[];
  message: string;
  returncode: number | null;
  log: string;
};

export async function media<T extends MediaStatus>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  return invoke<T>(command, args);
}

export async function openSongFolder(song: string, target: "source" | "output"): Promise<void> {
  await invoke("open_song_folder", { song, target });
}

export async function deleteSong(song: string): Promise<void> {
  await invoke("delete_song", { song, confirmation: song });
}

export async function openMedia(path: string): Promise<void> {
  await invoke("open_media", { path });
}

export async function openFfmpegDownload(): Promise<void> {
  await invoke("open_ffmpeg_download");
}

export async function startSpectrogramJob(song: string, roles: string[]): Promise<SpectrogramJobStatus> {
  return invoke<SpectrogramJobStatus>("start_spectrogram_job", { song, roles });
}

export async function spectrogramJobStatus(): Promise<SpectrogramJobStatus> {
  return invoke<SpectrogramJobStatus>("spectrogram_job_status");
}

export async function startRenderJob(song: string, roles: string[]): Promise<RenderJobStatus> {
  return invoke<RenderJobStatus>("start_render_job", { song, roles });
}

export async function renderJobStatus(): Promise<RenderJobStatus> {
  return invoke<RenderJobStatus>("render_job_status");
}
