# Task: LOCAL-wordnetwork-20260706 — ライブ音声のキーワード・ネットワーク可視化（言葉のネットワーク）

## Meta
- linear_id: LOCAL-wordnetwork-20260706（ローカルタスク。Linear 連携なし）
- tier: M
- created: 2026-07-06
- status: reviewed (PASS — deploy 待ち)
- base_branch: feature/moonshine-live-engine
- branch: feature/live-word-network（実装時に分岐）
- deploy: PR 作成まで（Linear 投稿なし）

## Tier Rationale
- Files: 6〜8（graph.py 新規 / session.py / config.py / live.html / テスト2件 / README / 本ファイル）→ M
- Complexity: バックエンドのイベント拡張 + フロント force-directed 描画の複数パターン → M
- Risk: ライブセッションの broadcast 経路に追加（既存バッチ・WS 既存イベントは不変更）→ Medium
- Hard Trigger: 該当なし（新規コア依存の追加なし — 可視化は vanilla JS 自作）

## Brief

### Current State
- ライブモード: `src/live/`（engine / engine_moonshine / vad / streaming / session / keywords）+ `live.html` + `WS /live/ws`。
- キーワード: final 確定のたびに `session.py::_on_worker_message` が全 final テキスト連結に対し
  `extract_keywords(text, limit=LIVE_KEYWORD_LIMIT)` を再計算し、`{"type":"keywords","items":[{word,score}]}` を broadcast。
- **既存 `keywords` イベントは全文書累積 top-N のみで、発話単位のキーワード集合を持たない**
  → 共起（co-occurrence）エッジは既存イベントだけでは構築不可。バックエンド拡張が必要。
- replay（再接続復旧）: status + final 履歴（ring buffer 100 件）+ keywords 1 件を送信。
- live.html の WS ハンドラは if/else チェーンで未知 type を無視 → 新イベント追加は後方互換。

### Goal
- ライブ録音中、final 確定のたびに重要キーワードがノードとして追加され、同一発話内で共起した
  語同士がエッジで結ばれる力学グラフ（word network）を live.html 上にリアルタイム表示する。

### Scope
- In: `src/live/graph.py`（新規: 共起グラフ集約）、`src/live/session.py`（graph イベント発行・replay・リセット）、
  `src/config.py`（LIVE_GRAPH_* 設定）、`src/web/templates/live.html`（canvas 力学描画パネル）、
  `tests/test_live_graph.py`（新規）、`tests/test_session.py`（graph broadcast/replay の検証追加）。
- Out: `src/live/keywords.py` / `tests/test_keywords.py`（無変更 — 下記 Constraints 参照）、
  既存バッチパイプライン、既存 WS イベント（status/partial/final/keywords/finalized/error）の変更、
  エンジン・VAD・streaming。

### Constraints
- CPU-first: サーバ側追加コストは final ごとの extract_keywords 1 回 + 小さな dict 更新のみ。
  描画（force simulation）は全てブラウザ側。ノード上限 40 で O(n²) 反発計算も軽量。
- 依存追加なし: d3-force 等は導入せず、vanilla JS + Canvas で力学配置を自作（live.html 内にインライン。
  既存の AudioWorklet インライン方式と同パターン。CDN 直リンクなし）。
- **本実行環境の権限設定で `src/live/keywords.py` / `tests/test_keywords.py` が読み取り不可**
  （`*key*` パターンの deny と推定）。設計はこれら 2 ファイルに一切触れない構成とする。
  `extract_keywords(text, limit) -> list[Keyword(word, score)]` の API は session.py の使用箇所から確定済み。
- GPU / 依存パッケージ / モデル重みなしの環境 → 検証は numpy のみで動くユニットテスト +
  py_compile。ブラウザ実描画・実音声は未実施検証として明記。

### Success Criteria
- final 確定ごとに `graph` イベント（nodes/edges スナップショット）が broadcast され、
  再接続時 replay にも最新 graph が含まれる。セッション開始でグラフがリセットされる。
- ノード上限（既定 40）超過時に低重み・古いノードから間引かれ、孤立エッジが残らない。
- 既存テスト全件 + 新規テストが numpy のみの環境で PASS。keywords.py は無変更。

