use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::{path::BaseDirectory, AppHandle, Manager};
#[cfg(target_os = "windows")]
use windows_sys::Win32::Media::Multimedia::{mciGetErrorStringW, mciSendStringW};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .expect("Choir Studio must live at <repo>/choir_studio/src-tauri")
        .to_path_buf()
}

fn copy_directory(source: &Path, destination: &Path) -> Result<(), String> {
    std::fs::create_dir_all(destination).map_err(|error| error.to_string())?;
    for entry in std::fs::read_dir(source).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_directory(&source_path, &destination_path)?;
        } else {
            std::fs::copy(&source_path, &destination_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn bundled_runtime_template(app: &AppHandle) -> Result<PathBuf, String> {
    let direct = app
        .path()
        .resolve("runtime_template", BaseDirectory::Resource)
        .map_err(|error| format!("Could not resolve bundled Choir runtime: {error}"))?;
    if direct.is_dir() {
        return Ok(direct);
    }
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("Could not resolve the app resource directory: {error}"))?;
    for candidate in [
        resource_dir.join("runtime_template"),
        resource_dir.join("resources").join("runtime_template"),
    ] {
        if candidate.is_dir() {
            return Ok(candidate);
        }
    }
    Err("The bundled Choir runtime is missing. Reinstall DECTALK Choir Studio.".to_string())
}

fn runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return repo_root()
            .canonicalize()
            .map_err(|error| error.to_string());
    }

    let workspace = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Could not resolve the Choir workspace: {error}"))?
        .join("workspace");
    if !workspace.is_dir() {
        let template = bundled_runtime_template(app)?;
        copy_directory(&template, &workspace)
            .map_err(|error| format!("Could not initialize the local Choir workspace: {error}"))?;
    }
    if !workspace.join("runtime-manifest.json").is_file() {
        return Err(
            "The local Choir workspace is incomplete. Reinstall DECTALK Choir Studio.".to_string(),
        );
    }
    Ok(workspace)
}

fn runtime_python(root: &Path) -> Result<PathBuf, String> {
    let packaged = root.join("python").join("python.exe");
    if packaged.is_file() {
        return Ok(packaged);
    }
    let development = root.join(".venv").join("Scripts").join("python.exe");
    if development.is_file() {
        return Ok(development);
    }
    Err(
        "Choir's Python runtime was not found. Reinstall the app or create .venv for development."
            .to_string(),
    )
}

fn configure_python(command: &mut Command, root: &Path, python: &Path) {
    command.current_dir(root);
    if python.starts_with(root.join("python")) {
        let python_home = root.join("python");
        command
            .env("PYTHONHOME", &python_home)
            .env("PYTHONPATH", root)
            .env("DECTALK_CHOIR_RUNTIME_ROOT", root);
        let mut paths = vec![root.to_path_buf(), python_home];
        if let Some(existing) = std::env::var_os("PATH") {
            paths.extend(std::env::split_paths(&existing));
        }
        if let Ok(path) = std::env::join_paths(paths) {
            command.env("PATH", path);
        }
    }
}

#[derive(Default)]
struct MediaPlayer {
    alias: String,
    is_open: bool,
    paused: bool,
}

