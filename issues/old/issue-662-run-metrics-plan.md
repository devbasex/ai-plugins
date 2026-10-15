# #662: 実行の所要時間と結末を作業ツリーの外へ残す（P1 の実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-662-598-537-619-584-583-requirements.md](issue-662-598-537-619-584-583-requirements.md)（P1 は AC1〜AC24 と AC70〜AC72）
- 設計文書: [issue-662-598-537-619-584-583-design.md](issue-662-598-537-619-584-583-design.md)（P1 は決定 1〜9）
- 契約の文書: [issue-662-598-537-619-584-583-design-contracts.md](issue-662-598-537-619-584-583-design-contracts.md)
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/666 （マージ済み）
- 課題: #662（マイルストーン 21）

## モード

`standard`。共通層に部品を 2 つ新設し、2 つの Skill の状態の保存と報告に差し込むため（判定済み）。

## 目的と非目的

達成したい状態:

- 監視の結果が担当ごとのファイルと追記だけの記録に残る
- cross-review / cross-refactoring の 1 回の実行の要約が、作業ツリーを消した後も利用者の状態ディレクトリに残る
- 要約を束ねて所要の分布を出せる

やらないこと:

- 上限の表・`--phase`・区切った待ち（P2）
- `usage_limit` / `cli_timeout` の検知、プロセスグループ、`read-result` の理由（P3）
- 要約の `apply_attempts`（D-C の P6、#665）

## 受け入れ条件

**要求の文書の AC1〜AC24 と AC70〜AC72 をそのまま使う。** ここでは写さない。タスクごとに満たす番号を指す。

## 修正対象

```text
plugins/ndf/scripts/lib/monitor_outcome.py        新設
plugins/ndf/scripts/lib/run_metrics.py            新設
plugins/ndf/scripts/lib/monitor.py                結果ファイルと記録の書き出し
plugins/ndf/scripts/lib/launch-cli.sh             <stem>-monitor.json の削除
plugins/ndf/scripts/lib/statefile.py              保存の後の差し込み口
plugins/ndf/scripts/lib/README.md                 置いてあるものの表
plugins/ndf/scripts/tests/test_run_metrics.py / test_run_metrics_docs.py  テスト
plugins/ndf/skills/cross-review/scripts/state.py  _save の末尾と report の最後の行
plugins/ndf/skills/cross-review/SKILL.md          measure.py の説明
plugins/ndf/skills/cross-review/docs/04-contracts.md  一時ディレクトリのファイル
plugins/ndf/skills/cross-review/docs/06-evidence.md   測定が要約へ写ること
plugins/ndf/skills/cross-review/tests/test_monitor_outcome_file.py / test_state_run_metrics.py  テスト
plugins/ndf/skills/cross-refactoring/tests/test_run_metrics_summary.py  テスト
scripts/tests/test_root_conftest.py               AC72 の前提
plugins/ndf/skills/cross-refactoring/scripts/refactor.py               差し込み口の登録
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/measure.py   新設
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/report.py  要約の行
plugins/ndf/skills/merged/SKILL.md                手順 4
conftest.py                                       NDF_METRICS_DIR
```

## タスク分解

### Task 1: 監視の結果ファイルと記録

- **対象ファイル:** `lib/monitor_outcome.py`、`lib/monitor.py`、`lib/launch-cli.sh`
- **変更内容:** 語彙（`REASONS`）と `reason_for` / `outcome_path` / `write_outcome` / `read_outcome` / `append_journal` / `read_journal`。`monitor.py` の CLI が担当ごとの監視を終えた直後に書く。`launch-cli.sh` は起動前に `<stem>-monitor.json` を消す
- **満たす受け入れ条件:** AC1〜AC7
- **進め方:** 偽の CLI を実プロセスで監視するテスト → 実装

### Task 2: 実行の要約の書き出し（cross-review）

- **対象ファイル:** `lib/run_metrics.py`、`cross-review/scripts/state.py`、`conftest.py`
- **変更内容:** 置き場所の解決、要約の組み立てと原子的な書き出し、`_save` の末尾からの呼び出し（例外は標準エラーへ 1 行）
- **満たす受け入れ条件:** AC8、AC10〜AC13、AC15〜AC18、AC72
- **進め方:** 一時ディレクトリの状態ファイルでテスト → 実装

### Task 3: 実行の要約の書き出し（cross-refactoring）

- **対象ファイル:** `lib/statefile.py`、`refactor.py`、`refactor_lib/measure.py`
- **変更内容:** `statefile.save` の差し込み口、`refactor.py` の登録、監視の記録から `phases` を組み立てる
- **満たす受け入れ条件:** AC9、AC14、AC16
- **進め方:** 監視の記録を手で書いた一時ディレクトリでテスト → 実装

### Task 4: 集計

- **対象ファイル:** `lib/run_metrics.py`
- **変更内容:** `aggregate` の副コマンド（`--since` / `--until` / `--repo` / `--kind` / `--version` / `--by` / `--dir`）
- **満たす受け入れ条件:** AC19〜AC22
- **進め方:** 手で作った要約 6 件（壊れた 1 件を含む）でテスト → 実装

