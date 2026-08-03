# Task: ADHOC — 「議論の構造」ネットワークもノード接続・編集可能にする

## Meta
- linear_id: ADHOC
- tier: M
- created: 2026-08-02
- status: done

## Brief

### Current State
- `output/{stem}.structure.json` は version 1 envelope 内に canonical な `statements`、有向・型付き `relations`、`topics`、それらを参照する派生 `decision_flows` を保持する。`Relation.type` は `supports / causes / elaborates / contrasts`、向きは `source -> target` である。`load_structure()` は現在 base payload の存在・kind のみを確認し、編集 overlay は扱わない。
- `GET /jobs/{filename}` は structure envelope をそのまま `detail.html` に渡す。一方、直前タスクで graph artifact 向けに revision 付き overlay、厳格 validation、プロセス内 lock、atomic replace、64 KiB streaming 制限、`PUT /jobs/{filename}/graph-edits` が確立済みである。
- 「議論の構造」は `statements / relations / topics / decision_flows` を一度読み、`buildFlowModel()` と `VIEWS`（rails/subway/ibis/ribbons/network）で描画する。`networkScene()` は seeded PRNG と同期反復で毎回 scene を静的に生成し、通常時は hover redraw のみで rAF simulation を持たない。
- network view は canonical statements/relations を直接描くが、rails/subway/ibis/ribbons は主に `decision_flows` の question/option/argument/outcome を描き、canonical relations を直接描かない。したがって「共通 structure を合成するだけで追加 relation が全ビューに自然反映」は現実装では成立しない。
- decision flow がある場合は topic ごとの section が生成され、network view も topic 内 relation のみを描く。flow がない場合だけ全 topic を一枚の network canvas に描く。既存 hit-test は scene の node hit のみで、edge 選択・drag・mutation はない。
- テストは artifact の structure 契約を `tests/test_artifacts_structure.py`、graph edits を `tests/test_artifacts.py` / `tests/test_web_app.py` で検証する。FastAPI が任意依存の direct-run 環境も考慮が必要である。

### Goal
- 完了済みジョブの「議論の構造」において、ネットワークビューからメモ的 statement の追加、有向 relation の2クリック追加、statement/relation の削除、ドラッグ固定を行え、編集のたびに再レイアウトされるようにする。
- 編集を immutable な抽出結果に対する revisioned overlay として structure artifact に永続化し、reload 後に復元し、競合時は silent overwrite しない。
- 合成済み structure を表示モデルの唯一の入力にし、各ビューで表現可能な編集は再描画へ反映する。ただし canonical relation を直接表現しない4ビューへの見せ方は Gate 1 で決定する。

### Scope
- In: `src/artifacts.py` の structure edit schema/load/update、`src/web/app.py` の `PUT /jobs/{filename}/structure-edits` と detail context、`detail.html` の合成モデル・network 編集 UI・再レイアウト・保存、artifact/API/template tests。
- In: user statement の topic 帰属、user relation の型・向き、base/user 要素の削除表現、normalized pinned position、revision conflict。
- Out: 抽出器と `src/discourse.py` の生成ロジック変更、既存 base structure の破壊的書換え、DB/認証、新規依存、word-network・live画面・topic treemap の挙動変更。

### Constraints
- 実装ブランチは open PR #18 の `feature/editable-word-network` から `feature/editable-structure-network` を stacked で切る。main 起点にしない。
- vanilla JS + Canvas、stdlib + 既存 FastAPI のみ。graph-edits で確立した traversal/job state/body limit/validation/revision/atomic write の防御を維持する。
- base statement/relation/topic/decision_flow は immutable。legacy structure artifact は空 overlay（revision 0）として表示・初回編集可能にする。
- user statement は transcript provenance を装わず、base statement と衝突しない安定 ID を持つ。relation は有向・型付きで、自己接続、重複、不可視 endpoint、未知型を拒否する。
- 派生 `decision_flows` の参照整合性を壊さない。base statement の非表示が flow 参照を残す場合は、派生ビュー側で欠落を fail-soft に処理するか、編集制約を設ける。

