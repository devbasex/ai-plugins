# 開発ループの上限の全体の表（#2505）

[issue-2505-design.md](issue-2505-design.md) から分けた節である。開発ループの中で働く上限を、種類と扱いで分けて 1 か所に並べる。

## 区分ごとの行数

| 区分 | 行数 |
| --- | ---: |
| 種類 A | 15 |
| 暴走止め | 4 |
| 種類 B | 1 |
| 配布の重複止め | 1 |
| 種類 C | 11 |
| 種類 D | 9 |
| 種類 E | 7 |
| 外 | 12 |
| 合計 | 60 |

## 種類と扱いの語

種類の分け方は要求の「残すもの」の表に揃える。

| 種類 | 意味 |
| --- | --- |
| A | 回数・時間の上限で開発ループを止める |
| 暴走止め | 回数を数える上限ではなく、壊れて回り続ける状態を止める |
| B | 費用 |
| C | 資源と安全。マシンとアカウントの限界 |
| D | 品質の収束。当たっても残りを記録して先へ進む |
| E | 状態の変化。上限ではなく人の判断が要る状態 |
| 外 | 打ち直せばよい、または開発ループを止めない |

扱いの語は次のとおりである。

| 扱い | 意味 |
| --- | --- |
| 無くす | この変更で上限を消す |
| 書いたときだけ | 既定を無制限にし、宣言に数を書いたときだけ効く |
| 置き換え | 別の判定へ置き換える |
| 足す | 上書きの手段を足す |
| 残す | 値も振る舞いも変えない |
| 利用者が決める | 承認ゲート 1 で決める。[設計文書の「利用者が決めること」](issue-2505-design.md#利用者が決めること)に対応する行がある |

## 種類 A: 回数・時間の上限で開発ループを止めるもの

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L1 | ラッパーの 1 日の起動回数 | 20 | `relay_lib/record.py:132` | 無くす |
| L2 | ループの予算: 周回の数 | 3 | `lib/pace.py:93` | 書いたときだけ |
| L3 | ループの予算: 経過時間 | 24 時間 | `lib/pace.py:93` | 書いたときだけ |
| L4 | 予算の値の検証（正の数だけを受ける） | — | `lib/pace.py:110-112` | `null` を受ける |
| L5 | 保留の周回の上限 | 2 | `lib/sprint_loop_record.py:63`・`lib/sprint_loop_select.py:368` | 無くす |
| L6 | 実装のステップの打ち切り | 3600 秒 | `supervise_lib/verify_steps.py:52` | 置き換え（無進捗の打ち切り） |
| L7 | 実績からの実装の上限の見込み | 同じ構成の直近の最大（下限 3600 秒） | `supervise_lib/verify_steps.py:55-79`・`lib/estimate_predict.py:202` | 無くす |
| L8 | レーンの起動の断りを問う回数 | 3 回 | `lib/parallel_run.py:139` | 足す |
| L9 | 同じ失敗の繰り返し | 2 回 | `lib/pace.py:94` | 利用者が決める（残す） |
| L10 | merge から取り込みへ戻る回数 | 3 回 | `merged_lib/base_sync.py:23` | 利用者が決める（残す） |
| L11 | 配布で先端が動いたときの入れ直し | 3 回（2 か所で使う） | `release-steps.py:126` | 利用者が決める（残す） |
| L12 | 耐久ワークフローの止まりの待ち | 30 日 | `lib/durable.py:49` | 利用者が決める（残す） |
| L13 | Factory の区間の上限 | 20 | `factory/runner/src/sections.ts:6` | 利用者が決める（書いたときだけ） |
| L14 | 1 周回で選ぶ課題の数 | 3 | `lib/pace.py:95` | 残す。実行環境の調査が候補を出す |
| L15 | 動かないレーンを問うまで | 60 分 | `lib/parallel_run.py:138` | 残す。止めずに問うだけ（#2477 の範囲） |

## 暴走止め

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L16 | 再起動ループの検出 | 3 区間続けて 120 秒未満 | `relay_lib/record.py:133,186` | 残す |
| L17 | プランのステップの上限（修正のループと戻りの輪の想定回数・手書きのプランの既定） | 3 回・30 | `supervise_lib/limits.py:20-21` | 残す |
| L18 | 駆動の段階の数 | 1000 | `skills/cross-review/scripts/drive.py:413` | 残す |
| L19 | 検証と修正の繰り返しの上限 | 100 | `skills/cross-refactoring/scripts/assignee_launch.py:16` | 残す |

## 種類 B: 費用

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L20 | ループの予算: 費用 | 60 ドル（このリポジトリは 200 ドル） | `lib/pace.py:93`・`.ndf/pace.json` | 利用者が決める（残す・数え方を直す） |

## 配布の重複止め

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L21 | 一時レーンの同時の数 | 1 本 | `relay_lib/hub.py:425` | 残す（利用者の指示） |

## 種類 C: 資源と安全

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L22 | 同時に動かすラッパーの数 | 3 | `relay_lib/hub.py:85`・`lib/parallel_plan.py:29` | 残す。実行環境の調査が候補を出す（利用者が決める） |
| L23 | プランの本数の合計 | 6 | `lib/parallel_plan.py:28` | 残す。実行環境の調査が候補を出す（利用者が決める） |
| L24 | 並列の計画の外の `queue --max` の既定 | 3 | `lib/parallel_lane.py:116` | 残す |
| L25 | 文脈の量の運用上限 | 200,000 | `lib/context_policy.py:24` | 残す |
| L26 | 文脈の量の試行の運用上限 | 400,000 | `lib/context_policy.py:26` | 残す |
| L27 | 利用上限の待ちの最大 | 10800 秒 | `supervise_lib/claude.py:72` | 残す |
| L28 | 利用上限のリセット時刻が読めないときの待ち | 900 秒 | `supervise_lib/claude.py:71` | 残す |
| L29 | GitHub のレート上限の待ちの最大 | 3600 秒 | `lib/gh_quota.py:21` | 残す |
| L30 | 判定で倍々にする GitHub の待ちの上限 | 3600 秒 | `supervise_lib/engine.py:122` | 残す |
| L31 | 担当 1 本の見込み・予備のメモリ | 1536 MiB・1024 MiB | `parallel-measure.py` の既定値 | 残す |
| L32 | 基盤待ちの再実行の回数 | 3 回 | `lib/design_pr_merge.py:30` | 残す |

## 種類 D: 品質の収束

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L33 | cross-review のラウンドの上限 | 設計 3・コード 12 | `skills/cross-review/scripts/classifications.py:28` | 残す |
| L34 | cross-review が読む設計文書の行数 | 1000 行 | `skills/cross-review/scripts/classifications.py:30` | 残す |
| L35 | 振動の検知 | 検知で止まる | `lib/drive_pause.py:86` | 残す |
| L36 | cross-refactoring の時間予算の既定 | 30 分 | `skills/cross-refactoring/scripts/refactor_lib/vocabulary.py:107` | 残す |
| L37 | 監視の上限（工程ごと） | 1200 秒・3600 秒 | `lib/limits.py:34-46` | 残す。実装のステップを外部のランタイムで動かすときだけ渡さない（決定 8） |
| L38 | 無進捗の許容（担当ごと） | 180〜900 秒 | `lib/limits.py:49-54` | 残す。実装のステップは引数で渡す（決定 8） |
| L39 | 駆動が pause で止まってよい回数 | 12 回 | `supervise_lib/worker_steps.py:23` | 残す |
| L40 | 遅れの見張りの判定の回数・待ち直し・打ち直し | 2 回・3 回・1 回 | `lib/slow_step.py:39-41` | 残す。無進捗の打ち切りのステップでは見張りが打ち切らない（決定 5） |
| L41 | 報告が無い Skill のステップの起こし直し | 3 回 | `supervise_lib/worker_steps.py:154` | 残す |

## 種類 E: 状態の変化

| # | 止まる条件 | 何が起きたか | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L42 | `mvv_changed` | MVV が改訂された | `lib/sprint_loop_record.py:65` | 残す |
| L43 | `outside_mvv` | MVV の外の判定 | `lib/sprint_loop*.py` | 残す |
| L44 | `gate2_returned` | 本番の配布の承認が差し戻された | `lib/sprint_loop*.py` | 残す |
| L45 | `waiting_only` | 選べる課題が無く、答えを待つ作業だけが残る | `lib/sprint_loop_record.py:351`・`lib/sprint_loop_select.py` | 残す（`release_pending_max` の理由だけが無くなる） |
| L46 | `user_stop` | 利用者が止めた | `lib/sprint_loop_record.py:65` | 残す |
| L47 | `refused` | 使ってよい条件に当たらない | `lib/sprint_loop.py` | 残す |
| L48 | 同じ分類の検査の失敗が続く周回 | 3 周で人へ戻す | `lib/sprint_loop_select.py:165` | 残す |

## 外: 打ち直せばよい、または開発ループを止めないもの

| # | 上限 | 値 | 定義の場所 | 扱い |
| --- | --- | --- | --- | --- |
| L49 | `parallel.py wait` | 3600 秒 | `parallel.py:437` | 残す（要求の対象外） |
| L50 | `supervise.py wait` | 10800 秒 | `supervise.py:173` | 残す（要求の対象外） |
| L51 | 耐久の記録を消すまでの日数 | 30 日 | `lib/durable.py:48` | 残す（要求の対象外） |
| L52 | 実装以外の worker の 1 回の上限 | 1800 秒 | `supervise_lib/worker_steps.py:22` | 残す（要求の前提 7） |
| L53 | Skill のステップの上限 | 3600 秒 | `supervise_lib/templates.py:270`・`supervise_lib/sprint_waves.py:61` ほか | 残す（要求の前提 7） |
| L54 | 見直し・大整理の work のステップ | 5400 秒 | `supervise_lib/sprint_close_steps.py:106` | 残す（要求の前提 7） |
| L55 | テストの上限の既定（範囲・全体・CI の待ち） | 900 秒・1800 秒・3600 秒 | `lib/test_strategy.py:52` | 残す（実績から決め直す） |
| L56 | 走っているプランが「まだ動いている」を書く間隔 | 600 秒 | `supervise_lib/state.py:28` | 残す |
| L57 | ラッパーの入れ替えの用意の待ち | 600 秒 | `relay_lib/handover.py:55` | 残す |
| L58 | worktree の lock のやり直し | 5 回 | `supervise_lib/paths.py:62` | 残す |
| L59 | 設計の結果の取得のやり直し | 合計 120 秒 | `supervise_lib/design_stage.py:33` | 残す |
| L60 | hook の締め切り | 3.5 秒 | `hook_lib/deadline.py:16` | 残す |

## 作り方

要求の「調べた範囲」が指す全体の表は、課題の本文とコメントに残っていない。`gh issue view 2505 --json body,comments` で確かめた範囲は、本文の種類 A の表と残すものの表だけである。そのため 6c78fe695 の `plugins/ndf/` と `factory/` を次の形で読み直して作った。

```bash
rg -n '^[A-Z_]+ = [0-9]' plugins/ndf/scripts/relay_lib plugins/ndf/scripts/supervise_lib plugins/ndf/scripts/hook_lib
rg -n '^[A-Z_]+ = [0-9]' plugins/ndf/scripts/lib/parallel*.py plugins/ndf/scripts/lib/sprint_loop*.py plugins/ndf/scripts/lib/design_pr_merge.py plugins/ndf/scripts/lib/context_policy.py plugins/ndf/scripts/lib/gh_quota.py
rg -n 'env_num\(' plugins/ndf/scripts/relay_lib
rg -n '^[A-Z_]+ = [0-9]|DEFAULT_MAX_ROUNDS|STEP_LIMIT' plugins/ndf/skills/cross-review/scripts plugins/ndf/skills/cross-refactoring/scripts
```

## 未確認・残リスク

- 数え方は上の検索の範囲に限る。#2343 の設計は上限の数字のリテラルを 104 か所と数えており、この表はそのうち開発ループを止めるか、止めない理由を示す必要があるものだけを載せた。104 か所との突き合わせは行っていない
- `outside_mvv`・`gate2_returned`・`refused` の定義の場所はファイルまでで、行まで確かめていない
