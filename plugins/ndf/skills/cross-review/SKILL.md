---
name: cross-review
description: "Review a PR with two CLIs picked from claude, codex, kiro and the host (agy only with --include agy), looping fixes until no new finding appears. Use when a converging multi-AI review is wanted（クロスレビュー・両AIレビュー・収束レビュー）."
argument-hint: "[PR番号] [--host claude|codex|agy|kiro] [--max-rounds N] [--rotate-after K] [--rotate-mode light|squash] [--only RUNTIME] [--exclude NAMES] [--include NAMES] [--require-all] [--focus TEXT] [--extra-instructions-file PATH] [--verify-command CMD] [--verify-exit-code N]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# クロスレビュー収束ループ

PR を**既定の母集合（claude / codex / kiro とホスト）から選んだ 2 者**にレビューさせ、**新しい指摘が出なくなるまで**
`/ndf:pr-review` と `/ndf:fix` を自動で回す。

既定の母集合は claude / codex / kiro とホストで、agy は `--include agy` で戻す（#786。agy は
テストを背景で起動したまま結果を残さずに終わることがある）。そのうち使える者から毎ラウンド 2 席を埋める
（実装は共通層の `lib/assignment.py`）。**1 者が使えなくても始まり**、使える者が 1 者なら
その者と同じランタイムの 2 つ目で埋める（`docs/05`）。**ホストも輪番に入れる**のは、
レビュー担当を CLI プロセスとして起動するためである。ホストと同じランタイムでも、ホストの
会話の作業文脈は持ち込まれない。外したい相手は `--exclude` で名指しする（既定の母集合に無い者の
指定は止めずに無視し、`ℹ` の 1 行で知らせる）。

/goalの引数として、または development-workflow の工程として呼ばれた場合は、新しい指摘が出なくなるまで/cross-reviewを繰り返す。
  * 担当のいずれかが不具合などで実行できなくなった場合は異常終了とする
   * この場合はPR ローテーションは実施しなくてよい。
   * 振動検知した場合はAIが判断して正しい状態を決める。

詳細手順は `docs/` 配下に、主要コマンドは `scripts/` 配下に分割している:

- [docs/01-state-and-review.md](docs/01-state-and-review.md) — Step 0〜4 (state init / round / 並列レビュー / 判定 / 振動検知)
- [docs/02-fix-and-rotation.md](docs/02-fix-and-rotation.md) — Step 5〜8 (サブエージェント修正 / PR ローテーション / 終了処理)
- [docs/03-review-output.md](docs/03-review-output.md) — レビュー出力の制約 / CI failure の分類 / アンチパターン / monitor.py の誤検知
- [docs/04-contracts.md](docs/04-contracts.md) — 状態ファイルの形式と AI への入出力の契約（手順の途中では読まない）
- [docs/05-pool-and-convergence.md](docs/05-pool-and-convergence.md) — 誰がレビューし、いつ止めるか（母集合・担当の輪番・認証・終了基準の 3 層）
- [docs/06-evidence.md](docs/06-evidence.md) — 指摘に求める根拠と反証条件、独立発見の規約、効果の測定（4 つの方式と限界）
- [scripts/drive.py](scripts/drive.py) — 収束ループの駆動（LLM が要る地点で pause を返して止まる）
- [scripts/state.py](scripts/state.py) — state.json 操作。起動・監視・巻き直しは `launch-reviewer.sh` / `monitor.py` / `rotate-pr.sh`、効果の測定は `measure.py`（状態を保存するたびに呼ばれる）

## 設計方針

