---
name: cross-review
description: "Review a PR with codex and kiro as read-only reviewers while claude fixes, looping rounds until no unresolved finding remains. Use when a converging multi-AI review is wanted（クロスレビュー・両AIレビュー・収束レビュー）."
argument-hint: "--pr N --state-dir DIR --base BRANCH [--worktree DIR] [--branch NAME] [--purpose P] [--reviewer RUNTIME]... [--max-rounds N] [--findings FILE]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# クロスレビュー収束ループ

PR を**読むだけの相手役（既定は codex と kiro）**にレビューさせ、**修正担当（claude）**が指摘を直すか理由を添えて見送る。
**未解決の指摘が無くなるまで**ラウンドを回す。1 回の起動でラウンドを回すのは `ndf cross-review run` で、メインは打って結果を読むだけである。

相手役の既定は、ランタイムの表（`contracts/domains/runtimes.json`）の既定の席のうち読むだけの起動（`review_args`）を持つ者から
修正担当を除いた並びで、選び方は `ndf.core.converge.participants` が cross-refactoring と 1 つだけ持つ。`--reviewer` で名指しすると
その並びで回し、表に無い名前や読むだけの起動を持たない名前は `invalid` で止まる。メインディレクトリの `.ndf/runtimes.json` の
`allowed` があれば、相手役はその中から選び、外の名前を名指せば `invalid` で止まる。修正担当（claude）が `allowed` に無ければ、どの担当も起動する前に `precondition` で止まる。

相手役は指摘の基準（利用者の経路の誤動作・秘密や戻せない操作・`.ndf/review.json` の重点・設計の食い違い）に当たる指摘だけを
重要度つきで書き、`minor`・`nit` の指摘は収束を止めず修正担当へ渡らない。相手役へは PR に既にあるコメントも渡し、
同じ論点を出し直させない。基準と重要度の扱いは [docs/01](docs/01-state-and-review.md) の「指摘の基準」にある。

/goal の引数として、または development-workflow の工程として呼ばれた場合は、`status: ok` になるまで同じコマンドを打ち直す。
  * `work_stopped`・`precondition`・`invalid` で止まったら異常終了とする

詳細は `docs/` 配下に分割している。収束ループは `ndf cross-review run` が 1 回の起動で回す:

- [docs/01-state-and-review.md](docs/01-state-and-review.md) — `run` の始め・1 ラウンド・範囲・判定・状態ファイル・部分の命令
- [docs/02-fix-and-rotation.md](docs/02-fix-and-rotation.md) — 修正・送る・終わり方・PR の巻き直し
- `ndf cross-review run` — 収束ループの駆動（相手役の起動・指摘のジャーナル・修正担当の起動・push・記録）。下位の命令と引数は [commands.md](../../docs/contracts/commands.md) の `ndf cross-review`
- **PR に残る記録**（#3939）: `run` は終わるときに、指摘を PR のインラインのスレッドにし、直した指摘には修正のコミットを、見送った指摘には理由を返信して resolve する。未解決の指摘のスレッドは開いたまま残し、別の `--state-dir` で始めた次の実行が未解決の指摘として引き継ぐ。あわせて、ラウンドと修正の記録・指摘ごとの終わり方を 1 つのコメントに書く（次の起動は同じコメントを書き換える）。書けなければ終わり方は変えず `warnings` に残すので、`ndf cross-review report --state-dir <D> --pr <N>` で書き足す。状態ファイルはセッションの作業場所にあって消えるため、PR の記録が後から読める唯一の記録になる

## 設計方針

長丁場のため、メインの文脈の消費を最小にする。修正の担い手・送る担い手・再開・終わり方の方針は
[references/design-principles.md](references/design-principles.md) にある。

## 引数

`ndf cross-review run` の引数である（`ndf cross-review run --help`）。

