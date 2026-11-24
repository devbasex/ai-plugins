# #4285 の詳細設計: 語の当て方の 1 本化・足した行の検査・cross-review と承認ゲートへの組み込み

設計文書（[issue-4285-design.md](./issue-4285-design.md)）の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

## 地図

```text
src/ndf/
├── core/words/            * パッケージ
│   ├── __init__.py        * 空
│   ├── rules_decide.py    * 語の規則と当て方（純粋）
│   ├── source.py          * 用語集と設定の読み取り
│   ├── added.py           * 足した行と文の検査
│   └── pull.py            * PR の検査
├── checks/words.py        禁止語の規則（変更）
├── domains/
│   ├── records/           glossary.py・glossary_decide.py・cli.py（変更）
│   └── crossreview/       changed.py・fixwords.py（*）、drive.py・cli.py・journal.py・push.py・criteria_decide.py（変更）
├── adapters/
│   ├── git.py             差分の全文とコミットの有無（変更）
│   └── github.py          PR の題とベースの先頭（変更）
└── progress/gate/         words.py（*）、entry.py・asking.py・answering.py（変更）
```

依存の向き: `checks` → `core/words`、`domains/records`・`domains/crossreview`・`progress/gate` → `core/words` → `adapters`（`git`・`markdown`）・`contracts`。`core/words` は上の層を読まない。`crossreview/fixwords.py` と `changed.py` は `drive.py` を読まない。循環は無い。関数を持たない変更（用語集・設定・スキーマ・手順書）は「用語集のデータと設定」に並べる。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/words/rules_decide.py` | 新設 | 用語集から語の規則を作り、1 行から当たりを返す | `contracts` | `Word`・`Found`・`Finder`・`WordsInvalid` | `words_of`・`term_words`・`bad_replaces`・`mask_code`・`Finder.find`（新設） |
| `src/ndf/core/words/source.py` | 新設 | 用語集の設定と用語集を worktree かコミットから読む | `contracts.facts`・`adapters.git`・`rules_decide` | `Source`・`WordsUnreadable` | `at_worktree`・`at_commit`（新設） |
| `src/ndf/core/words/added.py` | 新設 | 足した行と文に規則を当てる | `adapters.git`・`adapters.markdown`・`unidiff`・`pathspec`・`source`・`rules_decide` | `Hit`・`FileAdded` | `added_files`・`read_added`・`in_files`・`in_text`・`base_ref`（新設） |
| `src/ndf/core/words/pull.py` | 新設 | 読み終えた PR の足した行と文を検査する | `adapters.git`・`adapters.github`（`Pull` の型だけ）・`added`・`source` | `PullCheck` | `check_pull`・`fetch_missing`（新設） |
| `src/ndf/checks/words.py` | 変更 | プラグインの全行の禁止語 | `core.words` | モジュール | `banned_words`・`check`（変更）、`pattern`・`_part`・`_words_from`・`_standalone_from`（削除） |
| `src/ndf/domains/records/glossary_decide.py` | 変更 | 用語集の文書の組み立てと意味の検査 | `core.words.rules_decide` | モジュール | `deprecated_in_meanings`・`_deprecated`（変更）、`_hits`・`_alone_at`・`_masked`・`_live`（削除） |
| `src/ndf/domains/records/glossary.py` | 変更 | 用語集のサブコマンドの本体 | `core.words.rules_decide` | モジュール | `check_meanings`・`render`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のエントリポイント | `core.words` | モジュール | `_glossary_words`（新設）、`_parser`・`_run`（変更） |
| `src/ndf/domains/crossreview/changed.py` | 新設（移動） | 範囲の変わったファイルと本文を読む | `adapters.git`・`scope_decide` | `Changed` | `changed`・`sections`・`changed_sections`・`committed_texts`（`drive.py` から移す。公開の名前にする） |
| `src/ndf/domains/crossreview/fixwords.py` | 新設 | 修正の後の当たりを、記録・起動し直しの項目・指摘の行の形にする | `core.words`・`store` | `Scan` | `scan`・`prompt_items`・`write_reference`・`record_of`・`remaining`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `changed`・`fixwords` | `_Run`（値を足す） | `_check_words`・`_relaunch_words`・`_settle_words`（新設）、`_start`・`_round`・`_resend`・`_push`・`_settle_resolved`・`_fix_prompt`・`_launch_fixer`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | 読み先の名前の変更だけ | `changed` | モジュール | —（import だけ） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 語の指摘の行の規則 | — | `FindingJournal` | `enter_words`・`settle_words`（新設）、`apply_fix`・`reopen`・`restore_fixed`（変更） |
| `src/ndf/domains/crossreview/push.py` | 変更 | 修正の push | — | モジュール | `push_fix`（変更） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 修正担当への指示の文 | — | モジュール | `fixer_docs`・`fixer_words`（新設） |
| `src/ndf/progress/gate/words.py` | 新設 | 承認資料の前の検査 | `core.words`・`adapters.git` | モジュール | `gate_words`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | 承認ゲートの待ちの結果 | `gate.words` | モジュール | `present`・`_progress`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認資料の文と承認の対象 | `adapters.github`（`Pull` の型だけ） | `Target`（値を足す） | `material`（変更）・`material_text`・`quoted`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | 対象の読み取りと見直し | `gate.words` の型 | モジュール | `github_observe`・`recheck`（変更） |
| `src/ndf/adapters/git.py` | 変更 | 差分の全文とコミットの有無を返す | — | `Git` | `full_diff`・`has_commit`（新設）、`run_git`（変更） |
| `src/ndf/adapters/github.py` | 変更 | PR の題とベースの先頭のコミットを持つ | — | `Pull` | `from_rest`・`from_graphql`（`title`・`base_sha` を足す）、定数 `PULL_FIELDS`（`title`・`baseRefOid` を足す） |

## 語の規則と当て方

満たす決定: 決定 4・5、受け入れ条件 16〜20

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `words_of` | `core/words/rules_decide.py` | 新設 | `words_of(glossary: Mapping[str, Any]) -> tuple[Word, ...]` | すべての項目の廃止した語を、語ごとに 1 つの規則へまとめる |
| `term_words` | 同上 | 新設 | `term_words(term: Mapping[str, Any]) -> tuple[Word, ...]` | 1 つの項目の要素（文字列かオブジェクト）を規則の形にする |
| `Finder` | 同上 | 新設 | `Finder(words: Sequence[Word], live: Iterable[str] = (), code: bool = True)` | 正規表現を 1 度だけ組み、行ごとの当たりを返す。`code` が真ならインラインコードを外し、`live` の語の内側を外す |
| `Finder.find` | 同上 | 新設 | `find(line: str, heading: bool = False) -> list[Found]` | 1 行の当たり（語・当たった文字列・置き換え先）を出てくる順に返す |
| `bad_replaces` | 同上 | 新設 | `bad_replaces(glossary: Mapping[str, Any]) -> list[dict[str, str]]` | 置き換え先の文に別の廃止した語が残る登録を返す |
| `mask_code` | 同上 | 新設 | `mask_code(text: str) -> str` | インラインコード（同じ数のバッククォートで囲んだ範囲）を同じ長さの空白にする |
| `banned_words` | `checks/words.py` | 変更 | `banned_words(source: Optional[Source]) -> BannedWords` | `added_only` でない語と禁止語から、`Finder` と語 → 理由を作る |
| `check` | 同上 | 変更 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 全行に当てる。語の例外は用語集の設定の `check.examples`（リポジトリの根からのパス）から読む |
| `deprecated_in_meanings` | `domains/records/glossary_decide.py` | 変更 | `deprecated_in_meanings(g: Mapping[str, Any]) -> list[dict[str, str]]` | 意味の欄に全部の語を `Finder(words, live, code=True)` で当て、`bad_replaces` の行（`kind: replace`）を続ける |
| `check_meanings`・`render` | `domains/records/glossary.py` | 変更 | 変わらない | 要素の形の誤り（`WordsInvalid`）を `GlossaryError` に替える（今と同じ `precondition` で止まる） |
| `_deprecated` | `domains/records/glossary_decide.py` | 変更 | `_deprecated(term: Mapping[str, Any]) -> list[str]` | 文書の列に、語・（単独）・置き換え先（`replace` があるときだけ `（→ …）`）を並べる |

- `Finder.find`
  - 事後条件: 長い形から当てる。`except` の出現と重なる当たり、`live` の語の出現の内側にある当たり、`heading` が真のときの `skip_headings` の語を返さない。1 つの位置の当たりは 1 件
- `banned_words`
  - 事後条件: 文字列の要素だけの用語集で、今の当たりと理由の文が変わらない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `term_words` | 要素の形が違う | 知らないキー・型の違い・空の `word`・空の `forms` | `words_of` | `WordsInvalid` を投げる | 呼ぶ側の `config_broken`（終了コード 2）。項目と語を文に入れる | 用語集を直して打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Word` | 型（`word`・`forms`・`excepts`・`skip_headings`・`replaces`・`standalone`・`added_only`） | `core/words/rules_decide.py` | — |
