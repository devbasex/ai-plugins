# #417: v10.5.0 の事後レビューで出た 8 件を直す

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/417
- 要求と受け入れ条件: [issue-418-417-workflow-gate/01-requirements.md](issue-418-417-workflow-gate/01-requirements.md) の D1〜D10
- 設計: [issue-418-417-workflow-gate/02-design.md](issue-418-417-workflow-gate/02-design.md) の決定 5・決定 6
- 確定仕様: [ndf-workflow-unit-and-gates.md](../../docs/specifications/ndf-workflow-unit-and-gates.md)

## モード

`standard`。本番の振る舞い（検査スクリプトの走査範囲・hook の導入内容・認証ヘルパの探索先）を
変えるバグ修正であり、対象にはテストがある。

## 目的と非目的

達成したい状態:

- **失敗が表に出ない 3 つの経路が塞がる。** 日本語のファイル名を持つ文書が行数の検査を
  素通りする経路、相対パスや空白を含むパスで導入した hook が正常終了しながら効かない経路、
  除外の設定だけが古いまま残る経路
- 配布後の前提と食い違った案内文が直る

やらないこと:

- 工程表・gate・承認の関門（本 2 と本 3 が扱う）
- `check-doc-line-limit.py` の基準値（500 行）と `RECORD_PREFIXES` の見直し
- `_CANDIDATES` へ claude / codex のプラグインキャッシュの経路を足すこと。版数を含む
  ディレクトリの走査が要り、D7 が求める範囲を超える

## 前提

- 前提 1: `google-auth` は 4 つの manifest すべてに載っている（`grep -c` で確認済み）。
  そのため `_drive_auth.py` の「どの公開セットにも同梱していない」は事実と食い違う
- 前提 2: `assert-plugin-files.sh` の `find -print -quit` は GNU find の中でだけ動く。
  実行は `node:22-bookworm-slim` の `docker exec` の中であるため、この形は残す。
  D10 が塞ぐのは**パイプの残り**である

## 受け入れ条件

`01-requirements.md` の D1〜D10 をそのまま採る。検証手段を添える。

- [ ] D1: `git ls-files -z` を使い、非 ASCII のファイル名を含む文書が検査される
      — `test_doc_line_limit.py`
- [ ] D2: 除外の後に対象が 0 件のとき、終了コード 2 で失敗する — `test_doc_line_limit.py`
- [ ] D3: `--plugin-dir` の相対パスが絶対パスへ解決される — `test_agy_install_hooks.py`
- [ ] D4: 導入先に空白やシェルの特殊文字が含まれても、保存された command が `bash -c` で
      実行できる — `test_agy_install_hooks.py`（実際に `bash -c` で走らせる）
- [ ] D5: `notion-writing/SKILL.md` のバッククォートが対になっている
      — `test_notion_writing_backticks.py`（新設）
- [ ] D6: `EXEMPT` に載っているファイルが存在しないとき、終了コード 1 で失敗する
      — `test_doc_line_limit.py`
- [ ] D7: `_drive_auth.py` のメッセージが配布後の前提と一致し、`_CANDIDATES` に agy の
      パスが入っている — `plugins/playwright-kit` のテスト
- [ ] D8: `--uninstall` が消すのは配布する `hooks.json` が持つ名前だけである
      — `test_agy_install_hooks.py`
- [ ] D9: `hooks.json` の書き込みが一時ファイル経由の置き換えで行われる
      — `test_agy_install_hooks.py`
- [ ] D10: スモークの assertion がパイプを使わない形になっている
      — `test_smoke_assertions.py`（新設）
- [ ] 退行しないこと: 既存のテストと検査 10 本が終了コード 0 のまま

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `git ls-files -z` で `NUL` 区切りにする | 採用 | 出力の形だけを変え、利用者の設定を読まない（決定 5） |
| B | `git -c core.quotepath=false ls-files` | 不採用 | 同じ結果になるが、利用者の設定を上書きする形になる |
| C | command を `shlex.quote` した絶対パスで組み立て直す | 採用 | 語としての正しさを保証できる（決定 6） |
| D | `str.replace` のままクォートを足す | 不採用 | command 全体のどこを引用すべきかを決められない |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 控えの `EXEMPT` | 行数の検査から外す文書と、外した理由の対応表 |
| 名前付き hook | `hooks.json` の最上位の鍵。agy はこの単位で hook を読む |

## 不変条件

- **`hooks.json` の書き込みは、成功するか元のままかのどちらかである。** 中断で壊れた
  設定を残さない（D9）
- **`--uninstall` は、配布する `hooks.json` が持つ名前だけを消す。** 利用者が自分で
  書いた項目は、名前が似ていても残る（D8）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（コマンド） | `check-doc-line-limit.py` / `install-hooks.sh` の引数は変えない | 変えない |
| データスキーマ | 無し | 該当なし |
| 保存される `hooks.json` の command | 相対パスから絶対パスへ変わる | **利用者の設定が直る方向の変更**。導入し直しで反映される |

## 修正対象

- `scripts/check-doc-line-limit.py`
- `scripts/tests/test_doc_line_limit.py`
- `plugins/ndf/dev.agy/install-hooks.sh`
- `scripts/tests/test_agy_install_hooks.py`
- `plugins/ndf/skills/notion-writing/SKILL.md`
- `plugins/playwright-kit/skills/playwright-kit-ops/scripts/_drive_auth.py`
- `plugins/playwright-kit/skills/playwright-kit-ops/tests/`（D7 のテスト）
- `tests/runtime-smoke/assertions/assert-kiro-agent.sh`
- `scripts/tests/test_smoke_assertions.py`（新設。D5 と D10）

## タスク分解

### Task 1: 行数の検査が非 ASCII のファイル名を拾う

