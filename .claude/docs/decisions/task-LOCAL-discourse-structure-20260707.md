# Task: LOCAL-discourse-structure-20260707 — 言葉のネットワークを「議論の論理構造」可視化へ発展

## Meta
- linear_id: LOCAL-discourse-structure-20260707 (Linear 不使用・ローカルタスク)
- tier: L
- created: 2026-07-07
- revised: 2026-07-07 (Gate 1 差し戻し反映: A2 LLM併用 + Claude/Anthropic)
- status: planning (Gate 1 再提示中)
- base_branch: main

## Requirements (user)
1. 意味を持たない語の除去: 副詞・感動詞・接続詞・助詞・フィラー等の非内容語をネットワークから除外（`src/live/terms.py` の品詞フィルタ強化 + フィラー辞書）。
2. 「議論の論理構造」可視化: 無向・重みのみの共起グラフから、方向つき・型つき関係（supports / causes / elaborates / contrasts 等）+ 話題ごとのクラスタ（話題別ピラミッド/DAG）へ。

## Constraints
- Linear 一切不使用。ローカル TASK_FILE のみ。
- ベースブランチ = main。
- 設計フェーズで OpenCode に設計相談すること（ユーザー明示要望）。複数アプローチのトレードオフを Gate 1 に提示。
- 実行環境制約（GPU/一部依存/モデル重み無し、LLM 未接続の可能性）を踏まえ、実施できる検証と未実施検証を明記。
- CDN 不可・オフライン同梱。既存機能への回帰なし。決定性・テスト可能性の維持（LLM 導入時はモック可能な抽象化）。

## Decision Points (Gate 1 差し戻しで確定済み)
- 抽出方式 = **A2: LLM併用**。主抽出器は Claude (Anthropic)。ルールベースは主実装としては不採用だが、`ANTHROPIC_API_KEY` 未設定 / API 失敗時の**決定的フォールバック**として残す。
- LLM プロバイダ = **Claude (Anthropic)**。公式 `anthropic` Python SDK、既定モデル `claude-opus-4-8`（`DISCOURSE_MODEL` で切替可能）。
- スコープ = **B1（batch 先行）** 据え置き。
- 可視化 = **C1（Canvas 自作の決定的レイヤードレイアウト）** 据え置き。

## Brief

### Current State
- main ブランチ。語抽出 terms.py（janome 名詞抽出 + Legacy フォールバック）→ CooccurrenceGraph（無向共起）→ live.html（WS）/ detail.html（graph.json v1 スナップショット）の自作 Canvas force layout。
- バッチは diarization 済み Segment(speaker/start/end/text) を持つが、save_artifacts へは text のみ渡している。
- keywords.py / test_keywords.py は権限不可触。LLM 接続なし。CDN 不可。janome 不在環境の可能性あり。

### Goal
1. 非内容語除去: terms.py の名詞サブタイプ除外拡張（副詞可能・接尾 等）+ repo 内蔵ストップワード/フィラー辞書（janome 経路・Legacy フォールバック経路の両方に後段適用）。
2. 議論の論理構造可視化（A2: LLM併用）: バッチ完了時に会議全文（speaker/start/end/text つき Segment 列）を **Claude (Anthropic) の構造化出力**に渡し、statements（文相当断片・話者/原文参照つき）/ relations（方向つき・型つき: supports|causes|elaborates|contrasts）/ topics（statement クラスタ）を型つきで抽出 → 新成果物 {stem}.structure.json → detail ページに話題レーン + 型別矢印の決定的レイヤードDAG を Canvas 描画。`ANTHROPIC_API_KEY` 未設定・SDK 不在・API 失敗時は接続表現ベースの決定的フォールバック抽出器に自動切替（構造なしより情報量のある best-effort）。