fn require_ffmpeg() -> Result<(), String> {
    let executable = if cfg!(target_os = "windows") {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    match Command::new(executable).arg("-version").stdout(Stdio::null()).stderr(Stdio::null()).status() {
        Ok(status) if status.success() => Ok(()),
        _ => Err(
            "FFmpeg was not found on PATH. Install FFmpeg, add its bin folder to PATH, then restart Choir Studio."
                .to_string(),
        ),
    }
}

#[derive(Serialize)]
struct MediaStatus {
    position_ms: u32,
    duration_ms: u32,
    paused: bool,
    mode: String,
}

#[derive(Clone, Serialize)]
struct SpectrogramJobStatus {
    state: String,
    song: Option<String>,
    message: String,
    returncode: Option<i32>,
    log: String,
    path: Option<String>,
}

impl Default for SpectrogramJobStatus {
    fn default() -> Self {
        Self {
            state: "idle".to_string(),
            song: None,
            message: "No spectrogram job is running.".to_string(),
            returncode: None,
            log: String::new(),
            path: None,
        }
    }
}

#[derive(Default)]
struct SpectrogramJob {
    status: Arc<Mutex<SpectrogramJobStatus>>,
}

#[derive(Clone, Serialize)]
struct RenderJobStatus {
    state: String,
    song: Option<String>,
    selected_roles: Vec<String>,
    message: String,
    returncode: Option<i32>,
    log: String,
}

impl Default for RenderJobStatus {
    fn default() -> Self {
        Self {
            state: "idle".to_string(),
            song: None,
            selected_roles: Vec::new(),
            message: "No render job is running.".to_string(),
            returncode: None,
            log: String::new(),
        }
    }
}

#[derive(Default)]
struct RenderJob {
    status: Arc<Mutex<RenderJobStatus>>,
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
            return Err(message
                .trim_matches('\0')
                .trim()
                .to_string()
                .if_empty("Windows MCI error"));
        }
        Ok(String::from_utf16_lossy(&result)
            .trim_matches('\0')
            .trim()
            .to_string())
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
        let device = if kind == "midi" {
            "sequencer"
        } else {
            "waveaudio"
        };
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
        if self.paused {
            // MCI's `play ... from` resumes playback. Keep an explicit pause stable
            // while the alignment canvas moves its visual cursor.
            Self::send(&format!("seek {alias} to {position}"), 0)?;
        } else {
            Self::send(&format!("play {alias} from {position}"), 0)?;
        }
        self.status()
    }

    fn position_ms(&self) -> Result<u32, String> {
        if !self.is_open {
            return Ok(0);
        }
        let value = Self::send(&format!("status {} position", self.alias), 32)?;
        value
            .parse()
            .map_err(|_| format!("Could not read MCI position: {value}"))
    }

    fn duration_ms(&self) -> Result<u32, String> {
        if !self.is_open {
            return Ok(0);
        }
        let value = Self::send(&format!("status {} length", self.alias), 32)?;
        value
            .parse()
            .map_err(|_| format!("Could not read MCI duration: {value}"))
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
        if self.is_empty() {
            fallback.to_string()
        } else {
            self
        }
    }
}

fn resolve_media_path(root: &Path, raw_path: &str) -> Result<PathBuf, String> {
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let path = PathBuf::from(raw_path)
        .canonicalize()
        .map_err(|_| "Media file was not found.".to_string())?;
    if !path.starts_with(&root) {
        return Err("Media path must be inside the Choir repository.".to_string());
    }
    Ok(path)
}

fn safe_song_name(song: &str) -> bool {
    !song.is_empty()
        && song.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '_' || character == '-'
        })
}

#[tauri::command]
fn choir_bridge(app: AppHandle, request: Value) -> Result<Value, String> {
    let root = runtime_root(&app)?;
    let python = runtime_python(&root)?;

    let bridge = root.join("tools").join("choir_studio_bridge.py");
    let mut command = Command::new(&python);
    configure_python(&mut command, &root, &python);
    let mut child = command
        .arg(&bridge)
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
            .and_then(|value| {
                value
                    .get("error")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            });
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

fn trim_job_log(log: String) -> String {
    const MAX_LOG_BYTES: usize = 512 * 1024;
    const RETAINED_TAIL_BYTES: usize = 480 * 1024;
    if log.len() <= MAX_LOG_BYTES {
        return log;
    }
    let mut tail_start = log.len().saturating_sub(RETAINED_TAIL_BYTES);
    while !log.is_char_boundary(tail_start) {
        tail_start += 1;
    }
    let tail = &log[tail_start..];
    let preserved_timings = log
        .lines()
        .filter(|line| {
            line.starts_with("TIMING ") && !tail.lines().any(|tail_line| tail_line == *line)
        })
        .collect::<Vec<_>>()
        .join("\n");
    if preserved_timings.is_empty() {
        format!("... earlier generator output omitted ...\n{tail}")
    } else {
        format!("... earlier generator output omitted; timing summaries preserved below ...\n{preserved_timings}\n... recent generator output ...\n{tail}")
    }
}

fn stream_process_output<R>(reader: R, is_stderr: bool, sender: mpsc::Sender<(bool, String)>)
where
    R: std::io::Read + Send + 'static,
{
    std::thread::spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            let _ = sender.send((is_stderr, line));
        }
    });
}

fn render_progress_message(line: &str) -> String {
    let line = line.trim();
    if line.contains("Converting tracks to phonemes") {
        "Converting selected lyric inputs to phonemes.".to_string()
    } else if line.contains("Partial txt") {
        "Writing DECTALK note commands.".to_string()
    } else if line.contains("Partial wav") {
        "Rendering voice partials.".to_string()
    } else if line.contains("Combining") || line.contains("Combining tracks") {
        "Combining rendered tracks.".to_string()
    } else if line.is_empty() {
        "Rendering selected tracks in the background.".to_string()
    } else {
        format!("Rendering: {line}")
    }
}