## Design

### 1. エッジ定義（共起）
- 同一 final（確定発話）テキストに対し `extract_keywords(final_text, limit=LIVE_GRAPH_WORDS_PER_FINAL 既定 6)`
  を実行し、得た語集合の全ペアを共起エッジとする。エッジ重み = 共起回数（累積）。
- ノード重み = その語が現れた final 回数（累積）。`last_seen`（final 連番）を保持し減衰表示に使う。
- 判断: 既存 `keywords` イベントは per-utterance 情報を持たないため不十分 → バックエンドで
  per-final 抽出を追加する（案 b を採用）。クライアント側での日本語トークナイズ再実装（案 a）は却下。

### 2. 新規モジュール `src/live/graph.py`
```
class CooccurrenceGraph:
    def __init__(self, max_nodes: int) -> None
    def add_utterance(self, words: list[str]) -> None   # ノード/エッジ重み更新 + 上限剪定
    def snapshot(self) -> dict  # {"type":"graph","seq":int,"nodes":[{"id","weight","last_seen"}],"edges":[{"a","b","weight"}]}
    def reset(self) -> None
```
- 剪定: max_nodes 超過時、(weight 昇順, last_seen 昇順) で除去。除去ノードに接続するエッジも除去。
- スレッド安全性は session.py の既存 `self._lock` 内で操作するため不要（keywords 再計算と同じ場所）。

### 3. session.py 統合
- `start()`: graph reset。
- `_on_worker_message()`: final かつ text ありのとき、既存 keywords メッセージに加え
  per-final `extract_keywords` → `graph.add_utterance` → `graph.snapshot()` を extra broadcast。
- `replay()`: final 履歴・keywords に続けて最新 graph snapshot を送信（グラフがあれば）。

### 4. WS プロトコル拡張
- 新イベント `{"type": "graph", ...}` を追加（フルスナップショット方式）。
  ノード ≤40・エッジ ≤ 数百で毎回全量送信でも数 KB。差分プロトコルの複雑さを回避し、
  再接続復旧も snapshot 1 発で完結（既存 keywords イベントと同じ方針）。
- 既存イベントは無変更。旧クライアントは未知 type を無視するため後方互換。

### 5. フロントエンド（live.html 内パネル）
- 配置: 既存 `.layout`（transcript + keywords）の下に全幅の「言葉のネットワーク」パネル
  （`<canvas>` + 折りたたみトグル）。新規ビュー /live/graph は作らない（1 画面で完結、状態共有不要）。
- 描画: vanilla JS の force-directed 自作（反発 + エッジばね + 中心引力 + 速度減衰、
  requestAnimationFrame）。ノード半径/ラベル濃度 = weight、透明度 = last_seen の古さで減衰。
- 省電力: エネルギー低下時にシミュレーション停止（新 graph イベントで再加熱）、
  `document.hidden` 中は描画停止、パネル折りたたみ中は完全停止。
- graph イベント受信時: 既存ノードの位置・速度を維持したまま重み更新、新ノードは
  既存接続ノードの近傍に出現。セッション切替（session_id 変化）でクリア。

### 6. 設定（src/config.py）
- `LIVE_GRAPH_WORDS_PER_FINAL`（既定 6）: per-final 抽出語数上限。
- `LIVE_GRAPH_MAX_NODES`（既定 40）: グラフノード上限。

## Plan (Tasks)
1. ブランチ `feature/live-word-network` を `feature/moonshine-live-engine` から作成
2. `src/config.py` に LIVE_GRAPH_* 追加
3. `src/live/graph.py` 新規実装（CooccurrenceGraph）
4. `src/live/session.py` 統合（reset / per-final add + broadcast / replay）
5. `tests/test_live_graph.py` 新規（共起重み・剪定・snapshot 形式・reset）
6. `tests/test_session.py` に graph broadcast / replay / セッションリセットの検証追加
7. `src/web/templates/live.html` にパネル + force layout + graph ハンドラ実装
8. README のライブモード節に 1 段落追記
9. 検証: 全テスト実行（tests/_runner.py 方式）+ py_compile