### Scope
- In: src/live/terms.py, src/live/stopwords.py(新規), src/discourse.py(新規: 分割/Protocol/フォールバック抽出/トピック/組立), src/discourse_llm.py(新規: ClaudeDiscourseExtractor — anthropic SDK 依存はこのモジュールに隔離・遅延 import), src/artifacts.py, src/worker.py(Segment 全体を渡す), src/web/app.py, src/web/templates/detail.html, src/config.py, requirements.txt(`anthropic` 追加), tests(新規 test_discourse.py ほか + 既存更新), README.md
- Out: keywords.py/test_keywords.py(不可触), live の DAG 化(将来 Phase), 外部JSライブラリ vendoring(v1 は自作継続), Claude 以外の LLM プロバイダ

### Constraints
- graph.json v1 / keywords.json v1 / live WS wire format は不変更（回帰ゼロ）。structure.json は新規別ファイル。旧ジョブは後方互換（構造なし表示）。
- torch/pyannote のバッチ経路は不変更。依存追加は `anthropic` のみ（Pydantic は fastapi 経由で既に利用可能）。
- API キーはコードにハードコードしない。`ANTHROPIC_API_KEY` 環境変数のみ。未設定時はフォールバックへ（ジョブは ERROR にしない）。
- テストは fake extractor 注入で API 非依存・決定的。CDN 不可。janome 不在でも import/実行エラーなし。
- 本環境で実施可能な検証 = fake/フォールバック経路の全 unit テスト + py_compile + 既存無回帰。未実施検証 = 実 Claude 抽出品質、実音声 E2E、ブラウザ描画（下記「検証計画」参照）。

### Success Criteria
- 既存全テスト無回帰 PASS + 新規テスト（Protocol 準拠/fake 注入/フォールバック決定性/スキーマ検証/loader fail-closed/レイアウト決定性/worker best-effort）PASS。
- fake extractor 注入で statements/relations/topics が structure.json v1 スキーマ通りに保存・ロードされる（golden 一致・決定的）。
- `ANTHROPIC_API_KEY` 未設定環境で: 例外なくフォールバックが動作し、ジョブは DONE になり、structure.json（extractor=fallback メタつき）または構造なしが得られる。
- detail ページ: structure.json 有→話題別DAG表示、無→「なし」表示（旧ジョブ後方互換）。
- 非内容語（副詞可能名詞・汎用語・フィラー）がネットワークから消える。

