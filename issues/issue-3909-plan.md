# #3909: 中継のラッパーの外で NDF が上がっても、次のセグメントと `ndf` を今の導入のバージョンで動かす

## 関連リンク

- 要求仕様: [issue-3909-requirements.md](./issue-3909-requirements.md)（正は [#3909](https://github.com/devbasex/ai-plugins/issues/3909) の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3909-design.md](./issue-3909-design.md)、詳細設計書: [issue-3909-design-detail.md](./issue-3909-design-detail.md)
- モード: `standard`（公開インタフェース（`log.jsonl` の `plugin_update` の行のキーと子の `PATH`）が変わり、触るファイルが 11。conductor の判定）

## 修正対象

詳細設計書の「地図」から引き写す。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/adapters/claude.py` | `same_cache_rows`（新設）。`plugin_update._is_ndf` をここへ移す |
| `plugins/ndf/src/ndf/domains/relay/plugin_update.py` | `find_origin`（引数 `version`・`environ` を足す）・`Updater._origin_in`（変更）、属性 `Updater.version`・定数 `MANY_CACHED`（新設） |
| `plugins/ndf/src/ndf/domains/relay/cli.py` | `wrapper_settings` が `Updater` に起動時のバージョンを渡す |
| `plugins/ndf/src/ndf/domains/relay/session_env.py` | `with_launched_ndf`（新設） |
| `plugins/ndf/src/ndf/domains/relay/wrapper.py` | `_before_start`・`_start`・`_log_update`（変更）、属性 `known_version`・`launched_root`（新設） |
| `plugins/ndf/src/ndf/progress/lane/launch.py` | `installed_entry`・`_fallback_entry`・定数 `OWN_VERSION`（新設）、`entry`・`spawn`（変更） |
| `plugins/ndf/skills/development-workflow/references/relay.md` | 記録の読み方の `wrapper`・`outside`、自動更新の止め方・戻し方、ラッパーが自分で上げる経路を確かめる手順 |
| `plugins/ndf/tests/domains/relay/test_relay_plugin_update.py`・`test_relay_parts.py`・`test_relay_wrapper.py`・`plugins/ndf/tests/progress/test_lane_launch.py` | 詳細設計書の「単体テストの観点」の行 |

## タスク分解

種類は不具合なので、各タスクは再現のテスト（今は落ちる）から始める。

### Task 1: 外で上がった導入を更新の対象にする

- **対象ファイル:** `adapters/claude.py`・`domains/relay/plugin_update.py`・`domains/relay/cli.py`・`tests/domains/relay/test_relay_plugin_update.py`
- **変更内容:** 詳細設計書の「同じキャッシュの導入を選ぶ規則」のとおり。`same_cache_rows` を新設し、`_is_ndf` を `adapters.claude` へ移す。`find_origin` は根と一致する行を先に選び、無ければ同じキャッシュの親の有効な行がちょうど 1 件のときその行を選ぶ（2 件以上は `MANY_CACHED`）。`Updater.version` を足し、`wrapper_settings` が起動時のバージョンを渡す
- **満たす受け入れ条件:** 1・4・6
- **進め方:** 失敗するテスト（根がバージョン N のキャッシュ・一覧に同じ親の M だけで `skipped` になる今の振る舞いを落とす。メジャーバージョンが違う行で次のセグメントを起動しない）→ 通す最小実装 → 整理。退行のテスト（キャッシュの外の根・`ai-plugins/ndf/dev` の根・別のマーケットプレイス・2 つのスコープ・同じ親に 2 件）を同じタスクで足す

### Task 2: 子の `PATH` を起動先のバージョンにそろえる

- **対象ファイル:** `domains/relay/session_env.py`・`domains/relay/wrapper.py`・`tests/domains/relay/test_relay_parts.py`・`tests/domains/relay/test_relay_wrapper.py`
- **変更内容:** 詳細設計書の「子の `PATH` を起動先のバージョンにそろえる」のとおり。`with_launched_ndf` を新設し、`_before_start` が起動先の根を `launched_root` に覚え、`_start` が子の環境に通す
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト（`tmp_path` に N と M の `bin/ndf`・N を指すシンボリックリンクのディレクトリを作り、返した環境で `sh -c 'command -v ndf'` が M を返す）→ 通す最小実装 → 整理

### Task 3: 記録にラッパーのバージョンと外で上がったかを足す

- **対象ファイル:** `domains/relay/wrapper.py`・`tests/domains/relay/test_relay_wrapper.py`
- **変更内容:** 詳細設計書の「記録にラッパーのバージョンを足す」のとおり。`_log_update` が `wrapper` と `outside` を書き、`known_version` を `launched` へ進める。`outside` の 2 つの比べと、`failed` の行の説明できない差の null を含む
- **満たす受け入れ条件:** 2（コードの側）
- **進め方:** 失敗するテスト（詳細設計書の単体テストの観点の行を 1 つずつ）→ 通す最小実装 → 整理

### Task 4: 耐久ワークフローがラッパーを今の導入のバージョンで起動する

- **対象ファイル:** `progress/lane/launch.py`・`tests/progress/test_lane_launch.py`
- **変更内容:** 詳細設計書の「耐久ワークフローがラッパーを今の導入のバージョンで起動する」のとおり。`OWN_VERSION`・`_fallback_entry`・`installed_entry` を新設し、`entry` を `_fallback_entry` の上に組み直し（振る舞いと出力は同じ）、`spawn` の argv の先頭を `installed_entry(cwd)` にする
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト（一覧に同じ親の M があるとき `spawn` の argv の先頭が M の `bin/ndf`）→ 通す最小実装 → 整理。`entry` の今のテストはそのまま通す

### Task 5: 手順書に記録の読み方・自動更新の止め方・確かめる手順を載せる

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/relay.md`
- **変更内容:** 詳細設計書の「記録にラッパーのバージョンを足す」の relay.md の段落と「手順書: 自動更新の止め方と確かめる手順」のとおり。載せるコマンドと `jq` は書く前に手元で打ち、結果を確かめる
- **満たす受け入れ条件:** 2（手順書の側）・7・8
- **進め方:** テスト駆動を適用しない（手順書の文。`.md` の文言を照合するテストは書かない）。コマンドを手元で打った結果を Pull Request の本文に残す

### Task 6: relay.md の上限の表を 11 系のコードに合わせる

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/relay.md`
- **変更内容:** 上限の表の 10 系の再起動ループの行と、その下の「起動の回数は数えない」「`NDF_RELAY_MAX_STARTS` は読まない」の段落を、11 系のコードの事実（`contracts/limits.json` の `relay.max-starts`・`domains/relay/cli.py` の `NDF_RELAY_MAX_STARTS` の読み込み・上限に達したときのラッパーの振る舞い）に合わせる。同じ文書で再起動ループに触れるほかの行も合わせる
- **満たす受け入れ条件:** 10（利用者の指示で範囲に追加。2026-11-20）
- **進め方:** テスト駆動を適用しない（手順書の文。`.md` の文言を照合するテストは書かない）。書く前に事実をコードと `grep` で確かめる

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u1 導入元の選び方 | Task 1 | 3909:1・3909:4・3909:6 | 約 250 | （実装の後に書き足す） |
| u2 子の `PATH` と記録 | Task 2・3 | 3909:2・3909:3 | 約 350 | （実装の後に書き足す） |
| u3 耐久ワークフローと手順書 | Task 4・5・6 | 3909:5・3909:7・3909:8・3909:10 | 約 300 | （実装の後に書き足す） |

- u1 が `same_cache_rows` を持ち、u3 がそれを読むので u1 を最初に流す。u3 は u1 のマージの後に始める。u2 は u1 とファイルが重ならず u1 のコードを読まないので、u1 と並行で始めた
- 受け入れ条件 9 はリリース後テストで確かめる（要求仕様の「リリース後の確認の計画」）ので、どの単位にも載せない

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルは 6 つで最大は `wrapper.py` の 702 行。同じファイルを触るタスクは Task 2・3 の 2 つ（5 タスク中）で、`wrapper.py` の既存のテストは 698 行ある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `adapters/claude.same_cache_rows` を読む `plugin_update.find_origin` と `launch.installed_entry` の重なり | u1 と u3 が同じ規則を別の呼び手から使うため、選んだ後の扱い（件数の判定・理由の文）が重なっていないかを見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 子の `PATH` から NDF の仮想環境の `bin` を外すと、子の Bash で仮想環境の `python` を直に使う手順が動かなくなる | u2 の Pull Request で、リポジトリの手順書が仮想環境の `python` を直に呼んでいないかを `grep` で確かめ、結果を本文に残す |
| 同じファイル（`wrapper.py`・`launch.py`）を触る開いた Pull Request と衝突する | 各単位の worktree を切る前に `gh pr list --search` で確かめ、あれば読み直す |

## 完了の定義

- [ ] 受け入れ条件 1〜8 と 10 をすべて満たし、条件ごとに検証手段と結果が対応している（9 はリリース後テスト）
- [ ] 単位ごとの Pull Request の CI（pytest ほか）が通り、`cross-review` が収束している