| `Found` | 型（`word`・`text`・`replaces`・`start`） | 同上 | — |
| 漢字・カタカナの範囲 | 定数 | 同上（`checks/words.py` と `glossary_decide.py` から移し、1 か所にする） | 今の値 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `words_of`・`Finder` | `checks/words.py` の `pattern`・`_part`・`_words_from`・`_standalone_from`、`glossary_decide.py` の `_hits`・`_masked` | `git grep -n "deprecated_standalone" -- plugins/ndf/src` | 使う（移して 1 つにし、元を消す） |
| `mask_code` | `glossary_decide.INLINE_CODE` | 同上 | 使う（複数のバッククォートの形を足す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `words_of` | 文字列・オブジェクトの混ざった配列を 1 つの規則にまとめる | 同じ語が 2 項目にある（`forms`・`except` の和・`added_only` は全部が真のときだけ真） | 知らないキー・空の `word` で `WordsInvalid` |
| `Finder.find` | 活用形が当たる・長い語が先に当たる | 単独の語の前後の漢字・`except` と重なる当たり・`skip_headings` の見出しと本文・生きた語の内側と外側・カタカナと ASCII の境界 | 語が無ければ空 |
| `bad_replaces` | 置き換え先に別の語が残る登録を返す | 置き換え先が自分の語を含む（当てる） | — |

## 用語集の読み取り