## Decision Log
- [startproject] PRE 2026-07-07: startproject 開始。tier=L、ローカルタスク（Linear 全スキップ）、base=main。Gemini CLI 不在のため Researcher は Claude Lead 知識で代替、Architect=OpenCode 相談は実施完了（github-copilot/gpt-5.5、full回答取得）。keywords.py 権限不可を再確認。
- [startproject] DECISION 2026-07-07: 関係抽出はハイブリッド構造・実装はルールベース先行 — RelationExtractor Protocol + RuleBasedRelationExtractor(v1、日本語接続表現)。LLM プロバイダは将来枠（アーキテクチャの基盤にしない）。成果物に extractor 由来メタデータを記録。
- [startproject] DECISION 2026-07-07: 関係の単位は term 間ではなく statement（文相当断片）間。発話を 。！？+ 強接続詞で分割し、utterance_id/speaker/start への参照を保持。term→term の論理エッジは信頼性が低いため不採用。
- [startproject] DECISION 2026-07-07: 関係方向の規約 — supports: 根拠→主張 / causes: 原因→結果 / elaborates: 元→詳細化・要約 / contrasts: 後発言→先行発言。信頼度は決定的スコア（explicit causal 0.9 / elaboration 0.75 / turn-initial contrast 0.65 / weak contrast 0.45）+ evidence{marker, rule} を保存。
- [startproject] DECISION 2026-07-07: トピッククラスタは共起グラフ上の決定的手法 — 弱エッジ枝刈り後の連結成分、巨大成分時のみ決定的順序の label propagation。statement は所属 term の重み和で topic 割当。embedding/sklearn 不使用。
- [startproject] DECISION 2026-07-07: スキーマは graph.json v1 を不変更のまま、新規 {stem}.structure.json（version:1, kind:logical_structure, utterances/statements/relations/topics/extractors）を追加。既存ファイルの意味変更なし。
- [startproject] DECISION 2026-07-07: スコープは batch 先行。live は既存ワードネットワーク維持（terms.py フィルタ強化の恩恵のみ自動で受ける）。live の論理構造は将来 Phase（全snapshot方式で検討）。
- [startproject] DECISION 2026-07-07: 可視化は既存 Canvas の拡張（矢印・型別色/線種・話題レーン・時系列×トポロジカルのレイヤード配置、force 物理は不使用で決定的レイアウト）。ライブラリ vendoring（dagre 等）は自作レイアウトが不十分と判明した場合の将来候補。
- [startproject] DECISION 2026-07-07: 非内容語除去は (1) 名詞サブタイプ除外に 副詞可能・接尾 を追加、(2) repo 内蔵ストップワード/フィラー辞書 src/live/stopwords.py を janome 経路と Legacy フォールバック経路の両方の後段フィルタに適用（keywords.py 不可触のため terms.py 側でラップ）。
- [startproject] DECISION 2026-07-07: worker.py の save_artifacts 呼び出しを Segment リスト（speaker/start/end/text）を渡す形に拡張（text のみでは話者ターン起点の contrast 判定・statement メタが作れないため）。keywords/graph の既存ビルダーは text のみ使用を継続。
- [startproject] POST 2026-07-07: 計画完了。Linear 投稿スキップ（ローカルタスク）。tier=L + トレードオフ大のため Gate 1 発動（ユーザー承認待ち）。
- [orchestrate] GATE1-FEEDBACK 2026-07-07: ユーザー判断確定 — 抽出方式 = A2 (LLM併用)、プロバイダ = Claude (Anthropic)。ルールベースは主実装から降格し、オフライン/キー無し/API 失敗時のフォールバックとして存続。B1/C1 は据え置き。計画を改訂し Gate 1 再提示。
- [orchestrate] DECISION 2026-07-07: Anthropic 統合仕様 — 公式 `anthropic` Python SDK（requirements.txt 追加）、`anthropic.Anthropic()`（`ANTHROPIC_API_KEY` を SDK が環境から解決。ハードコード禁止）。既定モデル `claude-opus-4-8`、config `DISCOURSE_MODEL` で切替。構造化出力は `client.messages.parse(..., output_format=<Pydantic モデル>)` → `.parsed_output`（Pydantic は fastapi の既存依存で追加不要）。長い出力に備え `client.messages.stream()` + `get_final_message()` を採用（SDK は大 max_tokens の非ストリーミングを拒否するため）。`thinking={"type": "adaptive"}` + `output_config={"effort": ...}` を採用（構造化出力と併用可。`DISCOURSE_EFFORT` 既定 "high"）。共通 system プロンプト（抽出指示）には `cache_control: {"type": "ephemeral"}` を付与（opus-4-8 の最小キャッシュ長 4096 tokens 未満なら無害に不発。連続バッチ処理時のみ効く）。
- [orchestrate] DECISION 2026-07-07: モジュール分離 — anthropic import は src/discourse_llm.py に隔離し遅延 import。src/discourse.py（Protocol・フォールバック・トピック・組立）は stdlib のみで、anthropic 未インストール環境でも import エラーなし。extractor 選択は「キー有り かつ SDK import 可 → Claude / それ以外 → フォールバック」。API 例外（RateLimitError/APIStatusError/APIConnectionError 等）と parsed_output 検証失敗はすべて捕捉してフォールバックへ（best-effort、ジョブは ERROR にしない）。
- [orchestrate] DECISION 2026-07-07: 非決定性・コスト・レイテンシの扱い — LLM 出力は非決定的なため golden テストは fake/フォールバック経路のみ。structure.json の extractors メタに {name, model, effort} を記録し由来を追跡可能に。コスト目安: 2h 会議 ≈ 入力 3〜6万 tokens + 出力数千 tokens → $0.2〜0.5/件（claude-opus-4-8 $5/$25 per MTok）。レイテンシ数十秒〜数分は batch 完了後の best-effort 処理なので UX 影響なし。

