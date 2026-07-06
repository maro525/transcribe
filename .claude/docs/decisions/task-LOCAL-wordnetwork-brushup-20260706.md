# Task: LOCAL-wordnetwork-brushup-20260706 — ライブ「言葉のネットワーク」キーワード抽出・グラフ採用ルールのブラッシュアップ

## Meta
- linear_id: LOCAL-wordnetwork-brushup-20260706（ローカルタスク。Linear 連携なし — issue 作成・ステータス遷移・コメント投稿すべてスキップ）
- tier: M
- created: 2026-07-06
- status: implemented（Gate 1 承認済み 1A+2B+3A / team-implement 完了・review 待ち）
- base_branch: feature/live-word-network（言葉のネットワークの実体ブランチ）
- branch: feature/wordnetwork-brushup（feature/live-word-network から分岐済み）
- deploy: PR 作成まで（Linear 投稿なし）

## Tier Rationale
- Files: 約8〜9（src/live/terms.py 新規 / graph.py / session.py / config.py / requirements.txt / tests 3件 / README）→ M
- Complexity: 複数パターン（pluggable 抽出器 + グラフノード採用ルール変更）→ M
- Risk: Medium（ライブ機能の挙動変更のみ。バッチ・認証・DB・公開 API 変更なし）
- Hard Trigger 検討: janome 依存追加はコア依存ではない（純 Python / feature 局所 / janome 不在フォールバックあり）→ 自動 L 非適用。依存追加は Gate 1 承認対象。

## Brief

### Current State
- ブランチ `feature/live-word-network`（clean、タスクファイルのみ untracked）。janome は未インストール環境。
- `src/live/session.py` の `_graph_message_locked()` が final ごとに `extract_keywords(text, limit=LIVE_GRAPH_WORDS_PER_FINAL=6)`（`src/live/keywords.py`、読み書き禁止）を呼び、`CooccurrenceGraph.add_utterance(list[str])`（`src/live/graph.py`）へ投入。「1発話 top6 を無条件でノード採用」。
- `CooccurrenceGraph`: node weight=出現 final 数(int)、last_seen=seq、`LIVE_GRAPH_MAX_NODES=40` 超過で (weight, last_seen) 最小から間引き。エッジ=発話内共起カウント。
- Wire format: `{type:"graph", seq, nodes:[{id, weight, last_seen}], edges:[{a, b, weight}]}`。フロント（`src/web/templates/live.html` の Canvas 描画）は node 半径に `weight`、フェードに `seq - last_seen`、エッジ太さ/透明度に `edge.weight` を使用。
- キーワードパネル（`_keywords_message_locked()`、`LIVE_KEYWORD_LIMIT=15`）も同じ `extract_keywords` を使用（累積テキスト全体に対して）。
- テスト: pytest 非依存の素 python 実行（`tests/_runner.py`）。`tests/test_live_graph.py`（9件）、`tests/test_session.py` あり。`tests/test_keywords.py` は読み書き禁止。

### Goal
1. グラフ用キーワード抽出を janome ベースの新モジュール `src/live/terms.py` に差し替え（pluggable。janome 不在時は既存 `keywords.extract_keywords` へフォールバック。`keywords.py` は不変更）。
2. ノード採用ルールを「1発話 top6」→「セッション累積サリエンス/頻度ベース選抜」（`LIVE_GRAPH_*` env で調整可、任意で発話ごとの指数時間減衰）に変更。

### Scope
- In: `src/live/terms.py`（新規）/ `src/live/graph.py` / `src/live/session.py` / `src/config.py` / `requirements.txt` / `tests/test_terms.py`（新規）/ `tests/test_live_graph.py` / `tests/test_session.py`（必要時）/ `README.md`
- Out: `src/live/keywords.py`・`tests/test_keywords.py`（権限で不可触）、TF-IDF 化（将来課題として README/Design に言及のみ）、フロント `live.html` の変更（wire format 維持で不要）、バッチ系・認証・API 変更