満たす決定: 決定 5・9・10、受け入れ条件 7・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `at_worktree` | `core/words/source.py` | 新設 | `at_worktree(root: Path) -> Optional[Source]` | worktree のファイルから読む。設定が無ければ None |
| `at_commit` | 同上 | 新設 | `at_commit(g: Git, ref: str) -> Optional[Source]` | コミットのファイル（`git show <ref>:<パス>`）から読む。設定が無ければ None |

- `Source` の値: `words`・`live`（生きた語）・`paths`・`exclude`（用語集と生成する文書を足した後の glob）・`examples`（パス → 語の集合）・`banned`（禁止語）。`Source.covers(path)` が見るファイルか、`Source.finder(code)` が足した行の検査の `Finder` を返す。`Source` は値で比べられる（同じなら 2 回当てない）。`at_commit` は UTF-8 でない用語集を `WordsUnreadable` にする
- 設定の保存先は表 `ndf-files` の `glossary` の行から引く（今の `checks/words.py` は `glossary_config` を `rules.json` に持つ。その値と `words.examples` を `rules.json` から消す）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `at_worktree`・`at_commit` | 設定か用語集が読めない | JSON の誤り・`source` が無い・`source` のファイルが無い・`contexts` か `terms` の配列が無い・要素の形の誤り | この 2 つの関数 | `WordsUnreadable` を投げる | 呼ぶ側の `config_broken`（終了コード 2）。`records` の `render`・`meanings` の今の `GlossaryError` は `WordsUnreadable` に替わらず、今のまま `precondition` | 直して打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Source` | 型（`words`・`live`・`paths`（None は全ファイル）・`exclude`・`examples`・`banned`。メソッド `covers(path)`・`finder(code)`） | `core/words/source.py` | `paths` は None、`exclude`・`examples`・`banned` は空（`exclude` には用語集と生成する文書をいつも足す） |
| `WordsUnreadable` | 例外（理由の文） | 同上 | — |
| `WordsInvalid` | 例外（項目・語・理由）。`source.py` が `WordsUnreadable` に替える | `core/words/rules_decide.py` | — |
| `BannedWords` | 型（`finder`・`reasons`（語 → 理由）。今の型の `standalone` は `finder` が持つので消す） | `checks/words.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `at_worktree` | `records/glossary.py` の `layout`・`load`、`checks/model.read_json` | `git grep -n "glossary" -- plugins/ndf/src` | `layout` は文書の組み立ての保存先（`document` 必須）を決める別の責務なので残す。読み取りは `core` に置く必要があり使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `at_worktree`・`at_commit` | 同じ中身を返す | 設定が無ければ None・`check` が無い（全ファイル）・`paths` が空 | 壊れた JSON・`source` が無い・用語集が無い |

## 足した行の検査

満たす決定: 決定 6、受け入れ条件 8〜12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `added_files` | `core/words/added.py` | 新設 | `added_files(diff: str) -> list[FileAdded]` | 差分の文字列から、ファイルごとの変更の後の全文の行と、足した行の番号（移しただけの行を除く）を返す |
| `read_added` | 同上 | 新設 | `read_added(g: Git, base: str, head: str) -> list[FileAdded]` | `base` と `head` の差分を 1 回読む（`Git.full_diff`） |
| `in_files` | 同上 | 新設 | `in_files(files: Sequence[FileAdded], source: Source) -> list[Hit]` | 設定の範囲のファイルの足した行の当たりを返す。git を起動しないので、同じ `files` に別の `source` を当てられる |
| `in_text` | 同上 | 新設 | `in_text(name: str, text: str, source: Source) -> list[Hit]` | 文の全行（Markdown として）の当たりを返す。`name` は当たりの `path` になる |
| `check_pull` | `core/words/pull.py` | 新設 | `check_pull(g: Git, pull: Pull, texts: Sequence[tuple[str, str]] = ()) -> Optional[PullCheck]` | 渡された PR の題・本文・足した行と `texts` を、PR の head の用語集で検査する。設定が無ければ None |
| `fetch_missing` | 同上 | 新設 | `fetch_missing(g: Git, refspec: str, sha: str) -> None` | コミット `sha` が手元に無ければ `refspec`（`refs/pull/<番号>/head` か `refs/heads/<ベース>`）を 1 回取る。取った後も無ければ `GitError` |
| `full_diff` | `adapters/git.py` | 新設 | `full_diff(a: str, b: str) -> str` | 全文つきの差分を、置き換え文字で読んで返す |
| `has_commit` | 同上 | 新設 | `has_commit(sha: str) -> bool` | コミットが手元にあるか（`git cat-file -e <sha>`。終了コード 1 が無い） |
| `run_git` | 同上 | 変更 | `run_git(args: list[str], cwd: Optional[Path], lossy: bool = False) -> Completed` | `lossy` が真なら、バイト列で受けて UTF-8 の置き換えで復号する（改行を変えない）。偽なら今のまま |
| `base_ref` | 同上（`added.py`） | 新設 | `base_ref(g: Git, name: str) -> str` | リモート追跡ブランチがあればその参照、無ければ渡された名前を返す |
| `_glossary_words` | `domains/records/cli.py` | 新設 | `_glossary_words(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--pr` なら PR を 1 回読んで `check_pull` に渡し、`--base` なら HEAD の用語集で分岐点から HEAD までの足した行を、`--text` だけなら HEAD の用語集で文を見て、結果 JSON にする |
| `_run` | 同上 | 変更 | 変わらない | `WordsUnreadable` を `config_broken` の結果にする |

