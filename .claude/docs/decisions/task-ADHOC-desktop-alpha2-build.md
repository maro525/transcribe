# Task: ADHOC — v0.1.0-alpha.2: Windows デスクトップ初回ビルドとインストーラ添付リリース

## Meta
- linear_id: ADHOC
- tier: L
- created: 2026-08-02
- status: done

## Brief

### Current State
- `main` は PR #17 マージ点 `e9e5ab1`。Tauri v2 Windows x64 シェル、PowerShell パッケージング、4 ジョブ構成の Windows workflow は実装済みだが、Phase B/C は未検証。
- workflow は `python-tests` (Ubuntu) → `backend-archive` (Windows) → `tauri-build` (Windows) → `e2e-smoke` (Windows, `continue-on-error`)。dispatch は artifact のみ、`v*` タグでは backend zip/sha256/manifest と NSIS exe を Release に添付する。
- `v0.1.0-alpha.1` は `e9e5ab1` に存在するが asset なし。2026-08-04 の初回実行 #30920957454 は `make-lock.ps1` の Windows PowerShell 5.1 文字コード由来 parser error で停止し、pin/lock の本来のガードまで到達していない。
- blocker は (1) python-build-standalone pin、(2) BtbN FFmpeg pin、(3) Windows lock、(4) Rust/Tauri 初回コンパイル修正、(5) shell/app version が現在 `0.1.0` のまま、(6) tag build の release 作成・pre-release 指定が暗黙的、の6群。

### Goal
GitHub Actions の Windows CI を dispatch で安全に収束させた後、検証済み commit に `v0.1.0-alpha.2` を付け、公開 pre-release に backend runtime 一式と NSIS installer を添付する。

### Scope
- pin/lock の実値化と再現可能な検証、PowerShell 5.1 互換修正。
- Rust crate の Windows compile/check/build エラーを CI ログ駆動で修正し、`Cargo.lock` を確定。
- workflow の dispatch 検証経路と tag release 経路を分離し、alpha.2 の version/release metadata を整合。
- 必須 gate は Python tests、backend archive + relocation smoke、FFmpeg license checks、NSIS build、asset 完全性。現行 `e2e-smoke` は非 blocking のまま結果を記録する。

### Constraints
- Linux/WSL2 から Windows 実行確認はできない。Windows 固有挙動の成功は Actions の証跡がある場合のみ主張する。
- タグは CI 収束前に作らない。既存 alpha.1 は変更しない。alpha.2 タグの付け替え/force push はしない。
- public GitHub Release が backend 初回ダウンロード URL の契約。v1 は無署名で updater artifact なし。
- 外部 asset は dated immutable URL と upstream checksum、実ダウンロード SHA-256 の一致で pin する。

### Success Criteria
1. dispatch run で `python-tests` / `backend-archive` / `tauri-build` が success、NSIS artifact が取得可能。
2. backend manifest の version/URL/SHA/size と release asset が一致し、FFmpeg LGPL 検査・runtime relocation smoke が通る。
3. `v0.1.0-alpha.2` tag build が必須3ジョブ成功（experimental E2E は結果を明記）。
4. `v0.1.0-alpha.2` GitHub Release が public pre-release で、backend zip、`.sha256`、manifest、NSIS `.exe` を保持する。
5. release asset を再取得して SHA/名称/manifest URL を検証し、CI run URL と未検証の実機項目を記録する。

