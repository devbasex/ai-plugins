# #4217: 並列の計画の起動の許可: 2 つの計画の同時の判定と排他の待ちを確かめるテストが 79b02ac21 で消え、#3989 の受け入れ条件 13 を満たさない

正は課題の本文（#4217）で、この文書はその写しである。本文から作り直す。手で直さない。

## 何が起きたか

#3989（並列の計画の同時実行を 2 層にする。ndf 11.0.14 で出した）のリリース後テスト（2026-11-23）で、受け入れ条件 13「1〜10 を回数と状態で確かめるテストがある。計画 2 本が同時に起動を求める場合を含む」が origin/main（8c79b73aa）で満たされていなかった。

11.0.14 の時点では満たしていたが、その後のコミット 79b02ac21「Test: 本物の時間を待つテストと、子のプロセスを起動して確かめるテストを消す」（2026-11-23）が、#3989 の受け入れ条件を確かめるテストを 3 本消した。

| 消えたテスト | ファイル | 確かめていた条件 | 消された理由（コードから読んだもの） |
| --- | --- | --- | --- |
| `test_two_plans_admitting_at_once_do_not_pass_the_cap` | `plugins/ndf/tests/progress/test_lane_machine.py` | #3989 の条件 8（2 つの計画が同時に起動を求めても、起動の排他 `parallel/admit.lock` の中で数えるので上限を超えない） | 3 本のスレッドと本物の `admit_lock`（`fcntl` の錠を 0.2 秒ごとに取り直す）で取り合わせていた |
| `test_a_held_lock_makes_the_launch_wait_without_counting` | 同上 | #3989 の条件 9 の待ちの理由「排他」（`lock`）を起動の判定が返すこと | 本物の錠を持ったまま `admitted` を呼び、排他の上限の秒（`parallel-machine.admit-lock`）を本物の時間で待っていた |
| `test_cli_has_no_limits_subcommand_and_no_plans_option` | `plugins/ndf/tests/progress/test_plan.py` | #3989 の条件 1（`ndf lanes limits` と `ndf lanes set --plans` が無く、`ndf lanes status` に `max_lanes`・`max_plans`・`plans` が無い） | `ndf lanes status` が生死の材料を `ndf relay ls` の子のプロセスで読んでいた（`plan/run.py` の `probes`） |

## 種類

不具合（#3989 の受け入れ条件 13 と、確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` の「テスト観点」が、テストがあると今の SSOT で約束している。テストを戻すのは既存の約束の回復である）

## 今の姿

- コードは条件を満たす。`plugins/ndf/src/ndf/progress/lane/machine.py` の `admitted` が `admit_lock` の中で上限を読み、数え、計画を索引に書き、起動の手（`go`）を打つ。排他を取れなければ（`LockBusy`）`waiting(LOCK, None, None)` を返す
- 残っているテストは、排他を取らない形の待ち（`test_machine_waits_without_the_lock_*`）と、ステータスの `lock` の表示（`test_status_names_the_lock_and_the_unreadable_list`）だけで、`admitted` が排他を取れないときに数えずに待つこと・2 つの判定が重ならないことを確かめていない
- `ndf lanes limits` と `--plans` が無いことは導入済みの CLI（11.0.19）の `ndf lanes --help`・`ndf lanes set --help` で読めるが、テストは無い
- 確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` の「テスト観点」の 1 項目目は、`ndf lanes limits` と `ndf lanes set --plans` が引数の誤りで断ることを `tests/progress/test_plan.py` が確かめると書いており、テストと食い違う

SSOT: #3989 の受け入れ条件 1・8・9・13 と、確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` の「テスト観点」。

## 事象

```bash
git grep -n "admitting_at_once\|held_lock_makes\|no_limits_subcommand" origin/main -- plugins/ndf/tests
# 出力なし（終了コード 1）
```

## 期待する結果

本物の時間を待たず、子のプロセスを起動せずに、次を回数と状態で確かめるテストがある。

1. 2 つの計画の起動の判定が同時に求められても、起動した後の稼働中の数が上限を超えない（排他を偽の錠に差し替え、1 本目が排他を持つ間に 2 本目の判定を打つなど）
2. 排他を取れないとき、`admitted` は数えずに `reason: lock` の待ちを返す（`admit_lock` が `LockBusy` を上げる偽にする）
3. `ndf lanes limits` と `ndf lanes set --plans` が引数の誤りで断り、`ndf lanes status` の `metrics` とレーンの行に 3 つの値が無い（CLI を同じプロセスで呼ぶ）