長丁場のため、メインの文脈の消費を最小にする。投稿の担い手・修正の担い手・最終スイープ・再開・
巻き直し・振動・終了基準・母集合の方針は [references/design-principles.md](references/design-principles.md)、
「メイン」が何を指すかと、この形になっている理由は [references/context-budget.md](references/context-budget.md) にある。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | 対象 PR（省略時は直前 PR / 現在ブランチ） | — |
| `--max-rounds N` | 全体最大ラウンド数（PR ローテーションを含む通算） | 設計 PR は `3`、それ以外は `12`（「設計 PR と実装 PR の戦略」） |
| `--rotate-after K` | この round 数で未収束なら PR ローテーション | `8` |
| `--rotate-mode light\|squash` | ローテーション方式。`light`: 同ブランチで旧 PR を close → 新 PR (title/body は現状の差分・実装から再生成)。`squash`: squash 統合 + 新ブランチ + `(rotated)` suffix | `light` |
| `--host claude\|codex\|agy\|kiro` | この収束ループを起動している CLI。母集合には残る（外すなら `--exclude`） | 環境変数から推定。**推定できなければ失敗する** |
| `--only RUNTIME` | 1 者だけで回す。**そのラウンドの担当を 1 者へ絞り、席の埋め合わせを行わない。** 既定の母集合に無い者（agy）も名指しできる。外した者を指定したときと、その 1 者が確認を通らないときは `init` が弾く | 担当 2 者 |
| `--exclude NAMES` | 母集合から外す者。カンマ区切りで複数、繰り返しも可。既定の母集合に無い者の指定は止めずに無視し、`ℹ` の 1 行と完了報告の 1 行で知らせる。再開で `none` を渡すと空へ戻す | なし |
| `--include NAMES` | 母集合に足す者。agy を戻すときに使う。既に母集合にいる者（ホストを含む）を指定しても結果は変わらない（エラーにはしない）。書き方は `--exclude` と同じ | なし |
| `--require-all` | 確認を通らない者が 1 者でもいれば `init` を失敗させる。全員が揃わないなら始めたくない運用向け | 使える者で始める |
| `--focus TEXT` | 自動レビュー観点に上乗せして**そのラウンドのレビュー担当 2 者**に渡す追加観点。短い重点チェック向け | なし |
| `--extra-instructions-file PATH` | 自動レビュー観点に上乗せして**そのラウンドのレビュー担当 2 者**に渡す追加観点を UTF-8 テキストファイルから読む。長いチェックリスト向け | なし |
| `--verify-command CMD` | 実行検証（Step 2.5）で実行してよいコマンド。**渡さなければ実行検証を行わない** | なし |
| `--verify-exit-code N` | 再現とみなす終了コード。**「0 でない」を再現としない** | `1` |

例:

```
/ndf:cross-review 123
/ndf:cross-review 123 --max-rounds 4 --rotate-after 2
/ndf:cross-review 123 --rotate-mode squash
/ndf:cross-review 123 --only codex
/ndf:cross-review 123 --include agy --exclude kiro --require-all
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
/ndf:cross-review 123 --verify-command "pytest" --verify-exit-code 1
```

`init` は変更ファイルを分類して自動のレビュー観点を組み立て、`--focus` / `--extra-instructions-file` をその後ろに
上乗せする。分類の一覧は [docs/04-contracts.md](docs/04-contracts.md) の「自動レビュー観点テンプレート」にある。

### 設計 PR と実装 PR の戦略

**`init` は PR を design / code に分け、分類ごとに収束の条件と差分の渡し方を変える。** head のブランチが
`design/` で始まるか、変更が設計文書（`issues/*-design.md` など）だけなら design、それ以外は code である。
分類は状態ファイルの `review_kind` に残る。定義は `scripts/classifications.py` の `review_kind` にある。

| | design | code |
| --- | --- | --- |
| 収束の条件 | 上限 3 ラウンドで関門 1 へ渡す。**収束を待たない**（指摘の連鎖は文書の修正が生むため、回すほど増える） | 新しい指摘が出なくなるまで |
| `--max-rounds` を渡さないときの上限 | `3` | `12` |
| 担当へ渡す差分 | 2 ラウンド目以降は前のラウンドの head からの変更だけ（`git diff <前の head> <今の head>` をプロンプトに書く） | 全差分 |
| 大きさ | 設計文書 1 本は 1,000 行以下。超えると `init` が文書と行数を標準エラーへ出し、状態ファイルの `design_doc_oversize` に残す（止めない） | 制限なし |

