# Task: NSKETCH-883 — Strong / accuracy mode for BATCH transcription (openai-whisper)

## Meta
- linear_id: NSKETCH-883
- tier: M
- created: 2026-07-14
- status: implementing

## Brief

**Current State**
- Batch pipeline model is fixed at import time: `WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")` (`src/config.py:12`).
- `src/worker.py:42` loads it via `get_whisper_model(config.WHISPER_MODEL)`; `src/worker.py:51` logs `ready (device: {device}, whisper: {config.WHISPER_MODEL})`.
- `src/models.py` has `get_device()` (`"cuda" if torch.cuda.is_available() else "cpu"`) and a locked singleton registry.
- Precedent: live mode already does deterministic hardware-aware engine selection (`src/live/engine.py::_create_engine`) with dependency-free unit tests (`tests/test_engine_select.py`, run via `tests/_runner.py`, pytest-compatible).
- Only 2 consumers of `config.WHISPER_MODEL` exist (worker.py lines 42 and 51). Nothing else reads it.

**Goal**
- GPU hosts can opt into a stronger batch model via a mode switch: `strong` → `large-v3-turbo`, `max` → `large-v3`. CPU-only hosts always keep the light default (`medium`) — mode requests degrade gracefully, never force heavy models on CPU.
- Explicit `WHISPER_MODEL` always wins (zero behavior change for existing users).
- Ready log shows the resolved model and the reason it was chosen.

**Scope**
- In: pure resolver function + new env var `BATCH_WHISPER_MODE`, worker wiring, ready-log update, unit tests, README env-table update.
- Out: #2 ASR-first architecture, live-mode changes, engine swap (openai-whisper stays), new dependencies, changes to diarization.

**Constraints**
- stdlib/existing deps only. Deterministic; unit tests must run without torch/GPU/model downloads (follow the `test_engine_select.py` convention of avoiding torch imports).

**Success Criteria**
1. All 12 resolver matrix cases pass without torch installed or models downloaded.
2. Unset everything → behavior identical to today (`medium`).
3. `WHISPER_MODEL=small` + `BATCH_WHISPER_MODE=max` + CUDA → `small`.
4. `BATCH_WHISPER_MODE=strong` on CPU → `medium` with an explicit fallback reason in the ready log.
5. Ready log contains resolved model name + selection reason.

## Decision Log