## Design

### OpenCode 相談結果（github-copilot/gpt-5.5、2026-07-07 実施）要旨
- A) ルールベース v1 + mockable Protocol のハイブリッド構造。LLM-only 設計は不可（オフライン制約・テスト性）。単位は statement（文相当）。接続表現ベースは「high precision / low recall」— でも・けど は会話的用法が多く精度低、なぜなら・そのため・つまり・一方で は高精度。話者ターン頭の逆接は前話者への contrast の可能性が高い（rule に反映）。「検出された構造」であり ground truth でない旨を UI で明示。
- B) batch 先行が明確に優位（全文コンテキスト・diarization 利用・完全グラフでのレイアウト・golden テスト容易・live の視覚チャーン回避）。
- C) 自作 Canvas 拡張が v1 正解。d3(レイアウトなし)/dagre(古い)/cytoscape(過大)/elkjs(巨大)/graphviz(ネイティブ依存) はいずれも v1 では過剰。論理構造には force 物理でなく決定的レイヤードレイアウトが適切。
- スキーマ: *.structure.json 新設（graph.json の意味を変えない）。utterances / statements(id, utterance_id, speaker, text, terms, topic_id, position) / relations(source, target, type, confidence, evidence{marker, rule}) / topics(id, label=上位語, statement_ids) / extractors メタ。
- サイクル処理: 最低信頼度のバックエッジを切って DAG 化。
- 視覚エンコーディング案: supports=緑実線矢 / causes=青実線矢 / elaborates=灰点線矢 / contrasts=橙破線矢。話題ピラミッド = 上:トピックラベル、中:主要主張、下:根拠・例、横:対立。

### Anthropic API 統合仕様（確定）
- **SDK / 認証**: 公式 `anthropic` Python SDK を requirements.txt に追加。クライアントは引数なしの `anthropic.Anthropic()`（`ANTHROPIC_API_KEY` 環境変数を SDK が解決。コードへのハードコード禁止）。キー未設定・SDK 未インストール時は ClaudeDiscourseExtractor を構築せずフォールバックへ。
- **モデル**: 既定 `claude-opus-4-8`。config に `DISCOURSE_MODEL = os.environ.get("DISCOURSE_MODEL", "claude-opus-4-8")` を追加して切替可能に。
- **構造化出力**: `client.messages.parse(model=..., output_format=DiscourseExtraction, ...)` で Pydantic 検証済みの `.parsed_output` を取得（Pydantic は fastapi の既存依存 — 新規依存なし）。ストリーミング併用が必要な箇所は `output_config={"format": {"type": "json_schema", "schema": ...}}` + `json.loads` で等価に扱える（実装時にどちらか一方に統一。方針は parse 優先、長大出力時のみ stream+json_schema）。
- **長出力対策**: 会議全文入力 + 構造 JSON 出力は大きくなり得るため `max_tokens` は 32000〜64000 を想定し、`client.messages.stream(...)` + `stream.get_final_message()` を使用（SDK は ~10 分超と推定される非ストリーミング要求を拒否するため、streaming が安全側）。
- **thinking / effort**: `thinking={"type": "adaptive"}` + `output_config={"effort": config.DISCOURSE_EFFORT}`（既定 "high"）。opus-4-8 では adaptive のみ有効（budget_tokens/temperature/top_p/top_k は 400 になるため使用しない）。構造化出力・streaming と併用可。
- **prompt caching**: 会議横断で共通の抽出指示 system プロンプトに `cache_control: {"type": "ephemeral"}` を付与。opus-4-8 の最小キャッシュ長は 4096 tokens — system が閾値未満なら黙って不発（無害）。効果があるのは 5 分 TTL 内に複数ファイルを連続処理するケースのみ、と割り切る。
- **エラー処理**: `anthropic.RateLimitError` / `APIStatusError` / `APIConnectionError` / 検証失敗（parsed_output と原文の突合不整合を含む）をすべて捕捉 → ログ → フォールバック抽出器で再生成。worker からは従来通り best-effort（ジョブは ERROR にしない）。
- **コスト/レイテンシ/非決定性（明記）**: 2h 会議 ≈ 入力 3〜6万 tokens + 出力数千〜1万 tokens → 概算 $0.2〜0.5/件。呼び出しは batch 完了後 1 回のみ・非対話なので数十秒〜数分のレイテンシは許容。LLM 出力は非決定的 → CI/テストは実 API を一切呼ばず、fake extractor 注入とフォールバック経路の golden テストで担保。structure.json の `extractors` メタに {name: "claude"|"fallback", model, effort} を記録。

