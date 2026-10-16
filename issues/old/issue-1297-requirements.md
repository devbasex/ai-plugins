# #1297: waiting.md の待ちの雛形が timeout を使い、macOS では exit 127 になる

正は課題の本文（#1297）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> `skills/development-workflow/references/waiting.md` にある待ちの雛形は、先頭で `timeout 3600 bash -c '...'` を使う（101 行と 209 行）。macOS には `timeout` が標準で入っていない。そのため雛形をそのまま打つと `command not found` で exit 127 になる。GNU coreutils の `gtimeout` も入れていない端末では代わりのコマンドも無い。
>
> ```bash
> $ command -v timeout gtimeout    # macOS（Darwin 25.6.0）
> $ echo $?
> 1
> ```
>
> devbasex/devbase のスプリント issue-273 では、conductor がこの雛形の until ループで待とうとして exit 127 になった。
>
> ## どこで見つけたか
>
> - `plugins/ndf/skills/development-workflow/references/waiting.md` の 101 行（`progress.jsonl` の attention を待つ雛形）と 209 行（`.done` を待つ雛形）
>
> ## なぜこの変更の範囲外なのか
>
> devbase の変更の受け入れ条件と関係しない。NDF の待ちの雛形の移植性の問題である。
>
> ## 直さないと何が起きるか
>
> - macOS で 3 層を回すと、待ちの雛形が毎回すぐ失敗する。上限を付けない書き方へ直すか、その場で別の書き方を作ることになる。上限が無いと、待ちが終わらない
> - `NDF_SLEEP_GUARD` の検査は `timeout 590` の前置きを前提にしている（同じ文書の 240 行）。前置きを外した書き換えが、この検査と食い違うおそれがある
>
> 期待する振る舞いの案: 上限を bash の組み込み（`SECONDS` を使ったループの条件）で書くか、`timeout` の有無を確かめてから打つ共通の小さなスクリプトを置く。
>
> ## 由来
>
> devbasex/devbase issue #273（振り返りで拾った）

## 目的

- `waiting.md` の待ちの雛形 2 つ（attention と `report.md` を待つもの・完了マーカー `<置き場所>.done` を待つもの）を、`timeout` / `gtimeout` の入っていない端末（macOS の標準の状態）でもそのまま打てるようにする
- 上限（3600 秒）と、上限に達したときの終了コード 124 は今のまま保つ。雛形の後ろにある「終了コードごとの動き」の表を書き換えずに済むようにする

## 調べたこと

- `timeout` の無い `PATH`（`bash` / `cat` / `grep` だけを置いたディレクトリ）で `timeout 3 true` を打つと `timeout: command not found` で終了コード 127 になる（2026-09-30、この環境で実測）
- `plugins/ndf` の中で外部コマンドの `timeout` を打つのは、`waiting.md` の 101 行・209 行と `scripts/statusline.sh` の 110 行の 3 箇所である（`grep -rnE "(^|[;&|(\` ])timeout [0-9\"\$]" plugins/ndf plugins/playwright-kit scripts .github` の結果。`--timeout` の引数と `timeout=` を除く）。`statusline.sh` は `command -v timeout` で有無を確かめてから打つため、macOS でも落ちない
- sleep の検査（`hook_lib/token_guard.py` の `guard_sleep`）は `run_in_background: true` の呼び出しを見ない。`waiting.md` の 240 行の `timeout 590` は、検査がコマンドの位置を読むときに読み飛ばす前置きの例であり、雛形が `timeout` を使うことを前提にしてはいない
- 共通の層には、上限を `SECONDS` で数える待ちが既にある（`scripts/lib/bg-wait.sh` の `wait`。上限で 124 を返す）

## 前提

- 前提 1: 雛形を打つシェルは bash である。macOS の `/bin/bash` は 3.2 のため、雛形は bash 3.2 に無い構文（連想配列・`mapfile`・`${var,,}` など）を使わない
- 前提 2: 雛形は今と同じく Claude Code の Bash の `run_in_background: true` で起動する。前景で打つ使い方は sleep の検査が止める（今と同じ）
- 前提 3: 上限の数え方は壁時計の経過秒でよい。1〜5 秒の誤差（ループの 1 周の間隔）は許す
- 前提 4: macOS の実機はこの環境に無い。macOS での成立は「`timeout` / `gtimeout` の無い `PATH` で打てる」ことで代える。実機での確認はリリース後テストで行う

## 対象範囲

含む:
- `waiting.md` の待ちの雛形 2 つ（101〜102 行・209 行）の書き換え
- 書き換えた雛形が上の性質を持つことを確かめる自動テスト

