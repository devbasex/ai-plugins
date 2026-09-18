# #550: 無人の運転（工程を conductor / supervisor / worker の 3 層へ出す）

## 関連リンク

- 課題: #550（マイルストーン 18）
- 要求と受け入れ条件: [issue-550-657-requirements.md](issue-550-657-requirements.md)（**この Pull Request の範囲は AC1〜AC19 と AC4b**）
- 設計: [issue-550-657-design.md](issue-550-657-design.md)（決定 1 の 2 本目）
- 契約: [issue-550-657-design-contracts.md](issue-550-657-design-contracts.md)
- 1 本目（測定・`develop` にマージ済み）: #749

## モード

`standard`。規約の文書を新設し、`/goal` の進め方（公開の振る舞い）を変える。

## 目的と非目的

達成したい状態:

- `/goal /ndf:development-workflow <指示>` が、2 つの関門と失敗で止まる場合以外に人間の入力を求めず、
  複数の課題を設計から振り返りまで通せる規約が文書にある
- 3 層（conductor / supervisor / worker）の責務・持ち場・報告の形・起動の指示・モデルの基準が 1 つの参照文書にまとまっている
- 起動の指示が `description` に `<持ち場>: <課題番号>` / `<作業の種類>: <一言>` の形を必ず書かせる
  （1 本目の測定で、実物の記録の supervisor 8 件すべてが `role = その他` に落ちたため）

やらないこと:

- 中断と再開（`interrupted` / `wait-reset`、#657）。3 本目が `agent-layers.md` へ独立した節を足す
- 測定（`transcript_agents.py` / `skill-stats` / `retrospective`）。1 本目で `develop` に入っている
- `parallel-work.md` と `issue-plan-strategy` の編集（G2 の範囲。3 層と本数の対応だけを `agent-layers.md` が持つ）
- 関門の数・承認の形・提示物の変更

## 前提

- 前提 1: `development-workflow/SKILL.md` は 500 行で分割の基準（`scripts/check-doc-line-limit.py`）に達している。
  **この Pull Request の後も 500 行以下である**（決定 23）
- 前提 2: `agent-layers.md` は 3 本目が中断と再開の節を足す。**500 行の余りを 2 本目が使い切らない**
- 前提 3: 承認の印の判定（`workflow-guard.sh`）がサブエージェントの Bash に掛かるかは未確認（設計の U1）。
  掛からなくても関門は保たれる（マージするのは conductor である）

## 受け入れ条件

要求の文書の AC1〜AC19 と AC4b を、この Pull Request では**規約の文書とテスト**で満たす
（AC1・AC2・AC5・AC6・AC7 の実機の確認は、3 本がそろった後のリリース後テストで行う）。

- [ ] AC3・AC17・AC18: supervisor は承認を求めずに報告を返して終わる。worker は人間へ問わず、
  別のサブエージェントを起動しない。**worker の報告を conductor へ転送しない**
- [ ] AC4: supervisor の報告が 10 項目を持ち、その中に次の持ち場の名前が入る
- [ ] AC4b: conductor が止まるときに「持ち場の一覧」を 1 回出す。出す時点が 3 つ書かれている
- [ ] AC5: 報告の形を持たない終わりは `SendMessage` で 3 回まで続けさせ、4 回目は送らずに止まる
- [ ] AC9: 持ち場の表（5 つ・工程・単位・始まり・終わり・返す関門）と、切れ目 4 点・関門 2 つとの対応がある
- [ ] AC10: 層ごと・持ち場ごとにモデルを選べる。**既定**と**落とす判断基準 2 つ**がある
- [ ] AC11: `cross-review/SKILL.md` の「メイン」が**収束ループを駆動している supervisor**を指す。
  修正は worker で行い、supervisor の context window に diff を載せない
- [ ] AC12: 粒度の基準が値ではなく比で書かれ、**supervisor に当てる**と書かれている。規約の文書に固定費の実測値が無い
- [ ] AC13: `context-window.md` に**モデルに依る目安**と**リポジトリに依る固定費**の区別がある
- [ ] AC14: `/goal` の引数でない呼び方では 3 層へ出さない（進め方が変わらない）
- [ ] AC15: 関門以外の Skill の確認は提示して進め、確認待ちで止まらない
- [ ] AC16: `SKILL.md`・`context-window.md`・`agent-layers.md` の語が 3 層と `context window` に揃い、
  **「窓」と「親」（実行の主体の意味）が残っていない**
- [ ] AC19: 委譲しない 5 つが worker へ出されない
- [ ] AC64: 並行の本数を数える単位が supervisor（1 本 = 1 つの作業ツリー）で、同時に動かす worker の既定がある
- [ ] AC62（退行しない）: 関門の数（2）と承認の止まり方（`AskUserQuestion`）・提示物（`approval-request.md`）が変わっていない
- [ ] AC63（退行しない）: `check-skill-frontmatter.py` / `check-doc-line-limit.py` / `pytest` が通る

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 3 層の規約を新設の `references/agent-layers.md` に置く | 採用 | 設計の決定 22・25。`SKILL.md` は行数の上限に達しており、`parallel-work.md` は G2 が触る |
| B | `SKILL.md` の `/goal` の節へ直接書く | 不採用 | 500 行を超える。`check-doc-line-limit.py` が落ちる |
| C | `parallel-work.md` へ節を足す | 不採用 | 並行しないときに読まれない。#621 / #540 と差分が重なる |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| conductor | 人間と対話しているセッション。`/goal` を受け、`AskUserQuestion` を持つ唯一の層 |
| supervisor | 1 つの持ち場（連続する工程の束）を通すサブエージェント |
| worker | 1 つの作業を行うサブエージェント。別のサブエージェントを起動しない（葉） |
| 持ち場 | supervisor 1 つが通す工程の束。`設計` / `実装` / `検査` / `取り込み` / `仕上げ` |
| 作業の種類 | worker 1 つが行う作業の分類。`調査` / `修正` / `検証` / `集計` |
| context window | セッションが保持している内容の全体と、その量 |