### 構造化出力スキーマ（Pydantic → structure.json v1 へ写像）
```python
class Statement(BaseModel):
    id: str                      # "s1", "s2", ...
    utterance_index: int         # 入力 Segment 列への参照（話者/時刻はローカルで復元）
    speaker: str
    text: str                    # 原文断片（発話の部分文字列であることをローカル検証）
class Relation(BaseModel):
    source: str                  # statement id（supports: 根拠→主張 / causes: 原因→結果 /
    target: str                  #   elaborates: 元→詳細 / contrasts: 後→先 — 既存方向規約を踏襲）
    type: Literal["supports", "causes", "elaborates", "contrasts"]
    confidence: float            # 0..1（LLM 自己申告。フォールバックは規則スコア）
class Topic(BaseModel):
    id: str
    label: str
    statement_ids: list[str]
class DiscourseExtraction(BaseModel):
    statements: list[Statement]
    relations: list[Relation]
    topics: list[Topic]
```
- 受領後にローカル検証: relation の source/target が statements に存在、topic の statement_ids が実在、text が該当 utterance の部分一致（緩め）。不整合はその要素を落とすか全体フォールバック。サイクルは最低信頼度バックエッジ切断で DAG 化（既存決定）。
- {stem}.structure.json は既存 envelope 踏襲: {"version": 1, "kind": "logical_structure", "source", "generated_at", "utterances", "statements", "relations", "topics", "extractors"}。graph.json / keywords.json / live WS は不変更。

### モジュール構成
- src/live/stopwords.py（新規）: 内容語判定用ストップワード/フィラー集合（定数、env で追加可）。
- src/live/terms.py: _EXCLUDED_NOUN_SUBTYPES += {副詞可能, 接尾}、両抽出器の出力を stopwords 後段フィルタに通す。
- src/discourse.py（新規、stdlib + Pydantic のみ）: `RelationExtractor` Protocol（`extract(utterances) -> DiscourseExtraction | None`）/ split_statements() / **FallbackRelationExtractor**（旧 RuleBased — 接続表現マーカー辞書 + 方向/信頼度規則。決定的）/ cluster_topics()（フォールバック用: 枝刈り連結成分 + 決定的 label propagation。Claude 経路は topics を LLM 出力から採用）/ build_structure()（extractor 注入を受けて envelope 組立 + ローカル検証 + DAG 化）。
- src/discourse_llm.py（新規）: `ClaudeDiscourseExtractor` — anthropic SDK の import・クライアント生成・messages.parse/stream・prompt caching・例外→None 変換をこのモジュールに隔離（遅延 import。SDK 不在でも他モジュールは動作）。
- src/config.py: `DISCOURSE_MODEL`（既定 "claude-opus-4-8"）、`DISCOURSE_EFFORT`（既定 "high"）、`DISCOURSE_MAX_TOKENS`（既定 32000）等の DISCOURSE_* ノブ追加。
- src/artifacts.py: save_artifacts が Segment 相当(speaker/start/end/text の dict/dataclass)を受け、structure.json も出力（extractor は引数注入、既定は「キー有→Claude/無→フォールバック」のファクトリ）。load_structure() 追加（fail-closed 同型）。keywords/graph ビルダーは text のみ使用を継続（回帰ゼロ）。
- src/worker.py: save_artifacts へ segments 全体を渡す（現行は text のみ）。best-effort try/except は現行のまま。
- src/web/app.py: job_detail に structure を追加ロード（ファイルを読むだけ。API 呼び出しなし）。
- detail.html: 「議論の構造」パネル追加 — 話題ごとのレーン分割 + 型別矢印（supports=緑実線 / causes=青実線 / elaborates=灰点線 / contrasts=橙破線）の決定的レイヤード DAG を Canvas 自作描画 + 凡例 + hover で発話/根拠表示。「検出された構造であり ground truth ではない」旨を明示。既存ワードネットワークはトグルまたは並置で存置。