## Verification Plan
- 実施可能: `python3 tests/test_live_graph.py` / `test_session.py` / 既存全テスト（numpy のみで可）、
  `python3 -m py_compile`（graph.py / session.py / config.py）、live.html の構文目視 + node なし環境のため
  JS はレビューで確認。
- 未実施（環境制約により引き継ぎ）: ブラウザでの実描画・force layout の視覚品質、
  実音声での per-final キーワード品質、Moonshine 実エンジンでの E2E。

## Decision Log
- 2026-07-06: エッジ = 同一 final 内共起、重み = 共起回数。バックエンドで per-final 抽出を追加（既存 keywords イベントのみでは不可と判断）。
- 2026-07-06: graph イベントはフルスナップショット方式（差分なし）。replay 復旧が単純になるため。
- 2026-07-06: 描画は vanilla JS + Canvas 自作。依存追加・CDN 回避、ノード ≤40 なら O(n²) で十分。
- 2026-07-06: 配置は live.html 内パネル（新規ビューなし）。
- 2026-07-06: keywords.py / test_keywords.py は無変更（本環境の権限 deny 対象でもある）。
- 2026-07-06 [team-implement] POST: 計画どおり 7 ファイル変更（graph.py / session.py / config.py / live.html /
  test_live_graph.py / test_session.py / README.md）で実装完了。全テスト PASS（45 件、keywords 除く）。
  session.py には `graph_max_nodes` コンストラクタ引数を追加（既存 `history_size` と同パターン、テスト注入用）。
  replay は空グラフを送らない仕様とした。Linear 連携なし（ローカルタスク）。
- 2026-07-06 [team-review] POST: 判定 PASS（0 critical / 0 major、minor 4 件は申し送り）。
  4 レビュアー実施、テスト 55/55 PASS、keywords.py 無変更を diff で確認。Linear 投稿なし（ローカルタスク）。

## Implementation Notes

### 実装内容（2026-07-06, branch: feature/live-word-network, base: feature/moonshine-live-engine @33f5c40）
- `src/config.py`: `LIVE_GRAPH_WORDS_PER_FINAL`（既定 6）/ `LIVE_GRAPH_MAX_NODES`（既定 40）を追加（env 上書き可）。
- `src/live/graph.py`（新規）: `CooccurrenceGraph`。`add_utterance` は語をデデュープ（順序保持・空文字除去）し、
  ノード重み += 1・`last_seen = seq`、全ペアのエッジ重み += 1。剪定は `(weight 昇順, last_seen 昇順, word)` の
  最小ノードを除去し、そのノードに接続するエッジも同時に除去（孤立エッジなし保証）。
  スレッド安全性は呼び出し側ロック前提（docstring 明記）。
- `src/live/session.py`: コンストラクタに `graph_max_nodes` 追加（`history_size` と同パターン、テスト注入可能）。
  `start()` で `_graph.reset()`。`_on_worker_message()` の既存 `self._lock` 内で
  `_graph_message_locked(text)`（per-final `extract_keywords(limit=LIVE_GRAPH_WORDS_PER_FINAL)` →
  `add_utterance` → `snapshot()`）を `extra` に追加 broadcast。`replay()` はノードが存在する場合のみ
  graph snapshot を末尾に追加。既存イベントは無変更（keywords.py にも無変更）。
- `src/web/templates/live.html`: `.layout` 直下に「言葉のネットワーク」折りたたみパネル
  （`#graph-canvas` + 空状態プレースホルダ + トグルボタン）。vanilla JS force-directed
  （O(n²) 反発 + エッジばね + 中心引力 + 減衰 0.85、devicePixelRatio 対応、requestAnimationFrame）。
  エネルギー < 0.05 / `document.hidden` / 折りたたみ中はループ停止、graph イベント・resize・展開で reheat。
  新ノードは既接続ノード近傍にスポーン、既存ノードは位置・速度維持。ノード半径 = weight、
  透明度 = `seq - last_seen` でフェード（下限 0.35）。描画色は `getComputedStyle(body).color`（ライト/ダーク対応）。
  WS ハンドラに `graph` 分岐追加。`clearTranscript()`（session_id 変化時に呼ばれる）から `clearGraph()` を呼ぶ。
