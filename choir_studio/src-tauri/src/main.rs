use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use serde_json::Value;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Media::Multimedia::{mciGetErrorStringW, mciSendStringW};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .expect("Choir Studio must live at <repo>/choir_studio/src-tauri")
        .to_path_buf()
}

#[derive(Default)]
struct MediaPlayer {
    alias: String,
    is_open: bool,
    paused: bool,
}

#[derive(Serialize)]
struct MediaStatus {
    position_ms: u32,
    duration_ms: u32,
    paused: bool,
    mode: String,
}

impl MediaPlayer {
    fn alias(&mut self) -> &str {
        if self.alias.is_empty() {
            self.alias = "dectalk_choir_studio".to_string();
        }
        &self.alias
    }

    #[cfg(target_os = "windows")]
    fn send(command: &str, result_length: usize) -> Result<String, String> {
        let command = format!("{command}\0").encode_utf16().collect::<Vec<_>>();
        let mut result = vec![0_u16; result_length.max(1)];
        let result_pointer = if result_length == 0 {
            std::ptr::null_mut()
        } else {
            result.as_mut_ptr()
        };
        let code = unsafe {
            mciSendStringW(
                command.as_ptr(),
                result_pointer,
                result_length as u32,
                std::ptr::null_mut(),
            )
        };
        if code != 0 {
            let mut error = vec![0_u16; 256];
            unsafe { mciGetErrorStringW(code, error.as_mut_ptr(), error.len() as u32) };
            let message = String::from_utf16_lossy(&error);
            return Err(message.trim_matches('\0').trim().to_string().if_empty("Windows MCI error"));
        }
        Ok(String::from_utf16_lossy(&result).trim_matches('\0').trim().to_string())
    }

    #[cfg(not(target_os = "windows"))]
    fn send(_command: &str, _result_length: usize) -> Result<String, String> {
        Err("Local MIDI preview is available only on Windows.".to_string())
    }

    fn close(&mut self) {
        if self.is_open {
            let alias = self.alias().to_string();
            let _ = Self::send(&format!("close {alias}"), 0);
        }
        self.is_open = false;
        self.paused = false;
    }

    fn play(&mut self, path: &PathBuf, kind: &str, from_ms: u32) -> Result<MediaStatus, String> {
        self.close();
        let alias = self.alias().to_string();
        let device = if kind == "midi" { "sequencer" } else { "waveaudio" };
        let path = path.to_string_lossy().replace('"', "");
        Self::send(&format!("open \"{path}\" type {device} alias {alias}"), 0)?;
        self.is_open = true;
        self.paused = false;
        Self::send(&format!("set {alias} time format milliseconds"), 0)?;
        Self::send(&format!("play {alias} from {from_ms}"), 0)?;
        self.status()
    }

    fn toggle_pause(&mut self) -> Result<MediaStatus, String> {
        if !self.is_open {
            return Err("Nothing is playing.".to_string());
        }
        let alias = self.alias().to_string();
        if self.paused {
            let position = self.position_ms()?;
            Self::send(&format!("play {alias} from {position}"), 0)?;
        } else {
            Self::send(&format!("pause {alias}"), 0)?;
        }
        self.paused = !self.paused;
        self.status()
    }

    fn stop(&mut self) -> Result<MediaStatus, String> {
        if self.is_open {
            let alias = self.alias().to_string();
            Self::send(&format!("stop {alias}"), 0)?;
        }
        self.paused = false;
        self.status()
    }

    fn seek(&mut self, position_ms: u32) -> Result<MediaStatus, String> {
        if !self.is_open {
            return Err("Start preview playback before seeking.".to_string());
        }
        let alias = self.alias().to_string();
        let position = position_ms.min(self.duration_ms()?);
        Self::send(&format!("play {alias} from {position}"), 0)?;
        self.paused = false;
        self.status()
    }

    fn position_ms(&self) -> Result<u32, String> {
        if !self.is_open { return Ok(0); }
        let value = Self::send(&format!("status {} position", self.alias), 32)?;
        value.parse().map_err(|_| format!("Could not read MCI position: {value}"))
    }

    fn duration_ms(&self) -> Result<u32, String> {
        if !self.is_open { return Ok(0); }
        let value = Self::send(&format!("status {} length", self.alias), 32)?;
        value.parse().map_err(|_| format!("Could not read MCI duration: {value}"))
    }