## Decision Log
- [orchestrate] DECISION: tier=L（リリースエンジニアリング: pin 生成・Rust 修正・CI イテレーション・リリース添付。10+ ファイル・外部検証ループ）
- [orchestrate] DECISION: 完了条件は「Windows CI 完走 → NSIS インストーラを v0.1.0-alpha.2 リリースに添付」まで
- [orchestrate] DECISION: 前提 — PR #17 マージ済み（Tauri スキャフォールド）。pin 3件はプレースホルダ、Rust は未検証（windows-rs 0.61 等の機械的修正が必要と明記済み）
- [orchestrate] DECISION: 環境制約 — 開発機は Linux/WSL2 で Windows ビルド不可。GitHub Actions（windows-2022）でビルドし、CI 経由で検証ループを回す。workflow_dispatch / v* タグでトリガー可能
- 2026-08-09 [startproject] PRE: main/e9e5ab1、既存 desktop workflow、packaging pin/lock、Rust shell、alpha.1 run/release を調査して計画開始。Linear ID はローカル識別子 `ADHOC` のため Linear MCP 投稿をスキップする。
- 2026-08-09 [startproject] DECISION: release tag は最終 publish trigger とし、修正ループは `workflow_dispatch` で先行する。alpha.2 tag は必須ジョブ成功見込みが立つまで作成しない。
- 2026-08-09 [startproject] DECISION: lock の第一選択は Windows runner 上の既存 `pip-tools==7.4.1` 生成。Linux の `uv pip compile --python-platform x86_64-pc-windows-msvc --python-version 3.12 --generate-hashes` は事前診断/候補生成に使えるが、既存 `make-lock.ps1 -Check` と出力形式・index semantics が異なるため、そのまま SSoT にしない。
- 2026-08-09 [startproject] DECISION: Rust は Linux cross-check を補助に限定する。MSVC SDK/Windows COM/WASAPI/NSIS を必要とするため、Windows CI の `cargo check --target x86_64-pc-windows-msvc` を build 前に独立 step として置き、短い修正ループを作る。
- 2026-08-09 [startproject] DECISION: tag build 前に `Cargo.toml` と `tauri.conf.json` を `0.1.0-alpha.2` に同期し、確定した `Cargo.lock` を commit する。
- 2026-08-09 [startproject] DECISION: release は tag build より先に draft pre-release を明示作成し、workflow は既存 release へ asset を upload する。全必須 asset 検証後に publish することで、途中失敗の空/部分 release 公開を防ぐ。
- 2026-08-09 [startproject] DECISION: 現行 E2E は dispatch manifest が `UNRELEASED-DEV-BUILD` を指すため first-run health check を完遂できない。alpha.2 の blocking 条件は必須3ジョブと asset 検証とし、E2E は best-effort の結果を release notes に明記する。
- 2026-08-09 [startproject] DECISION: 設計 subagent 用 `task` tool は本セッションで提供されていないため起動不可。独立性の代替として既存設計記録、実 run #30920957454、公式一次情報を突合し Lead が設計した。
- 2026-08-09 [startproject] POST: Brief/Design/実装計画と Current Project を更新。Linear 投稿は `ADHOC` ローカル運用指定によりスキップ。解釈は一意で dispatch-first が安全面で支配的なため Gate 1 は自動承認（発動なし）。
- 2026-08-09 [team-implement] POST: feature/desktop-alpha2-build で plan 1–7 を実施。Windows lock bootstrap #31265467430 は成功し、実生成 lock を commit。full dispatch #31265944707 を開始済み。tag は作成していない。Linear `ADHOC` は MCP 上で存在しないため開始コメント投稿は失敗し、ローカル task file に記録した。
- 2026-08-09 [team-implement] POST: plan 8–11 を完了。final dispatch #31287481691 は Python/backend/NSIS の必須3ジョブと dispatch setup smoke が success。backend manifest と NSIS artifact を download して SHA/filename/size/URL contract を検証済み。tag は作成していない。
- 2026-08-09 [team-implement] POST: review B1 を修正。softprops/action-gh-release を full SHA `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` (v2.6.2) に pin し、workflow default を `contents: read`、tag-only release-upload job のみ `contents: write` に限定。first-party actions も full SHA pin。dispatch #31288562702 の必須3ジョブと setup smoke は success、tag-only release upload は skipped。tag は作成していない。
- 2026-08-09 [team-review] POST: **FAIL**。Windows CI/build・manifest 契約・draft release gate は確認できたが、release write 権限を持つ workflow が mutable tag の third-party action `softprops/action-gh-release@v2` を実行する supply-chain blocker を検出。full commit SHA pin（併せて job-level 最小権限化）後に再レビューが必要。Linear `ADHOC` は MCP 上に存在せず、開始/結果コメント投稿はいずれも失敗したため本ログで代替。
- 2026-08-09 [team-review re-review] POST: **PASS**。B1 は解消済み。release action `3bb12739...` が upstream v2.6.2 commit と一致すること、workflow default `contents: read`、tag-only `release-upload` のみ `contents: write`、draft+pre-release gate、主要 artifact actions の full SHA pin を確認。再検証 run #31288562702 は必須3ジョブと setup smoke が success、release-upload は dispatch で意図どおり skipped。critical/major の新規指摘なし。Linear `ADHOC` は MCP 上に存在せず、開始/結果コメント投稿はいずれも失敗したため本ログで代替。

