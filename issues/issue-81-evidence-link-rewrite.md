# 証跡リンクの置換が働かない状態を直す（#81）

- issue: https://github.com/devbasex/ai-plugins/issues/81
- ブランチ: `fix/issue-81-evidence-link-rewrite`
- モード: `standard`

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 証跡 | ブラウザ自動テストが残す記録ファイル（操作の記録、通信の記録、画面の画像、動画） |
| 報告書 | テストの実行結果をまとめた Markdown ファイル（`playwright_kit/pytest_report.py` が作る） |
| 置換 | 報告書に書かれた証跡のファイル位置を、共有ストレージ上の URL へ書き換える処理 |
| 一覧 | 共有ストレージの実行単位フォルダを再帰的に走査して得た、ファイル位置と識別子の対応 |
| ケースのディレクトリ | 1 つのテスト関数の証跡だけを入れる、実行単位フォルダの直下のディレクトリ |
| コード表記 | バッククォートで囲んだ書き方 |
| 証跡フィールド | 報告書が証跡の位置だけを書く行（`- trace: ` / `- HAR: ` で始まる行） |
| リンク記法 | `[文言](位置)` の書き方 |

## 依頼（原文）

> ## 事象
>
> `plugins/ndf-shared/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py` は Markdown 内の証跡パスを Drive リンクへ置換するが、同梱の `playwright_kit/pytest_report.py` が出す形式と噛み合わず、**置換が 1 件も起きない**。
>
> 置換側のパターン:
>
> ```python
> # scripts/build_gdoc_with_drive_links.py
> LINK_PATTERN = re.compile(r"\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)")
> ```
>
> 生成側の出力:
>
> ```python
> # playwright_kit/pytest_report.py:142-143
> if e.trace_path:
>     lines.append(f"- trace: `{e.trace_path}`")
> ```
>
> `- trace: \`reports/run-001/test_login/trace.zip\`` は Markdown リンクではなくコード span なので、`LINK_PATTERN` に一致しない。加えて case ディレクトリ名も `TC-*` 固定ではない。
>
> 再現:
>
> ```python
> import re
> LINK_PATTERN = re.compile(r"\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)")
> LINK_PATTERN.findall("- trace: `reports/run-001/test_login/trace.zip`")
> # => []
> ```
>
> 結果として Google Doc 化しても `Replaced links: 0 matches` になり、証跡リンクが埋まらない。
>
> ## 対応方針の候補
>
> 1. `pytest_report.py` 側を Markdown リンク `[trace](<相対パス>)` として出力するよう変える
> 2. `build_gdoc_with_drive_links.py` 側を、実際の report 形式（コード span / 任意の case ディレクトリ名）にも一致するパターンへ広げる
> 3. どちらを採るにせよ `rewrite_links` の単体テストを追加し、report の実出力を入力にした回帰検査を置く
>
> ## 経緯
>
> v5.0.0 の Skill 棚卸（release PR #66）の結合レビュー（PR #80）で codex が指摘した。両ファイルとも棚卸では変更しておらず（`git diff origin/main..release/skill-inventory` で差分なし）、**棚卸以前からある不具合**のため、リリースの範囲外として本 issue に切り出した。
>
> Drive 連携は optional dependency（`google-auth` はどの manifest にも載せていない）であり、この経路を使っていなければ影響しない。

## 目的

報告書を共有ストレージ上の文書へ変換したとき、文書に載った証跡の記述から証跡そのものへ
たどり着ける状態にする。

## 現状（実測）

報告書を作る処理を実際に呼び、置換する側の抽出パターンへ通した結果を次に示す。

```console
$ uv run python -c '
import datetime as _dt, re
from playwright_kit.pytest_report import PwkTestEntry, render_markdown
case = "/work/reports/20260831-024046/tests-test-login-py-test-ok-1a2b3c"
md = render_markdown([PwkTestEntry(nodeid="tests/test_login.py::test_ok", name="test_ok",
    outcome="failed", duration_s=1.0, error_message="boom",
    trace_path=f"{case}/trace.zip", har_path=f"{case}/request.har")],
    started_at=_dt.datetime(2026,4,26,12,0,0), finished_at=_dt.datetime(2026,4,26,12,0,5))
print([l for l in md.splitlines() if "trace" in l or "HAR" in l])
print(re.compile(r"\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)").findall(md))'
['- trace: `/work/reports/20260831-024046/tests-test-login-py-test-ok-1a2b3c/trace.zip`',
 '- HAR: `/work/reports/20260831-024046/tests-test-login-py-test-ok-1a2b3c/request.har`']
[]
```