### Success Criteria
- network view で明示編集モード、ノード追加、source→target の2クリック接続、ノード/edge 削除、drag pin/unpin が動作し、各 mutation 後に bounded deterministic layout を再計算する。
- 保存中/保存済み/未保存/409/失敗が判別でき、reload で user statements/relations、hidden base items、positions が復元される。
- structure loader/update が legacy、canonicalization、参照・型・件数・座標 validation、revision conflict、base 不変、atomic roundtrip を満たす。API は graph-edits と同等の traversal/job/done/artifact/schema/64 KiB 防御を持つ。
- 合成 structure が network と view model 再構築の共通入力になる。非-network 4ビューへの relation 表示仕様は Gate 1 の選択に従ってテスト可能な形で固定する。

## Decision Log
- [orchestrate] DECISION: tier=M（detail.html structure パネル + app.py API + artifacts.py 編集レイヤ + テストで 4-8 ファイル・複数パターン・中リスク）
- [orchestrate] DECISION: 対象は detail ページの「議論の構造」パネル（decision-flow マルチビュー + networkScene）。編集はネットワークビューで行う
- [orchestrate] DECISION: 操作は「言葉のネットワーク」と同じ全編集機能。ノード追加も可能（文字起こしにないメモ的ノードをユーザーが追加できる）
- [orchestrate] DECISION: 追加した接続はデータが共通のため rails / subway / ibis / ribbons の他ビューにも自動反映される
- [orchestrate] DECISION: 編集内容は structure artifact に永続化（言葉のネットワークと同じ overlay + revision 方式）
- [orchestrate] DECISION: PR #18（editable-word-network）未マージのため feature/editable-word-network から stacked ブランチを切る
- [orchestrate] Gate 1 承認 2026-08-02: 選択肢 A（ネットワーク中心）。ユーザー接続の完全表示はネットワークビュー限定、他4ビューは合成 structure での fail-soft + 「編集あり」注記。relation type は `elaborates` 固定、decision_flow semantics を任意接続から推測しない
- [startproject] PRE 2026-08-03: tier=M の計画を開始。ヒアリング済みのため DONT-ASK MODE で現ブランチと直前タスクを調査。LINEAR_ID=ADHOC のため Linear 操作は行わない。
- [startproject] DECISION 2026-08-03: base structure は immutable とし、同じ version 1 structure artifact に optional `edits` overlay を追加する。legacy artifact は空 revision 0 として扱う。
- [startproject] DECISION 2026-08-03: user node は provenance のない statement（stable user ID、text、topic_id）として保存し、user edge は canonical Relation と同じ directed `source/target/type` を持つ。2クリック順を方向とし、初期 relation type は中立的な `elaborates` とする案を推奨する。
- [startproject] DECISION 2026-08-03: overlay は `revision / statements / relations / hidden_statement_ids / hidden_relation_ids / positions` を持つ。base relation は既存 stable `id` で非表示化し、position は statement ID と normalized x/y で pin を表す。
- [startproject] DECISION 2026-08-03: API は `PUT /jobs/{filename}/structure-edits` で overlay 全体を期待 revision 付き置換する。graph-edits の request streaming/body上限、route guard、lock、atomic write を再利用可能な小さな共通 helper に整理しつつ、graph と structure の domain validation は分離する。
- [startproject] DECISION 2026-08-03: `networkScene()` は常駐 rAF force simulation に変更せず、mutation ごとに現在位置/保存 pin を seed として bounded synchronous relaxation を再実行する。これにより deterministic/static hover model と「編集ごとの再レイアウト」を両立する。
- [startproject] DECISION 2026-08-03: 編集 UI は word-network と同じ明示 edit mode、inline node form、2クリック接続、選択削除、drag pin/unpin、debounced PUT、saving/dirty/409 状態遷移を踏襲する。ただし relation は有向であることを UI 文言と矢印で明示する。
- [startproject] DECISION 2026-08-03: user statement は編集対象 network section の topic_id を継承する。全体 network では topic なし（「その他」）とし、topic/decision-flow 自体の編集は行わない。
- [startproject] DECISION 2026-08-03: 実装は `feature/editable-word-network` から stacked `feature/editable-structure-network` を作成する。
- [startproject] DECISION 2026-08-03: 実行環境に `task` / `todowrite` tool がないため、tier=M の subagent 設計相談は独立設計レビューとして Design に記録し、実装タスクは本 TASK_FILE の `Plan (Tasks)` に集約した。
- [startproject] DECISION 2026-08-03: 現行 rails/subway/ibis/ribbons は canonical relations を入力にせず decision_flows の派生 entity を描くため、「overlay 合成だけで全ビューへ接続が自然反映」という前提は不成立。表示仕様に実質的選択肢があるため Gate 1 を発動する。
- [startproject] POST 2026-08-03: Phase 1–3 の計画記録を完了。LINEAR_ID=ADHOC の明示指示により Linear コメント投稿をスキップ。非-network 4ビューへの relation 反映方式について Gate 1 承認待ち。
- [team-implement] POST 2026-08-03: Gate 1 A に従い structure overlay/API/network editor を実装。非-network views は composed structure の hidden reference を fail-soft に扱い、任意 relation から decision-flow semantics は導出しない。Linear ID `ADHOC` は MCP で issue 解決できずコメント投稿は失敗。
- [team-review] POST 2026-08-03: tier=M（Quality + Security）レビューを実施し FAIL。保存中の mutation が先行 PUT 応答で失われる競合と、refresh ごとに旧 save timer / resize listener が残る問題を blocker と判定。direct tests 27/27、compileall、JS syntax、diff check は通過。FastAPI 不在のためブラウザ実機確認は未実施。Linear ID `ADHOC` は MCP で issue 解決できず開始・結果コメント投稿は失敗。
- [team-review] POST 2026-08-03: blocker 再レビューを実施し PASS。RB1 は render lifecycle 外の単一 save coordinator と dirty 時の revision-only 継承により後続 mutation を保持、RB2 は単一 timer と resize dispatcher の handler 差替えにより listener 増加を解消したことを確認。direct tests 28/28、compileall、JS syntax、diff check、追加 structural check は通過。Linear ID `ADHOC` は MCP で issue 解決できず開始・結果コメント投稿は失敗。
- [deploy] POST 2026-08-03: feature/editable-structure-network を push し、feature/editable-word-network を base とする stacked PR #19 を作成。#18 マージ後に main へ retarget する。Linear ID `ADHOC` のため Linear 更新はスキップ。