## Design

### Research
- **python-build-standalone:** 最新調査時点の dated release は `20260807`、候補は `cpython-3.12.13+20260807-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`、GitHub asset digest は `18bcc65b17921806b72cdc88bcf000bf67a2c99a8fc381fe1629f2b9ba56858d`。同 release の `SHA256SUMS` と実ダウンロード digest も照合してから pin する。出典: https://github.com/astral-sh/python-build-standalone/releases/tag/20260807 および https://github.com/astral-sh/python-build-standalone/releases/download/20260807/SHA256SUMS
- **BtbN FFmpeg:** dated tag `autobuild-2026-08-02-13-17` の安定系列候補は `ffmpeg-n7.1.5-12-g1fdbca85aa-win64-lgpl-7.1.zip`、upstream `checksums.sha256` の digest は `7d6b66be8bafc839b15e66a2393d3cbbccee462e2388a1ce75a03dd3856ac453`。`lgpl-shared`/GPL/N nightly は不採用。出典: https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-08-02-13-17 および https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-02-13-17/checksums.sha256
- **uv cross-resolution:** 現行 uv CLI は `--python-platform x86_64-pc-windows-msvc`、`--python-version`、`--generate-hashes`、`--torch-backend cpu` を提供し Linux から Windows 候補を解決できる。ただし release lock は Windows で install/import まで検証する。出典: https://docs.astral.sh/uv/reference/cli/#uv-pip-compile および https://docs.astral.sh/uv/pip/compile/
- **Tauri/GitHub release:** Tauri 公式 pipeline は workflow dispatch と tag/release artifact upload を分離可能で、release draft/pre-release を明示できる。出典: https://v2.tauri.app/distribute/pipelines/github/

### CI / Release Architecture
1. **Prepare locally:** PowerShell scriptsを UTF-8 BOM/非ASCII依存から外し、pin を upstream checksum で確定。uv cross-lock を診断に用い、Windows lock-generation job で正式 lock artifact を作る。
2. **Bootstrap lock:** placeholder の間は `-Check` だけでは前進不能なので、一時的/明示的な dispatch input または専用 job で Windows 上 `make-lock.ps1` を生成・artifact 化する。内容を review して commit した後、通常の `-Check` gate に戻す。
3. **Fast compile loop:** backend の巨大 zip 生成と分離して Windows `cargo check --locked` を先に実行。main.rs の既知構造エラー、windows-rs/webview2-com/cpal API、wry の型 version coupling をログ順に修正する。
4. **Full dispatch:** committed lock/pins/Cargo.lock で backend relocation smoke、FFmpeg license validation、NSIS build を完走し、workflow artifacts を手元でも検証する。
5. **Release transaction:** alpha.2 version commit → draft pre-release 作成 → immutable tag push → tag workflow → assets/manifest checksum 検証 → release publish。失敗時は同じ tag を動かさず、tag workflow の rerun または未タグ commit で修正し、必要なら alpha.3 に進む。

### Rust Verification Layers
- Linux: `cargo fmt --check`、target-independent tests、可能なら `cargo check` で cfg 非依存エラーを早期発見。ただし Windows成功の証拠にはしない。
- Windows compile: `cargo check --target x86_64-pc-windows-msvc --locked`、`cargo tree -p wry` で `windows` / `webview2-com` 整合確認。
- Windows package: staged resources の存在確認後 `cargo tauri build --bundles nsis --locked`。
- Runtime: silent install と起動。first-run download は release asset がある tag run でのみ意味があるため、dispatch E2E の期待値を setup-screen smoke と tag E2E に分離する。

### Failure Loop Policy
- 各 run は最初の root cause だけを直し、run URL・job・error・修正 commit を Implementation Notes に追記する。
- network/transient failure は code change 前に rerun。checksum/API/compiler failure は再現可能な修正を commit。asset/version 契約変更は manifest/downloader/workflow を一体で更新。
- tag 作成後のコード修正で tag を移動しない。公開済み/配布済み asset の上書きもしない。