噛み合わない点は 3 つある。

| # | 置換する側の前提 | 報告書の実際 |
| --- | --- | --- |
| 1 | 丸括弧で囲まれたリンク記法 | バッククォートで囲まれたコード表記 |
| 2 | ケースのディレクトリ名が `TC-` で始まる | 命名は固定されていない（テスト識別子から作る） |
| 3 | 実行単位フォルダからの相対位置 | 実行環境の根からの絶対位置 |

3 点目は共有ストレージの一覧の作り方から出る。一覧は実行単位フォルダを起点に組み立てるため、
キーは `<ケースのディレクトリ>/trace.zip` になる。報告書が書くのは
`/work/reports/<実行単位>/<ケースのディレクトリ>/trace.zip` であり、前方に余分な要素が付く。

## 前提

- 前提 1: 一覧のキーは実行単位フォルダからの相対位置であり、報告書が書く位置の**末尾**と、
  区切り文字の境界でそろう。境界を無視した部分一致は採らない
- 前提 2: 一覧に末尾がそろうキーが複数あるときは、**最も長いキー**を採る。より多くの要素が
  そろうキーの方が、指している対象が一つに定まる
- 前提 3: コード表記を書き換えた結果はリンク記法とし、リンクの文言には報告書が書いた位置を
  そのまま使う。文言を短く作り直すと、報告書の原文と突き合わせられなくなる

## 対象範囲

含む:

- 置換する側（`plugins/playwright-kit/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py`）の抽出と突き合わせ
- 報告書の実出力を入力にした回帰検査の追加

含まない:

- 報告書を作る側（`plugins/playwright-kit/skills/playwright-kit-ops/playwright_kit/pytest_report.py`）の出力
- 共有ストレージへの問い合わせと、そこへの書き出し
- 配布 Skill の数と構成

## 受け入れ条件

- [ ] 報告書が出すコード表記の証跡が、一覧に載っていれば共有ストレージの URL を持つリンク記法へ書き換わる
- [ ] ケースのディレクトリ名が `TC-` で始まらない場合も書き換わる
- [ ] 既存のリンク記法の証跡が、引き続き共有ストレージの URL へ書き換わる
- [ ] 報告書が書く位置が実行環境の根からの絶対位置でも、一覧のキーと末尾がそろえば書き換わる
- [ ] 一覧に無い文字列は書き換わらない（テスト識別子のコード表記が原文のまま残る）
- [ ] 証跡フィールドとリンク記法の外は書き換わらない（FAIL の詳細の失敗メッセージが原文のまま残る）
- [ ] コード表記の中の外部の URL は書き換わらない
- [ ] 画面の画像は画像を直接表示する URL へ、それ以外は閲覧用の URL へ書き換わる
- [ ] 置換の件数が実行結果の標準出力に出る
- [ ] 報告書を作る処理の出力が変わっていない（`tests/test_pytest_report.py` の 15 件が通る）
- [ ] 回帰検査は、報告書を作る処理を実際に呼んで得た文字列を入力にしている
- [ ] 回帰検査は共有ストレージへ問い合わせず、一覧を引数で受け取る
- [ ] `uv run pytest plugins/playwright-kit/skills/playwright-kit-ops/tests -q` が終了コード 0 で終わる

## 非機能の条件