含まない:
- `scripts/statusline.sh` の `timeout 1`（既に有無を確かめている）と `stat -c`（macOS では別の書き方になるが、この課題の報告の外）
- sleep の検査（`token_guard.py`）の振る舞いの変更。`timeout 590` を読み飛ばす処理はほかの書き方のために残す
- `bg-wait.sh` と `supervise.py wait` の振る舞いの変更
- macOS 全般の移植性の棚卸し（ほかの GNU 固有のコマンドの洗い出し）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | conductor か supervisor が待ちの雛形を背景で起動した | フェーズ・worker を起動した後 | 起動できない（シェルが無い）: 完了通知が失敗で届く | — |
| E2 | 待つ条件が成り立った（`report.md` が書かれた・attention の行が増えた・`.done` が現れた） | 背景の処理の書き込み | — | E1 |
| E3 | 待ちが終了コード 0 で終わった | E2 | — | E2 |
| E4 | 上限（3600 秒）に達し、待ちが終了コード 124 で終わった | 経過秒が上限に達した | — | E1。E2 が起きていない |
| E5 | 待ちがその他の終了コードで終わった（今は `timeout` が無いと 127） | 雛形の中のコマンドが見つからない・構文の誤り | 呼び出し側は 0 と 124 の表に当てはまらず、手で書き換える（この課題が無くすもの） | E1 |

## 受け入れ条件

- [ ] 1. `waiting.md` の待ちの雛形 2 つは、外部コマンドの `timeout` と `gtimeout` を打たない
- [ ] 2. 前提: `PATH` に `timeout` と `gtimeout` が無い（雛形が使う `bash` とその他のコマンドはある）
      操作: 完了マーカーを待つ雛形を、上限を短くして（例: 2 秒）打ち、上限までに `<置き場所>.done` を作る
      結果: 終了コード 0 で終わり、`exit=0` を出す
- [ ] 3. 前提: 2 と同じ `PATH`
      操作: 完了マーカーを待つ雛形を、上限を短くして打ち、`<置き場所>.done` を作らない
      結果: 上限の後、上限 + 10 秒以内に終了コード 124 で終わり、`exit=124` を出す
- [ ] 4. 前提: 2 と同じ `PATH`。状態ディレクトリに `progress.jsonl` がある
      操作: attention を待つ雛形を、上限を短くして打ち、(a) `report.md` を書く、(b) `"kind": "attention"` の行を 1 行足す、(c) どちらもしない
      結果: (a) と (b) は終了コード 0、(c) は終了コード 124 で終わる
- [ ] 5. 起動の時点で既に attention の行が N 行ある状態で attention を待つ雛形を打つと、N 行のままでは 0 で終わらない（起動前の行を新しい知らせと読まない。今の振る舞いを保つ）
- [ ] 6. 雛形は bash 3.2 に無い構文を使わない（前提 1）。この環境に bash 3.2 が無いため、設計が雛形の使う構文と外部コマンドを列挙し、コードレビューでその一覧と照らす
- [ ] 7. 雛形を前景（`run_in_background` なし）で打とうとすると、sleep の検査が今と同じく止める（`test_token_guard.py` で雛形のコマンドを入力にして確かめる）
- [ ] 8. 雛形の後ろの「終了コードごとの動き」の表（0 と 124）は、書き換えずに成り立つ
- [ ] 9. 既存の全体テストが通る（`uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| システム環境 | GNU coreutils の無い macOS の標準の状態（bash 3.2・BSD のユーザーランド）で雛形が動く。Linux（bash 5）での振る舞いは変えない |
| 性能・拡張性 | 待ちの間の呼び出しの回数は今と同じ（背景の起動 1 回・完了通知 1 回）。ループの間隔は今の 5 秒を超えない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 雛形の打ち方（背景で起動する・終了コード 0 / 124）は変わらない |
| データ | 無し |
| 既存の振る舞い | `timeout` のある Linux では、上限に達したときの止め方が「`timeout` が子を止める」から「ループが自分で抜ける」へ変わる。終了コードは同じ 124 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`。受け入れ条件 2〜5 は、雛形を `timeout` の無い `PATH` で子プロセスとして打つテストで確かめる（前景の sleep の検査に当たらないよう、テストの中の subprocess で打つ） |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/doc-lint.py`（文書の参照切れ） |
| 手動確認 | macOS の実機で雛形 2 つを背景で起動し、0 と 124 で終わることを見る（リリース後テスト。この環境に実機が無いため未検証のまま残る） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 待ちの規約は `waiting.md` が正本。共通の処理を置くなら `plugins/ndf/scripts/lib/`（`bg-wait.sh` の隣）で、Skill ごとに分けない（MVV の Value 6） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」と「書く前に実行して確かめる」 |
| テスト戦略 | `.md` の文言を照合するテストは書かない（`AGENTS.md`）。雛形の振る舞い（終了コード）を子プロセスで確かめるテストにする |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 書き換えた雛形を `timeout` の無い `PATH` で実際に打って確かめる。既存の全体テストの実行 |
| 確認してから行う | sleep の検査の規則を変える必要が出たとき（範囲外。別の課題にする） |
| 行わない | `statusline.sh` とほかのスクリプトの macOS 対応。`timeout` を利用者の環境へ入れる案内（C6） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 雛形の中に上限のループを書くか、`scripts/lib/` に共通の小さなスクリプトを置いて雛形がそれを呼ぶか（依頼の 2 案）。雛形がリポジトリの外（利用者のプロジェクト）で打たれるときのスクリプトのパスの解決も含む | `design` | 設計 PR |
| 受け入れ条件 2〜5 のテストが雛形のコマンドを `waiting.md` から取り出すか、テストの中に同じコマンドを持つか（`.md` の文言テストの禁止との関係） | `design` | 設計 PR |
