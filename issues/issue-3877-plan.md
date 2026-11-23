# 3877: 報告の保存先を `ndf worker prepare` で用意し、確認ダイアログでレーンが止まらないようにする

## 関連リンク

- 要求仕様: [issue-3877-requirements.md](./issue-3877-requirements.md)。受け入れ条件は番号（1〜5）、起票者の条件は S1・S2 で指す
- 設計書: [issue-3877-design.md](./issue-3877-design.md)・詳細設計書: [issue-3877-design-detail.md](./issue-3877-design-detail.md)
- モード: `standard`（不具合の直しで、サブコマンドを 3 つ足し、hook の guard と手順書を変える安定版の変更。設計承認ゲートを通した設計文書を持つ）

## 修正対象

詳細設計書の「地図」から引いた。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/contracts/worker_paths.py`（新設） | `report_paths`（4 つのパスの唯一の決め方）と、リンクをたどらない読み書き（`open_workdir`・`entry_state`・`read_file`・`write_file`・`remove_file`） |
| `plugins/ndf/src/ndf/contracts/cli.py` | `ndf contracts emit --out` の書き込みを `write_file` にする |
| `plugins/ndf/src/ndf/core/worker.py` | `ndf worker prepare`・`append`・`collect` を足す。`worker run` の結果ファイルをリンクをたどらずに扱う |
| `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py`（新設） | 空かもしれない変数の `rm` を止める guard（Task 1 の実測で先に働くと分かったときだけ） |
| `plugins/ndf/contracts/hooks/guards.json` | `rm-variable` の 1 行（同上） |
| `plugins/ndf/docs/contracts/commands.md` | 3 つのサブコマンドの節（`ndf check --write-spec` の生成物） |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 起動指示の「置く場所」「作業の結果ファイル」の欄と守る規則（3 か所） |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 「中間通知を受けたとき」の用意・完了マーカー・報告の読み取り（3 か所） |
| `plugins/ndf/tests/contracts/test_worker_paths.py`（新設） | パスの組み立てとリンクをたどらない読み書き |
| `plugins/ndf/tests/core/test_worker_prepare.py`（新設） | `prepare`・`append`・`collect` の返りとファイルの始末 |
| `plugins/ndf/tests/domains/hooks/test_rm_variable.py`（新設） | guard の判定 |
| `plugins/ndf/tests/contracts/test_contracts_cli.py`・`plugins/ndf/tests/worker/test_delegate.py` | `emit --out` と `worker run` の委譲中・結果の読み取りがリンクをたどらないこと |

## タスク分解

不具合の再現のテスト（同じ名前で 2 回目の作業を出すと、1 回目の完了マーカーが残る）は Task 4 の最初のステップで書く。再現に使うサブコマンドが Task 2・3 のモジュールの上に立つためである。

### Task 1: 実測 — `PreToolUse` の hook と確認ダイアログの順序

- **対象ファイル:** `issues/issue-3877-design.md`（「未確認のまま残ること」の「hook と確認の順序」の行。決定 4 が結果の書き戻しを定める）
- **変更内容:** 実機の Claude Code で、一時的な `PreToolUse` の hook（Bash を `deny` するだけのもの。コミットしない設定で入れる）を有効にし、消えても困らない一時ディレクトリの中のパスに空の変数を混ぜた `rm -f` を 1 回打つ。確認ダイアログが出る前に hook の拒否がツールの結果として返るかを見る。結果（打ったコマンド・Claude Code のバージョン・返った文）を設計文書の行と単位の Pull Request に残す。先に働かなければ Task 9 を落とし、受け入れ条件 5 を落としたことを同じ行に書く
- **満たす受け入れ条件:** 5 の但し書き（起票者の条件 S2）
- **進め方:** テスト駆動を適用しない（実機の画面の振る舞いの確認で、コードを書かない）

### Task 2: 報告の保存先のパスを組む（`report_paths`）

- **対象ファイル:** `plugins/ndf/src/ndf/contracts/worker_paths.py`（新設）、`plugins/ndf/tests/contracts/test_worker_paths.py`（新設）
- **変更内容:** `ReportPaths`・`NameInvalid`・`report_paths` と定数 `NAME_RE`・4 つの接尾辞。詳細設計の「報告の保存先のパスを組む」の単体テストの観点の表どおり
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（4 つのパス・名前の境界 1 文字 / 64 文字 / 65 文字・相対パスの作業ディレクトリ）→ 通す最小実装 → 整理。範囲のテストは `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/contracts/test_worker_paths.py -q` で打つ

### Task 3: リンクをたどらない読み書き

- **対象ファイル:** `plugins/ndf/src/ndf/contracts/worker_paths.py`、`plugins/ndf/tests/contracts/test_worker_paths.py`
- **変更内容:** `EntryError`・`EntryUnsafe`・`EntryUnreadable`・`WorkDir`・`open_workdir`・`entry_state`・`read_file`・`write_file`（書き込みの手順 1〜6）・`remove_file`
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の表の 5 行。競合は `os.fstat`・`entry_state` を差し替えた関数の中で名前を差し替えて作る）→ 通す最小実装 → 整理

### Task 4: 報告の保存先を用意するサブコマンド（`ndf worker prepare`）

- **対象ファイル:** `plugins/ndf/src/ndf/core/worker.py`（`_cmd_prepare`・`_add_prepare`・`_parser`）、`plugins/ndf/tests/core/test_worker_prepare.py`（新設）、`plugins/ndf/docs/contracts/commands.md`（生成物）
- **変更内容:** 確かめ → 完了マーカー → 結果ファイル → 報告の順に変え、`metrics.paths` と `items` を返す。失敗のときは `metrics.paths` を出さない
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 最初に再現のテスト（同じ `--dir`・`--name` で 2 回目を打つと 1 回目の `.done` と結果ファイルが消え、報告が空になる。今はサブコマンドが無く落ちる）→ 詳細設計の単体テストの観点の表の残り（3 通り × 2 種類のリンク・途中の失敗 3 通り）→ 通す最小実装 → 整理。`ndf check --write-spec` で生成物を作り直す

### Task 5: 報告の追記と読み取り（`ndf worker append`・`ndf worker collect`）

- **対象ファイル:** `plugins/ndf/src/ndf/contracts/worker_paths.py`（`report_name`）、`plugins/ndf/src/ndf/core/worker.py`（`_cmd_append`・`_add_append`・`_cmd_collect`・`_add_collect`）、`plugins/ndf/tests/core/test_worker_prepare.py`、`plugins/ndf/docs/contracts/commands.md`（生成物）
- **変更内容:** 標準入力の本文を報告へ足し、`--done` で完了マーカーを作る。`collect` は最後の `## 作業の報告` から末尾までを `metrics.report` に返す
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の 2 行）→ 通す最小実装 → 整理。本文を `<<'NDF_REPORT'` のヒアドキュメントで渡す形が `heredoc` guard に止められないことを、`plugins/ndf/tests/domains/hooks/test_heredoc.py` の既存の判定を読んで確かめる（足すテストは無い）

