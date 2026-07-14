# Task: NSKETCH-873 — discourse UI: トピック内 意思決定フロー可視化（論点→分岐→収束）の設計

## Meta
- linear_id: NSKETCH-873
- tier: L （2026-07-14 昇格: M→L。ユーザー要望で「4 ビュー切替 + 現行 fallback」= 5 レンダラ + 切替 UI に拡大。Canvas レイアウトアルゴリズム 4 種の追加が主因）
- created: 2026-07-14
- status: implementing （Gate 1 = ユーザー承認済み「それで進めてほしい」→ 実装フェーズへ）
- scope: 設計 + 実装（decision_flows データ層 + 4 切替ビュー〈rails/subway/ibis/ribbons〉+ 現行 classic fallback + トピック単位切替 UI）
- opencode_logs: `.claude/logs/opencode-nsketch-873-decision-flow.log`（データモデル/オントロジー）, `.claude/logs/opencode-nsketch-873-multiview-switcher.log`（5 ビュー切替アーキテクチャ/決定的レイアウト）

## Brief

### Current State
- structure.json v1（`src/discourse.py` / `src/discourse_llm.py`）: statements（短い要約 text・utterance 参照・topic 所属）+ 型つき有向 relations（supports/causes/elaborates/contrasts、DAG 化済み）+ topics（label/summary/statement_ids、LLM 経路は全 statement をいずれかの topic に割当）。
- detail.html「議論の構造」パネル: トピック別の縦セクション。各 statement は論理深さ（入エッジ最長路）でインデントしたドット + 右に要約テキスト。関係は型別色の直線矢印。hover で原文・関係を表示。決定的レイアウト・Canvas 2D・外部 JS ライブラリなし・ライト/ダーク対応。
- fallback 抽出器（キー無し/API 失敗時）は接続表現マーカーのみ。topics はローカルクラスタリング、summary なし。

### Goal
各トピックが持つ「決定を要する中心的な問い（アジェンダ）」と、競合するアイデア（A案/B案/…）の議論、その収束（決定/保留/未決）を、**開始点 → 分岐 → 収束** のフローとして可視化する設計を確定する。データモデル拡張・抽出戦略・レイアウトアルゴリズム・劣化戦略・実装タスク分解までを成果物とする。**実装はしない。**

### Scope
- In（設計対象）: structure.json の追加レイヤ設計、`discourse_llm.py` スキーマ/プロンプト拡張案、`discourse.py` 検証規則案、detail.html レンダラ設計、フォールバック/後方互換戦略、テスト戦略、タスク分解
- Out: 実装・実 API 検証、live（リアルタイム）ページへの適用、外部 JS ライブラリ導入

### Constraints（既存決定の踏襲）
- 追加は additive: v1 ファイルは従来どおり描画（後方互換）。graph.json / keywords.json / live WS 不変更
- 抽出モジュールは stdlib-only、anthropic SDK は discourse_llm.py に隔離
- レイアウトは決定的（force 物理・乱数なし）、Canvas 2D 自作、CDN 不可
- fallback 経路は絶対にクラッシュしない（best-effort、ジョブは ERROR にしない）
- 構造化出力は additionalProperties:false、数値レンジ制約なし（クライアント側 clamp）

