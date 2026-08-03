# Security Review — Tauri Desktop App (NSKETCH-732 / Transcribe)

Static review only (no builds/tests run). Scope: the Python backend files and
the Rust Tauri shell listed in the task. Findings are tagged
`[critical/major/minor/info]` with file paths. A residual-risks list and an
acceptance report follow at the end.

---

## Summary

The implementation shows strong security hygiene for a local-first desktop
app: loopback-only binding, a Host-header allowlist (DNS-rebinding defense),
timing-safe secret comparison, Origin checks on browser WS handshakes, a
feeder-token gate, zip-slip protection with belt-and-braces canonicalization,
SHA-256-verified runtime download over HTTPS, WebView2 permission scoping to
microphone-only for the exact backend origin, no remote-origin Tauri IPC, HF
token stored in the Windows Credential Manager, and a CSP'd bootstrap page.

No `[critical]` issues were found. The most important gaps are:

1. **[major] No CSP / security headers on the backend origin.** Once the
   WebView navigates to `http://127.0.0.1:<port>/`, FastAPI serves the real
   UI with no `Content-Security-Policy`, no `X-Frame-Options`, no
   `frame-ancestors`. The Tauri CSP in `tauri.conf.json` only protects the
   bootstrap (tauri://) origin. Any XSS in a Jinja2 template (or a future
   one that uses `|safe`) executes with no mitigation, and the dashboard can
   be framed by any other local origin.
2. **[major] `script-src 'unsafe-inline'` in the bootstrap CSP** plus
   `withGlobalTauri: true` — an XSS on the bundled bootstrap page would reach
   the full Tauri IPC surface (`save_hf_token`, `start_backend`,
   `open_external`). The bootstrap page is bundled/trusted today, but the
   inline-script policy removes a key defense layer.
3. **[major] Shutdown secret is inherited by every sidecar child process**
   (ffmpeg). `TRANSCRIBE_SHUTDOWN_SECRET` is placed in the sidecar env and
   ffmpeg is spawned by the backend with that env, so the secret that gates
   `/internal/shutdown` and the feeder WS role leaks into the ffmpeg process
   environment.

Smaller items: feeder connections are accepted with **no token when the
secret is unset** (dev-only path), `GET /internal/models/moonshine` leaks
download status without the internal-header gate, the runtime-download
integrity is only as strong as the bundled manifest (code-signing is the real
boundary), and several `info`-level observations.

---

## Findings

### [major] No CSP or framing headers on the backend HTTP origin
- **Files:** `src/web/app.py` (no security-header middleware; `create_app`)
- The Tauri `csp` in `src-tauri/tauri.conf.json` applies only to the
  bootstrap/app origin (`tauri://localhost`, `tauri.localhost`). After
  `webview.rs::navigate_to_backend` navigates the WebView to
  `http://127.0.0.1:<port>/`, the page is served by FastAPI, which sets
  **no** `Content-Security-Policy`, `X-Frame-Options`, or
  `frame-ancestors`. Jinja2 autoescape is on for `.html` (verified:
  `{{ text }}`, `{{ structure|tojson }}` are escaped/safe-serialized), so
  there is no known live XSS today, but there is zero defense-in-depth:
  - a single future `|safe`/`Markup`/`innerHTML` sink becomes a script
    injection with no CSP to contain it;
  - the dashboard can be `<iframe>`-embedded by any other local origin
    (clickjacking). The Host allowlist does not block framing — it only
    checks the `Host` request header, which an iframe request still sends
    correctly as `127.0.0.1:<port>`.
- **Recommendation:** add a small middleware on the backend that sets
  `Content-Security-Policy` (e.g. `default-src 'self'; connect-src 'self'
  ws://127.0.0.1:<port>; ...`), `X-Frame-Options: DENY` (or
  `frame-ancestors 'none'`), and `X-Content-Type-Options: nosniff`. This is
  the single highest-value hardening in the review.

### [major] `script-src 'unsafe-inline'` + `withGlobalTauri` on the bootstrap page
- **Files:** `src-tauri/tauri.conf.json` (`security.csp`, `app.withGlobalTauri`),
  `src-tauri/bootstrap/index.html` (inline `<script>`)
- The bootstrap CSP allows `'unsafe-inline'` for scripts, and
  `withGlobalTauri: true` exposes `window.__TAURI__.core.invoke` to page
  script. The bootstrap page is bundled/trusted, but if any input ever
  reaches the page unsanitized (e.g. an error string rendered via
  `innerHTML` — see `status()` / `showErr` which uses `innerHTML` with
  concatenated invoke-error strings), inline-script execution would have
  direct access to `save_hf_token`, `delete_hf_token`, `start_backend`,
  `open_external`.