## Implementation Plan
1. [ ] alpha.1 run #30920957454 の PowerShell 5.1 parser error を回帰テスト化し、packaging scripts を Windows PowerShell 5.1 で parse 可能にする。
2. [ ] Python 3.12.13/20260807 archive を取得し SHA256SUMS + local SHA を照合して `python-pin.json` を更新する。
3. [ ] BtbN dated n7.1 LGPL static archiveを upstream checksum + local SHA で照合し `ffmpeg/pin.json` を更新する。
4. [ ] Windows lock bootstrap 経路を workflow に追加し `requirements-windows.lock` を生成、CPU-only/全 hash/wheel availability を review・commitする。
5. [ ] workflow に軽量 Windows Rust check job/step、`--locked`、診断 artifact/log を追加する。
6. [ ] known main.rs state/controller mismatch を直し、順次 windows-rs 0.61、webview2-com 0.37/wry、cpal、Tauri API error を CI で解消する。
7. [ ] `cargo tree` で dependency coupling を確定し `Cargo.lock` を commit、gitignore/docs を更新する。
8. [ ] backend build/relocation、FFmpeg validation/resource staging、NSIS build の順に dispatch を反復して必須3ジョブを green にする。
9. [ ] dispatch E2E を setup smoke と tag-only first-run smoke に整合させ、blocking/non-blocking 条件を workflow 上で明示する。
10. [ ] Cargo/Tauri version を `0.1.0-alpha.2` に同期し、release workflow が draft pre-release を誤って通常 release として作らないことを保証する。
11. [ ] clean commit で最終 dispatch、artifact SHA/installer filename/manifest URL を検証する。
12. [ ] `v0.1.0-alpha.2` draft pre-release を作成し同 commit に immutable tag を push、tag workflow 完走を監視する。
13. [ ] release の backend zip/.sha256/manifest/NSIS を再取得検証し、pre-release を publish。run URL、assets、experimental E2E、未実機検証項目を記録する。

## Implementation Notes

### 実装サマリー
- Python 3.12.13/20260807 と BtbN LGPL FFmpeg n7.1.5 を upstream checksum とローカル SHA-256 の双方で照合し pin 化。
- Windows PowerShell 5.1 parser、script path、pip-tools/pip 互換性、lock header 比較を修正し、Windows-only lock bootstrap を追加。
- Windows 生成 `requirements-windows.lock`、alpha.2 version 同期、既知の Tauri AppState/capture 初期化と shutdown state 修正を commit。

### 変更ファイル
- `.github/workflows/desktop-windows.yml` — lock bootstrap / parser gate / dispatch job 分岐。
- `packaging/backend/*` — pin、Windows lock、PS 5.1 と resolver 互換性。
- `packaging/ffmpeg/*` — dated LGPL pin と PS 5.1 互換性。
- `src-tauri/{Cargo.toml,tauri.conf.json,src/main.rs,Cargo.lock}` — alpha.2 と shell state 修正。