### Task 6: 手順書を 1 行のコマンドへ書き換える

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/agent-layers.md`（235・236・252 行）、`plugins/ndf/skills/development-workflow/references/waiting.md`（229・233・243 行）
- **変更内容:** 詳細設計の「手順書の報告の保存先の用意」の表の 6 か所だけを変える。ほかの行は並行するスプリントも触るので変えない。起票者の条件 S1（ほかの手順書・エージェント定義の同じ形）は、計画を書いた時点で `grep -rln '\.done' plugins/ndf/agents plugins/ndf/skills` が上の 2 ファイルだけを返した。実装の時点でもう一度打って確かめる
- **満たす受け入れ条件:** 1・2（S1）
- **進め方:** テスト駆動を適用しない（`.md` の文言を照合するテストを書かない決まり）。書き換えた 2 ファイルを読み、パスを組む変数・`rm`・リダイレクトが残らないことをコードレビューで見る。手順書に書くコマンドは、書く前に一時ディレクトリで 1 回打って結果 JSON を確かめる

### Task 7: `ndf worker run` の結果ファイルをリンクをたどらずに扱う

- **対象ファイル:** `plugins/ndf/src/ndf/core/worker.py`（`_cmd_run`・`delegate`・`_written`・`_read_result`・`search_record`・`_missing_record`・`_stopped_detail`・`outcome_of`）、`plugins/ndf/tests/worker/test_delegate.py`
- **変更内容:** 詳細設計の「使う側」の表の `ndf worker run` の 3 行。結果ファイルを読むのは委譲中に 1 回（`RECORD_KINDS` のとき）と委譲の後に 1 回だけにする
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（詳細設計の `_cmd_run` の行：外へのリンクで `stopped`（`invalid`）・委譲中の差し替えで `bad_report`・不正な UTF-8 で `bad_report`・`read_file` の呼び出しの回数）→ 通す最小実装 → 整理。範囲のテストは `plugins/ndf/tests/worker/` を指定して打つ

### Task 8: `ndf contracts emit --out` をリンクをたどらずに書く

- **対象ファイル:** `plugins/ndf/src/ndf/contracts/cli.py`（`_cmd_emit`）、`plugins/ndf/tests/contracts/test_contracts_cli.py`
- **変更内容:** `open_workdir` と `write_file` で書く。`EntryUnsafe` なら標準出力へ行を出さずに `stopped`（`invalid`）
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（外へのリンク・ハードリンク・読み取り専用のファイル）→ 通す最小実装 → 整理

### Task 9: 空かもしれない変数の `rm` を止める guard（Task 1 で先に働くと分かったときだけ）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py`（新設）、`plugins/ndf/contracts/hooks/guards.json`、`plugins/ndf/tests/domains/hooks/test_rm_variable.py`（新設）
- **変更内容:** `expanded_variables`・`risky_removal`・`check` と、表の行（`order: 26`・`deny_key: "none"`・`fail_closed: false`）
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の 4 行。上限の後も拒否が案内へ下がらないことを dispatch と組んで確かめる）→ 通す最小実装 → 整理。最後に Task 1 と同じ実機の手順で、guard の拒否と書き直しの案内が返ることを 1 回確かめて Pull Request に残す

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| `3877-u1-paths` | Task 2・3 | 3877:4 | 約 490 | （実装の後に書き足す） |
| `3877-u2-prepare` | Task 4・5・6 | 3877:1・3877:2・3877:3・3877:4 | 約 470 | （実装の後に書き足す） |
| `3877-u3-run` | Task 7・8 | 3877:4 | 約 220 | （実装の後に書き足す） |
| `3877-u4-guard` | Task 1・9 | 3877:5 | 約 270（Task 1 で guard を落とせば約 10） | （実装の後に書き足す） |