- `tests/test_live_graph.py`（新規 9 件）: ペアエッジ生成 / 重み累積（順序非依存キー）/ 発話内重複 1 回・自己ループなし /
  剪定と孤立エッジ除去 / last_seen 更新 / 重みタイ時は古いノード優先剪定 / reset / 空・空白発話 no-op / 単語のみ発話。
- `tests/test_session.py`（追加 3 件）: final ごとの graph broadcast（エッジ参照整合含む）/
  replay に graph snapshot 1 件（`_graph` 直接シードで抽出品質に非依存）/ start でのグラフリセット。
- `README.md`: LIVE_* 設定表に 2 行追加、リアルタイムモード節にパネル説明 1 段落、WS エンドポイント説明に graph 追記。

### 検証結果（実施済み）
- `python3 -m py_compile src/live/graph.py src/live/session.py src/config.py` → OK
- `tests/test_live_graph.py` 9/9 PASS、`tests/test_session.py` 12/12 PASS（既存 9 + 新規 3）
- 既存無回帰: `test_engine_select` 10/10、`test_moonshine_chunking` 10/10、`test_streaming_worker` 6/6、
  `test_vad_segmenter` 8/8（numpy 1.26.4 のみの環境で実行）
- `tests/test_keywords.py` は権限 deny のため実行不可（keywords.py / test_keywords.py とも無変更なので回帰リスクなし）

### 未実施検証（環境制約 — レビュー/デプロイへ引き継ぎ）
- ブラウザでの実描画（force layout の視覚品質、折りたたみ・ダークモード・DPR スケーリング）
- 実音声での per-final キーワード品質（keywords.py が読み取り不可のため抽出内容は未確認）
- Moonshine 実エンジンでの E2E（GPU/依存/モデル重みなし）

### 実装上の注意点（レビュー向け）
- `graph` イベントは text ありの final ごとに必ず発行される（抽出語が 0〜1 個でも空/エッジなし snapshot を送る）。
  フロントは空 nodes を許容（プレースホルダ表示）。意図どおり（Success Criteria「final 確定ごとに broadcast」）。
- replay はノード 0 個のとき graph を送らない（新規接続時の無意味なメッセージ抑制）。

## Review

### 判定: PASS（2026-07-06、対象コミット 6883053、diff 33f5c40..6883053）
- 0 critical / 0 major。指摘は全て minor（申し送り）。
- レビュアー: Claude / OpenCode (gpt-5.5) / Security / Simplify。
- 制約遵守確認: `git diff --name-only` で `keywords.py` / `test_keywords.py` が変更なしを確認（両ファイルは読み取りもせず遵守）。

### 主な確認事項
- graph.py: dedup・canonical edge key・空発話早期 return・剪定の孤立エッジ除去・`max(1, max_nodes)` 防御まで正しい。
- session.py: broadcast 順序（final → keywords → graph）、snapshot の新規 dict 生成によりロック外 broadcast でもエイリアス競合なし。
- Security: XSS 経路なし（キーワードは Canvas `fillText` のみ、DOM 挿入なし）。新規エンドポイントなし・認証変更なし。
- テスト: 全 55/55 PASS（py_compile OK、test_live_graph 9、test_session 12、既存無回帰 34。keywords スイートは権限 deny で除外）。

### minor 申し送り（次タスク推奨）
1. 空キーワード final でも graph broadcast → フロントが不要な reheat（`msg.seq === graphSeq` でスキップ可）。自己収束するため minor。
2. 毎フレーム `getComputedStyle` → 開始時/テーマ変更時にキャッシュ可。
3. graph.py docstring: ノード重みは「現在保持中ノードに限る（剪定で履歴は失われる）」の補足を推奨。
4. graph 用 `extract_keywords` のロック外 hoist は任意最適化（現状は既存パターンと整合）。

### 未検証（環境制約 — 引き継ぎ）
- ブラウザ実描画（force layout 品質・折りたたみ・ライト/ダーク・DPR・長いラベルのはみ出し）。
- 実音声でのキーワード品質、Moonshine E2E。

## Deploy
<!-- deploy が記入。Linear 連携なし・PR 作成まで -->