### Constraints
- エッジは発話内共起を維持。graph snapshot の wire format（キー名・意味）を維持しフロント無変更。
- janome 不在環境でも import 時・実行時にエラーなく動作（フォールバック）。
- ローカルタスク: Linear 一切不使用。ベースブランチ `feature/live-word-network`。

### Success Criteria
- janome 不在環境: 全既存テスト（test_live_graph / test_session / test_keywords 含む全 tests/）が無回帰で PASS、新テスト PASS、変更・新規ファイルの `py_compile` 成功。
- janome 有り環境（本タスクでは未実施と明記）: 日本語 final から複合名詞ベースの語がノード化される。
- 累積サリエンス上位のみがノード表示され、env で候補数・上限・減衰・足切りを調整できる。

## Decision Log
- [orchestrate] PRE 2026-07-06: tier=M 判定（Files 8-9 / 複数パターン / Medium risk）。janome はコア依存でないため Hard Trigger 非適用、ただし Gate 1 承認対象。Linear スキップ（ローカルタスク）。
- [startproject] PRE 2026-07-06: startproject 開始。tier=M、ローカルタスク（Linear スキップ）、base=feature/live-word-network。keywords.py / test_keywords.py は権限不可を実確認（ls も拒否）。janome 不在を実確認。
- [startproject] DECISION 2026-07-06: 抽出器は pluggable 設計 — terms.py に TermExtractor Protocol + JanomeTermExtractor / LegacyTermExtractor（keywords.extract_keywords 委譲）。janome import 可否で module-level cache 選択。keywords.py は import のみ（不変更・不読取）。
- [startproject] DECISION 2026-07-06: グラフは新クラスでなく CooccurrenceGraph を後方互換拡張（既存位置引数 max_nodes 維持、新パラメータは keyword-only + config 既定）。add_utterance は list[str] も受理（既存テスト互換）。
- [startproject] DECISION 2026-07-06: ノード選抜 = 累積サリエンス top-N（LIVE_GRAPH_MAX_NODES）+ 補助足切り（最小サリエンス/最小頻度）。threshold 単独でなく top-N 主体（表示安定性のため）。
- [startproject] DECISION 2026-07-06: 時間減衰は wall-clock でなく final 単位の指数減衰 LIVE_GRAPH_DECAY（既定 1.0=無効）。決定的でテスト容易なため。
- [startproject] DECISION 2026-07-06: wire format 維持のため node の weight は「出現 final 数(頻度)」を送出し続ける（サリエンスは選抜にのみ使用）。フロントの半径/太字ロジック（weight*2, weight>2）の意味を壊さない。エッジ weight は共起カウント（減衰有効時のみ float 化、フロントは Math.min 済みで安全）。
- [startproject] DECISION 2026-07-06: キーワードパネル（LIVE_KEYWORD_LIMIT の一覧）は extract_keywords のまま不変更（タスクスコープは「言葉のネットワーク」のみ）。→ Gate 1 選択肢 2 として提示。
- [startproject] DECISION 2026-07-06: janome は requirements.txt に live-mode 任意依存としてコメント付き追加（不在でも動作）。TF-IDF はスコープ外、README の将来課題に一文言及のみ。
- [startproject] POST 2026-07-06: 計画完了。Linear 投稿はローカルタスクのためスキップ。Gate 1 発動（承認待ち）。
- [orchestrate] GATE1 2026-07-06: ユーザー承認 = **1A + 2B + 3A**。2 は推奨 2A でなく **2B（サイドバー累積 top15 も terms.py 新抽出へ統一。WS `keywords` メッセージ形 `{"word","score"}` 維持、live.html 無変更）** が採択された。Design §3 の「`_keywords_message_locked()` は不変更」は 2B により上書き。CLAUDE.md 変更は今回スコープ外。
- [team-implement] PRE 2026-07-06: `feature/wordnetwork-brushup` を `feature/live-word-network` から作成。janome 不在を再確認（フォールバック経路が本環境の実行経路）。
- [team-implement] DECISION 2026-07-06: 名詞サブタイプ除外は承認済み設計どおり `{代名詞, 非自立, 数}` に限定（接尾等の追加除外は janome 実環境での品質検証後に検討する将来課題）。除外名詞は複合語の連結も分断する。
- [team-implement] DECISION 2026-07-06: `decay == 1.0`（既定）は完全 no-op とし、エッジ weight を int のまま維持（float 化は decay 有効時のみ）。既存テスト `test_repeated_cooccurrence_accumulates_weights` の int 期待と wire format を保護。
- [team-implement] DECISION 2026-07-06: `config.LIVE_GRAPH_WORDS_PER_FINAL` 定数は削除（参照が消えるため）。env 変数としては `LIVE_GRAPH_CANDIDATES_PER_FINAL` の deprecated alias として引き続き有効。
- [team-implement] DECISION 2026-07-06: `terms.py` の `keywords.py` import は `LegacyTermExtractor.extract` 内の遅延 import に限定（graph/terms の import 時に keywords が読み込まれない）。`graph.py` → `terms.py`（Term 型）の一方向依存のみで循環なし。
- [team-implement] POST 2026-07-06: 実装完了。全 8 テストモジュール 82 件 PASS（無回帰、うち新規/追加 21 件）。py_compile 全 PASS。Linear 更新なし（ローカルタスク）。