## Design

### 設計相談（独立レビュー相当）
指定された `task` subagent tool はこの実行環境に提供されていないため、既存 graph editor の成功/失敗事項と structure renderer の責務を切り離して独立レビューした。結論は、graph overlay の汎用 validator 化を急ぐより、atomic I/O・limited stream 等の機械的共通部だけ共有し、structure の有向型付き relation と flow 参照整合性は専用 validator にするべきである。また、静的 deterministic scene を常駐 simulation に変えると全 view 共通 hover contract と resize 再現性を崩すため、編集時だけ bounded relaxation を再実行する方が小さく安全である。

最大の設計上の発見は、rails/subway/ibis/ribbons が canonical `relations` を描いておらず、decision_flow entity を描く別表現だという点である。任意の user relation を option/argument/outcome に自動変換すると意味を捏造するため避けるべきである。推奨は、canonical relation の編集結果は network view で完全表示し、他4ビューは合成 statements による欠落防止と「ネットワークに編集あり」表示までに留める。全ビュー上に relation を可視化する要件が厳密なら、各 scene に statement anchor と relation overlay を追加する別スコープが必要である。

### Structure edit overlay
```json
{
  "edits": {
    "revision": 3,
    "statements": [{"id":"u:...", "text":"確認事項", "topic_id":"t1"}],
    "relations": [{"id":"ur:...", "source":"s2", "target":"u:...", "type":"elaborates"}],
    "hidden_statement_ids": ["s5"],
    "hidden_relation_ids": ["r2"],
    "positions": [{"id":"u:...", "x":0.42, "y":0.31}]
  }
}
```
- ID は client 生成の衝突しにくい prefix 付き値を使い、文字長・制御文字・重複をサーバで拒否する。表示 label は `text` であり ID と分離する。
- user statement は `utterance_index=null`, `speaker="メモ"`, `terms=[]` 相当として合成し、抽出由来でないことを tooltip で示す。永続 schema には必要最小限の `id/text/topic_id` のみを持つ。
- user relation は directed。UI の1点目が source、2点目が target。type selector を初回スコープに含めない場合は `elaborates` 固定、confidence/evidence は持たず表示時に user-created と識別する。base と同じ `(source,target,type)`、user relation ID、自己 loop を禁止する。
- base statement 削除は hidden、user statement 削除は配列から除去。いずれも incident user relations、hidden base relations、position を同時除去する。base relation 削除は stable relation ID で hidden 化する。
- base statement を hidden にしても base `decision_flows` は書き換えない。view model は hidden reference を無視し、flow view に「編集により一部要素を非表示」と注記する。