## Decision Log
- [orchestrate] PRE 2026-07-14: tier=M 判定（設計タスク、将来実装 ~4-5 ファイル、中リスク）。Linear ID なし → NSKETCH-873 を新規作成（In Progress）。
- [startproject] PRE 2026-07-14: Phase 1 Brief 完了（discourse.py / discourse_llm.py / detail.html / 既存決定ログ読解）。Phase 2 OpenCode 設計相談を実施（データモデル分解・IBIS オントロジー・レイアウトパターン比較・収束セマンティクス・抽出/検証戦略・時間軸の 6 論点）。
- [startproject] DECISION 2026-07-14: データモデル = top-level additive `decision_flows[]`（topic_id 参照の派生解釈レイヤ）。Topic 直接拡張・statement role 単独方式は不採用。envelope version は 1 のまま optional キー追加。relations に決定的 id 付番を additive 追加。
- [startproject] DECISION 2026-07-14: オントロジー = ミニマル IBIS（question / option / argument(pro|con|neutral) / outcome(decided|deferred|open × single_option|hybrid|no_option|unknown)）。細分類ロールは v1 見送り。情報共有トピックは decision_flow 省略（省略 = クラシック表示）。decision レイヤの confidence は enum（high|medium|low、数値レンジ制約不可のため）。
- [startproject] DECISION 2026-07-14: 可視化 = P2 縦型 decision rails（y=時間・x=意味レーン・git-graph 型）primary + P5 役割グリフオーバレイ fallback。横型 subway / Sankey / IBIS ツリーは不採用（日本語ラベル幅・量の偽装・議論が木でない、が各理由）。option>5・低confidence・幅<640px は P5 に降格。
- [startproject] DECISION 2026-07-14: 決定的フォールバック抽出器は decision_flows を出力しない（マーカーでは選択肢グルーピング不能、誤検出は視覚的確信の偽装）。強マーカー時のみ low confidence で出す案は将来枠としてログに記録。
- [startproject] DECISION 2026-07-14: stance の真実性 — relations は談話グラフの単一の真実のまま。arguments は decision 解釈で relation_ids により可能な範囲で逆リンク。不整合は warnings[]（fail-soft）。
- [startproject] POST 2026-07-14: 計画完了・Linear NSKETCH-873 に計画サマリー投稿。本タスクは planning-only のため Gate 1 でユーザー承認待ちとして終了（実装は承認後の後続 issue）。
- [orchestrate] DECISION 2026-07-14 (Gate 1 承認): ユーザーが「横型 fork-join / 縦型 git-graph / IBIS ツリー / Sankey リボンを切替可能にし、現行を fallback」を明示要望・実装 GO。tier を M→L に昇格。可視化スコープを「1 primary + 1 fallback」から「4 切替ビュー + classic fallback = 5 レンダラ + 切替 UI」に拡大。**バックエンド（decision_flows データモデル/抽出/検証、D1–D4）は不変**（4 ビューは同一データを消費）。
- [orchestrate] DECISION 2026-07-14: 2 回目の OpenCode 相談（multiview-switcher, gpt-5.5）実施。採用: (a) 正規化 FlowModel + ビュープラグイン方式（`canRender/layout/draw`、hit-test は scene.hitRegions[] 共有で重複排除）、(b) IBIS = top-down post-order 部分木幅積算、(c) Sankey = **固定幅リボン**（量の偽装を避ける）+ 可視注記、(d) 既定ビュー = 決定レール(rails)、(e) トピック単位セレクタ + localStorage `transcribe:decisionFlowView:v1`。
- [orchestrate] DECISION 2026-07-14: OpenCode は「Sankey 削除・IBIS 実験扱い・v1 は rails+classic のみ」を推奨したが、**ユーザー明示要望を優先し 4 ビュー全実装**。誠実性はビュー個別の劣化(canRender)・Sankey 注記・subway デスクトップ専用で担保。IBIS は argument 不足時 classic に劣化する experimental 品質として出す。

## Design

### パターン探索（Claude Lead による比較）

トピック内フロー「問い → 分岐 → 収束」の描画パターン候補と評価。評価軸: 可読性 / 実装コスト（vanilla Canvas・決定的）/ 抽出ノイズへの正直さ / モバイル劣化 / 既存縦型レイアウトとの親和性。

| # | パターン | 概要 | 強み | 弱み |
|---|---|---|---|---|
| P1 | 横型 fork-join（路線図） | 左に問いノード、選択肢ごとに水平レーン、右の決定ノードへ合流 | メタファ一致が最良。「分岐→収束」が一目 | 900px 幅で日本語ラベルが窮屈。選択肢 3+ や発言数大で崩れる。モバイル弱い |
| P2 | 縦型 git-graph（時間↓・選択肢=レール） | 時間が下方向、選択肢ごとの縦レール、最下部で決定ノードに合流 | 時系列の再訪（非線形議論）に自然に耐える。スクロール親和。現行の縦セクション構造をほぼ流用可能 | fork-join の「横に広がる」直感より弱い。レール数が多いと幅を食う |
| P3 | IBIS 議論マップ（ツリー） | 問い→ポジション→賛否論拠のツリー | 学術的裏付け（IBIS/gIBIS）。役割が明確 | 収束（決定）がツリーでは表現しにくい。時間が消える |
| P4 | Sankey 風収束リボン | 議論量を帯幅で表現し決定へ収束 | 見栄え・議論量の表現 | 実装コスト高。帯幅が「重要度」と誤読される（抽出ノイズに不正直）。決定的レイアウト難 |
| P5 | 現行リスト + 役割グリフ + fork/join スパイン | 現行の縦リストに ?/A/B/✓ バッジと左カラムの分岐スパインだけ足す | 実装コスト最小。劣化が最も上品（decision layer 無し=現行表示） | 「フロー感」は弱い。分岐の空間表現がない |
| P6 | 3 ステージ列（問い/議論/帰結） | 論理ステージを x 軸に固定した 3 カラム | 単純。ステージが明確 | 中央カラムに全部詰まる。選択肢間の対立構造が出ない |
| P7 | 放射状（問い中心・選択肢が扇形） | 中心から放射 | 密度に強い | 時間・収束とも表現不能。Canvas 文字配置が難 |
| P8 | トーナメント表 | 選択肢が淘汰されていく | 「勝者」明確 | 会議は対戦ではない（同時多面議論）。誤ったメタファ |