fn append_render_log(status: &Arc<Mutex<RenderJobStatus>>, line: &str, is_stderr: bool) {
    if let Ok(mut current) = status.lock() {
        if !current.log.is_empty() {
            current.log.push('\n');
        }
        if is_stderr {
            current.log.push_str("stderr: ");
        }
        current.log.push_str(line);
        current.log = trim_job_log(std::mem::take(&mut current.log));
        current.message = render_progress_message(line);
    }
}

fn spectrogram_progress_message(line: &str) -> String {
    let line = line.trim();
    if line.starts_with("Rendered ") {
        line.to_string()
    } else if line.starts_with("Compositing ") {
        "Compositing track clips and final audio.".to_string()
    } else if line.starts_with("TIMING stage=composition ") {
        "Final video composition completed.".to_string()
    } else if line.is_empty() || line.starts_with("TIMING ") {
        "Generating the spectrogram video in the background.".to_string()
    } else {
        format!("Spectrogram: {line}")
    }
}

fn append_spectrogram_log(status: &Arc<Mutex<SpectrogramJobStatus>>, line: &str, is_stderr: bool) {
    if let Ok(mut current) = status.lock() {
        if !current.log.is_empty() {
            current.log.push('\n');
        }
        if is_stderr {
            current.log.push_str("stderr: ");
        }
        current.log.push_str(line);
        current.log = trim_job_log(std::mem::take(&mut current.log));
        if !is_stderr {
            current.message = spectrogram_progress_message(line);
        }
    }
}

#[tauri::command]
fn start_render_job(
    app: AppHandle,
    song: String,
    roles: Vec<String>,
    job: tauri::State<'_, RenderJob>,
) -> Result<RenderJobStatus, String> {
    if !safe_song_name(&song) {
        return Err("Invalid song name.".to_string());
    }
    if roles.is_empty() || roles.iter().any(|role| role.trim().is_empty()) {
        return Err("Select at least one named track to render.".to_string());
    }
    require_ffmpeg()?;

    let root = runtime_root(&app)?;
    if !root.join("songs").join(&song).is_dir() {
        return Err(format!("Song '{song}' was not found."));
    }
    let python = runtime_python(&root)?;

    let status = Arc::clone(&job.status);
    {
        let mut current = status
            .lock()
            .map_err(|_| "Render job state is unavailable.".to_string())?;
        if current.state == "running" {
            return Err("A Choir render is already running.".to_string());
        }
        *current = RenderJobStatus {
            state: "running".to_string(),
            song: Some(song.clone()),
            selected_roles: roles.clone(),
            message: "Starting selected-track render in the background.".to_string(),
            returncode: None,
            log: String::new(),
        };
    }

    let response = status
        .lock()
        .map_err(|_| "Render job state is unavailable.".to_string())?
        .clone();
    std::thread::spawn(move || {
        let mut command = Command::new(&python);
        configure_python(&mut command, &root, &python);
        let mut child = match command
            .arg("-u")
            .arg(root.join("choir.py"))
            .arg(&song)
            .env("DECTALK_CHOIR_RENDER_ROLES", roles.join(","))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(child) => child,
            Err(error) => {
                if let Ok(mut current) = status.lock() {
                    *current = RenderJobStatus {
                        state: "failed".to_string(),
                        song: Some(song),
                        selected_roles: roles,
                        message: format!("Could not start Choir render: {error}"),
                        returncode: None,
                        log: String::new(),
                    };
                }
                return;
            }
        };

        let (sender, receiver) = mpsc::channel();
        if let Some(stdout) = child.stdout.take() {
            stream_process_output(stdout, false, sender.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            stream_process_output(stderr, true, sender.clone());
        }
        drop(sender);

        let exit_status = loop {
            while let Ok((is_stderr, line)) = receiver.try_recv() {
                append_render_log(&status, &line, is_stderr);
            }
            match child.try_wait() {
                Ok(Some(exit_status)) => {
                    while let Ok((is_stderr, line)) = receiver.try_recv() {
                        append_render_log(&status, &line, is_stderr);
                    }
                    break Ok(exit_status);
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(125)),
                Err(error) => break Err(error),
            }
        };

        if let Ok(mut current) = status.lock() {
            match exit_status {
                Ok(exit_status) if exit_status.success() => {
                    current.state = "completed".to_string();
                    current.message = "Selected-track render completed.".to_string();
                    current.returncode = exit_status.code();
                }
                Ok(exit_status) => {
                    current.state = "failed".to_string();
                    current.message = format!(
                        "choir.py exited {}. Review the compiler output.",
                        exit_status
                            .code()
                            .map_or("without a status".to_string(), |code| code.to_string())
                    );
                    current.returncode = exit_status.code();
                }
                Err(error) => {
                    current.state = "failed".to_string();
                    current.message = format!("Could not monitor Choir render: {error}");
                    current.returncode = None;
                }
            }
            current.log = trim_job_log(std::mem::take(&mut current.log));
        }
    });
    Ok(response)
}

