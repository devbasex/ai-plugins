# 進め方 `pace: fast`

SKILL.md の「進め方（`pace`）」の続きである。区分の表と関門の表は SKILL.md にあり、この文書は
使ってよい条件・宣言の形・計画のステージ・検査のトリガー・MVV の判定・記録の読み方を持つ。

## 具体例: マイルストーン 26 の 2026-09-25 の区間

タグ `ndf--v10.17.10` から `ndf--v10.17.18` までに、配布を除く Pull Request 41 本を develop へマージし、本番を
8 回配った。検査は Pull Request ごとに通さず、確定仕様化と振り返りは課題ごとに行わず、後で棚卸しを 55 件
まとめて行った。同じ区間を `fast` で回すと、次のように動く。

| 何が | `fast` での動き |
| --- | --- |
| 構造改善と実装レビュー | `check-trigger.py eval` が点数 15・変更量 5,000 行・逃げた不具合の重なり・期限 24 時間を見て、立ったときだけ次の開発版の前に検査の計画 1 本が入る。この区間なら約 4 回（PR 約 10 本・約 2 時間に 1 回） |
| 関門 1・関門 2 | 利用者が承認するのはミッションの開始時の MVV 1 回だけ。各回は `mvv-gate.py` が「従う」と判定すれば省く |
| 確定仕様化・受け入れ条件の確認・振り返り | `supervise.py new close` の計画が、ミッションの終わりに 1 回ずつ流す |
| 工程の飛ばしの案内 | 構造改善・実装レビューを `トリガー:`、確定仕様化・振り返りを `まとめる:` の行に出し、記録を求めない |

## 使ってよい条件

**`supervise.py new mission --pace fast --state <ミッションの状態>` が機械で確かめる。** 1 つでも欠ければ
`stopped`（終了コード 1）で計画を書かず、conductor は `normal` で進める。

| 条件 | 確かめ方 |
| --- | --- |
| 開発版のチャネルがある | `.ndf/worktree.json` の `base_branch` が `production_branch`（無ければ既定ブランチ）と違う |
| 導入の確認がある | `.ndf/pace.json` の `fast.verify` にコマンドが書かれている |
| リポジトリが許している | `.ndf/pace.json` の `fast.enabled` が `true` |
| モードが対象に入る | `--mode` が `fast.modes`（既定 `light` / `standard` / `legacy-refactor`）に入る。`operation` と `documentation` は書いても入らない |
| MVV が承認済み | ミッションの状態に関門 `MVV` の記録があり、その `sha256` が今の `mvv.md` と一致する |

**使ってはいけない場面:** 開発版のチャネルが無いリポジトリ（マージがそのまま本番に届く）、導入を確かめる
手段が無い配布、戻すのが高い変更（利用者のデータの移行など。下の「越えない線」に当たるものは関門を省かない）。

## 宣言（`.ndf/pace.json`）

**プロジェクトごとに違うものはこの宣言で受け、スクリプトに埋め込まない。** 利用者が変える設定であり、
リポジトリに置いてレビューを通す。

| 項目 | 型 | 空・無いとき | 意味 |
| --- | --- | --- | --- |
| `fast.enabled` | bool | 偽 | `fast` を許すか |
| `fast.modes` | 文字列の配列 | 既定の 3 つ | `fast` を使ってよいモード |
| `fast.verify` | 文字列 | `fast` を断る | 導入の確認のコマンド |
| `areas[].name` / `common` / `paths` | 文字列 / bool / glob の配列 | name と paths は許さない | 領域。`common` が真なら共通層で、触った Pull Request は `common_weight` 点。逃げた不具合の重なりはこの単位で数える |
| `boundary_paths` | glob の配列 | 機械の検査は無し | 越えない線に当たるファイル |
| `triggers.score` / `common_weight` / `lines` / `escapes` / `hours` | 数 | 15 / 2 / 5000 / 2 / 24 | トリガーの閾値 |

glob の `**` は区切りをまたぎ、`*` と `?` はまたがない。どの領域にも当たらないファイルは、ディレクトリの
先頭 3 階層（例 `plugins/ndf/skills`）を領域の名前にする。

## ミッションを始める

**承認の記録が無いうちは `new mission --pace fast` が計画を書かないため、設計は始まらない。**

| 順 | 打つもの | 失敗・未承認のとき |
| --- | --- | --- |
| 1 | `mission-state.py init <状態> --name <名> --pace fast --milestone <M>` | 終了コード 3（説明に `## Mission` / `## Vision` / `## Value` がそろわない・読めない）: 利用者に説明を直してもらい打ち直す |
| 2 | conductor が `mvv.md`（状態のファイルの隣）を利用者へ示し、承認を得る | 承認されない: 説明を直して 1 から。`normal` で進めてもよい |
| 3 | `mission-state.py gate <状態> MVV --what <要約>` | — |
| 4 | `supervise.py new mission ... --pace fast --state <状態>` | 終了コード 1（承認の記録が無い・ハッシュ不一致）: 2 へ戻る |