一次評価: **P2（縦型 fork-join / git-graph）を本命、P5 を劣化形**とするのが、既存コード（トピック別縦セクション + 行単位配置）との親和性・時系列再訪への耐性・モバイル対応の点で最有力。P1 は 1 トピックの選択肢が 2 個・発言が少ないときのみ美しく、実データ（可変・ノイズ）に弱い。→ OpenCode 相談で検証。

### OpenCode 相談結果（gpt-5.5、2026-07-14 実施）要旨

ログ全文: `.claude/logs/opencode-nsketch-873-decision-flow.log`

1. **データモデル = 新規 top-level `decision_flows[]`（topic_id 参照）を推奨**。Topic 直接拡張は「全トピックが決定形に見える圧力」を生むため不採用。statement への role 付与を単独の真実にするのはノイズに弱く不採用。decision_flows は canonical な statements/relations/topics への**参照のみを持つ派生解釈レイヤ**とし、欠損・部分成功・単独破棄が可能な additive 構造にする。
2. **オントロジー = ミニマル IBIS**: question / option / argument(stance: pro|con|neutral) / outcome(status: decided|deferred|open, kind: single_option|hybrid|no_option|unknown) の 4 役割のみ。clarification/risk/constraint 等の細分類は v1 では過剰分類を招くため見送り。情報共有だけのトピックは decision_flow を**省略**（省略 = クラシック表示、エラーではない）。複数の問いは `questions[]` で同一フロー内に許容。途中で出現する選択肢は `introduced_by` で表現。ハイブリッド決定は `kind: "hybrid"` + `selected_option_ids` 複数。決定の再訪は v1 では「最後の outcome を最終とする」に留める（`events[]` は将来枠）。
3. **可視化 = 縦型 git-graph（decision rails）を primary、現行リスト + 役割グリフ + スパインを fallback に推奨**（Claude Lead の一次評価と一致）。横型 subway は日本語ラベル・900px・再訪で破綻、Sankey は「量の偽装」で不正直、IBIS ツリーは議論が木でないため脆い。
4. **収束の視覚言語**: 採用レールは outcome ノードへ実線合流 / 非採用レールは手前でフェード終端(見送り) / hybrid は複数レール合流 / 保留は破線でパーキングノード / 未決は中空端点で終端。理由(why)はデフォルトでは outcome 近傍に上位 1-3 個のみ、詳細は hover（採用理由/懸念のグルーピング）。relation の全エッジは decision-flow ビューでは描かない（クラシックビューの役割と重複させない）。
5. **抽出 = LLM に「不確実なら省略」を明示指示**（全トピックを決定形にしない・選択肢は実際の提案のみ・少なめ優先）。数値レンジ不可のため decision レイヤの confidence は enum ("high"|"medium"|"low")。**決定的フォールバックは既定で decision_flows を出力しない**（マーカーでは選択肢のグルーピング不能、誤検出は視覚的確信の偽装になる）。強マーカー（問い + 選択肢 2+ + 後続の決定/保留マーカーが同一トピック内）が揃った場合のみ confidence: "low" で出す案は許容だが、v1 実装では非出力を推奨。
6. **時間軸 = y 軸に時間（発話順）、x 軸に意味レーン**。再訪は同レールの下方に置くだけで自然に耐える（git-graph が subway に勝る決定的理由）。長距離クロスレーン矢印は hover 時のみ。