| 種類 | 条件 |
| --- | --- |
| 性能 | 報告書 1 件あたりの置換は、一覧の件数と報告書の長さに対して線形の走査 1 回で終える |
| 権限 | 共有ストレージへの資格情報は読み込みも保存もしない。置換は文字列の変換だけで完結する |
| 記録 | 標準出力へ出すのは置換の件数のみ。ファイルの識別子と資格情報は出さない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 置換の関数の引数と戻り値は変わらない。呼び出し側の変更は不要 |
| データ | スキーマ変更なし |
| 既存の振る舞い | 一覧に載っている位置だけが書き換わる点は変わらない。拾える書き方が広がる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest plugins/playwright-kit/skills/playwright-kit-ops/tests -q` |
| 生成物の同期 | `bash scripts/build-runtime-plugins.sh` と `bash scripts/build-runtime-plugins.sh --check` |
| 配布物の検査 | `claude plugin validate` |
| 手動確認 | 共有ストレージへの実接続を伴う経路は、資格情報を持つ利用者が配布後に確認する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 変更を置くのは `plugins/playwright-kit/skills/playwright-kit-ops/` と `issues/` のみ（`AGENTS.md` のマーケットプレイス構造） |
| コーディング規約 | 要件を満たす最小限の実装（`AGENTS.md` のベストプラクティス）。文書は `markdown-writing` |
| テスト戦略 | 共有ストレージへ問い合わせない純粋な関数の単体テストで担保する。既存の `tests/test_upload_evidence.py` と同じ層 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更後の全テスト実行、生成物の同期、範囲外の発見のその場での起票 |
| 確認してから行う | 置換の関数の引数と戻り値の変更、依存パッケージの追加 |
| 行わない | 報告書を作る側の出力の変更、配布 Skill の数の変更、`scripts/` と `plugins/ndf/` の変更 |

## 残リスク

- 共有ストレージへ実接続する経路は、資格情報がこの作業環境に無いため未確認。
  一覧を組み立てる処理と書き出す処理には変更を入れないため、変更の影響は置換の関数に閉じる

---

# 実装計画

## 関連リンク

- 上の「受け入れ条件」の節が、この計画の完了判定である
- 並行して進める 3 件の境界: `issues/parallel-batch-01/00-overview.md`

## モード

`standard`。テストが備わっている領域への振る舞いの修正で、公開インタフェースと配布構成は変えない。

## 目的と非目的

達成したい状態:

- 報告書から作った共有ストレージ上の文書で、証跡の記述から証跡そのものへたどり着ける

やらないこと:

- 報告書を作る側の出力の変更
- 共有ストレージへ問い合わせる処理と書き出す処理の変更
- 証跡を共有ストレージへ保管する手順（`plugins/playwright-kit/skills/playwright-evidence/SKILL.md`）の
  説明の更新。書き換えてよいパスの外にあるため、#181 として残した

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 置換する側を広げる | 拾う書き方にコード表記を足し、ケースのディレクトリ名の限定を外す | 採用 | 報告書はローカルでも読まれる。読む側の見え方を変えずに、変換した文書だけを直せる |
| 報告書を作る側をリンク記法へ変える | 証跡の行を `[trace](位置)` の形で書き出す | 不採用 | 共有ストレージへ移す前の報告書に、開けない位置を指すリンクが並ぶ |

## 不変条件

- 一覧に載っていない文字列は書き換えない
- 置換の関数は共有ストレージへ問い合わせない。一覧は引数で受け取る
- 報告書を作る処理の出力は変わらない

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 置換の関数の引数と戻り値 | 変えない | 呼び出し側の変更は不要 |
| 実行時の依存 | 共有ストレージ向けの読み込みを関数の中へ移す | 追加の依存は無い。読み込みの時点が実行時へ移るだけ |
| データスキーマ | 変更なし | 移行は不要 |

## 修正対象

- `plugins/playwright-kit/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py`
- `plugins/playwright-kit/skills/playwright-kit-ops/tests/test_build_gdoc_with_drive_links.py`（新規）

## タスク分解

### Task 1: 報告書の実出力を入力にした回帰検査を置く

- **対象ファイル:** `tests/test_build_gdoc_with_drive_links.py`（新規）、`scripts/build_gdoc_with_drive_links.py`
- **変更内容:** 報告書を作る処理を実際に呼んで得た文字列を入力にし、証跡 2 件が書き換わることを検査する。
  検査から読み込めるようにするため、共有ストレージ向けの読み込みを実行時（`main` の中）へ移す。
  同じ扱いは `scripts/upload_evidence.py` が先に採っている
- **満たす受け入れ条件:** 1、9、10、11、12
- **進め方:** 検査を先に書き、置換が 0 件で落ちることを確認してから実装へ進む

### Task 2: 一覧との末尾一致でコード表記と任意のケース名を拾う

- **対象ファイル:** `scripts/build_gdoc_with_drive_links.py`
- **変更内容:** 拾う書き方をコード表記とリンク記法の 2 つにし、ケースのディレクトリ名の限定を外す。
  拾った位置は区切り文字の境界で末尾から順に一覧のキーと突き合わせ、最も長いキーを採る。
  コード表記の置換結果はリンク記法とし、リンクの文言には報告書が書いた位置をそのまま使う
- **満たす受け入れ条件:** 1、2、3、4、7
- **進め方:** Task 1 の検査を通す最小の実装を入れ、既存のリンク記法の検査を足して両方が通る状態にする

### Task 3: 書き換えない条件を検査で固定する

- **対象ファイル:** `tests/test_build_gdoc_with_drive_links.py`
- **変更内容:** 一覧に無いテスト識別子のコード表記、コード表記の中の外部の URL が原文のまま残ることを検査する。
  画面の画像とそれ以外で URL の形が変わることも検査する
- **満たす受け入れ条件:** 5、6、7、8
- **進め方:** 検査を先に書き、原文のまま残ることを確認する

## 影響範囲

- 置換の関数を使うのは同じファイルの `main` のみ。他の Skill と配布物からの参照は無い

```console
$ grep -rn "rewrite_links\|build_gdoc_with_drive_links" --include=* . | grep -v "^./plugins/playwright-kit/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py"
（Task 1 着手時点の結果を quality-gates の節へ残す）
```

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 末尾一致が、証跡ではない文字列を拾う | 一覧に載っている位置だけを書き換える。載っていない文字列は原文のまま残す検査を置く |
| 共有ストレージへ実接続する経路を検査できない | 一覧を引数で受け取る形にして、接続を伴う処理と切り離す。接続経路は配布後に利用者が確認する |
| 二重のバッククォートで囲んだ参照が、入れ子のリンクになる | 前後にバッククォートが続く並びは拾わない |

## 切り戻し手順

- 変更は 1 つのスクリプトと 1 つの検査ファイルに閉じる。コミットの取り消しだけで元に戻せる

## 完了の定義

- [ ] 受け入れ条件 13 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest plugins/playwright-kit/skills/playwright-kit-ops/tests -q` が終了コード 0 で終わる
- [ ] `bash scripts/build-runtime-plugins.sh --check` が終了コード 0 で終わる
- [ ] `claude plugin validate` が通る