    fn status(&self) -> Result<MediaStatus, String> {
        let mode = if self.is_open {
            Self::send(&format!("status {} mode", self.alias), 64)?.to_lowercase()
        } else {
            "stopped".to_string()
        };
        Ok(MediaStatus {
            position_ms: self.position_ms()?,
            duration_ms: self.duration_ms()?,
            paused: self.paused,
            mode,
        })
    }
}

trait NonEmptyFallback {
    fn if_empty(self, fallback: &str) -> String;
}

impl NonEmptyFallback for String {
    fn if_empty(self, fallback: &str) -> String {
        if self.is_empty() { fallback.to_string() } else { self }
    }
}

fn resolve_media_path(raw_path: &str) -> Result<PathBuf, String> {
    let root = repo_root().canonicalize().map_err(|error| error.to_string())?;
    let path = PathBuf::from(raw_path).canonicalize().map_err(|_| "Media file was not found.".to_string())?;
    if !path.starts_with(&root) {
        return Err("Media path must be inside the Choir repository.".to_string());
    }
    Ok(path)
}

fn safe_song_name(song: &str) -> bool {
    !song.is_empty()
        && song
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_' || character == '-')
}

#[tauri::command]
fn choir_bridge(request: Value) -> Result<Value, String> {
    let root = repo_root();
    let python = root.join(".venv").join("Scripts").join("python.exe");
    if !python.is_file() {
        return Err(format!("Local Choir Python environment was not found: {}", python.display()));
    }

    let bridge = root.join("tools").join("choir_studio_bridge.py");
    let mut child = Command::new(&python)
        .arg(&bridge)
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Could not start Choir bridge: {error}"))?;

    let payload = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
    child
        .stdin
        .take()
        .ok_or("Could not open Choir bridge input")?
        .write_all(&payload)
        .map_err(|error| format!("Could not send Choir bridge request: {error}"))?;

    let output = child
        .wait_with_output()
        .map_err(|error| format!("Could not read Choir bridge response: {error}"))?;
    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let bridge_error = serde_json::from_str::<Value>(&stdout)
            .ok()
            .and_then(|value| value.get("error").and_then(Value::as_str).map(str::to_owned));
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if detail.is_empty() {
            bridge_error.unwrap_or_else(|| {
                if stdout.is_empty() {
                    "Choir bridge failed without an error message.".to_string()
                } else {
                    format!("Choir bridge failed: {stdout}")
                }
            })
        } else {
            detail
        });
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("Choir bridge returned invalid JSON: {error}"))
}

#[tauri::command]
fn media_play(
    player: tauri::State<'_, Mutex<MediaPlayer>>,
    path: String,
    kind: String,
    from_ms: Option<u32>,
) -> Result<MediaStatus, String> {
    let path = resolve_media_path(&path)?;
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .play(&path, &kind, from_ms.unwrap_or(0))
}

#[tauri::command]
fn media_toggle_pause(
    player: tauri::State<'_, Mutex<MediaPlayer>>,
) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .toggle_pause()
}

#[tauri::command]
fn media_stop(
    player: tauri::State<'_, Mutex<MediaPlayer>>,
) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .stop()
}

#[tauri::command]
fn media_seek(
    player: tauri::State<'_, Mutex<MediaPlayer>>,
    position_ms: u32,
) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .seek(position_ms)
}

#[tauri::command]
fn media_status(
    player: tauri::State<'_, Mutex<MediaPlayer>>,
) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .status()
}

#[tauri::command]
fn open_song_folder(song: String, target: String) -> Result<(), String> {
    if !safe_song_name(&song) {
        return Err("Song name is invalid.".to_string());
    }
    let root = repo_root();
    let path = match target.as_str() {
        "source" => root.join("songs").join(&song),
        "output" => root.join("outputs").join(&song),
        _ => return Err("Folder target is invalid.".to_string()),
    };
    if target == "output" {
        std::fs::create_dir_all(&path).map_err(|error| format!("Could not create output folder: {error}"))?;
    }
    if !path.is_dir() {
        return Err("Requested song folder was not found.".to_string());
    }
    Command::new("explorer.exe")
        .arg(&path)
        .spawn()
        .map_err(|error| format!("Could not open folder: {error}"))?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Mutex::new(MediaPlayer::default()))
        .invoke_handler(tauri::generate_handler![
            choir_bridge,
            media_play,
            media_toggle_pause,
            media_stop,
            media_seek,
            media_status,
            open_song_folder,
        ])
        .run(tauri::generate_context!())
        .expect("error while running DECTALK Choir Studio");
}