あわせて、確定仕様のテスト観点の記述を実際のテストにそろえる。

## 再現手順

1. origin/main（8c79b73aa）を取り出す
2. 上の「事象」の `git grep` を打つ → 3 本とも見つからない
3. `plugins/ndf/tests/progress/test_lane_machine.py` で `admitted` を呼ぶテストを読む → `LockBusy` を経る経路と、1 本目の判定の途中に 2 本目を求める経路を通すテストが無い

## 環境

origin/main（8c79b73aa）。導入済みの ndf 11.0.19。

## なぜ困るか

起動の排他の中で数える作りは、2 つの計画が同時に起動を求めたときに上限を超えないための唯一の仕組みである（#3989 の決定）。テストが無いと、`admitted` の中の数える処理が排他の外へ出る変更や、`LockBusy` を握りつぶす変更が CI を通ってマージされ、マシンの上限を超えてセッションが起動し得る（メモリの枯渇。並列は 6 本までの実測がある）。確定仕様が無いテストを指しているため、仕様を読んだ人は確かめられていると誤る。

## 目的

- #3989 の受け入れ条件 1・8・9 を、本物の時間を待たず子のプロセスも起動しないテストで、もう一度 CI が確かめるようにする
- 確定仕様のテスト観点が、実際にあるテストだけを指すようにする

## 対象範囲

含む:
- `plugins/ndf/tests/progress/` へのテストの追加（上の期待する結果の 1〜3）
- 確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` の「テスト観点」の記述の直し

含まない:
- `admitted`・`admit_lock`・`ndf lanes` の CLI のコードの変更（コードは条件を満たしている）
- 79b02ac21 が消したほかのテスト（#3989 の受け入れ条件に当たらないもの）の復元
- 本物の `flock` の取り合いを別のプロセスで確かめるテスト（受け入れ条件 1 が禁じる）

## 受け入れ条件

- [ ] 1. 上の 1〜3 を確かめるテストが `plugins/ndf/tests/progress/` にあり、`time.sleep`・本物の錠の待ち・子のプロセスを使わない
- [ ] 2. 確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` のテストの節が、実際にあるテストと食い違わない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| データ | 変わらない |
| 既存の振る舞い | 変わらない（テストと確定仕様の文だけ）。重さ: 利用者に見える不具合は今は無く、退行を防ぐ網の穴である |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | 足したテストを PR の CI で走らせる（このリポジトリはテストを CI に寄せるので、手元で全体を走らせない）。合否は PR の CI の結果で決める |
| 待ちと子のプロセスを使わないこと | 足したテストの中で `time.sleep` と `subprocess.Popen` を呼ばれたら失敗する偽に差し替え、テストが通ることで確かめる |
| 確定仕様 | テスト観点に書いたテストのファイルと名前を、差分の中のテストと突き合わせる（cross-review と設計承認ゲートで見る） |

## 起票者の推定

| 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- |
| 期待する結果の 1 の「排他を偽の錠に差し替え、1 本目が排他を持つ間に 2 本目の判定を打つ」 | 起票者がテストの組み方を例として挙げた | 受け入れ条件 1 の手段の例。採るかは実装計画で決める |
| 期待する結果の 3 の「CLI を同じプロセスで呼ぶ」 | 消えた CLI のテストの形（`plan_cli.main`）から | 受け入れ条件 1（子のプロセスを使わない）の手段 |

## リクエスト（原文）

書き手: 起票者（#3989 のリリース後テストを行った AI）、2026-11-23。この課題の題と、上の「何が起きたか」「期待する結果」「受け入れ条件」の元の文が原文である（元の本文の版は課題の編集の履歴にある）。

書き手: AI の起動指示（conductor、計画 p12 のレーン lcrfast の行 r2）、2026-11-23:

> 変更はテストの追加と確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` のテストの節の直しだけの見込み。テストは `time.sleep`・本物の錠の待ち・子のプロセスを使わない（課題の受け入れ条件 1）。