## Design

### 1. `src/live/terms.py`（新規、pluggable）
```python
@dataclass(frozen=True)
class Term:
    word: str
    score: float

class TermExtractor(Protocol):
    def extract(self, text: str, limit: int) -> list[Term]: ...

class LegacyTermExtractor:      # janome 不在フォールバック
    def extract(...):           # keywords.extract_keywords(text, limit) を Term に写像

class JanomeTermExtractor:
    def __init__(self, tokenizer=None): ...   # テスト用 tokenizer 注入口
    def extract(self, text, limit) -> list[Term]: ...

def get_term_extractor() -> TermExtractor:    # module-level cache、janome import 一度だけ試行
def extract_terms(text: str, limit: int) -> list[Term]:  # session.py が使う唯一の入口
def reset_extractor_cache() -> None:          # テスト用
```
- janome 抽出: 品詞フィルタ（`名詞` 系採用、`代名詞`/`非自立`/`数` 除外）、連続名詞の複合名詞結合、base_form 正規化（`*` なら surface）、ASCII は lower。
- スコア（TF-IDF なし）: `count * (1 + min(len(word),12)*0.08)`、複合名詞（2形態素以上）は `*1.25` ボーナス。1文字語・記号除外。
- `JanomeTermExtractor(tokenizer=FakeTokenizer())` 注入で janome 不在環境でも品詞フィルタ・複合結合・スコアをテスト可能。

### 2. `src/live/graph.py` — CooccurrenceGraph 後方互換拡張
```python
def __init__(self, max_nodes: int, *,
             decay: float = 1.0,
             min_salience: float = 0.0,
             min_frequency: int = 1,
             max_candidates: int = 200) -> None
def add_utterance(self, terms: list[Term] | list[str]) -> None
```
- 内部状態を候補蓄積型に: word → `{salience: float, frequency: int, last_seen: int}`。str 入力は `Term(word, 1.0)` に写像（既存テスト・既定挙動互換: score=1.0 なら salience==frequency で従来と同等の順位）。
- `add_utterance` フロー: seq++ → decay<1.0 なら全 node salience / edge weight に乗算（微小エッジ epsilon 掃除）→ dedup 候補の salience/frequency/last_seen 累積 → 発話内候補ペアのエッジ加算（維持）→ 候補が `max_candidates` 超なら最小 salience から内部間引き。
- `snapshot()`: 累積 salience 降順（tie: frequency, last_seen, word）で足切り（min_salience/min_frequency）通過語の top `max_nodes` を可視ノードとし、**nodes の weight には frequency(int) を出す**。edges は可視ノード間のみ射影。wire format 完全維持 → `live.html` 無変更。
- 既存 `_prune()`（破壊的 top-N eviction）は「表示選抜 + 候補上限間引き」に置き換わる（表示から外れても候補として累積継続 — これが「セッション累積」の本質）。