| 引数 | 意味 | 既定 |
| --- | --- | --- |
| `--pr N` | 対象 PR（巻き直した後は元の番号でも今の番号でもよい） | 必須 |
| `--state-dir DIR` | 状態ファイル・ラウンドの差分・相手役の出力・要約を置く保存先。レビューする worktree の外に置く（中なら `precondition`）。再開と足すラウンドは同じ保存先で打つ | 必須（プランは `<プランの実行の保存先>/cross-review`） |
| `--base BRANCH` | 差分の起点（`--base...HEAD`） | 必須 |
| `--worktree DIR` / `--branch NAME` | レビューする worktree と head ブランチ | 打った場所とその現在のブランチ |
| `--purpose P` | PR の目的。設計のプランのテンプレートの purpose（`ndf loop review-args --purpose design` が組む値）なら、変わったセクションを必ず詳細レビューへ通す | `cross-review` |
| `--reviewer RUNTIME` | 相手役（繰り返して複数） | 既定の席（codex・kiro） |
| `--max-rounds N` | この起動で回すラウンドの上限 | `3` |
| `--findings FILE` | 組み直しが残した足すべき内容（組み直しの結果 JSON のファイル。`items` に文字列か `{"id", "text"}`）。指摘と同じく直したか見送ったかで終わらせる | なし |

例:

```
STATE_DIR=$(mktemp -d)   # 所有者だけが読み書きできる新しい保存先。再開と足すラウンドでも同じ $STATE_DIR を渡す
ndf cross-review run --pr 123 --state-dir "$STATE_DIR" --base <ベースブランチ>
ndf cross-review run --pr 123 --state-dir "$STATE_DIR" --base <ベースブランチ> --reviewer codex --max-rounds 5
```

## 前提

- `gh` CLI と git が origin へ送れる。相手役の CLI（既定は codex と kiro）と修正担当の claude がログイン済みで、PATH にある
- `--worktree`（省けば打った場所）が git に登録された worktree で、PR の head ブランチを持つ。リポジトリのメインディレクトリ
  そのものは渡さない（同期が PR の `.ndf/` の設定で書き換えるため、実行者の設定を読む場所にならない。`run` は precondition で止まる）
- ユーザー名前空間を作れない環境（`unshare -Ur true` が通らない）では、`run` は codex を自身のサンドボックスを外して
  （`--dangerously-bypass-approvals-and-sandbox`）起動する。そうした環境では外で隔離されたコンテナの中で打つ

**CI テストの設定（`.ndf/project.json` の `test.ci.only`）があるリポジトリでは、テストを手元で打たない。** テストの合否は
PR の CI の結果（`gh pr checks <PR>`）で読み、報告の「検証」には CI のチェックの名前と結論を書く。

## 全体フロー

1 ラウンドは「範囲を決める → 相手役を並べて起動する → 指摘をジャーナルへ入れる → 判定」で、修正が要れば修正担当を起動し、
修正のコミットを送ってから次のラウンドへ進む。未解決の指摘も足すべき内容も無くなれば `converged`、前々ラウンドの指摘が戻れば
`oscillating`、上限に当たれば `max_rounds` で終わる（[docs/01](docs/01-state-and-review.md)・[docs/02](docs/02-fix-and-rotation.md)）。

## 実行

**対象のリポジトリの中（worktree）で**、次の 1 行を打ち、最後の行の結果 JSON の `status` と終了コードを見る。
`run` はリポジトリを作業ディレクトリ（または `--worktree`）から求めるため、**Skill のディレクトリへ `cd` しない**。

待ちはコマンドの中で行う（1 ラウンドで 20 分を超えうる）。Claude Code では Bash の `run_in_background` で起動し、完了通知を
1 回受ける。NDF のコマンドは 1 回の呼び出しに 1 つだけ打つ（`&&`・`;`・パイプ・ループでつなぐと hook が止める）。