- `read_added`
  - 事前条件: `base` と `head` のコミットが手元にある。分岐点は呼ぶ側が `merge_base` で求めて渡す（cross-review は修正担当を起動する前の HEAD をそのまま渡す）
  - 副作用: `Git.full_diff` を 1 回
- `full_diff`
  - 実行する argv: `git -c core.quotePath=false diff --no-renames --no-ext-diff --no-color --no-textconv --src-prefix=a/ --dst-prefix=b/ -U1000000 --end-of-options <a> <b>`。`-c` は `diff` より前に置く（後ろに置くと git が終了コード 128 で止まる）ので、オプションを `diff` の後ろに並べる `Git.diff_between` は使わない
  - 事後条件: 出力の復号は起動の関数の側で行う（`Git` に項目 `lossy_runner: Runner`（既定は `lossy=True` の `run_git`）を足し、`full_diff` だけが使う）。今の `run_git` は厳密に復号し、文字列になってからでは置き換えられない
- `in_text`・`in_files`
  - 事後条件: `in_text` は名前と拡張子によらず全体を Markdown として、`in_files` は `.md` のファイルだけを Markdown として扱う（PR の題・本文の当たりの名前は拡張子を持たない）。Markdown のコードブロックの行（`markdown.code_lines`）と、左の空白を除いて `>` で始まる行は見ない。見出しの行（`markdown.block_kinds` の `heading`）は `heading=True` で当てる。`Source.examples` のファイルと語の組は返さない
