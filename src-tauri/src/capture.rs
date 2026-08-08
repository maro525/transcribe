//! Native system-audio capture: WASAPI loopback -> 16 kHz mono s16le -> WS.
//!
//! Contract (frozen):
//! - On `TAURI_EVENT {"capture":"start", ...}` (parsed in process.rs) capture
//!   the default output device via WASAPI loopback, downmix to mono, resample
//!   to 16 kHz, convert to s16le, and send BINARY frames of ~2048 samples
//!   (4096 bytes) over a dedicated WebSocket client connection to
//!   `ws://127.0.0.1:<port>/live/ws`. This is a second, PCM-feeder-only client
//!   next to the browser's own control connection.
//! - Never send text/JSON on the feeder connection. (tungstenite still answers
//!   protocol-level pings with pongs; those are control frames, not messages.)
//! - No Origin header: tokio-tungstenite's client handshake does not send an
//!   Origin header unless one is explicitly added, which we do not.
//! - On `capture:"stop"`, stop the stream and close the WS.
//! - Feeder auth (frozen contract addition): the handshake request carries
//!   `X-Feeder-Token: <TRANSCRIBE_SHUTDOWN_SECRET value>`. Python rejects
//!   Origin-less connections without a valid token when the secret is set.
//! - The feeder DOES poll incoming frames (tokio::select!): server broadcasts
//!   (Text/Binary) are read and discarded, protocol Pings are answered by
//!   tungstenite as a side effect of polling, and a server Close triggers the
//!   reconnect path. Without polling, uvicorn's 20 s ping timeout would
//!   disconnect the feeder in a loop.
//!
//! cpal / WASAPI loopback: on Windows, cpal (>= 0.15) opens a *loopback*
//! capture when `build_input_stream` is called on an *output* (render) device
//! — i.e. `host.default_output_device()` + `build_input_stream(...)` yields
//! the "what you hear" stream. No device-specific config type is required in
//! the cpal API for this; the wasapi backend detects the render data-flow and
//! initializes the client with AUDCLNT_STREAMFLAGS_LOOPBACK internally.
//! 【未検証】This behavior is from offline knowledge of cpal's wasapi backend
//! and MUST be smoke-tested on a real Windows machine; if the resolved cpal
//! version does not support it, the fallback is the `wasapi` crate with an
//! explicit loopback AudioClient.
//!
//! Resampling: simple linear interpolation (device rate, typically 48 kHz
//! f32, -> 16 kHz). For speech-to-text this is acceptable; a windowed-sinc /
//! polyphase resampler (e.g. `rubato`) is a drop-in upgrade if ASR quality
//! measurably suffers. Known limitation, accepted for v1.
//!
//! Threading model:
//! - one std thread owns the cpal stream (cpal streams are !Send);
//! - the audio callback pushes ready-made 4096-byte frames into a bounded
//!   tokio *broadcast* channel (ring semantics: never blocks the audio
//!   callback; on overflow the OLDEST buffered frame is overwritten, so after
//!   a reconnect the feeder sends fresh audio, not stale audio — the WS task
//!   observes the loss as `RecvError::Lagged`);
//! - one tokio task owns the WS connection, with reconnect/backoff.
//!
//! 【未検証】Never compiled or executed (WSL2 dev host).

use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio::sync::{broadcast, mpsc};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;

/// Samples per WS frame (s16 mono @16 kHz) => 4096 bytes per binary frame.
const FRAME_SAMPLES: usize = 2048;
const TARGET_RATE: f64 = 16_000.0;
/// Bounded frame queue between audio thread and WS task (~8 s of audio).
const FRAME_QUEUE: usize = 64;
const BACKOFF_MIN: Duration = Duration::from_millis(500);
const BACKOFF_MAX: Duration = Duration::from_secs(5);

/// Commands dispatched from the sidecar's TAURI_EVENT stdout lines
/// (process.rs) to the capture controller.
#[derive(Debug)]
pub enum CaptureCommand {
    Start { session_id: String },
    Stop,
}

struct ActiveCapture {
    stop: Arc<AtomicBool>,
    audio_thread: Option<std::thread::JoinHandle<()>>,
    ws_task: tauri::async_runtime::JoinHandle<()>,
}

