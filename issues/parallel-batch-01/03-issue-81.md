# 担当 C — 証跡リンクの置換が働かない状態を直す（#81）

- issue: https://github.com/devbasex/ai-plugins/issues/81
- ブランチ: `fix/issue-81-evidence-link-rewrite`
- モード: `standard`

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 証跡 | ブラウザ自動テストが残す記録ファイル（操作の記録、通信の記録、画面の画像、動画） |
| 報告書 | テストの実行結果をまとめた Markdown ファイル |
| 置換 | 報告書に書かれた証跡のファイル位置を、共有ストレージ上の URL へ書き換える処理 |

## 何を解決するか

ブラウザ自動テストの報告書を共有ストレージ上の文書へ変換するとき、報告書に書かれた証跡の
ファイル位置を共有先の URL へ書き換える。この置換が**1 件も成立しない**。

置換する側が期待する書き方と、報告書を作る側が実際に出す書き方が噛み合っていない。
結果として、変換した文書からは証跡へたどり着けない。

## 現状（実測）

置換する側は、リンク記法かつ先頭が `TC-` で始まる位置だけを拾う。

```python
# plugins/playwright-kit/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py
LINK_PATTERN = re.compile(r"\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)")
```

報告書を作る側は、リンクではなくコード表記で書き出す。

```python
# plugins/playwright-kit/skills/playwright-kit-ops/playwright_kit/pytest_report.py
if e.trace_path:
    lines.append(f"- trace: `{e.trace_path}`")
if e.har_path:
    lines.append(f"- HAR: `{e.har_path}`")
```

噛み合わない点は 2 つある。

| # | 置換する側の前提 | 報告書の実際 |
| --- | --- | --- |
| 1 | 丸括弧で囲まれたリンク記法 | バッククォートで囲まれたコード表記 |
| 2 | ケースのディレクトリ名が `TC-` で始まる | 命名は固定されていない |

再現する。

```console
$ python3 -c "
import re
p = re.compile(r'\(\.?\/?(TC-[\w-]+/[^\)\s]+)\)')
print(p.findall('- trace: \`reports/run-001/test_login/trace.zip\`'))
"
[]
```

## やること

置換する側を、報告書が実際に出す書き方へ合わせる。**報告書を作る側の出力は変えない。**

報告書は共有ストレージへ移す前にローカルでも読まれる。コード表記をリンク記法へ変えると、
移す前の報告書で、開けない位置を指すリンクが並ぶ。置換する側だけを広げれば、この影響は
出ない。**採否と理由は実装計画へ残す。**

広げる対象は次の 2 つとする。

- バッククォートで囲まれたファイル位置
- 既存のリンク記法（現在すでに置換できている書き方を壊さない）

ケースのディレクトリ名は `TC-` に限定しない。共有ストレージ側から取得したファイルの一覧と
突き合わせ、**一覧に載っている位置だけを置換する**。載っていない文字列は書き換えない。

## 完了条件

- [ ] 報告書が実際に出す書き方（コード表記）の証跡が置換される
- [ ] ケースのディレクトリ名が `TC-` で始まらない場合も置換される
- [ ] 既存のリンク記法の証跡が引き続き置換される
- [ ] 共有ストレージ側の一覧に無い文字列は書き換わらない
- [ ] 外部の URL やコード例など、証跡ではないコード表記が書き換わらない
- [ ] 置換の件数が実行結果として出力される
- [ ] 報告書を作る側の出力は変わっていない
- [ ] 報告書の実出力を入力にした回帰検査があり、`uv run pytest plugins/playwright-kit/skills/playwright-kit-ops/tests -q` が通る
- [ ] 既存のテスト 15 本が引き続き通る

### テストの書き方

置換の関数を単体で検証する。共有ストレージへの問い合わせは実行せず、ファイルの一覧を
差し替えて渡す。

報告書を作る側の出力を固定の文字列として書き写さず、**報告書を作る処理を実際に呼んで
得た文字列**を入力にする。書き写すと、報告書の書き方が変わったときにテストが追随せず、
同じ噛み合わない状態が再び起きる。

```bash
uv run pytest plugins/playwright-kit/skills/playwright-kit-ops/tests -q
```

## 触らない範囲

- `scripts/` 配下（担当 A が検査を変更する）
- `README.md` / `plugins/ndf/README.md`（担当 A が変更する）
- `plugins/ndf/` 配下すべて（担当 B が `cross-review` と `fix` を変更する）

## 参照

- `plugins/playwright-kit/skills/playwright-kit-ops/scripts/build_gdoc_with_drive_links.py` — 置換する側
- `plugins/playwright-kit/skills/playwright-kit-ops/playwright_kit/pytest_report.py` — 報告書を作る側
- `plugins/playwright-kit/skills/playwright-kit-ops/tests/test_pytest_report.py` — 報告書の既存のテスト