- `check_pull`
  - 手順: (1) `pull.head_sha` が手元に無ければ取る。(2) head の用語集を読み、設定が無ければ差分を読まずに None を返す。(3) `pull.base_sha` が手元に無ければ取る。(4) `merge_base(pull.base_sha, pull.head_sha)` から head までを `read_added` で 1 回読み、足した行・題・本文・`texts` を当てる
  - 事後条件: GitHub を読まない。取れないコミットがあれば `GitError`（コマンドは `precondition`、承認ゲートは `refused`）で、None を返さない。head の用語集が読めなければ `WordsUnreadable`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_added`・`check_pull` | git が失敗 | ref を解けない・PR のコミットを取れない | `Git` | `GitError` を呼び出し元へ返す | コマンドは `precondition`、承認ゲートは `refused`、cross-review は今の git の失敗と同じ | 取得して打ち直す |
| `full_diff` | 読めない文字 | UTF-8 でないファイル（Shift_JIS など） | `run_git`（`lossy`） | 置き換え文字で続ける | 出力なし | 打ち直せる |
| `_glossary_words` | 対象が無い・範囲が 2 つ | 引数が 3 つとも無い・`--base` と `--pr` を並べた | 引数の確かめ | 止める | `invalid` | 引数を足す |
| `_glossary_words` | ファイルを読めない | `--text` のパスの誤り | 読み取り | 止める | `precondition`。パスを文に入れる | 直して打ち直す |
| `_glossary_words` | GitHub を読めない | 認証・接続 | `GitHubError` | 止める | `transient`（今の `_run` の扱い） | 打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `added_files` | 足した行の中身（前後の空白を除く）と同じ消えた行が差分のどこかにあり、まだ使っていない | 移しただけの行として除き、消えた行の数を 1 減らす |
| `added_files` | 同じ消えた行がもう無い | 足した行として返す |
| `added_files` | 中身が空の足した行・バイナリ・消えたファイル | 足した行に数えない（消えたファイルの行は、消えた行には数える） |

- 除く順は差分に出るファイルの順・行の順とする。消えた行の数え上げは、足した行を見る前に全ファイルで済ませる

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `FileAdded` | 型（`path`・`lines`（変更の後の全文の行）・`added`（足した行の番号の集合。1 始まり）） | `core/words/added.py` | — |
| `Hit` | 型（`path`・`line`・`word`・`text`（当たった文字列）・`replace`（置き換え先を「・」で並べた文）・`content`（行の前後の空白を除いた中身））。結果 JSON の `items` には `content` を除いて載せる | 同上 | — |
| `PullCheck` | 型（`hits`・`head`・`base`（分岐点）） | `core/words/pull.py` | — |
| `lossy_runner` | `Git` の値（`Runner`） | `adapters/git.py` | `lossy=True` の `run_git` |
| `Pull.title`・`Pull.base_sha` | 型の値 | `adapters/github.py` | 空の文字列（今の位置引数の並びを変えず末尾に足す） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `added_files`・`full_diff` | `Git.diff_between`・`run_git`・`unidiff` | `git grep -n "diff" -- plugins/ndf/src/ndf/adapters/git.py` と、argv を打って確かめた | `unidiff` は使う。`diff_between` は `-c` を渡せず、`run_git` は厳密に復号するので、`full_diff` を足す |
| `check_pull` | `GitHub.pull`・`Git.fetch`・`Git.merge_base` | `adapters/` を読んで確かめた | 使う |
| `in_text` | `adapters/markdown.code_lines`・`block_kinds` | 読んで確かめた | `code_lines` と、`block_kinds` の見出しを使う。`block_kinds` の引用は継続行を含むので使わない |
| `Source.covers` | `pathspec`（`gate/checks.py` が使う） | `git grep -n pathspec -- plugins/ndf/src` | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `added_files` | 足した行だけを返す | 別のファイルへ移した行・移した行を 2 回足す・字下げだけ変えた行・`+++` で始まる本文・末尾の改行無し・日本語と空白のファイル名 | バイナリ |
| `in_files` | コードのコメント・テストの名前・文書の当たりと行の番号 | `paths`・`exclude`・`examples` の外 | — |
| `full_diff` | argv の並び（起動の関数を差し替えて見る） | — | UTF-8 でないバイト列が置き換え文字になる |
| `check_pull` | PR の head の差分と用語集で当たる | 打つ場所の HEAD が PR と違う・コミットが手元にあれば fetch しない・head に設定が無ければ差分を読まない・head が手元に無く用語集を新設する PR（取って当たる） | 取れない・取った後もコミットが無ければ `GitError`・head の用語集が壊れていれば `WordsUnreadable` |
| `Pull.from_rest`・`from_graphql` | 題と `base_sha` が入る | REST の上限で GraphQL に替わっても入る（`PULL_FIELDS` に 2 項目がある） | — |
| `in_text` | 拡張子の無い名前（PR の題・本文）と `.txt` も Markdown として見た当たり | コードブロック・インラインコード・`>` の行・`>` の次の本文 | — |
| `_glossary_words` | 0 件は `ok`、1 件以上は `check_failed`、対象をまとめる | 用語集の設定が無い（`ok`） | 壊れた用語集は `config_broken`・引数なしと `--base --pr` は `invalid` |

## cross-review の語の検査

満たす決定: 決定 1・2・10・11・13、受け入れ条件 1〜7・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `scan` | `crossreview/fixwords.py` | 新設 | `scan(g: Git, base: str, rules: Optional[Source], held: Optional[Scan] = None) -> Optional[Scan]` | `base` から HEAD の足した行に、`rules`（起動の始めの規則）と HEAD の規則の両方を当て、どちらかの当たり（パス・行・語で重ねない）を返す。2 つの規則が同じなら 1 回だけ当てる。どちらも無ければ None。`Scan` は `base`・`head`・`files`・`hits` で、`held` の `base`・`head` が同じなら差分を読み直さない |
| `prompt_items` | 同上 | 新設 | `prompt_items(ids: Sequence[str], reference: Path) -> list[dict[str, Any]]` | 指示に載せる項目を作る。持つのは `id`・`text`・`severity` だけで、`text` は `review-criteria.json` の起動し直しの固定文（言い換える・話題にする行のほかは囲まない・用語集と設定を変えない）と `reference` のパスである。`path`・`line`・語・置き換え先を持たないので、`_where` は場所を付けない。起動し直しは `w<通し番号>`、次のラウンドで渡す語の指摘はジャーナルの id を渡す |
| `write_reference` | 同上 | 新設 | `write_reference(path: Path, items: Mapping[str, Sequence[Hit]]) -> None` | id ごとの当たり（ファイルの名前・行・語・中身・置き換え先）を JSON で書く |
| `record_of` | 同上 | 新設 | `record_of(found: Sequence[Hit], left: Sequence[Hit]) -> dict[str, Any]` | 状態ファイルの `rounds[].words` の形にする |
| `remaining` | 同上 | 新設 | `remaining(g: Git, rows: Sequence[Mapping[str, Any]], rules: Optional[Source], merge_base: Callable[[], str], held: Optional[Scan]) -> dict[str, bool]` | 未解決の語の指摘の行ごとに、同じ語と中身の当たりがどのファイルかに残るかを返す。差分は全部の行で 1 回だけ読む |
| `enter_words` | `crossreview/journal.py` | 新設 | `enter_words(round_no: int, hits: Sequence[Hit], base: str) -> None` | 残った当たりをファイルごとの未解決の指摘の行（`by: words`・重さ major・`words`・`words_base`）として足す。未解決の行が既に持つ語と中身の組は足さない |
| `settle_words` | 同上 | 新設 | `settle_words(left: Mapping[str, bool]) -> None` | 語の指摘の行の終わり方を、当たりが残れば未解決、無ければ直したにする |
| `apply_fix`・`reopen`・`restore_fixed` | 同上 | 変更 | 変わらない | `by: words` の行を変えない。`apply_fix` が返す一覧にも入れない（`fix.fixed` に入らず、送り直しの後に直したへ戻らない） |
| `push_fix` | `crossreview/push.py` | 変更 | `push_fix(g, head_branch, fix_commit, before_resend: Optional[Callable[[str], None]] = None)` | 載せ直しに成功した後・送り直す前に、載せ直した先の `origin_head` を渡して呼ぶ。例外なら送らずに呼び出し元へ返す |
| `fixer_words` | `crossreview/criteria_decide.py` | 新設 | `fixer_words() -> str` | 起動し直しの項目に添える文を返す |
| `fixer_docs` | 同上 | 新設 | `fixer_docs(skill: Path) -> str` | 文書のファイルを直すときの規則への参照の 1 文を `review-criteria.json` の `fixer_docs` から組む |
| `_check_words` | `crossreview/drive.py` | 新設 | `_check_words(run: _Run, round_no: int, base: str, launch: Optional[Launcher]) -> None` | 検査・起動し直し・記録・指摘の更新を順に行い、状態ファイルへ書く。`launch` が None なら起動し直さない |
| `_settle_words` | 同上 | 新設 | `_settle_words(run: _Run) -> None` | 未解決の語の指摘があるときだけ、`remaining` と `settle_words` で終わり方を決め直す |
| `_push` | 同上 | 変更 | 変わらない | `push_fix` に、`_check_words(run, round_no, origin_head, None)` を打つ関数を渡す。替えるのは差分の起点だけで、`run.words_rules`（起動の始めの規則）は替えず、載せ直した後の HEAD の規則と合わせて当てる |
| `_settle_resolved` | 同上 | 変更 | 変わらない | `by: words` の行を変えない |
| `_relaunch_words` | 同上 | 新設 | `_relaunch_words(run: _Run, round_no: int, hits: Sequence[Hit], launch: Launcher) -> None` | 当たりだけを渡して修正担当を 1 回起動し、結果の `commit` をラウンドの修正の記録に書く |
| `_round` | 同上 | 変更 | 変わらない | 修正の結果ファイルを移した後・`_push` の前に `_check_words(run, round_no, prep.head, launch)` を呼ぶ。修正担当へ渡す未解決の指摘のうち `by: words` の行は、起動の前に `write_reference` で書き、`prompt_items` の項目に替えて `_fix_prompt` へ渡す |
| `_resend` | 同上 | 変更 | 変わらない | 送り直す前に `_check_words(run, round_no, synced.remote, None)` を呼ぶ |
| `_start` | 同上 | 変更 | 変わらない | 同期の後・前の起動の修正を扱う前に、同期で取った PR の head（`synced.remote`。送っていない修正のコミットを含まない）の規則を読んで `run.words_rules` に置き、読めなければ止める。`_resend` の後に `_settle_words` を呼ぶ |
| `_launch_fixer` | 同上 | 変更 | `_launch_fixer(run, round_no, prompt, result_path, launch, suffix: str = FIX_SUFFIX)` | 出力の名前の接尾辞を受ける（起動し直しは `-fix-words`） |
| `_fix_prompt` | 同上 | 変更 | 変わらない | 指摘の基準の後に `criteria_decide.fixer_docs(identity.ROOT / "skills/document-style/SKILL.md")` の行を足す |

- `_check_words`
  - 事前条件: 修正担当の結果がジャーナルと状態ファイルに入っている
  - 事後条件: 用語集があれば、そのラウンドの `words` と語の指摘の行が状態ファイルに書かれている。用語集が無ければ何も書かない。語の指摘の終わり方を変えるのは `_check_words`・`_settle_words` だけで、実際に送る（送った）head の検査の結果で決める
  - 副作用: 状態の保存先へ `round<n>.words.json` を書く。修正担当の起動が高々 1 回
- `_relaunch_words`
  - 事後条件: 結果ファイルが無い・形が違っても例外にせず、注意に載せて検査へ進む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_start` | 用語集が読めない | `WordsUnreadable` | 始め | `DriveStopped("config_broken", 理由)` | `config_broken`（終了コード 2） | 直して打ち直す |