## 不変条件

- 関門は 2 つで増えない。承認を取れるのは conductor だけである
- worker は葉である（深さ 3 を作らない）
- 規約の文書は固定費の実測値を持たない（比だけを持つ）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（`/ndf:development-workflow`） | `/goal` の引数として呼ばれたときの進め方が 3 層になる | 対話での呼び出しは変えない（AC14） |
| データスキーマ | なし | — |

## 修正対象

- `plugins/ndf/skills/development-workflow/references/agent-layers.md`（新設）
- `plugins/ndf/skills/development-workflow/SKILL.md`（`/goal` の節・参照・語）
- `plugins/ndf/skills/development-workflow/references/context-window.md`（語・比の基準・目安と固定費の区別）
- `plugins/ndf/skills/cross-review/SKILL.md`（「メイン」の定義）
- `plugins/ndf/skills/development-workflow/tests/test_agent_layers_doc.py`（新設）

## タスク分解

### Task 1: 規約の文書のテストを書く

- **対象ファイル:** `plugins/ndf/skills/development-workflow/tests/test_agent_layers_doc.py`
- **変更内容:** 設計の「テスト設計」の表が挙げる項目（3 層の責務の表・持ち場の表・報告の 10 項目と 5 項目・
  続けさせる回数 3・モデルの基準 2 つ・委譲しない 5 つ・転送しない文・AC4b の 3 つの時点・
  `description` の形・並行の単位・`/goal` 以外で 3 層へ出さない・「メイン」の定義・
  モデルに依る目安とリポジトリに依る固定費・固定費の実測値が無いこと・「窓」と「親」が無いこと）を固定する
- **満たす受け入れ条件:** AC3・AC4・AC4b・AC5・AC9〜AC19・AC64
- **進め方:** 先に落ちるテストを書く（対象の文書がまだ無いので `FileNotFoundError` で落ちる）

### Task 2: `agent-layers.md` を新設する

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/agent-layers.md`
- **変更内容:** 3 層の責務・持ち場の表（5 つ・関門の列・切れ目 4 点との対応）・モードごとの組み方・
  起動の指示（`description` の形と `prompt` の項目と守る規則）・報告の形（2 段）・
  報告なしで続けさせる回数・モデルの基準・委譲の線・並行の本数の単位・到達点の置き直し（`SKILL.md` から移す）
- **満たす受け入れ条件:** AC3・AC4・AC4b・AC5・AC9・AC10・AC12・AC15・AC17〜AC19・AC64
- **進め方:** Task 1 のテストを通す

### Task 3: `SKILL.md` の `/goal` の節を入口だけにする

- **対象ファイル:** `plugins/ndf/skills/development-workflow/SKILL.md`
- **変更内容:** 「他者の承認が要るときは、到達点を置き直す」の小節を `agent-layers.md` へ移し、
  節には 3 層へ出すことと参照先を残す。参照の一覧へ 1 行足す。「窓」を `context window` に直す
- **満たす受け入れ条件:** AC14・AC16
- **進め方:** 行数が 500 を超えないことを `check-doc-line-limit.py` で確かめる

### Task 4: `context-window.md` の語と基準を直す

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/context-window.md`
- **変更内容:** 「窓」を `context window`、「親」を「上の層」／`supervisor` へ直す。
  モデルに依る目安とリポジトリに依る固定費を区別する節を足し、比の基準を supervisor に当てると書く。
  3 層の運転（`agent-layers.md`）への参照を足す
- **満たす受け入れ条件:** AC12・AC13・AC16
- **進め方:** Task 1 のテストを通す

### Task 5: `cross-review` の「メイン」を定義する

- **対象ファイル:** `plugins/ndf/skills/cross-review/SKILL.md`
- **変更内容:** 「メイン」が**収束ループを駆動している supervisor**（3 層でない進行では、
  骨組みを回している会話そのもの）であることと、修正は worker で行い diff を載せないことを書く
- **満たす受け入れ条件:** AC11
- **進め方:** Task 1 のテストを通す

## 影響範囲

- `development-workflow` を `/goal` の引数として呼ぶ経路（conductor の進め方）
- `cross-review` を回す層の呼び名。手順そのものは変えない
- 3 本目（#657）が `agent-layers.md` へ節を足す

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `SKILL.md` が 500 行の上限に達しており、足すと検査が落ちる | **入口だけを残して移す**（決定 23）。移す量と足す量を合わせ、行数を増やさない |
| `agent-layers.md` が 3 本目の節で 500 行を超える | 2 本目は 350 行程度に収める。超えるときの分け先（`agent-interruptions.md`）は設計が決めてある |
| G1（#747）が直したばかりの「人手の承認を求める関門」の節と差分が重なる | 触る節を `/goal` の節と「参照」に限る。最新の `develop` から始める |
| 規約の語の揃え（AC16）が本文以外（コード・他の文書）へ波及する | 対象を 3 つの文書に限る。`monitor.py` の文脈窓のように別のものを指す語は触らない |

## 切り戻し手順

文書とテストだけの変更である。Pull Request を revert すれば元へ戻る。生成物の同期は
`bash scripts/build-runtime-plugins.sh` を再実行する。

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `python3 scripts/check-skill-frontmatter.py`
- [ ] `python3 scripts/check-doc-line-limit.py`
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q`
- [ ] `claude plugin validate .`（終了コードで見る）