### API / persistence
- `load_structure()` は base fields を検証後 `normalize_structure_edits()` を適用し、invalid overlay を含む artifact は fail closed。detail context には `structure`（base）と normalized `structure_edits`、endpoint URL を分けて渡す。
- `update_structure_edits(output_dir, stem, expected_revision, edits)` は structure 専用 lock 内で再読込→現 revision 比較→revision increment→normalize→atomic replace を行う。base envelope fields は一切変更しない。
- endpoint は graph route と同じ `{revision, edits}` exact schema、64 KiB streaming reader、404/409/413/422 mapping を使う。limited body reader は domain-neutral 名へ整理できるが、既存 graph API の互換をテストで保護する。

### Client composition / editing
- script 起動時に `composeStructure(base, edits)` を一度通し、`statements / relations / topics / flows` の各 index と `buildFlowModel()` は合成結果だけを見る。mutation 後は compose→対象 section/model/scene 再構築→save schedule の順にする。
- 編集 control は network view 選択時だけ有効化する。per-topic network section で追加した node はその topic に所属し、cross-topic edge は全体 network surface がない現状では作成不能とするか、別の全体編集 canvas を追加する必要がある。推奨はパネル先頭に「全体ネットワーク」編集 surface を1つ追加し、topic views は閲覧用とする案である。
- `networkScene()` は `opts.positions` と optional prior scene coordinates を受け取り、pin node は relaxation 中に動かさない。mutation 時は同期 relaxation を再実行し scene を即時差替えする。大規模 graph でも既存 bounded iteration を維持し、rAF loop は導入しない。
- edge hit-test は quadratic curve と pointer の距離を近似し、node selection を優先する。drag は pointer capture/cancel を扱い、release で normalized position を保存する。
- word editor で修正済みの saving/dirty behavior をコピーし、通信/422失敗では自動無限 retry せず、in-flight mutation のみ成功 revision を継承して再送する。409 は再読込を要求する。

### Gate 1 options
- **A（推奨）:** canonical relation の完全な編集反映は network view に限定。rails/subway/ibis/ribbons は合成 structure で再構築し、hidden reference を fail-soft に扱い、編集あり注記を出す。意味の捏造がなく、既存 flow semantics を保持する。
- **B:** 全4ビューに statement anchor + relation overlay layer を追加し、任意 user relation を重ねて描く。要件を視覚的に満たすが、4 view 個別の座標対応・交差・hit-test が増え tier=L 相当へ拡大する。
- 不採用: user relation から decision_flow の option/argument/outcome を自動生成する方式。接続だけから stance/status/outcome を推測し、検出データとユーザー入力の意味を混同するため。