### 確定設計

#### D1. データモデル（structure.json への additive 拡張）

envelope の `version: 1` は維持し、**optional キー `decision_flows[]` を追加**（v1 レンダラは未知キーを無視するため後方互換。ローダは欠損時 None/[] を返す fail-soft）。relations に安定参照用の `id`（"r1"〜、組立時に決定的付番）を additive に追加する。

```json
"decision_flows": [
  {
    "topic_id": "t1",
    "questions": [{ "id": "q1", "summary": "DBは何を使うか?", "statement_id": "s1" }],
    "options": [
      { "id": "o1", "label": "PostgreSQL案", "summary": "既存運用に合わせる",
        "statement_ids": ["s2", "s5"], "introduced_by": "s2",
        "status": "selected" },
      { "id": "o2", "label": "SQLite案", "summary": "軽量に始める",
        "statement_ids": ["s3", "s6"], "introduced_by": "s3",
        "status": "rejected" }
    ],
    "arguments": [
      { "id": "a1", "statement_id": "s5", "option_id": "o1", "stance": "pro", "relation_ids": ["r8"] },
      { "id": "a2", "statement_id": "s6", "option_id": "o2", "stance": "con", "relation_ids": ["r11"] }
    ],
    "outcome": {
      "status": "decided", "kind": "single_option",
      "summary": "PostgreSQLで進める", "statement_id": "s9",
      "selected_option_ids": ["o1"], "rationale_statement_ids": ["s5", "s6"]
    },
    "confidence": "medium",
    "warnings": []
  }
]
```

- enum 集合: option.status = selected|rejected|abandoned|unresolved|partial / outcome.status = decided|deferred|open / outcome.kind = single_option|hybrid|no_option|unknown / stance = pro|con|neutral / confidence = high|medium|low
- **stance の真実性**: relations（supports/contrasts）は談話グラフの真実のまま。arguments は decision 解釈であり、`relation_ids` で可能なときだけ逆リンク。argument に対応 relation が無いのは warn 止まり（fail ではない）
- 1 topic につき decision_flow は 0..1（複数の問いは questions[] 内で表現。複数フロー化は将来拡張）

#### D2. ローカル検証規則（discourse.py / stdlib、fail-soft）

必須（違反 = その decision_flow を丸ごと落とす。structure 全体は落とさない）:
- topic_id 実在 / 参照 statement 全実在かつ**同一トピック所属** / option・question・argument の id フロー内一意
- option は statement_ids >= 1 / introduced_by ∈ option.statement_ids / argument.option_id 実在
- outcome.statement_id（あれば）トピック所属 / selected_option_ids 実在
- decided + single_option → selected はちょうど 1 / decided + hybrid → selected >= 2 / open・deferred → selected 空可

警告（warnings[] に追記して保持）:
- argument の無い option / 対応 relation の無い argument / con が supports を引用 / selected option が con のみ / outcome が選択肢議論より時系列で先行

#### D3. LLM 抽出拡張（discourse_llm.py）

- EXTRACTION_SCHEMA に `decision_flows` を**optional 配列**として追加（additionalProperties: false、数値レンジなし・enum のみ）
- SYSTEM_PROMPT に手順 4 を追加。原則: 「不確実なら省略」「全トピックを決定形にしない」「選択肢は実際に提案されたもののみ・少なめ優先」「outcome はそのトピックで最終または最も強い決定的発言」「明示的な先送りのみ deferred」「statement id は既出のもののみ参照」
- `_parse_extraction` に decision_flows の写像を追加（欠損キーは空扱い）。既存 statements/relations/topics の写像は不変更

#### D4. フォールバック戦略

- **FallbackRelationExtractor は decision_flows を出力しない**（確定）。detail ページは decision_flow の無いトピックをクラシック表示（現行「議論の構造」）にするだけで、エラー扱いしない
- 将来枠: 強マーカー条件（問いマーカー + 選択肢マーカー 2+ + 後続の決定/保留マーカー、全て同一トピック）を満たす場合のみ confidence:"low" で出す案をログに記録済み。v1 実装では見送り

#### D5. レンダリング（detail.html「議論の構造」パネル、Canvas 2D・決定的・vanilla JS）