```bash
ndf cross-review run --pr <PR> --state-dir <状態の保存先> --base <マージ先のブランチ> \
  [--worktree <W>] [--branch <B>] [--purpose <P>] [--reviewer <R>]... [--max-rounds N] [--findings <F>]
```

待ち方の規約は [waiting.md](../development-workflow/references/waiting.md)、待ちから戻った後に同じ応答で次の手順へ
進む規則は [agent-layers.md](../development-workflow/references/agent-layers.md) の supervisor の規則にある。
結果の形と終了コードは [exit-codes.md](../../docs/contracts/exit-codes.md) と結果のコントラクト
（[result.schema.json](../../contracts/schemas/result.schema.json)）にある。

| 結果（`status`・`cause.kind`） | 止まった地点 | すること |
| --- | --- | --- |
| `ok` | 終わり方に欠けが無い（収束、または上限・振動・誤りで終わったが欠け 0 件。理由は `decisions`） | `items`・`metrics`・`decisions` を「作業完了報告」へ写す |
| `stopped`・`incomplete` | 未解決の指摘・扱わなかった足すべき内容・理由の無い見送りが残る（`gaps`） | `gaps` を報告する。続けるなら同じコマンドを打ち直す |
| `stopped`・`work_stopped` | 1 ラウンドも終えずに誤りで終わった・前の起動の修正を送れない・一部の相手役が起動し直しても差分を読めなかった（`incomplete_reviewers`。相手役ごとの直し方は `warnings`） | `summary` と `warnings` を報告して止まる |
| `stopped`・`precondition` / `invalid` | worktree が登録されていない・同期で衝突した・相手役の名指しの誤り・`.ndf/runtimes.json` の外の相手役・`.ndf/review.json` が `version: 1` でない・相手役が誰も差分を読めなかった（未ログイン・起動やサンドボックスの失敗。相手役ごとの直し方は `cause.detail`） | `summary` を報告して止まる。原因を取り除いて打ち直す |

**修正はメインで書かない。** 修正担当（claude）は `run` が起動し、送るのも `run` が行う（[docs/02](docs/02-fix-and-rotation.md)）。

再開は同じコマンドを打ち直すだけである。進みは状態ファイル（`--state-dir`）にあり、前の起動が残した修正の結果ファイルが
あればそれを読み込んでから続ける。**終わったレビューにもう一度回すときも、同じコマンドを打ち直すだけである。** 状態ファイルを
開かない・`mv` しない・書き換えない。最後のラウンドの続きから `--max-rounds` の数だけ回す。最後のラウンドが収束で head も
`--base` も変わっていなければ、相手役を起動せずに前回の結果を返す（[docs/01](docs/01-state-and-review.md) の「ラウンドを足す」）。

## 作業完了報告（必須）

`run` の結果（`status: ok`、または欠けを残して終わった `status: stopped`）と、状態ファイル（`ndf cross-review state-file` が返すパス）の
ラウンドの記録を材料に、次を報告する。

- **終わり方**（`summary` と `decisions`）・**ラウンド数**（`metrics.rounds`）・**指摘の数**（`metrics.findings`）
- **各ラウンドの表**（round・head・範囲・判定・修正のコミットと送れたか。状態ファイルの `rounds[]`）
- **指摘ごとの終わり方**（`items` の `id` と `outcome`。見送りは理由を状態ファイルの `findings[]` から写す）
- **欠け**（`gaps`）と**注意**（`warnings`。回答しなかった相手役・修正担当が結果を書かなかった・送れなかった）
- **最終 PR URL** と **記録のコメントの URL**（`items` の `kind: comment`）
- **検証**: CI のチェックの名前と結論（`gh pr checks <PR>`）

## 関連

- `/ndf:pr-review` — 単発レビュー
- `/ndf:fix` — 指摘の振り分けと修正
- `/ndf:external-ai` — codex / agy / kiro の CLI 呼び出し手順
- `/ndf:out-of-scope` — 範囲外と判断した指摘の起票