## Plan (Tasks)
1. `feature/editable-word-network` の最新 commit から `feature/editable-structure-network` を作り、PR #18 の graph-edit基盤が存在することを確認する。
2. Gate 1 の選択を確定し、relation type 初期値、全体/話題別編集 surface、非-network view の受入条件をテスト可能な仕様に固定する。
3. `tests/test_artifacts_structure.py` に legacy empty overlay、user statement/relation roundtrip、directed/type canonicalization、hidden/incident cleanup契約、position、limits、conflict、base不変、corrupt fail-closed tests を追加する。
4. `src/artifacts.py` に structure edits の空値・normalizer・domain errors・revision付き atomic update を実装し、必要なら graph/structure 共通の limited stream/atomic I/Oだけを小さく抽出する。
5. `src/web/app.py` に `PUT /jobs/{filename}/structure-edits` と detail context を追加し、graph endpoint と同等の traversal/job done/artifact/64 KiB/exact schema/status mapping を実装する。
6. `tests/test_web_app.py` に structure PUT の helper/route契約（success、unknown/not done/missing、bad JSON/schema、413、422、409）と既存 graph endpoint の回帰を追加する。
7. `detail.html` に structure toolbar、node form、save status、overlay JSON を追加し、base+overlay composition を全 index/model/view の手前に置く。
8. network scene を editable scene contract に拡張し、node/curve edge hit-test、明示 edit mode、memo node追加、directed 2-click relation追加、node/edge削除、pointer pin/unpin、mutationごとの同期再レイアウトを実装する。
9. view/section 再構築を実装し、Gate 1 Aなら非-network view の fail-soft + edit note、Bなら各 scene の statement anchor/relation overlay を実装する。resize/reload/flowなし/その他topicを検証する。
10. debounced PUT と saving/dirty/409/error 状態を word editor の確立済み挙動に揃え、失敗時無限retryやhidden endpoint残存を回帰テストする。
11. direct tests/pytest、compileall、editor JS `node --check`、Jinja render smoke、`git diff --check` を実行し、可能なら desktop/touch、reload、409、offline、dark/light のブラウザ確認結果を Implementation Notes に記録する。

## Implementation Notes

### 実装サマリー
- structure artifact に revision 付き immutable overlay（メモ、relation、非表示、pin）を追加し、legacy artifact も revision 0 として読み込むようにした。
- `PUT /jobs/{filename}/structure-edits` を graph edits と同じ 64 KiB streaming / conflict / atomic-write 契約で追加した。
- 構造ネットワークに全体編集 canvas、メモ追加、有向2クリック接続、node/edge 削除、drag pin/unpin、debounced 保存を追加した。Gate 1 A により決定フローの意味は推測せず、他ビューには編集注記のみを表示する。
- フォローアップで、ドラッグ中は既存 scene の canvas だけを再描画して座標を保持し、pointerup 時にそのライブ座標を pin として保存するよう修正した。resize listener は単一の active-editor dispatcher にし、保存成功・新規編集時に error status class を解除するようにした。
- review blocker 修正として save coordinator（dirty/saving/timer）を editor render lifecycle の外へ移動した。in-flight mutation がある成功応答ではローカル overlay を保持して revision のみ更新し、後続 debounce が完全なローカル状態を送信する。structure の resize は単一 window listener と refresh ごとの handler リスト差替えに統一し、旧 canvas/section を捕捉する listener を残さない。

### 変更ファイル
- `src/artifacts.py` — structure overlay の validation、canonicalization、atomic revision update。
- `src/web/app.py` — structure edit endpoint と template context。
- `src/web/templates/detail.html` — composed structure、編集 UI、static bounded relayout。
- `tests/test_artifacts_structure.py` — overlay の legacy/roundtrip/validation 契約。