### テスト
- Windows lock bootstrap: https://github.com/maro525/transcribe/actions/runs/31265467430 — Python tests と Windows dependency lock bootstrap success。
- #31265944707 は Python dependency install が進行しないため cancel（transient/hung runner）。
- #31266036663 — Python tests / lock check は success、backend relocation smoke が PowerShell による Python sentinel quote 脱落で失敗。`1d02f43` で修正。
- #31287481691（final dispatch）: https://github.com/maro525/transcribe/actions/runs/31287481691 — `python tests` / `backend archive` / `tauri NSIS build` はすべて success。dispatch setup smoke も success、tag-only first-run smoke は意図どおり skipped。
- #31286093210: https://github.com/maro525/transcribe/actions/runs/31286093210 — `python tests` / `backend archive` / `tauri NSIS build` success。E2E は `continue-on-error` のため failure でも gate 外（dispatch manifest の `UNRELEASED-DEV-BUILD` を取得できないことが理由）。
- Artifact verification (#31286093210): installer `Transcribe_0.1.0-alpha.2_x64-setup.exe`, SHA-256 `cbf0d73685db12c1f3c62d34fbf4454d3b56b58319635a6eb0a40aadbcb87036`, 37,048,028 bytes。backend `transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`, SHA-256 `f107a973092591ca4c705fd1a8b2ef2ddfb3562743eac95295b1fe9ebd69ebb5`, 424,363,082 bytes。manifest の version/size/SHA と `.sha256` sidecar は一致し、dispatch URL は `https://github.com/maro525/transcribe/releases/download/UNRELEASED-DEV-BUILD/transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`（tag build では同じ release の tag URL に解決）。
- Final artifact verification (#31287481691): installer `Transcribe_0.1.0-alpha.2_x64-setup.exe`, SHA-256 `0284eb31aae845c3c4f3bd24d4b9c9c43900323a48d2532d531974a902940544`, 37,039,665 bytes。backend `transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`, SHA-256 `623e5028ef56e78e9f54fe5d81a45b952a540cfae2ed7773fc15933224c095c1`, 424,363,083 bytes。manifest version/size/SHA と `.sha256` sidecar は archive 実測と一致。dispatch manifest URL は `https://github.com/maro525/transcribe/releases/download/UNRELEASED-DEV-BUILD/transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`; tag build は `v0.1.0-alpha.2` release URL を使用する。

### Failure Loop
- #31264984475: PS 5.1 non-ASCII parser failure → `4aef0a8`, `d2e6617`。
- #31265104661 / #31265339224: PowerShell parameter-path resolution → `e7b7fe6`, `678d1bc`。
- #31265339224: pip-tools 7.4.1 vs pip 25 → `b3f38e1`。
- #31265706105: volatile temporary output path in lock header → `ea3150e`。
- #31266036663 / backend archive / `print("RELOCATION_SMOKE_OK", ...)` が PowerShell quote で崩れて `NameError` → `1d02f43`。
- #31267509404 / NSIS build / webview2-com 0.37 と wry 0.55 の 0.38 bindings が混在 → `6d67f2b`（0.38 に整合、public `take_pwstr`、i64 token）。

### 残課題・注意点
- dispatch E2E は silent-install setup smoke と tag-only first-run health smoke に分離し、workflow 上で明示。E2E は continue-on-error の非 blocking job。
- tag asset upload は deploy が作成した draft pre-release かつ pre-release であることを workflow が確認してから実行する。tag/release 作成・publish は deploy phase のみが行う。
- plan 8–11 完了。最終 dispatch の必須3ジョブと installer artifact を確認済み。tag/release は deploy phase のみが行う。
- [team-implement] Review B1 remediation: default workflow permission is `contents: read`; only the tag-only `release-upload` job receives `contents: write`. `softprops/action-gh-release` is pinned to `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` (v2.6.2), and first-party checkout/setup-python/artifact actions are full-SHA pinned. The release-upload job verifies deploy's draft pre-release before upload. Revalidation #31288562702: required three jobs and dispatch setup smoke succeeded; `release-upload` was skipped on dispatch as intended.

## Review

### 判定: PASS（再レビュー 2026-08-09）

> 初回判定は B1 により FAIL。`e96a951` の修正と run #31288562702 を再検証し、blocker 解消を確認した。以下の初回レビュー記録は監査証跡として保持する。

### コードレビュー統合結果

#### Quality Reviewer
- [minor] `.github/workflows/desktop-windows.yml:4-5`、`src-tauri/Cargo.toml:3-5`、複数 Rust module の冒頭コメントが「未実行・未コンパイル」のままで、final Windows run #31287481691 の成功証跡と齟齬がある。保守時の判断を誤らせるため、実機未検証項目（WASAPI/WebView2 first-run 等）だけに限定して更新すること。
- [minor] Windows 生成 `requirements-windows.lock` は CRLF のため `git diff --check` が大量の trailing whitespace として報告する。lock 自体の動作には影響せず、`make-lock.ps1 -Check` と backend build は CI で成功している。

#### Logic Reviewer
- [minor] manifest 契約は整合している。tag `v0.1.0-alpha.2` では backend version `cpu-0.1.0-alpha.2`、asset 名 `transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`、同一 release URL、実 SHA-256/size が downloader の HTTPS・size・SHA-256 検証と一致することを静的確認した。
- [minor] release transaction は draft pre-release の存在確認後だけ asset upload し、公開は deploy phase に分離されている。途中失敗で部分 release が公開される経路は確認されなかった。tag-only first-run smoke は draft asset が public download できない可能性を含むため非 blocking のままであり、publish 後の再取得検証が必須。

#### Security Reviewer
- [major] `.github/workflows/desktop-windows.yml:35-36,177-184,308-312` — workflow 全体に `contents: write` を付与した状態で、full commit SHA ではなく mutable tag の third-party action `softprops/action-gh-release@v2` を実行している。upstream tag の侵害・移動時に release write token と生成 artifact を奪われ、release asset 改ざん/公開につながる supply-chain blocker。既知の commit SHA に pin し、可能なら build/test は `contents: read`、release upload を行う job/step のみ `contents: write` に分離すること。
- [minor] python-build-standalone / FFmpeg は dated URL + pinned SHA-256 で取得し、backend archive は downloader が size + SHA-256 を検証する。FFmpeg は LGPL 表示、GPL/nonfree/x264/x265 flag、LICENSE/SOURCE 同梱を CI で検査しており、対象範囲に checksum bypass や hardcoded secret は確認されなかった。
- [minor] `.claude/rules/security.md` はリポジトリに存在しなかったため、指定された認証・入力検証・secret・injection・supply-chain 観点で代替レビューした。

#### Simplify Reviewer
- [minor] backend と installer で draft/pre-release 検証 + upload が重複しているが、job dependency と成果物所有が明確で、現時点で共通化する方が workflow を複雑化する。重複は許容範囲。
- [minor] lock bootstrap と通常 build の条件分岐、dispatch setup smoke と tag-only first-run smoke の分離にはそれぞれ failure loop と公開 asset 契約上の理由があり、デッド経路とは判定しない。

#### 統合サマリー
- release transaction、version/asset/manifest 契約、Windows lock/pin、PowerShell 5.1 parser 対応、Cargo.lock/webview2 coupling は CI 証跡と差分上で整合している。
- Security と Quality の観点を統合し、write token を渡す mutable third-party action を release blocker（major）と判定した。これが唯一の FAIL 条件。

### 動作検証結果

#### ブラウザ表示確認（該当時）
- 通常ブラウザ UI 変更はなし。Windows WebView2/初回 download は tag/release 前のため本環境では未確認。
- dispatch #31287481691 の NSIS silent install + setup smoke は success。tag-only first-run health smoke は設計どおり skipped。

#### テスト実行結果（該当時）
- `gh run view 31287481691 --json ...`: success。`python tests`、Windows lock check/backend relocation、FFmpeg verification、`cargo check --target x86_64-pc-windows-msvc --locked`、NSIS build、silent install がすべて success。CI head `0c686584...` から review head `e784e89...` までの差分は task document のみ。
- `cargo fmt --manifest-path src-tauri/Cargo.toml -- --check`: success。
- `cargo check --manifest-path src-tauri/Cargo.toml --locked`: Linux host の GLib/GObject system package 不在で失敗。Windows target の成否は上記 CI success を採用。
- `python -m pytest -q`: host に `python` executable がなく未実行。上記 CI の pytest step success を採用。
- `git diff --check main...feature/desktop-alpha2-build`: Windows lock の CRLF を trailing whitespace として報告。コード差分の機能 failure ではない。

### 申し送り事項（minor）
- blocker 修正後、同一 commit で dispatch を再実行し、必須3ジョブ success を確認してから immutable tag を作る。
- deploy は draft pre-release → tag push → tag workflow → 4 asset の名称/SHA/size/manifest URL 検証 → publish の順序を維持し、tag を移動しない。
- publish 後に tag-only first-run download、`/healthz`、終了時 orphan process、WASAPI/WebView2 microphone を実機確認する。
- stale な「未検証」コメントを CI 検証済み範囲と実機未検証範囲に整理する。

### 再レビュー結果（B1 remediation）

#### 判定: PASS

- [resolved] `softprops/action-gh-release` は full SHA `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` に固定され、GitHub API で upstream `v2.6.2` tag が同 commit を直接指すことを確認した。
- [resolved] workflow default は `contents: read`。`contents: write` は `if: github.ref_type == 'tag'` の独立 `release-upload` job のみに限定され、build/test jobs は write token を受け取らない。
- [resolved] release upload 前に `gh release view` の `isDraft == true` かつ `isPrerelease == true` を検証し、失敗時は upload step に到達しない。backend/installer upload を1 jobに集約したため、検証と upload の境界も明確になった。
- [resolved] checkout、setup-python、upload-artifact、download-artifact は全て40桁 SHAに固定され、各 commit の存在を GitHub API で確認した。
- [verified] run #31288562702（code commit `e96a951`）は python tests、backend archive/relocation、Windows locked Rust check、NSIS build、silent install/setup smoke が success。dispatch のため tag-only `release-upload` と first-run smoke は意図どおり skipped。
- [verified] review head `e2a758a` と CI commit `e96a951` の差分は task document のみ。再レビュー対象 workflow は CI 実行時と同一。
- [result] critical / major はゼロ。新規 blocker なし。初回 minor（stale コメント、CRLF、publish 後 first-run/実機確認）は申し送りとして継続する。

## Deploy

### デプロイ結果: SUCCESS

### 実行内容
- デプロイ日時: 2026-08-10T02:00:00Z
- feature ブランチ: `feature/desktop-alpha2-build`
- PR: https://github.com/maro525/transcribe/pull/20（merge commit `5a42ebe086741dedd292d41559ac54d08197e71a`）
- immutable tag: `v0.1.0-alpha.2`（annotated tag `512a0ed8aef93efd535c717a9ba1016b2ddd3952`、target `5a42ebe086741dedd292d41559ac54d08197e71a`）
- tag workflow: https://github.com/maro525/transcribe/actions/runs/31289158254
- release: https://github.com/maro525/transcribe/releases/tag/v0.1.0-alpha.2（published pre-release）
- tag workflow の build/test jobs は success。`release-upload` は checkout 不在で `gh release view` が repository context を解決できず失敗したため、deploy が同 run の artifact を検証して draft release へ手動 upload し、その後 publish した。

### デプロイ後検証結果

#### スモークテスト
- Python tests、backend archive/relocation、Windows locked Rust check、NSIS build、silent install は tag workflow で success。
- tag-only first-run smoke は asset upload 前に実行されたため failure。experimental/non-blocking として release notes に記録。実機での初回 download、`/healthz`、orphan process、WASAPI/WebView2 microphone は継続して未検証。

#### 公開 asset 検証
- `Transcribe_0.1.0-alpha.2_x64-setup.exe`: `37,040,244 bytes`、SHA-256 `cf601a27e9b046eb5c257d927202c53db505fb50973aa490843d16b706feb5f8`。
- `transcribe-backend-cpu-0.1.0-alpha.2-win64.zip`: `424,363,084 bytes`、SHA-256 `44f53fdeebb0f7521436b326651eb3a294d00bbfd0ea58e212bda59065d2ebd1`。
- `.sha256` sidecar、manifest の SHA-256 / size、manifest URL `https://github.com/maro525/transcribe/releases/download/v0.1.0-alpha.2/transcribe-backend-cpu-0.1.0-alpha.2-win64.zip` は backend zip の公開後再ダウンロード実測と一致。
- Implementation Notes の `#31287481691` にある installer `0284eb31...` / backend `623e5028...` は B1 remediation 前の別 commit に由来する参考値であり、tag run asset の期待値には用いない。

### 申し送り事項
- `v0.1.0-alpha.2` tag は immutable のまま維持し、公開 asset を上書きしていない。
- 次回 release 向けに release-upload repository-context 修正を PR #21 として merge 済み: https://github.com/maro525/transcribe/pull/21（merge commit `56b3d2e990967449825d9c824f2edbeed38bedfc`）。`gh release view` に `--repo "${{ github.repository }}"` を指定し、checkout 非依存にした。
- team-review minor: stale verification comments の整理、CRLF lockfile の `git diff --check` ノイズへの注意、実機 E2E の実施を推奨。

### Decision Log
- [deploy] POST: draft pre-release → immutable tag → tag workflow → artifact 自己整合性検証 → 手動 upload →公開後再ダウンロード検証 → publish を完了。Linear ID `ADHOC` はローカル運用のため投稿・ステータス更新をスキップ。