| `_check_words` | 修正の後で用語集が読めない | 修正担当か載せ直した先が用語集を壊した | `scan` | 状態ファイルへ書いてから `DriveStopped("config_broken", 理由)`。push しない | 同上。修正のコミットは手元に残る | 用語集を直してコミットし、打ち直す（始めの送り直しが検査してから送る） |
| `_relaunch_words` | 修正担当が結果を書かない | 起動の失敗・上限 | 結果ファイルの有無 | 注意に載せて続ける | `warnings` に 1 行 | 残った語は未解決の指摘になる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_check_words` | 起動の始めの規則も HEAD の規則も無い | 何もしない |
| `_check_words` | 修正担当が設定を消した・規則を減らした | 起動の始めの規則での当たりが残る |
| `_check_words` | 当たりが無い | `words` に空の 2 つの配列を書き、前のラウンドの語の指摘だけを決め直す |
| `_check_words` | 当たりがあり `launch` がある | 参考資料を書き、起動し直し、もう一度検査した結果を `left` にする |
| `_check_words` | `launch` が無い（送り直し・載せ直し） | 当たりを `left` にする。ラウンドに `found` が既にあれば替えず、無ければ `found` にも同じ値を書く |
| `_check_words` | `left` が空でない | `enter_words` で未解決の指摘にする |

- `remaining` の起点は 1 つだけ選ぶ: 渡された行のうち最も古いラウンドの `words_base`。それが HEAD の祖先でない・手元に無いなら `merge_base()`（`--base` と HEAD の分岐点。`_merge_base` が同じ head で求め直さない）。`words_base` を持たない行（別の実行から引き継いだ語の指摘）があるときも `merge_base()` にし、その行は同じファイルに当たりがあれば残りとする。分岐点からの足した行には、巻き直しの前に足した行と、別のファイルへ移した行も入る
- `remaining` は `held`（そのラウンドの最後の `Scan`）と起点・HEAD が同じなら差分を読み直さない。`run.words_scan` に最後の 1 つだけを持つ
- 直したとした行は渡さない（見直さない）。語が戻れば `scan` の新しい当たりになる
- 1 ラウンドの `git diff`（この検査の分）は、直しの直後の `scan`・起動し直した後の `scan`・`remaining` の高々 3 回で、未解決の行の数では増えない。載せ直しが入れば `scan` と `remaining` の 2 回が増える

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Scan` | 型（`base`・`head`・`files`（`FileAdded` の並び）・`hits`） | `crossreview/fixwords.py` | — |
| `_Run.words_rules` | 値（起動の始めの規則。`Optional[Source]`） | `crossreview/drive.py` | None |
| `_Run.words_scan` | 値（最後の `Scan`。`Optional[Scan]`） | 同上 | None |
| `WORDS_SUFFIX` | 定数（起動し直しの出力の名前の後ろ `-fix-words`） | 同上 | — |
| `BY_WORDS` | 定数（語の指摘の行の `by` の値 `words`） | `crossreview/journal.py` | — |
| `Changed` | 型（`files`・`docs`。`drive.py` の `_Changed` を移して公開の名前にする） | `crossreview/changed.py` | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_check_words` | 当たりの件数と起動し直した後の件数 | 過程（`process`）の `round <n> words` の行 | 当たりの行の中身 |
| `record_of` | 当たりの一覧 | 状態ファイルの `rounds[].words` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `enter_words` | `_journal_row`・`_located` | `journal.py` を読んで確かめた | 使う |
| `_relaunch_words` | `_fix_prompt`・`_where`・`_launch_fixer`・`_take_fix`・`_consume` | `drive.py` を読んで確かめた | 使う（指示の組み立てを増やさない。`_where` は項目の `path` を指示へ連結するので、項目に `path` を持たせない。項目の id がジャーナルに無いので、結果は語の指摘の終わり方に効かない） |
| `write_reference` | `store.write_object`・`_remarks_file` | 同上 | `store.write_object` を使う |
| `remaining` | `read_added`・`in_files`・`Git.is_ancestor`・`drive._merge_base` | 同上 | 使う |
| `fixer_docs`・`fixer_words` | `fixer_ci_only` | `criteria_decide.py` を読んで確かめた | 同じ形で足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_check_words` | 直しの直後に当たり、起動し直して 0 件なら指摘が増えず push する | 最後のラウンドで残ると `gaps` に入る・用語集が無ければ起動の回数が変わらない・修正担当が設定を消す／`except` `exclude` `examples` を足しても当たりが残る | 修正の後に用語集が壊れると push せずに止まる |
| `_relaunch_words` | 起動が 1 回だけ増え、出力の名前が分かれる | — | 結果ファイルが無くても検査へ進む |
| `settle_words`・`remaining` | 当たりが無くなれば直した、残れば未解決 | 別のファイルへ移した行（残る）・コードブロックへ入れた行（直した）・前からある同じ中身の行（直した）・起点の違う行が 2 つあっても `git diff` は 1 回・未解決の行が無ければ 0 回 | 起点が祖先でないときの、別のファイルへ移した行（残る）と消えた行（直した） |
| `apply_fix`・`restore_fixed`・`reopen` | 語でない行は今のまま | 直した・見送ったと申告した語の指摘が未解決のまま・送り直しの後も変わらない | — |
| `_push`・`push_fix` | 載せ直しが無ければ検査を呼ばない | 1 回拒まれると、載せ直した head を検査して状態ファイルへ書いてから送り直す・載せ直した先が規則を減らしていても、起動の始めの規則での当たりが残る | 検査が例外なら送り直さない |
| `prompt_items` | 固定文と参考資料のパスだけが指示に入る | 改行と指示の文を含むファイルの名前が、指示に現れず JSON にだけある | — |
| `_resend` | 送り直しの前に検査して指摘にする | 当たりが無い | — |
| `_fix_prompt` | 参照の文と開ける絶対パスが入る | 用語集が無いリポジトリでも入る | — |