`--max-rounds` を渡せば、どちらの分類でも渡した値が勝つ。

### `--rotate-mode` の選び方

- **`light` (default)**: PR を読む人 (将来のレビュアー / 後続 PR を作る人) が cross-review の存在を意識せずに済む。release branch 戦略・TODO 参照・コミット単位レビューを破壊しない。**通常はこちらを使う**
- **`squash`**: 巨大 PR を 1 commit に潰したい / `(rotated)` suffix で rotation 履歴を PR title に残したい場合のみ。release branch 戦略を使う運用とは併用しない

## 前提

- `/ndf:fix` が **サブエージェント起動 + 重要度ベース自動修正 + Resolve Conversation** に対応
- `gh` CLI が認証済み。担当になる CLI は `init` が起動前に確かめ、通らない者は外して続ける（誤検知するときは `NDF_SKIP_AUTH_CHECK=1`）
- worker（`general-purpose` のサブエージェント）を起こせる

## 事前確認

自分の PR の判定・作業ツリーの分離・agy の作業領域・既存コメントの控えの 4 つは `state.py init` が行う。
中身と `intent` / `posted_as` の両保持は [docs/04-contracts.md](docs/04-contracts.md) の「事前確認」にある。

## 全体フロー

1 ラウンドは「2 席の並列レビュー → 根拠の検証 → 判定（`intent` ベース）」で、一方でも REQUEST_CHANGES なら
修正 → 収束チェック（`max-rounds`・振動・CI の失敗・`rotate-after` の巻き直し）→ 次のラウンドへ進む。結果を残さなかった
席は同じラウンドで 1 度だけ起動し直す。ループを抜けたら（`final` がどの値でも）最終スイープで open thread を 0 にし、
`verify-sweep` が GitHub 側の実数で確かめる。

## 実行

Skill のディレクトリで次の 1 行を打ち、最後の行の結果 JSON の `status` と終了コードを見る。**値のある引数だけを
渡す**（再開で渡した引数の扱いは [docs/04-contracts.md](docs/04-contracts.md) の「再開で渡した引数の扱い」）。

```bash
python3 scripts/drive.py <PR> [--rotate-mode light|squash] [--max-rounds N] [--rotate-after K] [--host H] [--only R] \
  [--exclude N] [--include N] [--require-all] [--focus TEXT] [--extra-instructions-file PATH] [--verify-command CMD] [--verify-exit-code N]
```

待ちはコマンドの中で行う（1 ラウンドで 20 分を超えうる）。Claude Code では `run_in_background` で起動し、
完了通知を 1 回受ける。Codex / Kiro / agy では共通層の `scripts/lib/bg-wait.sh` で背景に起動し、区切った待ちを
124 が返るあいだ**別の呼び出しとして**打ち直す。終わると駆動の出力の全体を出し、駆動の終了コードで終わる。

```bash
RC="${TMPDIR:-/tmp}/cross-review-drive-pr<PR>.rc"
bash ../../scripts/lib/bg-wait.sh run "$RC" -- python3 scripts/drive.py <PR> [上と同じ引数]
bash ../../scripts/lib/bg-wait.sh wait "$RC"   # 1 回 540 秒以内。124 = まだ終わっていない
```

待ち方の規約は [waiting.md](../development-workflow/references/waiting.md)、待ちから戻った後に同じ応答で次の段へ
進む規則は [agent-layers.md](../development-workflow/references/agent-layers.md) の supervisor の規則にある。
JSON の形と終了コードの表は共通層の `scripts/lib/drive_pause.py` にある。