行数は追加と削除の和（テストを含む）で、`plugins/ndf/docs/contracts/commands.md` と `uv.lock` は数えない。課題の見込みの和が約 1,450 行で 1,000 行を超えるため、依存の向き（パスと読み書きの関数 → それを使うサブコマンド → `worker run` の結果の扱い）で分けた。guard はほかの単位と触るファイルが重ならない。

**単位の順序（#3908・#4036 を含むスプリント全体）:** 最初に `3877-u1-paths` を流す（#3908 の `3908-u3-prompt` が使う `report_paths`・`open_workdir`・`write_file` を含む準備の単位）。同時に、触るファイルが重ならない `3877-u4-guard`・`3908-u1-kinds`・`3908-u2-values`・`4036-u1-record` を並列で流してよい。`core/worker.py` を触る 3 つの単位は `3877-u2-prepare` → `3877-u3-run` → `3908-u4-outputs` の順に 1 つずつ流し、並列にしない（`3877-u3-run` と `3908-u4-outputs` はどちらも `outcome_of`・`_cmd_run` の引数を変える）。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `core/worker.py` は 756 行で、触るのは 9 タスク中 3 タスク（Task 4・5・7）。`tests/worker/` に 52 件、`test_delegate.py` に 18 件のテストがある。ほかの触るファイルは新設か 253 行以下 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `core/worker.py` の `outcome_of`・`_cmd_run`（`3877-u3-run` と `3908-u4-outputs`）。`worker_paths.write_file` と `domains/relay/signals.replace_json`（`4036-u1-record` の `write_activity` が使う）の置き換えで書く処理 | 前者は 2 つの単位が同じ関数の引数を足す。後者は一時ファイルを作って置き換える同じ形の処理が 2 か所に並ぶ |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `agent-layers.md` を並行するほかのスプリントも触り、マージの時点で衝突する | Task 6 は詳細設計の表の 6 か所だけを変える。衝突したら該当の行だけを取り直す |
| リリースの途中で、古い手順書を読んだ supervisor と新しい worker の決まりが混ざる | worker の側の完了マーカーの名前（`<置く場所>.done`）と報告のパスの形（`<名前>.report.md`）は変えない。古い手順の `: >` で作ったファイルも `collect` が読める |
| Task 1 で hook が確認ダイアログより後に働くと分かる | Task 9 と `rm-variable` の行を入れず、設計文書の行と Pull Request に結果を残して受け入れ条件 5 を落とす（要求仕様の但し書きのとおり） |
| 本文を渡すヒアドキュメントが `heredoc` guard か `script-chain` guard に止められる | Task 5 で既存の guard の判定を読んで確かめ、Task 6 で手順書の形を書く前に 1 回打つ |

## 切り戻し手順

- 単位ごとの Pull Request の revert で戻る。データ移行は無い。`3877-u2-prepare` を戻すときは、手順書（Task 6）とサブコマンド（Task 4・5）を同じ revert で戻す（手順書だけが残ると、無いコマンドを指す）
- `3877-u4-guard` は表の 1 行を消すだけで働かなくなる

## 完了の定義

- [ ] 受け入れ条件 1〜4 を満たし、条件ごとに検証手段（テスト関数の名前）と CI の結果（`gh pr checks <PR>`）が対応している
- [ ] 受け入れ条件 5 は、Task 1 の実測の結果に従って、guard のテストと実機の確認を Pull Request に残すか、落とした理由を設計文書の行と Pull Request に残している
- [ ] 起票者の条件 S1 の検索の結果（実装の時点の `grep` のコマンドと返り）を Pull Request に残している
- [ ] `standard` のモードの検証（単位ごとの cross-review の収束・CI の必須のチェック）を通している（quality-gates）