/// Spawns the long-lived capture controller task. `port` is the backend port
/// (0 until TAURI_READY); `feeder_token` mirrors the current
/// TRANSCRIBE_SHUTDOWN_SECRET (set by main.rs on each backend spawn) and is
/// sent as `X-Feeder-Token` on the /live/ws handshake. Returns the command
/// sender wired to process.rs.
pub fn spawn_controller(
    port: Arc<AtomicU16>,
    feeder_token: Arc<Mutex<String>>,
) -> mpsc::UnboundedSender<CaptureCommand> {
    let (tx, mut rx) = mpsc::unbounded_channel::<CaptureCommand>();
    tauri::async_runtime::spawn(async move {
        let mut active: Option<ActiveCapture> = None;
        while let Some(cmd) = rx.recv().await {
            match cmd {
                CaptureCommand::Start { session_id } => {
                    // Contract sends paired start/stop; a start while running
                    // means the previous session ended abnormally — restart.
                    stop_active(&mut active).await;
                    let p = port.load(Ordering::SeqCst);
                    if p == 0 {
                        eprintln!("[capture] start requested but backend port unknown; ignoring");
                        continue;
                    }
                    eprintln!("[capture] starting system-audio capture (session {session_id})");
                    active = Some(start_capture(p, feeder_token.clone()));
                }
                CaptureCommand::Stop => {
                    eprintln!("[capture] stopping system-audio capture");
                    stop_active(&mut active).await;
                }
            }
        }
        // Controller channel closed (app shutdown): tear down.
        stop_active(&mut active).await;
    });
    tx
}

async fn stop_active(active: &mut Option<ActiveCapture>) {
    let Some(mut cap) = active.take() else { return };
    cap.stop.store(true, Ordering::SeqCst);
    // Join the audio thread off the async runtime; it exits within ~50 ms and
    // drops the frame sender, which lets the WS task close cleanly.
    if let Some(handle) = cap.audio_thread.take() {
        let _ = tokio::task::spawn_blocking(move || {
            let _ = handle.join();
        })
        .await;
    }
    let _ = tokio::time::timeout(Duration::from_secs(5), cap.ws_task).await;
}

fn start_capture(port: u16, feeder_token: Arc<Mutex<String>>) -> ActiveCapture {
    let stop = Arc::new(AtomicBool::new(false));
    // Broadcast channel as a drop-oldest ring buffer: the audio callback's
    // `send` never blocks; when the WS side lags by more than FRAME_QUEUE
    // frames the oldest frames are overwritten (fresh audio wins).
    let (frames_tx, frames_rx) = broadcast::channel::<Vec<u8>>(FRAME_QUEUE);

    let audio_stop = stop.clone();
    let audio_thread = std::thread::Builder::new()
        .name("wasapi-loopback".into())
        .spawn(move || run_audio_thread(frames_tx, audio_stop))
        .expect("spawn wasapi-loopback thread");

    let ws_stop = stop.clone();
    let ws_task = tauri::async_runtime::spawn(ws_feeder(port, frames_rx, ws_stop, feeder_token));

    ActiveCapture {
        stop,
        audio_thread: Some(audio_thread),
        ws_task,
    }
}

// ---------------------------------------------------------------------------
// Audio thread: cpal loopback stream -> mono -> 16 kHz -> s16le frames
// ---------------------------------------------------------------------------