#[tauri::command]
fn render_job_status(job: tauri::State<'_, RenderJob>) -> Result<RenderJobStatus, String> {
    job.status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "Render job state is unavailable.".to_string())
}

#[tauri::command]
fn start_spectrogram_job(
    app: AppHandle,
    song: String,
    roles: Vec<String>,
    job: tauri::State<'_, SpectrogramJob>,
) -> Result<SpectrogramJobStatus, String> {
    if !safe_song_name(&song) {
        return Err("Invalid song name.".to_string());
    }
    if roles.is_empty() {
        return Err("Enable at least one track before generating a spectrogram video.".to_string());
    }
    require_ffmpeg()?;

    let root = runtime_root(&app)?;
    if !root.join("songs").join(&song).is_dir() {
        return Err(format!("Song '{song}' was not found."));
    }
    let python = runtime_python(&root)?;

    let status = Arc::clone(&job.status);
    {
        let mut current = status
            .lock()
            .map_err(|_| "Spectrogram job state is unavailable.".to_string())?;
        if current.state == "running" {
            return Err("A spectrogram video is already being generated.".to_string());
        }
        *current = SpectrogramJobStatus {
            state: "running".to_string(),
            song: Some(song.clone()),
            message: format!("Generating the composite spectrogram video for {} enabled track(s) in the background.", roles.len()),
            returncode: None,
            log: String::new(),
            path: None,
        };
    }

    let response = status
        .lock()
        .map_err(|_| "Spectrogram job state is unavailable.".to_string())?
        .clone();
    std::thread::spawn(move || {
        let mut command = Command::new(&python);
        configure_python(&mut command, &root, &python);
        let mut child = match command
            .arg("-u")
            .arg(root.join("generateSpectrograms.py"))
            .arg(&song)
            .env("DECTALK_CHOIR_SPECTROGRAM_ROLES", roles.join(","))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(child) => child,
            Err(error) => {
                if let Ok(mut current) = status.lock() {
                    *current = SpectrogramJobStatus {
                        state: "failed".to_string(),
                        song: Some(song),
                        message: format!("Could not start the spectrogram generator: {error}"),
                        returncode: None,
                        log: String::new(),
                        path: None,
                    };
                }
                return;
            }
        };

        let (sender, receiver) = mpsc::channel();
        if let Some(stdout) = child.stdout.take() {
            stream_process_output(stdout, false, sender.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            stream_process_output(stderr, true, sender.clone());
        }
        drop(sender);

        let exit_status = loop {
            while let Ok((is_stderr, line)) = receiver.try_recv() {
                append_spectrogram_log(&status, &line, is_stderr);
            }
            match child.try_wait() {
                Ok(Some(exit_status)) => {
                    while let Ok((is_stderr, line)) =
                        receiver.recv_timeout(Duration::from_millis(50))
                    {
                        append_spectrogram_log(&status, &line, is_stderr);
                    }
                    break Ok(exit_status);
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(125)),
                Err(error) => break Err(error),
            }
        };
        let video = root
            .join("songs")
            .join(&song)
            .join("outputs")
            .join("_finished")
            .join(format!("{song}.mp4"));
        if let Ok(mut current) = status.lock() {
            match exit_status {
                Ok(exit_status) if exit_status.success() && video.is_file() => {
                    current.state = "completed".to_string();
                    current.message = "Spectrogram video generated.".to_string();
                    current.returncode = exit_status.code();
                    current.path = Some(video.to_string_lossy().to_string());
                }
                Ok(exit_status) => {
                    current.state = "failed".to_string();
                    current.message = if exit_status.success() {
                        "Spectrogram generator completed without producing the expected video."
                            .to_string()
                    } else {
                        format!(
                            "Spectrogram generation exited {}.",
                            exit_status
                                .code()
                                .map_or("without a status".to_string(), |code| code.to_string())
                        )
                    };
                    current.returncode = exit_status.code();
                    current.path = None;
                }
                Err(error) => {
                    current.state = "failed".to_string();
                    current.message = format!("Could not monitor spectrogram generation: {error}");
                    current.returncode = None;
                    current.path = None;
                }
            }
            current.log = trim_job_log(std::mem::take(&mut current.log));
        }
    });
    Ok(response)
}