- **Recommendation:** move bootstrap JS to an external file under
  `frontendDist` so `script-src 'self'` can be used, and keep
  `withGlobalTauri` only if unavoidable. Render dynamic status via
  `textContent`, not `innerHTML`.

### [major] Shutdown secret leaks into child-process environment (ffmpeg)
- **Files:** `src-tauri/src/process.rs` (`contract_env` pushes
  `TRANSCRIBE_SHUTDOWN_SECRET`; `build_env_block` inherits the whole parent
  env + extras), `src/web/app.py` (`internal_shutdown` trusts the secret),
  `src/ffmpeg_patch.py` (spawns ffmpeg via whisper/`subprocess`)
- The 32-byte random secret generated in `main.rs` is placed in the sidecar
  env as `TRANSCRIBE_SHUTDOWN_SECRET`. The backend then spawns ffmpeg
  (whisper's `run`, and batch conversion) which **inherits** that env, so
  `TRANSCRIBE_SHUTDOWN_SECRET` is readable in the ffmpeg process
  environment. The same secret doubles as the feeder WS token. ffmpeg is a
  bundled trusted binary, so this is not an immediate RCE, but it is a
  gratuitous secret-propagation surface: any descendant process, any
  third-party library that shells out, or a crash-dump/environment-reading
  tool sees the token that gates shutdown and feeder auth.
- **Recommendation:** strip `TRANSCRIBE_SHUTDOWN_SECRET` (and `HF_TOKEN`)
  from the environment of any subprocess the backend spawns that does not
  need them (e.g. via a `subprocess.run(..., env=sanitized_env)` helper in
  `ffmpeg_patch.py` / the batch converter). The backend holds the secret in
  `app.state`; it does not need it in `os.environ` for child processes.

### [minor] Feeder WS connections accepted with no token when the secret is unset
- **Files:** `src/web/app.py` (`live_ws`, feeder branch)
- In desktop mode the secret is always set, so this is dev-only. But the
  logic is: `is_feeder = not Origin`; `if secret is not None: check token`.
  When `TRANSCRIBE_SHUTDOWN_SECRET` is absent (plain `uvicorn` dev run with
  no env), any local process can open an Origin-less WS to `/live/ws` and
  feed arbitrary PCM / drive the live session with **no authentication at
  all**. Browsers always send `Origin`, so this is not a browser-CSRF vector,
  but a co-located local process can.
- **Recommendation:** when the secret is unset, either reject feeder
  connections outright, or require an explicit dev opt-in. At minimum,
  document that dev mode has no feeder auth.

### [minor] `GET /internal/models/moonshine` has no internal-header gate
- **Files:** `src/web/app.py` (`moonshine_status` vs `moonshine_download`)
- `POST /internal/models/moonshine` correctly requires `x-transcribe-internal: 1`
  (forcing a CORS preflight that fails without CORS middleware — good
  CSRF defense). The `GET` status endpoint has **no** such gate. It only
  exposes download status (`status`, `error`, `dest` path, license accepted,
  weights present), not weights or secrets, so the impact is low. But it is
  an unauthenticated information leak available to any local origin (Host
  allowlist passes for loopback) and via simple-GET CSRF (no preflight).
- **Recommendation:** apply the same `x-transcribe-internal` gate to the GET
  status endpoint, or accept the leak with a documented note.

### [minor] No `X-Frame-Options` / `frame-ancestors` → local clickjacking
- **Files:** `src/web/app.py`
- See the [major] CSP finding. Separately noted because the fix is cheap and
  independent: a single `X-Frame-Options: DENY` header on the backend removes
  the framing surface regardless of CSP.

### [minor] `tojson` embedded in `<script>` relies on Jinja2 version escaping
- **Files:** `src/web/templates/detail.html` (`{{ structure|tojson }}`,
  `{{ graph|tojson }}`)
- Modern Jinja2 `tojson` escapes `<`, `>`, `&` for safe embedding inside
  `<script>` and prevents `</script>` breakout. This is fine on current
  Jinja2, but it is version-dependent. If the pinned Jinja2 is ever
  downgraded, structure/graph JSON (which can contain model-derived strings)
  could break out of the script context. Low likelihood, but worth pinning
  the Jinja2 version and/or adding a `</script>`-scan rejection.
- **Recommendation:** pin `jinja2>=3.1` (HTML-safe `tojson`) in the
  packaged requirements and add a regression note.

### [minor] Runtime-download integrity is only as strong as the bundled manifest
- **Files:** `src-tauri/src/downloader.rs` (`read_manifest`, `download_with_resume`)
- The downloader correctly enforces `https://`, a safe `version` charset,
  SHA-256 verification of the downloaded archive, size cap, and zip-slip
  protection. However the `url` **and** `sha256` both come from the same
  bundled `backend-manifest.json` resource. An attacker who can replace that
  resource (write access to the install/resource directory) can supply a
  self-consistent `(url, sha256)` pair pointing at their own payload, and the
  download will "verify" against the attacker's hash. The real boundary is
  installer code-signing / resource-directory ACLs, not the in-app check.
- **Recommendation:** document that the resource directory must be
  user-writable-protected and that the NSIS installer is code-signed; consider
  signing the manifest or embedding the expected hash at build time in a
  read-only section.

### [minor] Zip-slip via symlink entries is not explicitly handled
- **Files:** `src-tauri/src/downloader.rs` (`extract_zip`)
- `extract_zip` has two solid layers against classic `../` zip-slip
  (`enclosed_name()` + canonicalize-inside-dest). It does **not** special-case
  symlink entries: the `zip` crate can surface symlink entries, and the code
  treats everything as either `is_dir()` or a file copied with
  `std::io::copy`. If a symlink entry is created inside `dest` pointing
  outside, a subsequent file entry whose path traverses that symlink would
  `File::create` outside the destination (the canonicalize check is on the
  parent *before* the file is written, and a freshly-created symlink's
  canonicalize would resolve the link target). Whether this is exploitable
  depends on the exact `zip`-crate behavior for symlinks and ordering; it is
  not confirmed.
- **Recommendation:** explicitly reject entries that are symlinks (check the
  entry mode / `enclosed_name` is not enough), or canonicalize the final
  `out_path` (not just the parent) after `File::create` and assert it stays
  inside `dest_canon`. Confirm against the resolved `zip` crate version.

### [minor] Inherited user environment leaks into the sidecar
- **Files:** `src-tauri/src/process.rs` (`build_env_block` merges
  `std::env::vars_os()`)
- `build_env_block` starts from the **entire** parent env and only overrides
  the listed contract keys. Good: `WEB_HOST`, `PYTHONPATH`, `PYTHONHOME`,
  `PYTHONSTARTUP` are pinned. Not stripped: arbitrary user env (e.g. an
  existing `HF_TOKEN`, `HTTP_PROXY`/`HTTPS_PROXY`, `no_proxy`,
  `WHISPER_MODEL`, `TRANSCRIBE_*` not in the override list). Consequences:
  - a pre-existing `HF_TOKEN` in the user session is inherited by the sidecar
    even when the user configured none via the Credential Manager — the
    backend may then make authenticated HF calls the user did not intend;
  - `HTTPS_PROXY` is deliberately honored by the runtime downloader
    (`downloader.rs` does not call `.no_proxy()`), which is a documented
    trade-off; integrity is still guaranteed by SHA-256.
- **Recommendation:** consider an allow-list env block (start empty, add only
  what the sidecar needs + a safe minimal PATH) instead of inherit-then-
  override, or at least explicitly drop `HF_TOKEN` when no keyring token is
  configured.

### [minor] `open_external` is correctly scheme-restricted; verify tauri-plugin-opener cannot be used to pass arguments
- **Files:** `src-tauri/src/main.rs` (`open_external`)
- Good: validates `http`/`https` only. The call passes `None` for the
  handler/with-program, so it opens in the default browser. No `file://`,
  no `javascript:`, no custom handler. Low risk. Note: if
  `tauri_plugin_opener::open_url` ever gains the ability to pass a `with`
  program from the URL, re-audit. Info-level in practice; flagged minor for
  future-proofing.

### [info] Shutdown-secret / feeder-token comparison is timing-safe
- **Files:** `src/web/app.py` (`internal_shutdown`, `live_ws` feeder branch)
- Both use `hmac.compare_digest(token.encode(), secret.encode())`. Correct.
  When `secret` is falsy the shutdown endpoint 404s (`if not secret: 404`),
  so the compare path is only reached with a non-empty secret. Good. The
  secret is generated with `OsRng` over 32 bytes (`main.rs`) —
  cryptographically strong. No finding; recorded as a positive control.

### [info] DNS-rebinding defense is correctly applied to HTTP and WS
- **Files:** `src/web/app.py` (`_host_allowed`, `host_allowlist` middleware,
  `live_ws` Host check)
- The Host allowlist (`127.0.0.1`, `localhost`, `::1`, `config.WEB_HOST`) is
  applied as HTTP middleware to all requests **and** re-checked inside the
  `/live/ws` handshake (closes with 1008). The Rust shell pins
  `WEB_HOST=127.0.0.1` in `contract_env`. Positive control. Minor edge: the
  check is `urlsplit(f"//{host_header}").hostname`; malformed Host headers
  return False, which is safe-by-default.

### [info] CSWSH defense (browser WS) is correct
- **Files:** `src/web/app.py` (`_origin_allowed`)
- Browser WS handshakes always carry `Origin`; the feeder path is
  `not Origin`. For browser connections, `origin_host == host` is required.
  Cross-origin browsers are rejected. Positive control.

### [info] `TAURI_EVENT` stdout emission has no untrusted input
- **Files:** `src/live/session.py` (`_emit_tauri_event`)
- The payload is `{"capture": "start"|"stop", "session_id": <generated id>}`.
  `capture` is a hardcoded literal; `session_id` is generated from
  `strftime("%Y%m%d_%H%M")` + a numeric counter (`_unique_session_id`) — no
  user/transcription input reaches this line. No injection into the
  stdout→Rust command channel. Positive control. General note: ensure no
  library `print`s untrusted text with the `TAURI_EVENT ` / `TAURI_READY `
  prefix; the drain in `process.rs` trusts those prefixes unconditionally.

### [info] WebView2 permission scope is tight
- **Files:** `src-tauri/src/webview.rs` (`install_permission_handler`)
- Only `COREWEBVIEW2_PERMISSION_KIND_MICROPHONE` is granted, and only when
  the request URI matches the exact backend origin (`uri == origin || uri.starts_with(origin + "/")`).
  Everything else is denied. The prefix-match is safe against
  `http://127.0.0.1:<port>.evil/` and userinfo-form tricks because the byte
  after the port must be `/` (or end-of-string for the exact match).
  Positive control. (Caveat: the COM interop is `【未検証】` per the module
  comment — the *policy* is correct; the *wiring* must be confirmed at first
  Windows build.)

### [info] No remote-origin Tauri IPC
- **Files:** `src-tauri/capabilities/default.json`
- `dangerousRemoteDomainIpcAccess` is intentionally not configured; the
  capability is scoped to `windows: ["main"]` with `core:default` only, and
  the description explicitly notes that after navigation to the backend
  origin Tauri exposes no IPC. Good. The IPC surface (setup commands) is only
  reachable from the bundled bootstrap origin.

### [info] Updater ships placeholder values and is effectively disabled
- **Files:** `src-tauri/tauri.conf.json` (`plugins.updater`,
  `bundle.createUpdaterArtifacts: false`)
- `endpoints` points at `https://PLACEHOLDER.invalid/...` and `pubkey` is a
  placeholder string. With `createUpdaterArtifacts: false` no update
  artifacts are produced, so this is inert today. Risk: if the updater is
  later enabled without replacing the placeholder pubkey/endpoint, signature
  verification could behave unexpectedly. Recommendation: remove the
  `plugins.updater` block until a real endpoint+key exist, to avoid shipping
  a half-configured updater.

### [info] `/events` SSE and `/live/status` are unauthenticated
- **Files:** `src/web/app.py` (`events`, `live_status`, `healthz`)
- These expose job/worker/live-session status to any caller that passes the
  Host allowlist (i.e. any local process). This is by design for a local
  dashboard; the data is metadata, not transcripts-of-secrets beyond what the
  UI already shows. Noted for completeness; no change recommended unless the
  threat model includes co-located untrusted users on the same machine.

### [info] Backend `/internal/shutdown` returns 202 before finalize completes
- **Files:** `src/web/app.py` (`internal_shutdown`, `_graceful_shutdown`)
- Correct: the secret is checked before spawning the daemon shutdown thread.
  The 202-then-background-finalize design is fine. The Rust shell separately
  waits up to 75 s and then `TerminateProcess`es. No issue; recorded as a
  positive control on the shutdown ordering.

---

## Residual risks

- The whole Rust shell is `【未検証】` (authored on WSL2, never compiled/run on
  Windows) per the module comments. The *security policy* (permission scope,
  navigation allowlist, env hardening, Job Object kill-on-close, zip-slip
  checks, secret generation) is sound on paper; the *wiring* (windows-rs /
  webview2-com / cpal signatures) must be confirmed at first real Windows
  build. A signature mismatch that compiles-but-misbehaves (e.g. permission
  handler not actually registering, KILL_ON_JOB_CLOSE not set) would silently
  weaken the protections reviewed here.
- Integrity of the runtime download depends on the bundled manifest, which
  depends on install-directory ACLs / installer code-signing — not on any
  check in this codebase.
- The backend HTTP origin has no CSP/framing headers (the [major] finding);
  until that is addressed, a single template XSS sink has no containment.
- The secret-in-child-env leak ([major]) is a defense-in-depth gap, not a
  confirmed exploit, since ffmpeg is a bundled trusted binary.

---

## Acceptance report