**方針**: ユーザー要望により 4 ビュー全てを切替可能にし、現行を classic fallback とする。誠実性は各ビューの `canRender` 劣化・Sankey 注記・subway デスクトップ専用で担保。

**アーキテクチャ: 正規化モデル + ビュープラグイン**（OpenCode 推奨、hover コードの重複排除が要点）
- 共有 `buildFlowModel(topic, decisionFlow, statements, utterances)` を 1 回だけ構築: `statement_id → utterance_index` 解決 / option の**決定的順序**（`introduced_by` の utterance_index 昇順、tie-break `option.id`）/ argument を option にひも付け / topic 色は `window.detailTopics.color` を流用。
- 各ビュー = プラグイン: `{ id, label, canRender(model, width) -> bool, layout(model, width, theme) -> scene, draw(scene, ctx, hoverId) }`
- `scene = { bounds, nodes, edges, labels, hitRegions[], notes[] }`。**hit-test はビュー非依存**（共有ハンドラが `scene.hitRegions[]` を最近傍/矩形判定）→ 5 ビュー分の hover 重複を排除。
- 共有基盤: DPR/ResizeObserver + rAF、tooltip、light/dark トークン、`measureText` ラベル省略、empty/refuse ノート、**トピックごとに再利用する単一 `<canvas>`**（アクティブビューのみ描画。canvas-per-view / page-single-canvas は却下）。

**5 ビュー**（切替ボタン: 決定レール / 路線図 / IBIS / リボン / 従来表示）:

1. **決定レール（rails, 既定）** — 縦型 git-graph。y = 発話順（トピック内 statement_ids のユニーク昇順を行に割当。生 index スケールではない）、x = option レーン。`laneGap = clamp(42, railWidth/N, 110)`、`xOption[i] = spineX + laneGap*(i+1)`。stance グリフ +/−/・。採用 = 実線合流 / partial = 破線 / rejected・abandoned = フェード終端「見送り」/ deferred = 破線 + 保留 / open = 中空端点。
2. **路線図（subway, 横型 fork-join・デスクトップ専用）** — x = ステージ（xQuestion → xOptionStart → xOutcome）、option ごと水平レーン `laneGap = clamp(48, laneArea/(N-1), 84)`、分岐/合流は cubic bezier。日本語ラベルは `measureText` で最大 2 行 → `…`、全文 tooltip。幅 < 640 または option > 4 で refuse。
3. **IBIS ツリー（top-down tidy tree, experimental）** — post-order 部分木幅積算（`leaf.width=1`, `node.width=Σchildren`, `node.x = avg(先頭child.x, 末尾child.x)`）。論点(question) → option → pro/con argument。共有 argument は主 option（最早導入）配下に 1 回だけ、副次は破線クロスエッジ。outcome は最下部の収束行に分離。複数 question は合成「論点」ルート下に。argument が乏しいトピックは classic に劣化。
4. **リボン（Sankey 風・誠実性担保）** — **固定幅リボン**（データに真の量が無いため幅で量を偽装しない）。順序は時間順（交差最小化しない = 決定的）。帯 = cubic bezier 2 本を閉じた形。status は色/不透明度/破線で表現。**可視注記（必須）:「帯の太さは量や重要度を表すものではありません。」**
5. **従来表示（classic, fallback）** — 現行「議論の構造」レンダラを `classic` プラグインとして温存（回帰リスク最小化）。decision_flow 欠損・全ビュー refuse 時の既定。

**劣化ルール**（各ビュー `canRender`）: option=1 → subway/ribbons refuse・rails 描画 / outcome 無(open) → ribbons refuse・rails は中空端点 / argument 無 → IBIS は classic 劣化 / option > 5 → 4 ビュー refuse → classic / 幅 < 640 → rails(option≤3) か classic のみ / confidence=low → rails・classic のみ。refuse 時は当該ビューに「この幅／データでは表示できません（従来表示に切替可）」ノート。

**切替 UX**: **トピック単位のセレクタ**（可用ビューはトピックで異なるため。global は却下）。最後の選択は `localStorage` キー `transcribe:decisionFlowView:v1`（値 `rails|subway|ibis|ribbons|classic`）にグローバル保存。初期 = decision_flow があれば `rails`、無ければ `classic`。保存ビューが `canRender=false` なら `rails → classic` にフォールバック。no-flow ノート:「この話題に決定フローは検出されませんでした。従来表示で表示しています。」