### 3. `src/live/session.py`（最小変更）
- `from .terms import extract_terms` を追加し、`_graph_message_locked()` のみ変更:
  `terms = extract_terms(text, limit=config.LIVE_GRAPH_CANDIDATES_PER_FINAL)` → `self._graph.add_utterance(terms)`。
- `__init__` で新 config 値を `CooccurrenceGraph(...)` に引き渡し。`_keywords_message_locked()` は不変更（Gate 1 選択肢 2）。

### 4. `src/config.py` — env（LIVE_GRAPH_*）
| env | 既定 | 意味 |
|---|---|---|
| `LIVE_GRAPH_MAX_NODES` | 40 | 表示ノード上限（既存・意味維持） |
| `LIVE_GRAPH_CANDIDATES_PER_FINAL` | 20 | 1 final から取る候補語数（旧 `LIVE_GRAPH_WORDS_PER_FINAL` を deprecated alias としてフォールバック参照） |
| `LIVE_GRAPH_MIN_SALIENCE` | 0.0 | 表示足切り（累積サリエンス） |
| `LIVE_GRAPH_MIN_FREQUENCY` | 1 | 表示足切り（出現 final 数） |
| `LIVE_GRAPH_DECAY` | 1.0 | final ごと指数減衰（1.0=無効、推奨 0.98） |
| `LIVE_GRAPH_MAX_CANDIDATES` | 200 | 内部候補プールの上限（メモリ抑制） |

### 5. requirements.txt / README
- `janome` を live セクションにコメント付き追加（不在でもフォールバック動作、と明記）。
- README: env 表更新、「言葉のネットワーク」節の挙動説明更新（累積サリエンス選抜・減衰）、将来課題として TF-IDF 化に一文言及。

### 6. 検証計画
**janome 不在環境（本環境）で実施:**
- 新規 `tests/test_terms.py`: FakeTokenizer 注入で JanomeTermExtractor の品詞フィルタ/複合名詞/正規化/スコア順、janome 不在時に `get_term_extractor()` が Legacy を返すこと、`extract_terms` の Term 型契約。
- `tests/test_live_graph.py`: 既存9件を維持（互換確認）+ 追加: Term 入力での累積サリエンス選抜、表示外候補が後の出現で復帰、decay 減衰、min_salience/min_frequency 足切り、可視ノード間エッジ射影、max_candidates 間引き。
- 既存無回帰: `for f in tests/test_*.py; do python3 "$f"; done`（test_keywords.py 含む — 実行は可、編集不可）。
- `python3 -m py_compile` を変更・新規 .py 全部に実施。

**依存導入環境での未実施検証（Implementation Notes / PR に明記する残課題）:**
- 実 janome での日本語抽出品質（複合名詞・ノイズ語）。
- 実音声でのライブセッション E2E（final → graph 配信）。
- ブラウザでの Canvas 描画確認（ノードサイズ=頻度、減衰時の見え方）。

## Plan (Tasks)
1. `feature/live-word-network` から実装ブランチ作成（例: `feature/wordnetwork-brushup`）
2. `src/config.py` に `LIVE_GRAPH_*` 新 env 追加（WORDS_PER_FINAL alias 込み）
3. `src/live/terms.py` 新規作成（Term / Protocol / Janome / Legacy / cache / extract_terms）
4. `src/live/graph.py` を候補蓄積 + 累積サリエンス選抜 + 任意減衰に拡張（wire format 維持）
5. `src/live/session.py` の `_graph_message_locked` と graph 生成引数を差し替え
6. `requirements.txt` に janome 追加、`src/live/__init__.py` docstring に terms 追記
7. `tests/test_terms.py` 新規、`tests/test_live_graph.py` 拡張（既存9件は不変更で PASS 維持）
8. README 更新（env 表・パネル説明・TF-IDF 将来課題）
9. 検証: 全 tests 実行 + py_compile、未実施検証を Implementation Notes に明記