fn run_audio_thread(frames_tx: broadcast::Sender<Vec<u8>>, stop: Arc<AtomicBool>) {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

    let host = cpal::default_host();
    let Some(device) = host.default_output_device() else {
        eprintln!("[capture] no default output device; system-audio capture unavailable");
        return;
    };
    let supported = match device.default_output_config() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[capture] default_output_config failed: {e}");
            return;
        }
    };
    let sample_format = supported.sample_format();
    let config: cpal::StreamConfig = supported.config();
    let channels = config.channels as usize;
    let src_rate = config.sample_rate.0 as f64;
    eprintln!(
        "[capture] loopback device rate={src_rate} channels={channels} format={sample_format:?}"
    );

    let mut pipeline = Pipeline::new(channels, src_rate, frames_tx);
    let err_fn = |e| eprintln!("[capture] stream error: {e}");

    // WASAPI loopback: an *input* stream built on an *output* device.
    // (See module docs; 【未検証】on real hardware.)
    let stream = match sample_format {
        cpal::SampleFormat::F32 => device.build_input_stream(
            &config,
            move |data: &[f32], _| pipeline.push_f32(data),
            err_fn,
            None,
        ),
        cpal::SampleFormat::I16 => device.build_input_stream(
            &config,
            move |data: &[i16], _| {
                let scratch: Vec<f32> = data.iter().map(|&s| s as f32 / 32768.0).collect();
                pipeline.push_f32(&scratch);
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::U16 => device.build_input_stream(
            &config,
            move |data: &[u16], _| {
                let scratch: Vec<f32> = data
                    .iter()
                    .map(|&s| (s as f32 - 32768.0) / 32768.0)
                    .collect();
                pipeline.push_f32(&scratch);
            },
            err_fn,
            None,
        ),
        other => {
            eprintln!("[capture] unsupported sample format: {other:?}");
            return;
        }
    };
    let stream = match stream {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[capture] build_input_stream (loopback) failed: {e}");
            return;
        }
    };
    if let Err(e) = stream.play() {
        eprintln!("[capture] stream.play failed: {e}");
        return;
    }

    while !stop.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(50));
    }
    // Dropping the stream stops the callback and drops its frame sender,
    // which signals EOF (RecvError::Closed) to the WS feeder.
    drop(stream);
    eprintln!("[capture] audio thread exit");
}

/// Downmix -> linear resample -> s16 -> 2048-sample framing.
struct Pipeline {
    channels: usize,
    resampler: LinearResampler,
    pending: Vec<i16>,
    frames_tx: broadcast::Sender<Vec<u8>>,
    mono_scratch: Vec<f32>,
}

impl Pipeline {
    fn new(channels: usize, src_rate: f64, frames_tx: broadcast::Sender<Vec<u8>>) -> Self {
        Self {
            channels: channels.max(1),
            resampler: LinearResampler::new(src_rate, TARGET_RATE),
            pending: Vec::with_capacity(FRAME_SAMPLES * 2),
            frames_tx,
            mono_scratch: Vec::new(),
        }
    }

    fn push_f32(&mut self, interleaved: &[f32]) {
        // 1. downmix: average all channels per frame.
        self.mono_scratch.clear();
        let inv = 1.0 / self.channels as f32;
        for frame in interleaved.chunks_exact(self.channels) {
            self.mono_scratch.push(frame.iter().sum::<f32>() * inv);
        }
        // 2+3. resample to 16 kHz and quantize to s16.
        let mono = std::mem::take(&mut self.mono_scratch);
        self.resampler.process(&mono, &mut self.pending);
        self.mono_scratch = mono;
        // 4. cut into fixed 2048-sample frames -> 4096-byte LE buffers.
        while self.pending.len() >= FRAME_SAMPLES {
            let rest = self.pending.split_off(FRAME_SAMPLES);
            let frame = std::mem::replace(&mut self.pending, rest);
            let mut bytes = Vec::with_capacity(FRAME_SAMPLES * 2);
            for s in frame {
                bytes.extend_from_slice(&s.to_le_bytes());
            }
            // Audio callback must never block. broadcast::send is wait-free
            // for the sender: on overflow the OLDEST buffered frame is
            // overwritten (the WS task counts the loss via RecvError::Lagged).
            // Err here only means the WS receiver is gone; nothing to do.
            let _ = self.frames_tx.send(bytes);
        }
    }
}

/// Streaming linear-interpolation resampler. Keeps the last input sample so
/// interpolation is continuous across callback blocks. First output sample
/// interpolates from silence (inaudible transient, accepted).
struct LinearResampler {
    step: f64,
    /// Position in the virtual buffer [prev, input[0], input[1], ...],
    /// where 0.0 == prev.
    pos: f64,
    prev: f32,
}

impl LinearResampler {
    fn new(src_rate: f64, dst_rate: f64) -> Self {
        Self {
            step: src_rate / dst_rate,
            pos: 0.0,
            prev: 0.0,
        }
    }

    fn process(&mut self, input: &[f32], out: &mut Vec<i16>) {
        if input.is_empty() {
            return;
        }
        let n = input.len() as f64;
        // Virtual buffer v[0] = prev, v[i] = input[i-1]; valid interpolation
        // range for pos is [0, n): between v[floor] and v[floor+1].
        while self.pos < n {
            let idx = self.pos.floor();
            let frac = (self.pos - idx) as f32;
            let i = idx as usize;
            let a = if i == 0 { self.prev } else { input[i - 1] };
            let b = input[i];
            let sample = a + (b - a) * frac;
            out.push((sample.clamp(-1.0, 1.0) * 32767.0) as i16);
            self.pos += self.step;
        }
        self.pos -= n;
        self.prev = input[input.len() - 1];
    }
}