## 承認資料の前の検査

満たす決定: 決定 7・8、受け入れ条件 12〜15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_words` | `progress/gate/words.py` | 新設 | `gate_words(cwd: Path) -> WordsCheck` | 質問・読んだ対象・承認資料の文を受けて `check_pull` の結果を返す関数を作る（`WordsCheck = Callable[[Question, Target, Optional[str]], Optional[PullCheck]]`）。GitHub を読まない |
| `Target` | `progress/gate/asking.py` | 変更 | `Target(head, material, pull: Optional[Pull] = field(default=None, compare=False, repr=False))` | 対象を読んだときの PR を持つ。`differs`・`as_dict`・`from_dict` は扱わない |
| `github_observe` | `progress/gate/answering.py` | 変更 | 変わらない | 読んだ `Pull` を `Target.pull` に入れる（読む回数は今の 1 回） |
| `recheck` | 同上 | 変更 | `recheck(st, machine_id, observe, docs_only=never_docs_only, words: Optional[WordsCheck] = None)` | 文書だけの変更と決まった後・`_accept` の前に `words(q, now, None)` を打つ。当たりがあるか例外なら、今の「待ちへ戻す」処理へ進む。MVV 判定が省いた回答（payload に `model.MVV_KEY`）では打たない |
| `quoted` | `progress/gate/asking.py` | 新設 | `quoted(items: Iterable[str]) -> list[str]` | 項目を `str.splitlines()` で分け、すべての行を引用の行にする（先頭の行は `> - `、続きの行は `>   `）。空なら `bullets` と同じ 1 行 |
| `material_text` | `progress/gate/asking.py` | 新設 | `material_text(root: Path, v: run.View, q: Question) -> str` | 書いてある承認資料があればその文、無ければ `material(v, q)` を返す |
| `material` | 同上 | 変更 | 変わらない | 「未確認の点」を `quoted` で出す |
| `present` | `progress/gate/entry.py` | 変更 | `present(st, machine_id, cwd, out, observe, env=None, words: Optional[WordsCheck] = None)` | `observe` で対象を読み、その `Target` で検査し、当たりがあれば `check_failed` の結果を返す。無ければ承認資料を書き、`replace(読んだ対象, material=書いた資料の sha256)` を質問の対象にする（対象を読み直さない） |
| `_progress` | 同上 | 変更 | 変わらない | `gate_words(cwd)` を 1 つ作り、`recheck` と `present` に渡す |

- `gate_words` が返す関数
  - 事前条件: `q.gate` が設計かリリースの承認ゲート。プランの中の承認ゲート（区分なし）では検査しない
  - 副作用: PR の head が手元に無いときと、用語集がありベースの先頭が手元に無いときだけ `git fetch`（`check_pull` の手順）。PR の番号が無い質問では文だけを見る
  - 事後条件: 検査した head は `Target.head` と同じである。用語集は PR の head のコミットから読む（PR が無ければ `cwd` の HEAD）。設定が無ければ None

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `gate_words` が返す関数 | PR のコミットを取得できない | git の失敗 | その関数の中 | `model.Refused` を投げる | `refused`（今の観測の失敗と同じ） | 待ちのまま。打ち直せる |
| `present` | PR の head の用語集が読めない | `WordsUnreadable` | `gate_words` が返す関数 | 資料を書かずに止まる結果を返す | `config_broken` | 待ちのまま。直して打ち直す |
| `present` | 当たりがある | 廃止した語 | 検査 | 資料もリクエストも出さずに止まる結果を返す | `check_failed`（終了コード 1）。`items` に当たり、`summary` に件数 | 待ちのまま。直して打ち直せば承認ゲートになる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `WordsCheck` | 型の別名（質問・対象・承認資料の文 → `Optional[PullCheck]`） | `progress/gate/words.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `gate_words` | `answering.github_observe`・`core/words/pull.check_pull` | `gate/` を読んで確かめた | `github_observe` が読んだ PR を `Target` で受け、`check_pull` に渡す |
| `quoted` | `bullets` | 同上 | 空の表し方をそろえる |
| `material_text` | `write_material`・`material_path` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `present` | 当たりがあれば資料のファイルもリクエストも無く `check_failed`、直せば `gate` | 2 回目の検査も全部の語・プランの中の承認ゲートでは検査しない・用語集が無い・PR の読み取りは 1 回で、質問の head は検査した head と同じ | 用語集が壊れていれば `config_broken`・PR を読めなければ `refused` |
| `recheck` | 当たりの無い文書だけの変更は今と同じく新しい head を記録する | 当たりがあれば記録せずに待ちへ戻る・MVV 判定が省いた回答では検査を呼ばない | 検査できなければ待ちへ戻る |
| `material`・`quoted` | 未確認の点が引用の行になる | 未確認の点が無い・複数行の名前と理由のすべての行が `>` で始まる | — |