### テスト
- `python3 tests/test_artifacts.py`、`python3 tests/test_artifacts_structure.py`、`python3 tests/test_web_app.py`。
- `tests/test_web_app.py` に coordinator の revision 継承と単一 resize dispatcher を確認する静的回帰 assertion を追加した。
- `python3 -m compileall -q src`、editor JS `node --check`、`git diff --check` pass。
- `pytest` は環境に未導入のため direct-run harness を使用。

### 残課題・注意点
- Linear ID `ADHOC` は実在 issue として解決できず、開始・完了コメント投稿は MCP 側で失敗した。
- ブラウザ実機確認は未実施。編集 canvas は全体 network surface、topic sections は閲覧用である。

## Review

### 判定: FAIL

### コードレビュー統合結果

#### Quality Reviewer
- [major] `src/web/templates/detail.html:1434-1435` — 保存状態（`dirty` / `saving` / `timer`）を `renderEditor()` のローカルに置いたまま、各 mutation で `refresh()` が editor を作り直している。保存中に次の編集が入ると、先行 PUT の成功応答が global `edits` を古い snapshot に置換して後続編集を消す。さらに各 refresh の debounce timer が独立して残るため、同じ revision の PUT が並行し、不要な 409 を発生させる。成功基準の「in-flight mutation のみ成功 revision を継承して再送」を満たさない。
- [major] `src/web/templates/detail.html:1386,1419,1435` — `refresh()` のたびに `renderPerTopic()` / `renderWholePanelNetwork()` が新しい resize listener を追加し、旧 canvas を捕捉する listener を解除しない。editor 自体の dispatcher は単一化されているが、閲覧 surface の listener leak は残っており、編集回数に比例して resize 時の再レイアウトと保持 DOM が増える。
- [minor] `src/web/templates/detail.html:1382` — Gate 1 A の「編集あり」注記条件に user statement と pin が含まれないため、メモ追加のみの overlay では注記されない。
- [minor] `tests/test_artifacts_structure.py:194-207`, `tests/test_web_app.py` — directed/type/reference の基本 rejection はあるが、structure の重複 relation/ID、上限境界、座標境界、revision conflict、corrupt overlay、PUT route の 404/409/413/422 を直接検証していない。`tests/test_web_app.py` は今回未変更で graph helper のみを検証している。

#### Security Reviewer
- [minor] `.claude/rules/security.md` はリポジトリに存在しないため、指定観点で独立確認した。traversal guard、DONE check、64 KiB の Content-Length/stream 二重制限、exact top-level schema、domain allow-list validation、409、atomic replace、fail-closed load は graph-edits と同等に適用されている。
- [minor] user text は Canvas `fillText` と tooltip `textContent` に渡され、初期 JSON は Jinja `tojson` のため、確認した経路に DOM XSS はない。秘密情報のハードコード、SQL、認証境界の追加もない。
- [minor] client ID の `Date.now()` / `Math.random()` は予測可能だが、認可 token として使われず、server が衝突・重複を拒否するため security blocker ではない。ただし relation ID は時刻のみなので衝突時の UX 改善余地がある。

#### 統合サマリー
- blocker は client の保存 state lifecycle。mutation 後の再描画と永続化 state を分離し、単一 save coordinator が送信 snapshot と送信中に発生した mutation を保持して、成功 revision を後続 payload に継承する必要がある。
- resize handler も単一 dispatcher または明示 cleanup に統一し、refresh で旧 section/canvas を捕捉しないこと。
- Security 上の critical / major は検出なし。

### 動作検証結果

#### ブラウザ表示確認（該当時）
- 対象は UI 変更だが、実行環境に FastAPI がなく detail page を起動できないため未実施（`ModuleNotFoundError: fastapi`）。
- structure editor script を抽出し `node --check /tmp/opencode/structure-editor.js` は通過。
- blocker の保存競合と listener leak はコードパスから確定でき、ブラウザ確認の有無にかかわらず FAIL。

