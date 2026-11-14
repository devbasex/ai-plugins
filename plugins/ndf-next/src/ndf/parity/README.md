# 機能の対応表と直しの棚卸し

作り直しの入れ替え（S2）の前に、今のプラグインの機能が新しい側のどの子に移るかを 1 か所で持つ。11.0.0 の受け入れ条件の 1（全行が行き先の子か作らない理由を持つ）と 5（作り直しの間に起票された不具合の受け付け）の材料である。

例: 今の `scripts/merged-steps.py`（マージの後の後片付け）は、対応表の次の行で M8 へ移る。移行後の置き場は `places` の `M8` の値である。#3317（メインディレクトリの取り込みが止まる）はこの行を指して受け付け、M8 で直す。

```json
{"id": "entrypoint:scripts/merged-steps.py", "kind": "entrypoint", "feature": "merged の後片付けの決まった手順。", "now": "scripts/merged-steps.py", "to": "M8", "trial": null, "conductor": true}
```

| ファイル | 中身 |
| --- | --- |
| `features.json` | 機能の対応表。今の機能 1 つにつき 1 行 |
| `fixes.json` | 設計の承認（PR #3280、2026-11-13 19:40Z）以後に旧いプラグインへ入った直し・旧い側にしか無い回帰のテスト・受け付けた不具合 |
| `schema.json` | 2 つのファイルの形（JSON Schema） |
| `table.py` | 読み手。形の確かめ（`shape_errors`）・エントリポイントの一覧（`entrypoints`）・利用者のコマンドの行き先（`user_commands`） |

## 対応表の行

**入れ替えの確かめが読むのは `feature`・`to`・`not_built`・`trial` の 4 つの鍵だけである。** ほかの鍵は人と後続の子が読む。

| 鍵 | 中身 |
| --- | --- |
| `feature` | 今の機能 |
| `to` | 行き先の子の記号（`M8` など）。`places` に無い記号は誤り |
| `not_built` | 作らない理由。`to` とどちらか 1 つだけを持つ |
| `trial` | 試走（T2）の結果。T2 が書くまでは `null`。`{"result": "passed"}`、または `failed` か `not_run` に `reason` を添える |
| `id` | `<種類>:<今の置き場>`。行の同一性で、書き換えない |
| `kind` | `entrypoint`・`hook-judgment`・`hook`・`skill`・`manifest`・`agent`・`contract`・`data`・`experimental`・`workflow`・`non-regression`・`from-issue` |
| `now` | 今の置き場（旧いプラグインの根からの相対）。置き場の無い行は `—` |
| `unowned` | 子の本文に持ち主が無く、この表で行き先を決めた行（#3342 の「持ち主の無いエントリポイント」）。オーガナイザーが行き先の子の本文へ足す |
| `conductor` | conductor の手順（development-workflow の Skill）と並列の計画の手順が直に打つエントリポイント |
| `user_command` | 利用者が手で打つコマンド |
| `source` | ほかの Issue から寄せた行の出所 |

`places` は子ごとの移行後の置き場である（`issues/issue-1142-design-next-design-detail.md` の「ファイル配置」と #3395 の構成要素）。エントリポイントの移行後の置き場は、行の `to` の `places` から引く（`table.entrypoints`）。利用者のコマンドの移行後の副命令の名前は、表に写さず、行き先の子の置き場にモジュールがある副命令を `cli.COMMANDS` から引く（`table.user_commands`）。副命令を足す子は、この表を書き換えずに済む。

退行させないもの（設計承認ゲートとリリース承認ゲート・hook の守り・秘密の検査・Codex 向けのリリース・Factory の契約）は `kind` が `non-regression` の 5 行である。

## 書き換えてよい場合

| 誰が | 何を |
| --- | --- |
| T2 | 各行の `trial` |
| 行き先の子 | 自分の行の `to` を、子を分けた・寄せたときに書き直す。理由は子の PR 本文に書く |
| 受け付け（下の節） | 対応表に無い今の機能を見つけたら行を足す |

## 作り直しの間に起票された不具合の受け付け

旧いプラグインの不具合の Issue を見つけたら、次の順で行き先の子を決める。

1. 旧いプラグインの機能の不具合でなければ、受け付けない。`fixes.json` の `intake` に `not_taken` と理由を書く
2. 不具合が当たる機能の行を対応表から探す。無ければ、行を足して行き先の子を決める
3. その行の `to` を行き先の子にする。行が `not_built` なら、作らない理由を添えて Issue を閉じる
4. `fixes.json` の `intake` に、Issue・要旨・指した行（`row`）・行き先（`to`）を書く
5. ラベル `rebuild-1142` を付け、`issues/issue-3395-design-bugs.md` の一覧へ載せ、行き先の子の本文の「この子で解消する Issue」へ足す。済んだら `listed` を `true` にする。手順 5 はオーガナイザーが行う

旧いプラグインで既に直した不具合は、直しを `fixes` にも書く（次の節）。

## 直しの棚卸し

旧いプラグインへ入った直しは、入れ替えで消える前に新しい側へ移す。

| 部分 | 1 行の中身 |
| --- | --- |
| `fixes` | 直し 1 つ。Issue・PR・コミット・旧い側のテストと、移す先の子（`to`）か移さない理由（`not_moved`） |
| `regressions` | 閉じた不具合のうち、回帰のテストが旧い側にしか無いもの。移す先の子が同じ振る舞いをテストで縛る |
| `intake` | 受け付けた不具合（上の節） |

`fixes` は承認以後のコミットに加え、承認の前に develop へ入った実装済みの直し（行き先の表の R5）も持つ。develop で実装済みの件数は、数を書かずに `fixes` の `to` の行から数える。

承認以後に旧いプラグインへ入ったコミットは次のコマンドで出す。出た直しが `fixes` に無ければ足す。

```bash
git log origin/develop --since=2026-11-13T19:40:00Z --no-merges --format='%h %s' -- plugins/ndf
```