| 終了コード（`items[0].pause`） | 止まった地点 | すること |
| --- | --- | --- |
| 0 | 最終スイープと検証まで終わった | `items[0].report` と `metrics` を「作業完了報告」へ写す |
| 20（`fix`） | 一方でも REQUEST_CHANGES | worker を起こし、`prompt_file` を読ませて `result_file` を書かせる。書けたら同じコマンドを打ち直す |
| 21（`sweep`） | ループを抜けた（どの `final` でも） | 同上（最終スイープ） |
| 22（`newtext`） | light の巻き直し | 同上（新しい title / body） |
| 1 | 中断（`metrics.exit` に元の終了コード） | `summary` を報告して止まる |

**修正はメインで書かない。** `prompt_file` は `/ndf:fix` の呼び出しと PR 固有の値（ブランチ・前ラウンドの
レビュー・戻り値ファイル）だけを持つ。担当はコミットまでで、送信・返信・決着は駆動の取り込みが行う。
振動を検知したときは、メインが正しい状態を決めてから打ち直す。

再開は同じコマンドを打ち直すだけである。進みは `$TMP_DIR/drive-pr<PR>.json` と state.json にあり、pause の
結果ファイルがあればその続きから進む。`metrics` は state.json から数えた件数（`rounds` / `prs` / `findings` /
`fixed` / `deferred` / `rejected` / `unresolved` / `final` / `review_status`）である。

## レビュー出力の制約と運用の切り分け

投稿の書式・継続的統合の失敗の分類・過去に踏んだ形は
[docs/03-review-output.md](docs/03-review-output.md) にある。読む場面は次のとおり。

| 節 | 読む場面 |
|---|---|
| レビュー出力の制約 | レビュアーの起動プロンプトを変えるとき。投稿の書式と判定の付け方の規約 |
| CI failure の分類 | 継続的統合の失敗で中断するかを決めるとき（Step 5 後段） |
| アンチパターン | 手順を変えるとき、または進行が止まったとき |
| monitor.py が誤って kill する場合の手順 | 結果ファイルが生成されないとき |

## 作業完了報告（必須）

駆動の結果（`status: ok`）の `items[0].report`（`state.py report` の出力）と `metrics` を材料に、次を報告する。

- **最終ステータス**（`final`: `approved` / `max_rounds` / `oscillation` / `error`）・**ラウンド数 / PR 数**（`rounds` / `prs`）
- **各ラウンドのサマリ表**（round・PR・担当と判定・fix・CI。担当はラウンドごとに変わるため、担当と判定を 1 つの列にする）
- **最終スイープ結果**: `resolved` / `fixed_in_sweep` / `remaining_open`（`unresolved`）。**0 が正常**で、0 でなければ理由を書く
- **残 deferred nit**・**rejected 件数**・**最終 PR URL**
- **検証**（スイープの結果ファイルの `verification`）: 実行したコマンドと終了コード。実行しなかったときはその理由

詳細は PR 上のインラインコメントと state.json に残っているため、本報告では繰り返さない。

この工程に入ったら、起動指示の「記録のコマンド」で `実装レビュー` を 1 行記録する。設計だけを載せた
Pull Request で呼ばれたときは `ドキュメントレビュー` を記録する。

## 関連

- `/ndf:pr-review` — 単発レビュー（AI 直接投稿対応）
- `/ndf:fix` — 指摘の分類・修正・返信・Resolve（サブエージェント起動対応）
- `/ndf:external-ai` — codex / agy CLI 呼び出し手順（CLI 別の差分は `references/cli-codex.md` / `references/cli-agy.md`）
- `/ndf:issue-plan-strategy` — multi-PR ワークフローでは **個別 PR ごとに本 cross-review が原則必須**。
  `/ndf:pr-review` 単発や Claude Code の `code-reviewer` は代替にせず、release ブランチへ merge する前に
  codex + agy の APPROVE 収束を確認する (Step 6)
- `general-purpose` エージェント — fix / sweep / newtext の worker
- `/ndf:out-of-scope` — 範囲外と判断した指摘の起票