#### テスト実行結果（該当時）
- `python3 tests/test_artifacts.py` — 12/12 pass
- `python3 tests/test_artifacts_structure.py` — 8/8 pass
- `python3 tests/test_web_app.py` — 7/7 pass
- `python3 -m compileall -q src` — pass
- `git diff --check` — pass
- 失敗テストなし。ただし上記 major の非同期保存競合を覆う client test と structure PUT route test は存在しない。

### 申し送り事項（minor）
- 修正後は「保存開始直後に別 mutation」「450 ms 内の連続 mutation」「409」「通信失敗後の手動再編集」を browser/fake-fetch test で固定する。
- memo-only overlay でも Gate 1 A の編集注記を表示する。
- structure validator/API の重複・件数・座標・conflict・status mapping の境界テストを追加する。
- Linear `ADHOC` は issue として解決できず、必須コメントは MCP 400 のため投稿不能。

---

### 再レビュー判定: PASS

#### 前回 blocker の検証
- [resolved] RB1 — `src/web/templates/detail.html:1422-1466` で `editorDirty / editorSaving / editorTimer` が `renderEditor()` の外に移動し、refresh 後も単一 coordinator が維持される。in-flight 中に mutation が起きた場合、成功応答は `edits.revision` のみ更新し、`finally` から後続 debounce を予約するため、ローカル overlay を古い応答で上書きしない。timer も coordinator に1つだけ存在する。
- [resolved] RB2 — `src/web/templates/detail.html:1422-1434,1477` で window resize listener は dispatcher の1本だけ登録され、refresh は `structureResizeHandlers = []` で現行 canvas/section の handler に差し替える。旧 DOM を捕捉する listener は増加しない。

#### 新規ブロッカー
- critical / major ともに検出なし。
- 前回の minor（memo-only 編集注記、validator/API 境界テスト拡充）は非ブロッカーの申し送りとして継続。

#### 再検証結果
- `python3 tests/test_artifacts.py` — 12/12 pass
- `python3 tests/test_artifacts_structure.py` — 8/8 pass
- `python3 tests/test_web_app.py` — 8/8 pass（coordinator/dispatcher 静的回帰 test を含む）
- structure editor 抽出 + `node --check` — pass
- coordinator/dispatcher の追加 structural assertions — pass
- `python3 -m compileall -q src` — pass
- `git diff --check` — pass
- FastAPI が環境にないためブラウザ実機確認は引き続き未実施。
- Linear `ADHOC` は issue として解決できず、再レビュー開始・結果コメントは MCP 400 のため投稿不能。

## Deploy

### デプロイ結果: SUCCESS

### 実行内容
- デプロイ日時: 2026-08-03T22:46:52+09:00
- feature ブランチ: `feature/editable-structure-network`
- コミット: `d575f35 feat(web): enable editable structure network`
- PR: https://github.com/maro525/transcribe/pull/19
- stacked PR の base: `feature/editable-word-network`（#18 マージ後に `main` へ retarget）

### デプロイ後検証結果

#### ブラウザ確認
- FastAPI が環境にないため未実施（`ModuleNotFoundError: fastapi`）。

#### スモークテスト
- `python3 tests/test_artifacts.py` — 12/12 pass
- `python3 tests/test_artifacts_structure.py` — 8/8 pass
- `python3 tests/test_web_app.py` — 8/8 pass
- `python3 -m compileall -q src`、structure editor の `node --check`、`git diff --check` — pass

### 申し送り事項
- team-review は PASS。RB1（保存競合）と RB2（resize listener leak）は解消済み。
- memo-only 時の Gate 1 A 注記、および validator/API の境界テスト拡充は minor のフォローアップ候補。
- `src-tauri/` は無関係の未追跡ディレクトリのためコミット対象外。