### 実装ステップ（team-implement 向け）
1. src/live/stopwords.py 新規 + terms.py フィルタ強化（サブタイプ除外拡張・後段辞書フィルタ・Legacy 経路ラップ）+ test_terms.py 追加ケース
2. src/discourse.py 新規: Protocol / Pydantic スキーマ / split_statements / FallbackRelationExtractor / cluster_topics / build_structure（ローカル検証 + DAG 化）+ tests/test_discourse.py（fake extractor 注入 + フォールバック golden フィクスチャ）
3. src/discourse_llm.py 新規: ClaudeDiscourseExtractor（messages.parse 構造化出力・streaming・adaptive thinking・cache_control・例外→フォールバック）+ requirements.txt に `anthropic` 追加 + config に DISCOURSE_* ノブ + tests（SDK/クライアントをモックし、リクエスト構築・キー無し分岐・例外分岐のみ検証。実 API 不呼出）
4. src/artifacts.py に structure ビルド/保存/ロード追加、src/worker.py で Segment メタを引き渡し + test_artifacts.py 更新（fake extractor 注入で決定的に）
5. src/web/app.py detail ルート拡張 + detail.html に決定的レイヤード DAG レンダラ（矢印・型別スタイル・話題レーン・凡例・hover）+ レイアウト割当の決定性テスト（サーバ側で計算する部分があれば）
6. README（ANTHROPIC_API_KEY 設定手順・コスト注意・フォールバック挙動）/ 全テスト + py_compile 実行

### 検証計画: 実施可能（本環境 = anthropic SDK/API キー無し前提）
- 全 tests/ 無回帰（keywords/graph/live 経路は不変更のため既存テストがそのまま回帰ガード）。
- test_discourse.py: fake extractor（固定 DiscourseExtraction を返す）注入で build_structure→save→load の往復 golden 一致。フォールバック抽出器の分割/マーカー/方向/信頼度/クラスタの決定的 golden。ローカル検証（不正 relation 参照の除去・サイクル切断）のユニット。
- test_discourse_llm（またはモック節）: キー未設定→Claude 経路に入らない、SDK 例外→None→フォールバック、messages.parse へ渡すパラメータ（model/output_format/thinking/cache_control）の形。unittest.mock でクライアントを差し替え、実 API・実 SDK 不要（SDK 未インストール時は import ガードで skip）。
- test_artifacts.py: worker 相当の呼び出しで structure.json が best-effort 生成され、失敗してもジョブが落ちないこと。loader の fail-closed（欠損/破損/版違い→None）。
- py_compile 全対象ファイル。janome 不在経路の import 安全性。
### 検証計画: 未実施（実接続が必要 — team-review / 実環境で別途）
- 実 Claude API での抽出品質（statements 粒度・relation の妥当性・topics の凝集度）と `claude-opus-4-8` 実プロンプトのチューニング。
- 実 API のコスト/レイテンシ実測、prompt caching のヒット確認（usage.cache_read_input_tokens）。
- 実会議音声での E2E（batch→structure.json→detail 表示）。
- ブラウザでの Canvas DAG 描画の目視（スクリーンショット確認推奨）。
- janome 実環境での非内容語除去の体感品質。

## Implementation Notes
<!-- team-implement が記入 -->

## Review
<!-- team-review が記入 -->

## Deploy
<!-- deploy が記入 -->