### Task 5: 報告と文書

- **対象ファイル:** `state.py` の `report`、`refactor_lib/commands/report.py`、`merged/SKILL.md`、`cross-review/SKILL.md`、`cross-review/docs/04-contracts.md`、`lib/README.md`
- **満たす受け入れ条件:** AC23、AC24
- **進め方:** 最後の行と文言を確かめるテスト → 実装

## 実装上の決定

設計の「P1 の実装で決める」とされた点と、契約の文書が形だけを決めて中身を残した点を、ここで決める。

| # | 項目 | 決めたこと | 理由 |
| --- | --- | --- | --- |
| 1 | 監視の記録の同時の追記（設計の未確認 8） | 1 行を `O_APPEND` で開いたファイルへ 1 回の `os.write` で書き、`fcntl.flock` の排他を掛ける | 担当ごとのスレッドと、別プロセスの監視（レビューと反証）が同じファイルへ追記しうる。行の混ざりを 2 段で防ぐ |
| 2 | 版数の読み先（設計の未確認 7） | `run_metrics.py` の実体の位置から `../../.claude-plugin/plugin.json` を読み、読めなければ `null` | agy の配布は `scripts` を symlink にしており、`.resolve()` で実体へ届く |
| 3 | 結果ファイルを書く位置 | `monitor.py` の CLI（`main` の担当ごとのスレッド）で、`monitor_agent` が戻った直後に書く | `monitor_agent` を直接呼ぶ既存テストが一時ディレクトリへ書き出さない。戻り口が 8 か所ある関数本体を触らない |
| 4 | 共通層から Skill 固有の測定を呼ぶ形 | `run_metrics.after_save(path, state, kind, extra)` が、種類ごとの鍵を返す関数 `extra` を受け取る | 共通層が `cross-review/scripts/measure.py` を読み込むと、配る Skill を絞る配布先で読み込みが失敗する（`lib/README.md`） |
| 5 | `launches[]` に入れる行 | 監視の記録のうち、`started_at` がその実行の開始以降（終わった実行は終了以前）の行。cross-review は stem の `pr<番号>` が一致する行に限る | 一時ディレクトリは同じ Pull Request の再実行で使い回され、記録は追記だけで消えない。前の実行の起動を数えない |
| 6 | `phases` に置く工程 | 起動があった工程だけを置く。`other_seconds` は種類ごとに、終わりの分かるラウンドの所要から、そのラウンドの `cli_seconds` を引いた値（0 未満にしない）。終わりの分かるラウンドが無ければ `null` | 起動の無い工程を 0 で置くと、測っていない工程と 0 秒の工程を区別できない |
| 7 | `aggregate --by total` の「終わっていない」 | 種類ごとに `終わっていない（<種類>）` の行を置き、所要の列は `—` | 終わっていない実行は所要を持たない。種類をまたいで 1 行にすると、どちらの失敗かが読めない |
| 8 | `--since` / `--until` の日付 | 日付だけのときは地方時の 0 時を起点にし、`--until` はその日を含む | 利用者が「その日まで」と書いた意図に合わせる |
| 9 | `--by round-count` の対象 | 終わった cross-review の実行だけ | 所要の中央値を出すため |
| 10 | `report` の要約の行 | `report` の中で要約を書き直し、その結果のパスか理由を最後の行に出す | `report` は状態を保存しないため、保存の差し込み口を通らない |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `state.py` は 4338 行の 1 ファイル | 触るのは `_save` の末尾と `report` の最後の行だけにし、要約の組み立ては共通層へ置く。タスクごとにテストを通す |
| 既存テストが利用者のホームへ要約を書く | リポジトリ直下の `conftest.py` がセッションの間 `NDF_METRICS_DIR` を一時ディレクトリへ向ける。テストの前後で `find` で確かめる（AC72） |
| 要約の失敗が収束ループを止める | 差し込み口で例外を受け、標準エラーへ 1 行だけ出す（AC16） |

## 切り戻し手順

- 追加だけの変更で、状態ファイルの鍵も監視の標準出力も変えない。Pull Request を revert すれば戻る。利用者の状態ディレクトリに残った要約は、消しても何も読まない

## 後続への申し送り

- P3: レビュー（`launch-reviewer.sh`）・反証（`critique.sh`）・cross-refactoring の起動はどれも共通層の `launch-cli.sh` を通るため、起動し直すと前の `<stem>-monitor.json` は消える。`read-result` が監視の結果ファイルを読むのは、同じ起動の監視の後だけと考えてよい。ただし起動に失敗して監視を通らなかった担当はファイルを持たない（AC55 の「ファイルが無い」の枝）
- P2: 監視の記録に `phase` を足したら、`refactor_lib/measure.py` の工程の判定は `phase` を優先する（契約の文書の「stem から工程を読む規則」）

## 完了の定義

- [ ] AC1〜AC24 と AC70〜AC72 をすべて満たし、条件ごとに確かめたテストかコマンドが対応している
- [ ] cross-refactoring と cross-review を通し、CI の必須チェックがすべて pass