#### D6. 実装ステップ（team-implement 向け・L tier）

**バックエンド（D1–D4 のまま。additive・fail-soft。4 ビューは同一データを消費するため不変）**
1. `src/discourse.py`: `DecisionFlow`/`Question`/`Option`/`Argument`/`Outcome` dataclass、relations への決定的 id 付番、`validate_decision_flows()`（D2 必須 = 違反フローのみ破棄／警告 = warnings 保持）、`build_structure()` へ組込み + tests（fake extractor 注入 / 検証規則 golden / fail-soft / 後方互換）
2. `src/discourse_llm.py`: `EXTRACTION_SCHEMA` に optional `decision_flows`（additionalProperties:false・enum のみ・数値レンジなし）、`SYSTEM_PROMPT` に手順 4（不確実なら省略・全トピックを決定形にしない・実提案のみ・少なめ優先）、`_parse_extraction` 拡張（欠損キー空扱い、既存写像不変）+ tests（実 API 不呼出）
3. `src/artifacts.py`: envelope/`load_structure` は decision_flows を透過（欠損 fail-soft、必須キー検査は statements/relations のまま）+ 旧 structure.json 後方互換テスト
4. **FallbackRelationExtractor は decision_flows を出力しない**（確定）

**フロントエンド（今回拡大の中心 = detail.html）**
5. `detail.html`「議論の構造」パネルをプラグイン方式に再構成: (a) 共有 `buildFlowModel` + scene/hitRegions 共有基盤 + tooltip/DPR/resize、(b) 5 ビュープラグイン（rails / subway / ibis / ribbons / classic）を D5 のレイアウト式どおり実装、(c) トピック単位切替ボタン UI + localStorage 永続化 + `canRender` 自動フォールバック、(d) Sankey 注記。**レイアウトは純関数化**（同一入力→同一出力で決定性担保）。現行 classic ロジックは挙動不変で `classic` プラグイン化。

**ドキュメント/検証**
6. README / 凡例: 「検出された構造であり ground truth ではない」を decision レイヤ + 各ビューに明示（Sankey 注記含む）
7. 検証: 全既存テスト無回帰 + py_compile。フロントは各ビューを option 1/2/3/5/6・未決/hybrid/保留・幅<640 の各ケースで目視（実環境）。純関数レイアウトの決定性はユニットで担保可能な範囲で。

### 検証計画
- 本環境で実施可能: fake extractor 注入による decision_flows の保存/読込/検証規則のユニット、旧 structure.json（decision_flows 無し）の後方互換、フォールバック経路が decision_flows を出さないこと、py_compile
- 実環境で別途: 実 Claude 抽出の decision 判定品質（過剰決定化しないか）、Canvas 描画の目視（選択肢 1/2/5/6 個・未決・hybrid・保留の各ケース）、モバイル幅での P5 降格

## Implementation Notes

実装完了（2026-07-14, branch `feature/nsketch-873-decision-flow-views`）。

**バックエンド（additive・fail-soft、テスト green）**
- `src/discourse.py`: 列挙定数（OPTION_STATUS 等）、`Question/Option/Argument/Outcome/DecisionFlow` dataclass、`Relation.id` 追加、`DiscourseExtraction.decision_flows` 追加、`_assign_relation_ids`（LLM 提供 id を一意なら保持・他は決定的 `r{n}`）、`validate_decision_flows`（hard=フロー破棄／soft=warnings、未知 enum は破棄）、`_serialize_decision_flows`、`build_structure` 配線。
- `src/discourse_llm.py`: `EXTRACTION_SCHEMA` に optional `decision_flows`（top-level required に**入れない**＝後方互換、nullable は `["string","null"]`）、`SYSTEM_PROMPT` 手順4（不確実なら省略）、`_parse_decision_flows`/`_parse_one_flow`（**完全 fail-soft**＝壊れたフローは skip し base 抽出を壊さない）。
- 設計判断: LLM は relation id / argument.relation_ids を出力しない（既存 relations 経路をゼロリスクに保つ）。relation id は組立時に決定的付番。
- `src/artifacts.py`: 変更なし（`load_structure` が全 dict を透過）。
- テスト: test_discourse 14→23 / test_discourse_llm 7→10 / test_artifacts_structure 3→5。リポジトリ全体無回帰（全 12 モジュール green）。