`--mvv <ファイル>` を渡すと、マイルストーンから写さずにそのファイルを使う。**承認の後に MVV を書き換えると
ハッシュが食い違い、判定は利用者の承認へ戻る。**

## 計画のステージ

`normal` のミッションとの違いは 4 点である。ミッションのブランチを作らない。実装の Pull Request が develop へ
直接入る（閉じる語を書かない）。検査に実行の条件が付く。関門のステージが MVV の判定のステップへ入る。

| ステージ | 計画 | 流し方 |
| --- | --- | --- |
| 設計 | `design-<N>`（review の後に `mvv` → `approve`（ラベル `design-approved` と判定のコメント）→ `merge`） | `queue --max 3` |
| 関門 1 | 無し。設計の計画がすべて `完了` なら通過し、`関門` を返した計画の Pull Request だけ利用者の承認を取ってマージする | conductor |
| 実装 | `impl-<N>`（`base` は develop） | `queue <実装>... --max 3 --then <検査> --then <開発版> --then <本番>` |
| 検査 | `check`（実行の条件 `check-trigger.py eval --id <ミッション>-1`） | 1 つ目の `--then` |
| 開発版 | `release`（`facts` は `gate_as_ok`） | 2 つ目の `--then` |
| 本番 | `release-prod`（先頭が `mvv` のステップ） | 3 つ目の `--then`。`関門` なら queue の結果が `gate` になり、承認の後に `run <計画> --from bump` で続ける |

**ミッションの終わり**は `supervise.py new close --name M --worktree <根> --issue N... --version <開発版> --prod <正式版>
--state <状態>` が組む。最終の検査（実行の条件 `eval --final`）→ 開発版と本番（実行の条件 `changed --id <M>-final`。
最終の検査で変更があったときだけ）→ まとめ（`spec` 確定仕様化 → Pull Request → `close` 後片付け → `retro` 振り返り）
を `--then` のステージで流す。`close` のステップは `mission-close.py --record-pr {queue_pr:release-prod} --issues <課題>
--with-verification` で、本番が飛ばされたときは `--record-pr 0`（本番の記録なし）になる。まとめの計画は課題すべてへ
工程を記録するため、控えの報告が `まとめる:` から `記録あり:` へ移る。

## 検査のトリガー

**トリガーの判定はスクリプトが行い（`check-trigger.py eval`）、通信しない**（git の履歴だけを読む）。立てば
終了コード 0、立たなければ 3、宣言が読めない・git が無い・範囲を決められないときは 2。**読めないのに黙って飛ばすと
検査が永久に立たなくなるため、2 と 3 を混ぜない。**

| トリガー | 立つ条件 | 数え方 |
| --- | --- | --- |
| `score` | 点数 ≥ `triggers.score` | 範囲の `Merge pull request #N from <所有者>/<ブランチ>`（`release/` と `check/` を除く）を 1 本とし、共通層を触れば `common_weight` 点、ほかは 1 点 |
| `lines` | 行数 > `triggers.lines` | `git diff --shortstat <from> <to>` の追加 + 削除 |
| `escapes` | ある領域の逃げた不具合 ≥ `triggers.escapes` | 前回の検査の後の記録を領域ごとに数える。1 件の領域は検査の範囲の 2 番目の群へ入れるだけ |
| `hours` | 経過 ≥ `triggers.hours` かつ PR ≥ 1 | 前回の検査の時刻から今まで |
| `final` | `--final` を渡し、PR ≥ 1 | 範囲が空なら立たない（最終の検査を飛ばす） |

**共通層を触ったことは単独のトリガーにしない。** 2026-09-25 の区間では 41 本のうち 31 本（76%）が共通層を触っており、
単独で立てると PR ごとの検査と変わらない。点数の重みと、検査の中で先に見る範囲にだけ使う。

**前回の検査は、結果が `merged` か `no_change` の検査の記録の最新の行である。** その `to` が次の範囲の `from` に
なり、時刻が期限の起点になる。行が無ければ `--since <ref>`、配布の宣言（`.ndf/supervise.json` の `release`。
`package-plugin` なら `<plugin>--v*`）が決める最新の正式版のタグ、起点のブランチとの分岐点の順に使う。

### 検査の計画（`new check --since-last --id <名>`）

範囲は**前回の検査の時点（`check-base/<名>`）を宛先にした Pull Request** で表す。`cross-refactoring` と
`cross-review` は Pull Request 1 本を入力に取るため、駆動を変えずに差分全体を見られる。