// ---------------------------------------------------------------------------
// WS feeder task: dedicated PCM-only client on /live/ws
// ---------------------------------------------------------------------------

/// Builds the /live/ws client request with the `X-Feeder-Token` header
/// (frozen contract: value == TRANSCRIBE_SHUTDOWN_SECRET). 【未検証】
fn feeder_request(
    url: &str,
    feeder_token: &Mutex<String>,
) -> Result<tokio_tungstenite::tungstenite::handshake::client::Request, String> {
    let mut request = url
        .into_client_request()
        .map_err(|e| format!("build WS request: {e}"))?;
    let token = feeder_token.lock().map(|g| g.clone()).unwrap_or_default();
    if token.is_empty() {
        // Should not happen (main.rs sets the token before the backend can
        // request capture); connect anyway and let the server decide.
        eprintln!("[capture] feeder token is empty; connecting without X-Feeder-Token");
    } else {
        let value = HeaderValue::from_str(&token)
            .map_err(|e| format!("feeder token is not a valid header value: {e}"))?;
        request.headers_mut().insert("X-Feeder-Token", value);
    }
    Ok(request)
}

async fn ws_feeder(
    port: u16,
    mut frames_rx: broadcast::Receiver<Vec<u8>>,
    stop: Arc<AtomicBool>,
    feeder_token: Arc<Mutex<String>>,
) {
    let url = format!("ws://127.0.0.1:{port}/live/ws");
    let mut backoff = BACKOFF_MIN;
    let dropped = AtomicU64::new(0);

    'outer: loop {
        if stop.load(Ordering::SeqCst) {
            break;
        }
        let request = match feeder_request(&url, &feeder_token) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("[capture] {e}; retrying in {backoff:?}");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(BACKOFF_MAX);
                continue;
            }
        };
        match connect_async(request).await {
            Ok((mut ws, _resp)) => {
                backoff = BACKOFF_MIN;
                loop {
                    // select! keeps the incoming side of the stream polled:
                    // tungstenite only answers protocol Pings when the stream
                    // is polled, and uvicorn drops unanswered pings after 20 s.
                    tokio::select! {
                        frame = frames_rx.recv() => match frame {
                            Ok(frame) => {
                                // Binary frames only — never text/JSON (contract).
                                if let Err(e) = ws.send(Message::Binary(frame.into())).await {
                                    eprintln!("[capture] WS send failed, reconnecting: {e}");
                                    break; // reconnect; unsent frame is dropped
                                }
                            }
                            Err(broadcast::error::RecvError::Lagged(n)) => {
                                // Ring overflowed: n oldest frames were
                                // overwritten while we were behind.
                                dropped.fetch_add(n, Ordering::Relaxed);
                            }
                            Err(broadcast::error::RecvError::Closed) => {
                                // Capture stopped: close the feeder connection.
                                let _ = ws.send(Message::Close(None)).await;
                                let _ = ws.close(None).await;
                                break 'outer;
                            }
                        },
                        incoming = ws.next() => match incoming {
                            // Server broadcasts (live JSON to browser clients)
                            // also reach this client; discard them. Ping/Pong
                            // control frames are handled by tungstenite itself
                            // as a side effect of this poll.
                            Some(Ok(Message::Close(_))) => {
                                eprintln!("[capture] WS closed by server, reconnecting");
                                break; // reconnect path
                            }
                            Some(Ok(_)) => {}
                            Some(Err(e)) => {
                                eprintln!("[capture] WS read failed, reconnecting: {e}");
                                break; // reconnect path
                            }
                            None => {
                                eprintln!("[capture] WS stream ended, reconnecting");
                                break; // reconnect path
                            }
                        },
                    }
                }
            }
            Err(e) => {
                if stop.load(Ordering::SeqCst) {
                    break;
                }
                eprintln!("[capture] WS connect to {url} failed: {e}; retrying in {backoff:?}");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(BACKOFF_MAX);
            }
        }
    }
    let d = dropped.load(Ordering::Relaxed);
    if d > 0 {
        eprintln!("[capture] dropped {d} frames (WS behind)");
    }
    eprintln!("[capture] WS feeder exit");
}