## 用語集のデータと設定

満たす決定: 決定 3・9・12、受け入れ条件 16・19・21・22

関数を持たない変更を並べる。

| ファイル | 変える中身 |
| --- | --- |
| `docs/glossary/glossary.json` | 設計文書の「登録する語」の表の語を登録する。項目「直訳の語」「足した行」「当てない形」を足す。`席` の項目を「シート」に改める。意味の欄の 63 項目を言い換え、`ndf records glossary render` で文書を作り直す |
| `.ndf/glossary.json` | `check.paths`・`check.exclude`・`check.examples`（`rules.json` の `words.examples` をリポジトリの根からのパスにしたもの） |
| `contracts/schemas/ndf-settings/glossary.schema.json` | `check.exclude`・`check.examples` を足し、`paths` の説明を直す。読む側（`x-ndf-read-by`）に `ndf check`・`ndf cross-review`・`ndf progress` を足す |
| `src/ndf/checks/rules.json` | `words.glossary_config` と `words.examples` を消す |
| `skills/requirements-design/references/spec-template.md`・`document-roles.md`・`SKILL.md` | 見出し `事象` を「現象」に、欄の名前 `書き手` を「書き込み元」にし、参照をそろえる |
| `skills/requirements-design/references/glossary-format.md` | 要素のオブジェクトの形・`check` のキー・サブコマンド `words` を書く |
| `skills/design/references/design-template.md` | 「どこを変えるか」の定型文の `畳む` を「まとめる」にする |
| `skills/development-workflow/references/approval-request.md` | 「提示を組み立てる前に」へ検査のコマンド（`--pr`・`--text`）と、当たりがあれば提示しないことを足す |
| `skills/cross-review/SKILL.md`・`docs/02-fix-and-rotation.md` | 語の検査・起動し直し・指摘の終わり方・`config_broken` を足す |
| `contracts/data/review-criteria.json` | `fixer_docs` と起動し直しの文 `fixer_words` を足す（語の一覧は持たない） |
| `.github/workflows/glossary.yml` | 説明のコメントを直す（ジョブは増やさない） |

### 単体テストの観点

| 対象 | 確かめ方 |
| --- | --- |
| 登録と 3 つの検査 | `ndf check`・`ndf records glossary render --check`・`ndf records glossary meanings` を打つ（CI の今のジョブ） |
| テンプレート | 見出しと欄の名前だけの文書に `ndf records glossary words --text` を当てて 0 件（worker の確かめ） |