| ステップ | 内容 |
| --- | --- |
| `prepare` | `check-trigger.py prepare`: `check-base/<名>` を `from` に作って送る（残っていれば付け直す）。範囲を状態ディレクトリの `check.json` へ |
| `pr` → `assess` → `refactor` → `review` → `test-all` | いつもの検査と同じ。`refactor` の範囲は `check-trigger.py scope`（共通層 → 逃げた不具合の領域 → その他） |
| `finish` | 宛先を起点のブランチへ付け替える。検査で変更が無ければ Pull Request を閉じて `record` へ飛ぶ |
| `ready` → `merge` → `record` | マージして検査の記録を足し、`check-base/<名>` を消す |
| `abort` / `abort-before-pr` | 落ちた run のステップの行き先。`result: failed` と落ちたステップを記録し、`check-base/<名>` を消し、Pull Request を閉じて止まる |

**`failed` の後の次の評価は同じ `from` から数え直すため、範囲を取りこぼさない。**

## 逃げた不具合の記録

**マージ済みの変更の不具合をその場で直すと決めたら、`new impl --escape-of <持ち込んだ PR>` で計画を組む。**
マージの後に `check-trigger.py escape` のステップが入り、直した Pull Request が触った領域を記録する。持ち込んだ
Pull Request が分からなければ `0`（不明）を渡す。`fix/` のブランチの本数では数えない。
前から起票されていた不具合が大半で、題名で数えると重なりのトリガーが毎回立つ。

## MVV の判定（`mvv-gate.py check`）

```text
mvv-gate.py check --mission <状態> --gate design|release [--material F...] [--pr N...] [--mode M] [--root DIR] [--note F]
```

**機械の検査を LLM の判定より先に通し、1 つでも外れれば LLM を呼ばずに関門（終了コード 10）へ戻す。**

1. MVV の承認の記録がある
2. 今の `mvv.md` のハッシュ・状態の `mvv.sha256`・承認の記録の `sha256` が一致する
3. `--mode` が `operation` でも `documentation` でもない
4. `--pr` の変更したファイルが `boundary_paths` に当たらない

通れば最小構成の `claude -p` に MVV と材料を渡し、`verdict`（follow / not_follow / unknown）・`reasons`・`boundary`
を JSON で返させる。**「従う」で `boundary` が空のときだけ** `mission-state.py gate` で関門の記録（`by: mvv`・判定・
理由・ログのパス）を書いて 0 を返す。ほか（従わない・判定できない・読めない・材料が無い・記録を書けない）は 10 で、
利用者の承認へ戻る。判定はすべて `mvv-gate.jsonl` へ 1 行ずつ残る。**配布の前に取れない実測（配布した後の効果）は
判定の対象にせず、配布の後の測定へ回す。**

### 越えない線

**次のどれかに当たれば、判定が「従う」でも利用者の承認を求める。**

| 種類 | 機械で見る所 | LLM で見る所 |
| --- | --- | --- |
| 秘密（トークン・鍵・認証情報）・認証認可・利用者のデータ | `boundary_paths` に当たるファイルを変えた | `boundary` 欄 |
| 戻せない操作（履歴の書き換え・強制 push・データの削除。本番の配布の定型の手順が打つマージとタグは含めない） | — | 同上 |
| 他のリポジトリへの公開 | — | 同上 |
| `operation` / `documentation` のモード | 計画の `モード` | — |
| MVV が承認されていない、または承認の後に変わった | `mvv.sha256` と承認の記録の `sha256` | — |

**MVV の承認を関門 1・2 の事前の許可として扱うのは、承認の記録・ハッシュの一致・判定の記録（ミッションの状態と
Pull Request のコメント）の 3 つがそろったときに限る。** レビューの approve（`gh pr review --approve`）は `fast`
でもしない（`AGENTS.md`）。

## 記録の読み方と閾値の見直し

検査の記録は通過工程の控えと同じ置き場（`${CLAUDE_PLUGIN_DATA}` → `${XDG_STATE_HOME:-~/.local/state}/ndf` →
`${TMPDIR:-/tmp}/ndf-checks`）の `checks/<所有者>__<リポジトリ>.jsonl` に、事象を追記するだけで持つ。

| `kind` | いつ足すか | 主な列 |
| --- | --- | --- |
| `eval` | 評価のたび（立たなかった回も） | `id`・`from`・`to`・`fired`・`metrics`（`prs`・`score`・`lines`・`hours`・`escapes`） |
| `check` | 検査の終わり | `result`（`merged` / `no_change` / `failed`）・`failed_at`・`pr`・`findings`（`applied`・`reverted`・`findings`・`unresolved`） |
| `escape` | 逃げた不具合を直したマージの後 | `pr`・`of`（0 は不明）・`areas` |

**`check-trigger.py stats` が集計する**（評価の回数と立った回数・トリガーごとの回数・検査ごとの指摘の件数・検査の後に
逃げた不具合の件数）。ミッションの終わりの振り返りはこの出力を材料にし、検査を 5 回回した後に閾値を見直す。
別の端末では記録が無く、最新の正式版のタグから数え直す（範囲が広がる側に倒れ、取りこぼしは起きない）。