**フロントエンド（`src/web/templates/detail.html`）**
- 「議論の構造」パネルをプラグイン方式に再構成。共有 `buildFlowModel` + scene(`{height,hit[],draw}`) + 共有 hit-test/tooltip/DPR/resize。5 ビュー: `railsView`(既定) / `subwayView`(デスクトップ・option2–4) / `ibisView`(argument必須) / `ribbonsView`(固定幅・注記「帯の太さは量を表さない」) / `classicView`。`canRender` による自動降格、トピック単位切替 UI、`localStorage["transcribe:decisionFlowView:v1"]`。
- 後方互換: `decision_flows` 無し → 従来の whole-panel classic を描画（`classicScene` 共有関数、per-topic と共用）。
- 決定的（Math.random / 物理なし）、外部 JS/CDN なし、light/dark 対応、tooltip は position:fixed。

**検証**
- `node --check` で JS 構文 OK。jinja2 で実 payload（build_structure 生成の 3 option flow）をレンダ리ング OK。
- Node + DOM/Canvas スタブで**実モジュールを実行**: flow あり（2 セクション・5 ビュー全て layout+draw+hover）例外 0 / flow なし（whole-panel classic・switcher なし）例外 0。
- ブラウザ実機の目視は未（サンドボックスがリスニングソケット/一部 node 実行を遮断）。mock の `preview.html` をユーザーに共有し実機確認を依頼。

## Review

`/team-review`（tier L, 4 観点 + OpenCode）実施, 2026-07-14。初回判定 **FAIL**（ブロッカー 1 件）→ 修正済み → 再検証グリーン。

**ブロッカー（修正済み）**
- [major] classic の後方互換崩れ: 統合 `classicScene` が旧 `summaryFor`（summary 空なら先頭2 statement 派生要約）と relation hover の `evidence.marker` を落とし、レイアウト定数もドリフト。→ **fallback/クラスタリング経路の全ジョブで見出し要約行が消失**（不変条件「decision_flows 無し＝従来どおり」違反）。修正: 派生要約復元 + marker 復元 + 旧定数（LEFT20/DSTEP30/ROWH30/HEAD44/GAP12/TEXTX+16）に一致。

**採用した minor 修正**
- per-topic 経路で無所属 statement を "その他" セクションとして描画（whole-panel との整合、silent drop 防止）。
- `validate_decision_flows`: 空白エンティティ id を hard 拒否（uniqueness すり抜け防止）。
- テスト追加: 空白 id 破棄 / hybrid カーディナリティ（>=2 で保持・1 で破棄）。test_discourse 23→25。

**却下（設計どおり・対応不要）**: OpenCode の major 3 件 — argument.relation_ids の未知参照 warn 止まり（D2 規定・LLM は relation_ids 非出力）/ 空 statement 参照は `in_topic` の `bool(sid)` で既に破棄 / first-topic-wins は backend・frontend で一貫。Security findings 0（全 textContent/fillText、innerHTML 不使用、tojson エスケープ、localStorage は既知 id にフォールバック）。

**follow-up（非ブロッカー・次タスク）**: outcome 状態遷移表のコメント化 / `_validate_one_flow` 関数分割 / resize を ResizeObserver 集約 / rails の幅<640・option>5 劣化は未実装（rails は安全な既定として常時可用の設計判断） / 実 Claude 抽出の過剰生成チェック / 5 ビュー実機目視。

再検証: 全 12 テストモジュール green（130 assertions）、py_compile clean、`node --check` OK、Node+DOM/Canvas スタブで flow/no-flow 両経路 例外 0。

## Deploy
<!-- 未実施。PR/push は外向き操作のためユーザー確認後に /deploy。branch: feature/nsketch-873-decision-flow-views -->

## Open Items / Follow-ups
- 実機ブラウザでの各ビュー目視（option 1/2/3/5/6・未決/hybrid/保留・幅<640・dark）。
- 実 Claude 抽出での decision 過剰生成チェック（プロンプト手順4 の効き）。
- 視覚デザインの磨き込み（rails/subway/ibis/ribbons の間隔・ラベル・配色）は design skill で別途。