- `[startproject] DECISION` — Env interface is `BATCH_WHISPER_MODE=light|strong|max` (new var), not a `WHISPER_MODEL=auto` sentinel; `WHISPER_MODEL` keeps its exact existing contract and always wins.
- `[startproject] DECISION` — Tier mapping: light=`medium` (current default), strong=`large-v3-turbo`, max=`large-v3`. Per requirement.
- `[startproject] DECISION` — CPU host + strong/max → silent-degrade to `medium` with explicit reason in ready log (never force heavy model on CPU; never fail startup for an honored-but-unmet preference). Users who truly want heavy-on-CPU set `WHISPER_MODEL` explicitly.
- `[startproject] DECISION` — Invalid `BATCH_WHISPER_MODE` value → `ValueError` at startup (fail-fast, consistent with live engine's invalid `LIVE_ENGINE` handling); surfaces via existing `startup failed:` system message.
- `[startproject] DECISION` — Resolver is a pure function in `src/config.py` (stdlib-only module) taking `cuda_available` as a parameter; resolution happens at worker startup, not config import time. Enables dependency-free deterministic tests.
- `[startproject] DECISION` — Ready log format: `ready (device: {device}, whisper: {resolved.name} — {resolved.reason})`.
- `[startproject] DECISION` — Scope excludes #2 (ASR-first architecture) and any live-mode changes.
- `[startproject] PRE` — 2026-07-14 startproject Phase 1–3 executed (tier=M, OpenCode consulted). DONT-ASK MODE: Gate 1 auto-approved.
- `[startproject] POST` — Plan complete; Linear comment posted to NSKETCH-883.
- `[team-implement] POST` — 2026-07-14 implemented on `feature/nsketch-883-batch-strong-mode`; py_compile OK; new tests 15/15; full suite 145/145 no regression.
- `[team-review] POST` — 2026-07-14 PASS. 4-perspective review (Claude/Security/Simplify + OpenCode gpt-5.5); full suite 145/145, all success criteria verified. 0 critical/0 major. Minor follow-ups: `.env`-timing for BATCH_WHISPER_MODE (env-var only, consistent w/ existing behavior; README note added), invalid-mode error diagnostics. `.claude/rules/security.md` not present in repo.

## Design

**Interface**

```
WHISPER_MODEL=<model name>            # always wins, unchanged contract
BATCH_WHISPER_MODE=light|strong|max   # default light; consulted only when WHISPER_MODEL unset
```

Rejected alternatives: `WHISPER_MODEL=auto` sentinel (breaks the "value is a real model name" contract); `WHISPER_MODEL_STRONG` (redundant — power users already set `WHISPER_MODEL` directly).

**Resolution table**

| WHISPER_MODEL | BATCH_WHISPER_MODE | CUDA | Resolved |
|---|---|---|---|
| set (non-empty) | anything (even invalid) | any | exactly that value |
| unset | unset / `light` | any | `medium` |
| unset | `strong` | yes | `large-v3-turbo` |
| unset | `strong` | no | `medium` (reason: CUDA unavailable) |
| unset | `max` | yes | `large-v3` |
| unset | `max` | no | `medium` (reason: CUDA unavailable) |
| unset | invalid | any | `ValueError` → existing `startup failed: ...` path |

**Placement**: resolver lives in `src/config.py` (stdlib-only), not `src/models.py` (imports torch/whisper at top). CUDA state passed in as parameter → pure function, no probe patching.

**Code shape**

- `src/config.py`:
  - `WHISPER_MODEL = os.environ.get("WHISPER_MODEL")` (raw; None/empty = unset)
  - `BATCH_WHISPER_MODE = os.environ.get("BATCH_WHISPER_MODE", "light")`
  - Tier constants: `BATCH_MODEL_LIGHT = "medium"`, `BATCH_MODEL_STRONG = "large-v3-turbo"`, `BATCH_MODEL_MAX = "large-v3"`
  - `@dataclass(frozen=True) class ResolvedWhisperModel: name: str; reason: str`
  - `def resolve_whisper_model(*, explicit_model: str | None, mode: str | None, cuda_available: bool) -> ResolvedWhisperModel` — strips/lowercases mode, strips explicit model, explicit-first return (mode never validated when explicit is set), empty string treated as unset.
- `src/worker.py::run()`: resolve at startup, pass `resolved.name` to `get_whisper_model`, ready log `ready (device: {device}, whisper: {resolved.name} — {resolved.reason})`, startup print next to `Using device:` line.
- `tests/test_batch_model_select.py` — mirrors `test_engine_select.py` style; imports only `src.config` (no torch, no mocking).

**Test matrix** (12 cases): 7 table rows + explicit-wins-over-strong (`small`+strong+cuda→`small`), explicit-wins-on-cpu (`large-v3`+light+cpu→`large-v3`), whitespace explicit (`" medium "`→`medium`), case/whitespace mode (`" STRONG "`→large-v3-turbo), explicit set + invalid mode → no raise; plus default-mode-constant check (`config.BATCH_WHISPER_MODE` default `"light"`).

**Docs**: README env table — update `WHISPER_MODEL` row, add `BATCH_WHISPER_MODE` row (light/strong/max, GPU opt-in semantics, CPU fallback).

**Risk notes for implementer**
- Do not resolve CUDA at config import time — `src/config.py` must stay torch-free.
- `config.WHISPER_MODEL` may now be `None`; nothing else reads it, but don't reintroduce a `"medium"` default there.
- Keep `get_whisper_model(name)` signature untouched.

**Task list**
1. `src/config.py` — raw env read; add `BATCH_WHISPER_MODE`, tier constants, `ResolvedWhisperModel`, `resolve_whisper_model()`.
2. `src/worker.py` — resolve at startup, update ready log + startup print.
3. `tests/test_batch_model_select.py` — 12-case matrix, `_runner` compatible.
4. README — env table updates.
5. Full test suite run; verify no regression.

## Implementation Notes

**Branch**: `feature/nsketch-883-batch-strong-mode` (from `main`)

**Changed files**
- `src/config.py` — `WHISPER_MODEL` now raw env read (None when unset); added `BATCH_WHISPER_MODE` (default `"light"`), tier constants (`BATCH_MODEL_LIGHT="medium"`, `BATCH_MODEL_STRONG="large-v3-turbo"`, `BATCH_MODEL_MAX="large-v3"`), frozen dataclass `ResolvedWhisperModel(name, reason)`, pure `resolve_whisper_model(*, explicit_model, mode, cuda_available)`. Module stays stdlib-only (torch-free).
- `src/worker.py` — `run()` resolves at startup (`cuda_available=device == "cuda"`), prints `Whisper model: {name} ({reason})`, passes `resolved.name` to `get_whisper_model`, ready log now `ready (device: {device}, whisper: {resolved.name} — {resolved.reason})`. Invalid mode raises inside the existing try → surfaces via `startup failed: ...` (fail-fast per Decision Log).
- `tests/test_batch_model_select.py` — new; 15 tests (12-case design matrix + extras), `_runner`-compatible, imports only `src.config` (no torch/GPU/downloads).
- `README.md` — env table: `WHISPER_MODEL` row updated (default now "unset, resolved via mode; effective default medium"), `BATCH_WHISPER_MODE` row added.

**Verification**
- `py_compile` on all 3 touched Python files: OK.
- New tests: 15/15 pass without torch.
- Full suite: 13 modules / 145 tests, all pass — no regression (incl. `test_engine_select.py` 10/10).
- Grep confirms only consumer of `config.WHISPER_MODEL` is the resolver call in `worker.py:43`; no other `None`-safety fixups needed.

**Reviewer notes**
- `config.WHISPER_MODEL` may now be `None`; do not reintroduce a `"medium"` default there.
- `test_default_mode_constant_is_light` reads the import-time value — suite must run with `BATCH_WHISPER_MODE` unset (normal case).
- Transcription ACCURACY not verifiable in this environment (no GPU/audio/models); real-audio GPU validation is a required user follow-up (stated in PR body).

## Review

**Verdict: PASS** (2026-07-14, tier=M — Claude/Security/Simplify perspectives + OpenCode gpt-5.5)

| Criterion | Result |
|---|---|
| Explicit `WHISPER_MODEL` always wins (zero behavior change) | PASS |
| Unset everything → `medium` (identical to before) | PASS |
| CPU + strong/max degrades to `medium` with reason | PASS |
| Invalid mode fails fast via startup-failed path | PASS |
| `config.py` stays torch-free (verified at runtime) | PASS |
| No other consumers of `config.WHISPER_MODEL` broken by `None` | PASS (sole consumer worker.py:43, None-safe) |
| Ready log shows resolved model + reason | PASS |

- Tests: 145/145 across 13 modules green (new resolver matrix 15/15); no regression.
- Security: no secrets; `resolved.reason` → dashboard via Jinja2 autoescape (no XSS); operator-trusted env boundary. Note: `.claude/rules/security.md` absent in repo — general secure-coding checks applied.
- OpenCode "major" items dissolved on verification (no other WHISPER_MODEL consumers; test env-dependence documented + consistent with `_runner` convention).

**Minor follow-ups (non-blocking)**
1. `BATCH_WHISPER_MODE` is read at import time, before `load_dotenv()` — works only as a real env var, not from `.env` (consistent with pre-existing `WHISPER_MODEL` behavior). → README doc note added post-review.
2. Invalid-mode error shows normalized value, not raw input (diagnostic nit).
3. CPU-degrade ready log contains two em-dashes (cosmetic).

## Deploy
<!-- deploy が記入 -->
