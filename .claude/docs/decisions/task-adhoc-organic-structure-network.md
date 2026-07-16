# Task: (no Linear) — 論理構造ビジュアライズを有機的ネットワーク表現に刷新

## Meta
- linear_id: none (Linear起票なし・ユーザー指示)
- tier: M
- created: 2026-07-16
- status: done

## Brief
detail.html「議論の構造」パネルの classic ビュー（左: 点＋矢印の2カラム / 右: テキスト）を、
グラフィックレコーディング風の有機的ネットワーク表現に刷新する。
文字と点のペアが 1 ノードとなり、relation で有機的に曲線接続、topic ごとにクラスタ配置。
公開データ契約（structure JSON: statements/relations/topics/decision_flows）は不変。

## Decision Log
- three.js は不採用: ページはビルドレス・依存ゼロのインライン JS 構成。canvas 2D で既存の
  view-plugin 枠組み（scene = {height, hit, draw}）に適合させる方が整合的で軽量。
- レイアウトは同期実行の force simulation（layout() 時に収束まで回して静的 scene を返す）。
  既存 decision-flow framework の「rAF なし・hover は再 draw のみ」の契約を維持。
- 決定論性: seeded PRNG (mulberry32)。Math.random 不使用（NSKETCH-873 の方針踏襲）。
- rails/subway/ibis/ribbons の decision-flow ビューは NSKETCH-873 の別機能なので不変。
  classic ビューのみを network ビューに置換（switcher ラベル「従来表示」→「ネットワーク」）。
- localStorage に保存済みの旧 "classic" ビュー ID は pick() のフォールバックで network に解決。

## Design
- networkScene(width, sts, rels, laneDefs): 全 classic 描画経路の置換。
  - ノード = statement: topic 色の点（半径は relation 次数でスケール）+ 最大2行の短縮ラベル。
  - エッジ = relation: 種別色（supports 緑 / causes 青 / elaborates 灰 / contrasts 橙）の
    有機曲線（seeded 曲率の quadratic）+ 矢頭。contrasts は破線。
  - 時系列スレッド: 同一 topic 内の連続 statement を極細の糸で接続（弱スプリング兼、
    グラレコ的な「会話の流れ」表現）。
  - トピッククラスタ: seeded 不整形ブロブ（半透明 topic 色）+ topic ラベル。
    クラスタアンカーは serpentine 配置（時系列順）で全体キャンバスに分散。
  - hover/tooltip は既存 bindHover の hit-circle 契約を流用（本文全文 + relation tip）。
  - キャンバス右上に relation 種別ミニ凡例を描画（network ビュー時のみ）。
- renderWholePanelClassic → renderWholePanelNetwork（decision_flows 無しジョブの既定表示）。
- 高さはノード数・クラスタ行数から算出（clamp）。resize で再レイアウト。

## Implementation Notes
- 変更ファイル: src/web/templates/detail.html のみ（+302/-77）。
- classicScene/classicView を networkScene/networkView に置換。
  - mulberry32 seeded PRNG / estWidth+wrapLabel（全角11px・半角6px 概算で2行折返し+省略）。
  - クラスタアンカー: serpentine グリッド（幅340px毎に列、蛇行で時系列の読み順維持）。
  - ノード初期配置: クラスタ内 golden-angle スパイラル（時系列順）。
  - Force: 反発 5600/d² + 最小距離68px 分離 + relation スプリング(rest118)
    + クラスタ内時系列チェーン(rest88, 弱) + アンカー引力(0.011/0.016) + 減衰0.8。
    iters = clamp(90, 26000/n, 300) を layout() 内で同期実行。
  - 描画: 有機ブロブ（quadratic 中点スムージング+seeded wobble）、波線下線タイトル、
    relation 曲線矢印（種別色・confidence でα/太さ・contrasts破線・hover強調）、
    クラスタ内時系列点線スレッド、右上に出現 relation 種別のみのミニ凡例。
  - hover: 既存 hit-circle 契約。tooltip = 話者+全文 + relation tips（種別を日本語化）。
- per-topic switcher モードは opts.showTitles=false でキャンバス内タイトル抑制（df-head と重複回避）。
- localStorage の旧 "classic" 選択は pick() で "network" に移行。
- rails/subway/ibis/ribbons・データ契約・バックエンドは無変更。

## Review
- Self-review (Claude, M tier):
  - node --check で全インライン script 構文 OK。ブラウザ console エラーなし。
  - 視覚検証: light/dark、1000px/390px、flows有無 の各組合せをスクリーンショット確認。
  - hover tooltip をグリッド走査で検証（7ノードでヒット・全文表示確認）。
  - resize イベント再レイアウトのエラーなし。
  - セキュリティ: innerHTML 不使用（textContent のみ）、データ注入は既存 tojson 経路のまま。
  - 決定論性: Math.random 不使用、seeded PRNG のみ（NSKETCH-873 方針維持）。
- 結果: PASS

## Deploy
- branch: feature/organic-structure-network
- commit: 44461dc feat(web): organic network canvas for logical-structure panel
- PR: https://github.com/hidemaro-nsketch/transcribe/pull/14
- Linear 投稿: なし（起票なし・ユーザー指示）