---

# 完了判定

## 検証結果

| 段階 | コマンド | 対象範囲 | 実行時刻 | 結果 |
| --- | --- | --- | --- | --- |
| 限定的な検証 | `uv run pytest tests/test_build_gdoc_with_drive_links.py tests/test_pytest_report.py -q` | 置換と報告書生成 | 2026-08-31 02:49:30 | 28 passed / exit=0 |
| 全体テスト | `uv run pytest tests` | `playwright-kit-ops` の全テスト | 2026-08-31 02:49:34 | 176 passed / exit=0 |
| 構文検査 | `uv run python -m py_compile scripts/build_gdoc_with_drive_links.py tests/test_build_gdoc_with_drive_links.py` | 変更した 2 ファイル | 2026-08-31 02:49:45 | exit=0 |
| 生成物の同期 | `bash scripts/build-runtime-plugins.sh --check` | リポジトリ全体 | 2026-08-31 02:49:52 | up to date / exit=0 |
| 配布物の検査 | `bash scripts/validate-runtime-plugins.sh` | リポジトリ全体 | 2026-08-31 02:49:56 | passed / exit=0 |
| 配布物の検査 | `claude plugin validate .` | リポジトリ全体 | 2026-08-31 02:50:05 | passed with warnings / exit=0 |
| 全体テスト（レビュー反映後） | `uv run pytest tests` | `playwright-kit-ops` の全テスト | 2026-08-31 03:12 | 179 collected / exit=0 |

