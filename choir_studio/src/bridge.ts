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

export async function media<T extends MediaStatus>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  return invoke<T>(command, args);
}

export async function openSongFolder(song: string, target: "source" | "output"): Promise<void> {
  await invoke("open_song_folder", { song, target });
}