#[tauri::command]
fn spectrogram_job_status(
    job: tauri::State<'_, SpectrogramJob>,
) -> Result<SpectrogramJobStatus, String> {
    job.status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "Spectrogram job state is unavailable.".to_string())
}

#[tauri::command]
fn media_play(
    app: AppHandle,
    player: tauri::State<'_, Mutex<MediaPlayer>>,
    path: String,
    kind: String,
    from_ms: Option<u32>,
) -> Result<MediaStatus, String> {
    let root = runtime_root(&app)?;
    let path = resolve_media_path(&root, &path)?;
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .play(&path, &kind, from_ms.unwrap_or(0))
}

#[tauri::command]
fn media_toggle_pause(player: tauri::State<'_, Mutex<MediaPlayer>>) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .toggle_pause()
}

#[tauri::command]
fn media_stop(player: tauri::State<'_, Mutex<MediaPlayer>>) -> Result<MediaStatus, String> {
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
fn media_status(player: tauri::State<'_, Mutex<MediaPlayer>>) -> Result<MediaStatus, String> {
    player
        .lock()
        .map_err(|_| "Media player is unavailable.".to_string())?
        .status()
}

#[tauri::command]
fn open_song_folder(app: AppHandle, song: String, target: String) -> Result<(), String> {
    if !safe_song_name(&song) {
        return Err("Song name is invalid.".to_string());
    }
    let root = runtime_root(&app)?;
    let path = match target.as_str() {
        "source" => root.join("songs").join(&song),
        "output" => root.join("songs").join(&song).join("outputs"),
        _ => return Err("Folder target is invalid.".to_string()),
    };
    if target == "output" {
        std::fs::create_dir_all(&path)
            .map_err(|error| format!("Could not create output folder: {error}"))?;
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

#[tauri::command]
fn delete_song(app: AppHandle, song: String, confirmation: String) -> Result<(), String> {
    if !safe_song_name(&song) || confirmation != song {
        return Err("Confirm deletion with the exact song name.".to_string());
    }
    let root = runtime_root(&app)?;
    let songs_dir = root.join("songs");
    let target = songs_dir.join(&song);
    if !target.is_dir() {
        return Err("The selected song folder no longer exists.".to_string());
    }
    std::fs::remove_dir_all(&target)
        .map_err(|error| format!("Could not delete {song} and its outputs: {error}"))?;
    Ok(())
}

#[tauri::command]
fn open_media(app: AppHandle, path: String) -> Result<(), String> {
    let root = runtime_root(&app)?;
    let path = resolve_media_path(&root, &path)?;
    Command::new("cmd")
        .args(["/C", "start", "", path.to_string_lossy().as_ref()])
        .spawn()
        .map_err(|error| format!("Could not open media in the default player: {error}"))?;
    Ok(())
}

#[tauri::command]
fn open_ffmpeg_download() -> Result<(), String> {
    Command::new("cmd")
        .args(["/C", "start", "", "https://ffmpeg.org/download.html"])
        .spawn()
        .map_err(|error| format!("Could not open FFmpeg downloads: {error}"))?;
    Ok(())
}

fn reveal_main_window(app: &AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "The main Studio window is unavailable.".to_string())?;
    main.show()
        .map_err(|error| format!("Could not show the main Studio window: {error}"))?;
    main.set_focus()
        .map_err(|error| format!("Could not focus the main Studio window: {error}"))?;
    if let Some(splashscreen) = app.get_webview_window("splashscreen") {
        splashscreen
            .close()
            .map_err(|error| format!("Could not close the Studio splash screen: {error}"))?;
    }
    Ok(())
}

#[tauri::command]
fn finish_startup(app: AppHandle) -> Result<(), String> {
    reveal_main_window(&app)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Mutex::new(MediaPlayer::default()))
        .manage(SpectrogramJob::default())
        .manage(RenderJob::default())
        .on_page_load(|webview, payload| {
            if webview.label() == "splashscreen"
                && matches!(payload.event(), tauri::webview::PageLoadEvent::Finished)
            {
                let _ = webview.window().show();
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(8));
                if handle.get_webview_window("splashscreen").is_some() {
                    let _ = reveal_main_window(&handle);
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            choir_bridge,
            start_render_job,
            render_job_status,
            start_spectrogram_job,
            spectrogram_job_status,
            media_play,
            media_toggle_pause,
            media_stop,
            media_seek,
            media_status,
            open_song_folder,
            delete_song,
            open_media,
            open_ffmpeg_download,
            finish_startup,
        ])
        .run(tauri::generate_context!())
        .expect("error while running DECTALK Choir Studio");
}