テストの実行は `plugins/playwright-kit/skills/playwright-kit-ops` を作業ディレクトリにして行う。
リポジトリの根には `uv` の対象プロジェクトが無く、根から `uv run pytest <パス>` を実行すると
`Failed to spawn: pytest` で終わる。この結果は変更のない clone 側でも同じである。
根から実行する場合は `uv run --project plugins/playwright-kit/skills/playwright-kit-ops pytest
plugins/playwright-kit/skills/playwright-kit-ops/tests -q` を使う。

静的解析・型検査・カバレッジの設定は `pyproject.toml` にも `.ruff.toml` / `ruff.toml` /
`setup.cfg` / `.flake8` にも無い。閾値の記載が無いため、これらの判定は行わない。

## 受け入れ条件ごとの合否

- [x] 報告書が出すコード表記の証跡が、一覧に載っていれば共有ストレージの URL を持つリンク記法へ書き換わる
      → `TestReportRegression::test_code_span_evidence_is_rewritten` / `::test_code_span_becomes_a_link_keeping_the_original_path`
- [x] ケースのディレクトリ名が `TC-` で始まらない場合も書き換わる
      → `TestReportRegression::test_case_dir_not_starting_with_tc_is_rewritten`
- [x] 既存のリンク記法の証跡が、引き続き共有ストレージの URL へ書き換わる
      → `TestExistingLinkNotation::test_relative_link_is_rewritten`
- [x] 報告書が書く位置が実行環境の根からの絶対位置でも、一覧のキーと末尾がそろえば書き換わる
      → `TestReportRegression::test_absolute_path_matches_listing_key_by_suffix` / `TestSuffixMatching::test_longest_matching_key_wins` / `::test_partial_component_is_not_matched`
- [x] 一覧に無い文字列は書き換わらない
      → `TestReportRegression::test_nodeid_code_span_is_left_untouched` / `::test_report_is_unchanged_when_listing_is_empty`
- [x] 証跡フィールドとリンク記法の外は書き換わらない
      → `TestFailureMessageIsPreserved::test_code_span_in_failure_message_is_left_untouched` / `::test_parenthesized_path_in_failure_message_is_left_untouched`
- [x] コード表記の中の外部の URL は書き換わらない
      → `TestNonEvidenceIsPreserved::test_external_url_in_code_span_is_left_untouched` / `::test_external_url_in_link_notation_is_left_untouched`
- [x] 画面の画像は画像を直接表示する URL へ、それ以外は閲覧用の URL へ書き換わる
      → `TestExistingLinkNotation::test_png_uses_the_direct_image_url` / `test_relative_link_is_rewritten`
- [x] 置換の件数が実行結果の標準出力に出る
      → `rewrite_links` が件数を返し、`main` が `Replaced links: <件数> matches` を出す。件数は上記の各検査が確認する
- [x] 報告書を作る処理の出力が変わっていない
      → `tests/test_pytest_report.py` の 15 件が通る。`playwright_kit/pytest_report.py` に差分は無い
- [x] 回帰検査は、報告書を作る処理を実際に呼んで得た文字列を入力にしている
      → `_report_markdown()` が `render_markdown` を呼ぶ
- [x] 回帰検査は共有ストレージへ問い合わせず、一覧を引数で受け取る
      → 検査は `rewrite_links(md, mapping)` だけを呼ぶ。`_drive_auth` と共有ストレージの読み込みは `main` の中へ移した
- [x] テストが終了コード 0 で終わる
      → 176 passed / exit=0

受け入れ条件: 13/13 満たす

## 未検証の項目

- 共有ストレージへ実接続して文書を作る経路。資格情報がこの作業環境に無いため実行していない。
  `release-verification` で、資格情報を持つ利用者が配布後に確認する

## 既存の失敗

- なし

## 範囲外と判断したもの

- 証跡を共有ストレージへ保管する手順（`plugins/playwright-kit/skills/playwright-evidence/SKILL.md`）が、
  置換の対象を `TC-` 始まりのリンク記法に限って説明している → #181 として起票した
- リポジトリの根から `uv run pytest <パス>` が動かない件は起票しない。リポジトリ側の文書に
  この書き方は無く、直す対象がリポジトリに存在しないため