## Gate 1（承認待ちの判断事項）
1. **janome の requirements.txt 追加** — 案 A（推奨）: live セクションにコメント付き追加 / 案 B: README 記載のみの完全任意依存
2. **キーワードパネル（サイドバー累積 top15）の扱い** — 案 A（推奨）: グラフ経路のみ terms.py 化、パネルは不変更（最小スコープ） / 案 B: パネルも extract_terms に統一
3. **wire format の node weight の意味** — 案 A（推奨）: weight=頻度(int) 維持（フロント無変更） / 案 B: weight=サリエンス(float) 送出（フロント調整が必要になり得る）

推奨組み合わせ: 1A + 2A + 3A。承認後に team-implement へ進む。

## Implementation Notes

### 変更ファイル（branch: feature/wordnetwork-brushup）
| ファイル | 変更 |
|---|---|
| `src/live/terms.py` | **新規**。`Term`（frozen dataclass）/ `TermExtractor` Protocol / `JanomeTermExtractor`（tokenizer 注入可）/ `LegacyTermExtractor`（keywords.extract_keywords へ遅延委譲）/ `get_term_extractor()`（module cache、janome import 一度だけ試行）/ `extract_terms()` / `reset_extractor_cache()` |
| `src/live/graph.py` | 候補蓄積型に書き換え。内部状態 word→{salience, frequency, last_seen}。`add_utterance(list[Term]|list[str])`（str は Term(word,1.0) 写像）。`snapshot()` で足切り（min_salience/min_frequency）→ 累積サリエンス降順（tie: frequency, last_seen, word 昇順）top max_nodes を可視化。nodes.weight=frequency(int)、edges は可視ノード間のみ射影。decay<1.0 で発話ごと指数減衰（エッジ 1e-3 未満掃除）。`_prune_candidates()` は max_candidates 超過時のみ最小サリエンスから内部間引き |
| `src/live/session.py` | import を keywords→terms へ差し替え。graph 生成に新 config 4 引数。`_graph_message_locked`: `extract_terms(text, LIVE_GRAPH_CANDIDATES_PER_FINAL)` → `add_utterance(terms)`。`_keywords_message_locked`（**2B**）: `extract_terms(累積テキスト, LIVE_KEYWORD_LIMIT)`、メッセージ形 `{"word","score"}` 維持 |
| `src/config.py` | `LIVE_GRAPH_CANDIDATES_PER_FINAL`（既定20、旧 `LIVE_GRAPH_WORDS_PER_FINAL` env を alias 参照）/ `LIVE_GRAPH_MIN_SALIENCE`(0.0) / `LIVE_GRAPH_MIN_FREQUENCY`(1) / `LIVE_GRAPH_DECAY`(1.0=無効) / `LIVE_GRAPH_MAX_CANDIDATES`(200) を追加。旧定数は削除（env は alias で有効） |
| `requirements.txt` | janome をコメント付き追加（不在時フォールバック明記） |
| `src/live/__init__.py` | docstring に terms / graph を追記 |
| `tests/test_terms.py` | **新規** 11 件: FakeTokenizer 注入で品詞フィルタ / 代名詞・非自立・数除外 / 複合結合+1.25 ボーナス / 除外語による連結分断 / base_form 正規化（`*`→surface）/ ASCII 小文字化 / 1文字除外 / 出現累積 / limit・limit=0 / janome 可否による extractor 選択+cache / extract_terms・Legacy の Term 型契約 |
| `tests/test_live_graph.py` | 既存 9 件不変更（PASS 維持）+ 新規 9 件: Term スコア選抜と weight=頻度int / 表示外候補の累積・復帰 / decay のノード・エッジ減衰 / decay 無効時エッジ int 維持 / min_salience・min_frequency 足切り / 可視ノード間エッジ射影 / max_candidates 内部間引き |
| `tests/test_session.py` | 新規 1 件: keywords メッセージ形 `{"word","score"}` 維持の回帰ガード（2B） |
| `README.md` | ファイルツリーに terms/graph 追加、env 表更新（alias 明記）、「言葉のネットワーク」節を累積サリエンス選抜・減衰・フォールバックの説明に更新、TF-IDF 将来課題を一文言及 |