- **対象ファイル:** `scripts/check-doc-line-limit.py` / `scripts/tests/test_doc_line_limit.py`
- **変更内容:** `git ls-files -z` へ変え、`NUL` で分ける
- **満たす受け入れ条件:** D1
- **進め方:** 日本語のファイル名で 501 行の文書を追跡させ、終了コード 1 を期待する
  テストを先に書く（いまは 0 で通る）

### Task 2: 走査の結果が実質 0 件のときに失敗する

- **対象ファイル:** `scripts/check-doc-line-limit.py` / `scripts/tests/test_doc_line_limit.py`
- **変更内容:** 除外の後の件数（`counts`）が 0 のときに終了コード 2 を返す
- **満たす受け入れ条件:** D2
- **進め方:** 記録だけのリポジトリで終了コード 2 を期待するテストを先に書く

### Task 3: `EXEMPT` の不在を失敗にする

- **対象ファイル:** `scripts/check-doc-line-limit.py` / `scripts/tests/test_doc_line_limit.py`
- **変更内容:** `EXEMPT` の鍵に対応するファイルが無いとき、終了コード 1 で失敗する。
  **除外は片方向ではない**（基準を下回ったときに落ちる既存の扱いと同じ側に置く）
- **満たす受け入れ条件:** D6
- **進め方:** `CHANGELOG.md` の無いリポジトリで終了コード 1 を期待するテストを先に書く

### Task 4: 導入先のパスを絶対化し、語として正しく組み立てる

- **対象ファイル:** `plugins/ndf/dev.agy/install-hooks.sh` /
  `scripts/tests/test_agy_install_hooks.py`
- **変更内容:** `--plugin-dir` を絶対パスへ解決し、`bash ./scripts/<名前>` の形の command
  だけを `shlex.quote` した絶対パスで組み立て直す。当たらない形は書き換えず、書き換え
  なかったことを出力へ出す
- **満たす受け入れ条件:** D3 / D4
- **進め方:** 相対パスで導入したときに保存された command が絶対パスになること、空白を
  含む導入先で保存された command が `bash -c` で走ることを、先にテストで固定する

### Task 5: 削除の対象を配布する定義の名前に限る

- **対象ファイル:** `plugins/ndf/dev.agy/install-hooks.sh` /
  `scripts/tests/test_agy_install_hooks.py`
- **変更内容:** `--uninstall` の対象を `n.startswith("ndf-")` から、配布する
  `hooks.json` が持つ名前との一致へ変える
- **満たす受け入れ条件:** D8
- **進め方:** 利用者が書いた `ndf-mine` が残ることをテストで固定する

### Task 6: `hooks.json` の書き込みを置き換えにする

- **対象ファイル:** `plugins/ndf/dev.agy/install-hooks.sh` /
  `scripts/tests/test_agy_install_hooks.py`
- **変更内容:** 同じディレクトリへ一時ファイルを書き、`os.replace` で置き換える
- **満たす受け入れ条件:** D9
- **進め方:** 書き込みの後に一時ファイルが残らないこと、内容が正しいことを固定する

### Task 7: 認証ヘルパの案内と候補を配布後の状態へ合わせる

- **対象ファイル:** `plugins/playwright-kit/skills/playwright-kit-ops/scripts/_drive_auth.py`
  とそのテスト
- **変更内容:** モジュールの docstring と `RuntimeError` の本文から「どの公開セットにも
  同梱していない」を外し、`_CANDIDATES` へ agy の経路
  （`~/.gemini/config/plugins/ndf/skills/google-auth/scripts`）を足す
- **満たす受け入れ条件:** D7
- **進め方:** 候補に agy の経路が含まれること、案内の本文に古い前提の語が無いことを固定する

### Task 8: 文書と assertion の残りを直す

- **対象ファイル:** `plugins/ndf/skills/notion-writing/SKILL.md` /
  `tests/runtime-smoke/assertions/assert-kiro-agent.sh` /
  `scripts/tests/test_smoke_assertions.py`
- **変更内容:** バッククォートを含むコード表記を二重のバッククォートで囲む。
  `awk ... | grep -qw` を、`awk` の結果を変数で受けてから照合する形へ寄せる
- **満たす受け入れ条件:** D5 / D10
- **進め方:** 行ごとのバッククォートが偶数であること、assertion に `| grep -q` が
  現れないことを、先にテストで固定する

## 影響範囲

- 継続的統合の検査（`check-doc-line-limit.py` は今回の変更で対象が増える。既存の文書が
  基準を超えていないかを実行して確かめる）
- agy の hook を導入済みの利用者。**導入し直すまで、保存された command は相対のまま**
- runtime-smoke（kiro）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 非 ASCII を拾うようになった結果、既存の文書が基準を超えて検査が落ちる | 変更後に実行して確かめる。落ちたら分割か `EXEMPT` の追加を同じ本で行う |
| `EXEMPT` の不在を失敗にすると、`CHANGELOG.md` を持たない他リポジトリで落ちる | この検査はこのリポジトリの継続的統合でだけ走る。`--root` を渡す使い方は検査の対象を選ぶためのもので、他リポジトリへの配布物ではない |
| command の組み立て直しが、対象外の形を黙って素通りする | 書き換えなかったことを出力へ出す（決定 6） |

## 切り戻し手順

- すべて Pull Request 単位で戻せる。データの移行は無い
- 利用者の `hooks.json` は `.bak` へ退避されるため、導入前の状態へ戻せる

## 完了の定義

- [ ] D1〜D10 のすべてに、テストか実行の証跡が対応している
- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q`
      が終了コード 0
- [ ] 検査 10 本が終了コード 0
- [ ] `cross-review` が収束している（未解決スレッド 0 件）
