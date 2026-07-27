//! WebView / window management.
//!
//! - The single "main" window starts on the bundled bootstrap page (Tauri app
//!   origin). After the backend passes /healthz, it is navigated to
//!   `http://127.0.0.1:<port>/` (same-origin with the backend's own WS/SSE,
//!   so the Python app's Origin checks and relative URLs keep working, and
//!   127.0.0.1 is a secure context for getUserMedia).
//! - Navigation is restricted via `on_navigation`: only the bootstrap origin
//!   and the exact backend origin are allowed inside the WebView; any other
//!   http(s) URL is opened in the default browser instead.
//! - WebView2 `PermissionRequested` is handled natively: ONLY the Microphone
//!   permission for the exact backend origin is granted; everything else is
//!   denied (camera, notifications, clipboard-read, ...).
//!
//! API used for the permission hook: `WebviewWindow::with_webview` hands us a
//! `tauri::webview::PlatformWebview`; on Windows it exposes `.controller()`
//! (an `ICoreWebView2Controller` from the `webview2-com` crate re-exporting
//! the WebView2 Win32 COM API). From the controller we get `ICoreWebView2`
//! and register `add_PermissionRequested`.
//! 【未検証】The whole module is unverified (WSL2 dev host — never compiled).
//! In particular the exact `webview2-com`/`windows` type paths and the
//! `WebviewWindow::navigate` signature must be checked against the resolved
//! tauri/wry versions on the first Windows build.

use std::sync::{Arc, RwLock};

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use url::Url;

/// The origin (e.g. "http://127.0.0.1:49182") the WebView is allowed to
/// navigate to. None until the backend is up.
pub type AllowedOrigin = Arc<RwLock<Option<String>>>;

/// Creates the main window on the bootstrap page with the navigation guard
/// and (on Windows) the WebView2 permission handler installed.
pub fn create_main_window(
    app: &AppHandle,
    allowed_origin: AllowedOrigin,
) -> tauri::Result<WebviewWindow> {
    let nav_origin = allowed_origin.clone();
    let nav_app = app.clone();
    let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Transcribe")
        .inner_size(1160.0, 800.0)
        .min_inner_size(760.0, 560.0)
        .on_navigation(move |url| navigation_allowed(&nav_app, &nav_origin, url))
        .build()?;

    #[cfg(windows)]
    install_permission_handler(&window, allowed_origin);

    Ok(window)
}

/// Navigation policy:
/// - bootstrap/app origin (tauri://localhost or http(s)://tauri.localhost on
///   Windows): allowed
/// - exact backend origin (http://127.0.0.1:<port>): allowed
/// - any other http(s) URL: denied in the WebView, opened in the default
///   browser instead
/// - everything else: denied
fn navigation_allowed(app: &AppHandle, allowed: &AllowedOrigin, url: &Url) -> bool {
    // Tauri app origin. On Windows the app protocol is served from
    // http(s)://tauri.localhost; on other platforms it is tauri://localhost.
    if url.scheme() == "tauri" {
        return true;
    }
    if matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost") {
        return true;
    }
    // "about:blank" during window setup.
    if url.scheme() == "about" {
        return true;
    }

    if let Some(origin) = allowed.read().ok().and_then(|g| g.clone()) {
        if origin_of(url).as_deref() == Some(origin.as_str()) {
            return true;
        }
    }

    // External links (e.g. Hugging Face gate pages linked from the app UI):
    // hand off to the default browser, keep the WebView where it is.
    if matches!(url.scheme(), "http" | "https") {
        let _ = tauri_plugin_opener::open_url(url.as_str(), None::<String>);
    } else {
        let _ = app; // non-http scheme: just deny
    }
    false
}

/// "scheme://host:port" with the effective port made explicit.
fn origin_of(url: &Url) -> Option<String> {
    let host = url.host_str()?;
    let port = url.port_or_known_default()?;
    Some(format!("{}://{}:{}", url.scheme(), host, port))
}

/// Publishes the backend origin and navigates the main window to it.
/// Call only after /healthz succeeded.
pub fn navigate_to_backend(app: &AppHandle, allowed: &AllowedOrigin, port: u16) -> Result<(), String> {
    let origin = format!("http://127.0.0.1:{port}");
    if let Ok(mut guard) = allowed.write() {
        *guard = Some(origin.clone());
    }
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window not found".to_string())?;
    let url = Url::parse(&format!("{origin}/")).map_err(|e| e.to_string())?;
    // WebviewWindow::navigate — tauri 2.x. 【未検証】signature/mutability.
    window.navigate(url).map_err(|e| format!("navigate failed: {e}"))
}

/// Registers a native WebView2 PermissionRequested handler that grants ONLY
/// Microphone for the exact backend origin and denies everything else.
///
/// 【未検証】COM interop below is written from offline knowledge of
/// webview2-com 0.3x and has never been compiled. Check:
/// - `PlatformWebview::controller()` availability in the resolved tauri,
/// - `EventRegistrationToken` path (windows::Win32::System::WinRT),
/// - `PermissionRequestedEventHandler::create` closure signature,
/// - `take_pwstr` helper location (webview2_com::pwstr).
#[cfg(windows)]
fn install_permission_handler(window: &WebviewWindow, allowed_origin: AllowedOrigin) {
    let result = window.with_webview(move |platform_webview| {
        use webview2_com::pwstr::take_pwstr;
        use webview2_com::Microsoft::Web::WebView2::Win32::{
            COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
            COREWEBVIEW2_PERMISSION_STATE_ALLOW, COREWEBVIEW2_PERMISSION_STATE_DENY,
        };
        use webview2_com::PermissionRequestedEventHandler;
        use windows::core::PWSTR;
        use windows::Win32::System::WinRT::EventRegistrationToken;

        // SAFETY: executed on the WebView's UI thread by with_webview; the
        // controller and core webview outlive the registered handler.
        unsafe {
            let controller = platform_webview.controller();
            let core = match controller.CoreWebView2() {
                Ok(core) => core,
                Err(e) => {
                    eprintln!("[webview] CoreWebView2() failed: {e}");
                    return;
                }
            };
            let mut token = EventRegistrationToken::default();
            let handler = PermissionRequestedEventHandler::create(Box::new(
                move |_sender, args| {
                    let Some(args) = args else { return Ok(()) };
                    let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                    args.PermissionKind(&mut kind)?;
                    let mut uri_raw = PWSTR::null();
                    args.Uri(&mut uri_raw)?;
                    let uri = take_pwstr(uri_raw);

                    let origin_ok = allowed_origin
                        .read()
                        .ok()
                        .and_then(|g| g.clone())
                        .map(|origin| {
                            uri == origin || uri.starts_with(&format!("{origin}/"))
                        })
                        .unwrap_or(false);

                    let state = if origin_ok && kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                        COREWEBVIEW2_PERMISSION_STATE_ALLOW
                    } else {
                        // Deny everything else outright: no camera, no
                        // notifications, no clipboard, and no microphone for
                        // any origin other than the exact backend origin.
                        COREWEBVIEW2_PERMISSION_STATE_DENY
                    };
                    args.SetState(state)?;
                    Ok(())
                },
            ));
            if let Err(e) = core.add_PermissionRequested(&handler, &mut token) {
                eprintln!("[webview] add_PermissionRequested failed: {e}");
            }
            // `token` is intentionally not stored: the handler stays
            // registered for the lifetime of the WebView, which equals the
            // lifetime of the app.
        }
    });
    if let Err(e) = result {
        eprintln!("[webview] with_webview failed (permission handler not installed): {e}");
    }
}