### 検証結果（janome 不在の本環境）
- 全 8 テストモジュール **82/82 PASS**（`for f in tests/test_*.py; do python3 "$f"; done`）。`test_keywords.py` 6/6 PASS（不可触・実行のみ）。既存 graph 9 件・session 12 件無回帰。
- `python3 -m py_compile` 変更・新規 8 ファイル全 PASS。
- wire format 不変を確認: graph `{type,seq,nodes:[{id,weight,last_seen}],edges:[{a,b,weight}]}`（weight は decay 無効時 int）、keywords `{type,items:[{word,score}]}`。`live.html` 無変更。

### 未実施検証（依存導入環境での残課題 — PR 本文に明記すること）
1. 実 janome での日本語抽出品質（複合名詞の粒度・ノイズ語。除外サブタイプ `{代名詞,非自立,数}` の妥当性、接尾等の追加除外要否）。
2. 実音声でのライブセッション E2E（final → keywords/graph 配信、janome 経路）。
3. ブラウザでの Canvas 実描画確認（ノードサイズ=頻度、decay 有効時の見え方）。

## Review

### 判定: PASS（2026-07-06、対象 commit 9f7c9ea）
- 0 critical / 0 major / minor のみ。全 82 テスト PASS を実行再確認。wire format 不変を live.html 消費側コードと照合済み。承認済み決定 1A / 2B / 3A に整合。
- レビュー体制: Claude (Quality/Logic) + OpenCode gpt-5.5 + Security pass + Simplify 手動分析（tier=M 相当）。Linear 投稿なし（ローカルタスク）。

### 却下した誤検知（OpenCode 提起 → 精査で無効）
- 「`LIVE_GRAPH_DECAY>1.0` で重み指数増加」→ 減衰分岐は `decay < 1.0` 条件のため >1.0 は no-op。増幅しない。
- 「表示外候補が復帰しない」→ 表示落ち候補は pool に残り復帰する（`test_hidden_candidate_keeps_accumulating_and_revives` で担保）。pool eviction は別物のメモリガード。

### 申し送り（minor、次タスク/PR 向け）
1. [deploy/QA] janome 導入でサイドバー(2B)とグラフの語構成が変わる（意図的挙動変更）— PR 本文に明記。
2. [次タスク] config 範囲検証: `LIVE_GRAPH_DECAY` の clamp/fail-fast（<0 でグラフ空化、>1.0 は silent no-op）、MIN_* の下限。
3. [次タスク] `get_term_extractor()` の `except Exception` を ImportError 系に限定 or 警告ログ（破損 janome の silent 劣化防止）。
4. [次タスク] 抽出語の NFKC 正規化（全角/半角英数の別ノード化防止）。janome 実品質検証と併せて。
5. [nit] snapshot の nodes を sort 順で射影 / `morpheme_counts` を max 集計 / decay エッジ内包表記の重複乗算解消。任意。
6. [note] `janome` はバージョン未固定（既存の未固定依存と整合）。`.claude/rules/security.md` はリポジトリに不在のため一般セキュリティパスで代替（脆弱性所見なし）。

## Deploy
<!-- deploy が記入 -->
