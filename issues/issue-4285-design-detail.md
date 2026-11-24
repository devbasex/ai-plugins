# #4285 の詳細設計: 語の当て方の 1 本化・足した行の検査・cross-review と承認ゲートへの組み込み

設計文書（[issue-4285-design.md](./issue-4285-design.md)）の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

## 地図

```text
src/ndf/
├── core/words/            * パッケージ
│   ├── __init__.py        * 空
│   ├── rules_decide.py    * 語の規則と当て方（純粋）
│   ├── source.py          * 用語集と設定の読み取り
│   └── added.py           * 足した行と文の検査
├── checks/words.py        禁止語の規則
├── domains/
│   ├── records/{glossary,glossary_decide,cli}.py
│   └── crossreview/{changed*,fixwords*,drive,cli,journal,criteria_decide}.py
└── progress/gate/{words*,entry,asking}.py
```

依存の向き: `checks` → `core/words`、`domains/records`・`domains/crossreview`・`progress/gate` → `core/words` → `adapters`（`git`・`markdown`）・`contracts`。`core/words` は上の層を読まない。`crossreview/fixwords.py` と `changed.py` は `drive.py` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/words/rules_decide.py` | 新設 | 用語集から語の規則を作り、1 行から当たりを返す | `contracts` | `Word`・`Found`・`Finder`・`WordsInvalid` | `words_of`・`term_words`・`bad_replaces`・`mask_code`・`Finder.find`（新設） |
| `src/ndf/core/words/source.py` | 新設 | 用語集の設定と用語集を worktree かコミットから読む | `contracts.facts`・`adapters.git`・`rules_decide` | `Source`・`WordsUnreadable` | `at_worktree`・`at_commit`（新設） |
| `src/ndf/core/words/added.py` | 新設 | 足した行と文に規則を当てる | `adapters.git`・`adapters.markdown`・`unidiff`・`pathspec`・`source`・`rules_decide` | `Hit`・`FileAdded` | `added_files`・`in_diff`・`in_text`・`base_ref`（新設） |
| `src/ndf/checks/words.py` | 変更 | プラグインの全行の禁止語 | `core.words` | モジュール | `banned_words`・`check`（変更）、`pattern`・`_part`・`_words_from`・`_standalone_from`（削除） |
| `src/ndf/domains/records/glossary_decide.py` | 変更 | 用語集の文書の組み立てと意味の検査 | `core.words.rules_decide` | モジュール | `deprecated_in_meanings`・`_deprecated`（変更）、`_hits`・`_alone_at`・`_masked`・`_live`（削除） |
| `src/ndf/domains/records/glossary.py` | 変更 | 用語集のサブコマンドの本体 | `core.words.source` | モジュール | `check_meanings`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のエントリポイント | `core.words` | モジュール | `_glossary_words`・`_pr_texts`（新設）、`_parser`・`_run`（変更） |
| `src/ndf/domains/crossreview/changed.py` | 新設（移動） | 範囲の変わったファイルと本文を読む | `adapters.git`・`scope_decide` | `Changed` | `changed`・`sections`・`changed_sections`・`committed_texts`（`drive.py` から移す。公開の名前にする） |
| `src/ndf/domains/crossreview/fixwords.py` | 新設 | 修正の後の当たりを、記録・起動し直しの項目・指摘の行の形にする | `core.words`・`store` | `Verified` | `scan`・`relaunch_items`・`write_reference`・`record_of`・`remaining`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `changed`・`fixwords` | モジュール | `_check_words`・`_relaunch_words`（新設）、`_start`・`_round`・`_resend`・`_fix_prompt`・`_launch_fixer`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | 読み先の名前の変更だけ | `changed` | モジュール | —（import だけ） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 語の指摘の行の規則 | — | `FindingJournal` | `enter_words`・`settle_words`（新設） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 修正担当への指示の文 | — | モジュール | `fixer_docs`（新設） |
| `src/ndf/progress/gate/words.py` | 新設 | 承認資料の前の検査 | `core.words`・`adapters.github`・`adapters.git` | モジュール | `github_words`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | 承認ゲートの待ちの結果 | `gate.words` | モジュール | `present`・`_progress`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認資料の文 | — | モジュール | `material`・`material_text`（`material_text` は新設） |
| `src/ndf/adapters/github.py` | 変更 | PR の題を持つ | — | `Pull` | `from_rest`・`from_graphql`（`title` を足す） |

## 語の規則と当て方

満たす決定: 決定 4・5、受け入れ条件 16〜20

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `words_of` | `core/words/rules_decide.py` | 新設 | `words_of(glossary: Mapping[str, Any]) -> tuple[Word, ...]` | すべての項目の廃止した語を、語ごとに 1 つの規則へまとめる |
| `term_words` | 同上 | 新設 | `term_words(term: Mapping[str, Any]) -> tuple[Word, ...]` | 1 つの項目の要素（文字列かオブジェクト）を規則の形にする |
| `Finder` | 同上 | 新設 | `Finder(words: Sequence[Word], live: Iterable[str] = (), code: bool = True)` | 正規表現を 1 度だけ組み、行ごとの当たりを返す。`code` が真ならインラインコードを外し、`live` の語の内側を外す |
| `Finder.find` | 同上 | 新設 | `find(line: str) -> list[Found]` | 1 行の当たり（語・当たった文字列・置き換え先）を出てくる順に返す |
| `bad_replaces` | 同上 | 新設 | `bad_replaces(glossary: Mapping[str, Any]) -> list[dict[str, str]]` | 置き換え先の文に別の廃止した語が残る登録を返す |
| `mask_code` | 同上 | 新設 | `mask_code(text: str) -> str` | インラインコード（同じ数のバッククォートで囲んだ範囲）を同じ長さの空白にする |
| `banned_words` | `checks/words.py` | 変更 | `banned_words(source: Optional[Source]) -> BannedWords` | `added_only` でない語と禁止語から、`Finder` と語 → 理由を作る |
| `check` | 同上 | 変更 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 全行に当てる。語の例外は用語集の設定の `check.examples`（リポジトリの根からのパス）から読む |
| `deprecated_in_meanings` | `domains/records/glossary_decide.py` | 変更 | `deprecated_in_meanings(g: Mapping[str, Any]) -> list[dict[str, str]]` | 意味の欄に全部の語を `Finder(words, live, code=True)` で当て、`bad_replaces` の行（`kind: replace`）を続ける |
| `_deprecated` | 同上 | 変更 | `_deprecated(term: Mapping[str, Any]) -> list[str]` | 文書の列に、語・（単独）・置き換え先（`replace` があるときだけ `（→ …）`）を並べる |

- `Finder.find`
  - 事後条件: 長い形から当てる。`except` の出現と重なる当たり、`live` の語の出現の内側にある当たりを返さない。1 つの位置の当たりは 1 件
- `banned_words`
  - 事後条件: 文字列の要素だけの用語集で、今の当たりと理由の文が変わらない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `term_words` | 要素の形が違う | 知らないキー・型の違い・空の `word`・空の `forms` | `words_of` | `WordsInvalid` を投げる | 呼ぶ側の `config_broken`（終了コード 2）。項目と語を文に入れる | 用語集を直して打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Word` | 型（`word`・`forms`・`excepts`・`replaces`・`standalone`・`added_only`） | `core/words/rules_decide.py` | — |
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
| `Finder.find` | 活用形が当たる・長い語が先に当たる | 単独の語の前後の漢字・`except` と重なる当たり・生きた語の内側と外側・カタカナと ASCII の境界 | 語が無ければ空 |
| `bad_replaces` | 置き換え先に別の語が残る登録を返す | 置き換え先が自分の語を含む（当てる） | — |

## 用語集の読み取り

満たす決定: 決定 5・9・10、受け入れ条件 7・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `at_worktree` | `core/words/source.py` | 新設 | `at_worktree(root: Path) -> Optional[Source]` | worktree のファイルから読む。設定が無ければ None |
| `at_commit` | 同上 | 新設 | `at_commit(g: Git, ref: str) -> Optional[Source]` | コミットのファイル（`git show <ref>:<パス>`）から読む。設定が無ければ None |

- `Source` の値: `words`・`live`（生きた語）・`paths`・`exclude`（用語集と生成する文書を足した後の glob）・`examples`（パス → 語の集合）・`banned`（禁止語）。`Source.covers(path)` が見るファイルか、`Source.finder(code)` が足した行の検査の `Finder` を返す
- 設定の保存先は表 `ndf-files` の `glossary` の行から引く（今の `checks/words.py` は `glossary_config` を `rules.json` に持つ。その値と `words.examples` を `rules.json` から消す）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `at_worktree`・`at_commit` | 設定か用語集が読めない | JSON の誤り・`source` が無い・`source` のファイルが無い・`contexts` か `terms` の配列が無い・要素の形の誤り | この 2 つの関数 | `WordsUnreadable` を投げる | 呼ぶ側の `config_broken`（終了コード 2）。`records` の今の `GlossaryError` は `WordsUnreadable` に替わらず、`render` は今のまま `precondition` | 直して打ち直す |

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
| `added_files` | `core/words/added.py` | 新設 | `added_files(diff: str) -> list[FileAdded]` | 差分の文字列から、ファイルごとの全文の行と足した行の番号（移しただけの行を除く）を返す |
| `in_diff` | 同上 | 新設 | `in_diff(g: Git, base: str, head: str, source: Source) -> list[Hit]` | `base` と `head` の差分を 1 回読み、設定の範囲のファイルの足した行の当たりを返す |
| `in_text` | 同上 | 新設 | `in_text(name: str, text: str, source: Source) -> list[Hit]` | 文の全行（Markdown として）の当たりを返す。`name` は当たりの `path` になる |
| `base_ref` | 同上 | 新設 | `base_ref(g: Git, name: str) -> str` | リモート追跡ブランチがあればその参照、無ければ渡された名前を返す |
| `_glossary_words` | `domains/records/cli.py` | 新設 | `_glossary_words(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 3 つの対象の当たりをまとめて結果 JSON にする |
| `_pr_texts` | 同上 | 新設 | `_pr_texts(args: argparse.Namespace, root: Path) -> list[tuple[str, str]]` | PR の題と本文を、当たりの名前とともに返す |

- `in_diff`
  - 事前条件: `base` と `head` のコミットが手元にある。`head` を含む分岐点からの差分は呼ぶ側が `merge_base` で求めて渡す（cross-review は修正担当を起動する前の HEAD をそのまま渡す）
  - 副作用: 外部コマンド `git diff`（`--no-renames --no-ext-diff --no-color --no-textconv -U1000000`、`-c core.quotePath=false`）を 1 回
- `in_text`・`in_diff`
  - 事後条件: Markdown（`.md`）のコードブロックの行（`markdown.code_lines`）と、左の空白を除いて `>` で始まる行は見ない。`Source.examples` のファイルと語の組は返さない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `in_diff` | git が失敗 | ref を解けない・手元に無い | `Git.run` | `GitError` を呼び出し元へ返す | コマンドは `precondition`、承認ゲートは `refused`、cross-review は今の git の失敗と同じ | 取得して打ち直す |
| `in_diff` | 読めない文字 | UTF-8 でないファイル | `added_files` | 置き換え文字で続ける | 出力なし | 打ち直せる |
| `_glossary_words` | 対象が無い | 引数が 3 つとも無い | 引数の確かめ | 止める | `invalid` | 引数を足す |
| `_glossary_words` | ファイルを読めない | `--text` のパスの誤り | 読み取り | 止める | `precondition`。パスを文に入れる | 直して打ち直す |
| `_pr_texts` | GitHub を読めない | 認証・接続 | `GitHubError` | 止める | `transient`（今の `_run` の扱い） | 打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `added_files` | 足した行の中身（前後の空白を除く）と同じ消えた行が差分のどこかにあり、まだ使っていない | 移しただけの行として除き、消えた行の数を 1 減らす |
| `added_files` | 同じ消えた行がもう無い・中身が空 | 中身が空の行は足した行に数えない。ほかは足した行 |
| `added_files` | バイナリ・消えたファイル | 見ない |

- 除く順は差分に出るファイルの順・行の順とする。消えた行の数え上げは、足した行を見る前に全ファイルで済ませる

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `added_files` | `Git.diff_between`・`unidiff` | `git grep -n "diff" -- plugins/ndf/src/ndf/adapters/git.py` | 使う |
| `in_text` | `adapters/markdown.code_lines`・`block_kinds` | 読んで確かめた | `code_lines` を使う。`block_kinds` の引用は継続行を含むので使わない |
| `Source.covers` | `pathspec`（`gate/checks.py` が使う） | `git grep -n pathspec -- plugins/ndf/src` | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `added_files` | 足した行だけを返す | 別のファイルへ移した行・移した行を 2 回足す・字下げだけ変えた行・`+++` で始まる本文・末尾の改行無し・日本語と空白のファイル名 | バイナリ |
| `in_diff` | コードのコメント・テストの名前・文書の当たりと行の番号 | `paths`・`exclude`・`examples` の外 | ref を解けない |
| `in_text` | 題・本文の当たり | コードブロック・インラインコード・`>` の行・`>` の次の本文 | — |
| `_glossary_words` | 0 件は `ok`、1 件以上は `check_failed`、3 つの対象をまとめる | 用語集の設定が無い | 壊れた用語集は `config_broken`・引数なしは `invalid` |

## cross-review の語の検査

満たす決定: 決定 1・2・10・11、受け入れ条件 1〜7・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `scan` | `crossreview/fixwords.py` | 新設 | `scan(g: Git, base: str) -> Optional[list[Hit]]` | HEAD の用語集で `base` から HEAD の足した行の当たりを返す。設定が無ければ None |
| `relaunch_items` | 同上 | 新設 | `relaunch_items(hits: Sequence[Hit], reference: Path) -> list[dict[str, Any]]` | 当たりをファイルごとの指摘の項目（`id` は `w<通し番号>`・`text`・`path`・`line`・`severity`）にする。`text` は語の数と `reference` のパスだけで、置き換え先の文を持たない |
| `write_reference` | 同上 | 新設 | `write_reference(path: Path, hits: Sequence[Hit]) -> None` | 当たりの一覧を JSON で書く |
| `record_of` | 同上 | 新設 | `record_of(found: Sequence[Hit], left: Sequence[Hit]) -> dict[str, Any]` | 状態ファイルの `rounds[].words` の形にする |
| `remaining` | 同上 | 新設 | `remaining(rows: Iterable[Mapping[str, Any]], texts: Mapping[str, str]) -> dict[str, bool]` | 語の指摘の行ごとに、当たった行の中身が head のファイルに残るかを返す |
| `enter_words` | `crossreview/journal.py` | 新設 | `enter_words(round_no: int, hits: Sequence[Hit]) -> None` | 残った当たりをファイルごとの未解決の指摘の行（`by: words`・重さ major・`words`）として足す |
| `settle_words` | 同上 | 新設 | `settle_words(left: Mapping[str, bool]) -> None` | 語の指摘の行の終わり方を、行が残れば未解決、消えれば直したにする |
| `fixer_docs` | `crossreview/criteria_decide.py` | 新設 | `fixer_docs(skill: Path) -> str` | 文書のファイルを直すときの規則への参照の 1 文を `review-criteria.json` の `fixer_docs` から組む |
| `_check_words` | `crossreview/drive.py` | 新設 | `_check_words(run: _Run, round_no: int, base: str, launch: Optional[Launcher]) -> None` | 検査・起動し直し・記録・指摘の更新を順に行う。`launch` が None なら起動し直さない |
| `_relaunch_words` | 同上 | 新設 | `_relaunch_words(run: _Run, round_no: int, hits: Sequence[Hit], launch: Launcher) -> None` | 当たりだけを渡して修正担当を 1 回起動し、結果の `commit` をラウンドの修正の記録に書く |
| `_round` | 同上 | 変更 | 変わらない | 修正の結果ファイルを移した後・`_push` の前に `_check_words(run, round_no, prep.head, launch)` を呼ぶ |
| `_resend` | 同上 | 変更 | 変わらない | 送り直す前に `_check_words(run, round_no, synced.remote, None)` を呼ぶ |
| `_start` | 同上 | 変更 | 変わらない | 同期の後・前の起動の修正を扱う前に、用語集を 1 度読んで読めなければ止める |
| `_launch_fixer` | 同上 | 変更 | `_launch_fixer(run, round_no, prompt, result_path, launch, suffix: str = FIX_SUFFIX)` | 出力の名前の接尾辞を受ける（起動し直しは `-fix-words`） |
| `_fix_prompt` | 同上 | 変更 | 変わらない | 指摘の基準の後に `criteria_decide.fixer_docs(identity.ROOT / "skills/document-style/SKILL.md")` の行を足す |

- `_check_words`
  - 事前条件: 修正担当の結果がジャーナルと状態ファイルに入っている
  - 事後条件: 用語集があれば、そのラウンドの `words` と語の指摘の行が状態ファイルに書かれている。用語集が無ければ何も書かない
  - 副作用: 状態の保存先へ `round<n>.words.json` を書く。修正担当の起動が高々 1 回
- `_relaunch_words`
  - 事後条件: 結果ファイルが無い・形が違っても例外にせず、注意に載せて検査へ進む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_start` | 用語集が読めない | `WordsUnreadable` | 始め | `DriveStopped("config_broken", 理由)` | `config_broken`（終了コード 2） | 直して打ち直す |
| `_check_words` | 修正の後で用語集が読めない | 修正担当が用語集を壊した | `scan` | 状態ファイルへ書いてから `DriveStopped("config_broken", 理由)`。push しない | 同上。修正のコミットは手元に残る | 用語集を直してコミットし、打ち直す（始めの送り直しが検査してから送る） |
| `_relaunch_words` | 修正担当が結果を書かない | 起動の失敗・上限 | 結果ファイルの有無 | 注意に載せて続ける | `warnings` に 1 行 | 残った語は未解決の指摘になる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_check_words` | 用語集が無い | 何もしない |
| `_check_words` | 当たりが無い | `words` に空の 2 つの配列を書き、前のラウンドの語の指摘だけを決め直す |
| `_check_words` | 当たりがあり `launch` がある | 参考資料を書き、起動し直し、もう一度検査した結果を `left` にする |
| `_check_words` | 当たりがあり `launch` が無い（送り直し） | `left` は `found` と同じ |
| `_check_words` | `left` が空でない | `enter_words` で未解決の指摘にする |

- `remaining` が読むファイルの本文は、語の指摘の行のファイルだけをまとめて 1 回で読む（`changed.committed_texts`）

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_check_words` | 当たりの件数と起動し直した後の件数 | 過程（`process`）の `round <n> words` の行 | 当たりの行の中身 |
| `record_of` | 当たりの一覧 | 状態ファイルの `rounds[].words` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `enter_words` | `_journal_row`・`_located` | `journal.py` を読んで確かめた | 使う |
| `_relaunch_words` | `_fix_prompt`・`_launch_fixer`・`_take_fix`・`_consume` | `drive.py` を読んで確かめた | 使う（指示の組み立てを増やさない。項目の id がジャーナルに無いので、結果は語の指摘の終わり方に効かない） |
| `write_reference` | `store.write_object`・`_remarks_file` | 同上 | `store.write_object` を使う |
| `remaining` | `drive._committed_texts` | 同上 | 使う（`changed.py` へ移す） |
| `fixer_docs` | `fixer_ci_only` | `criteria_decide.py` を読んで確かめた | 同じ形で足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_check_words` | 直しの直後に当たり、起動し直して 0 件なら指摘が増えず push する | 最後のラウンドで残ると `gaps` に入る・用語集が無ければ起動の回数が変わらない | 修正の後に用語集が壊れると push せずに止まる |
| `_relaunch_words` | 起動が 1 回だけ増え、出力の名前が分かれる | — | 結果ファイルが無くても検査へ進む |
| `settle_words`・`remaining` | 行が消えれば直した、残れば未解決 | 見送りと申告した行・別のファイルへ移った行 | 読めないファイル（消えた）は直した |
| `_resend` | 送り直しの前に検査して指摘にする | 当たりが無い | — |
| `_fix_prompt` | 参照の文と開ける絶対パスが入る | 用語集が無いリポジトリでも入る | — |

## 承認資料の前の検査

満たす決定: 決定 7・8、受け入れ条件 12〜15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `github_words` | `progress/gate/words.py` | 新設 | `github_words(cwd: Path, hub: Optional[Callable[[], GitHub]] = None) -> WordsCheck` | 質問と承認資料の文を受けて当たりを返す関数を作る（`WordsCheck = Callable[[Question, str], list[Hit]]`） |
| `material_text` | `progress/gate/asking.py` | 新設 | `material_text(root: Path, v: run.View, q: Question) -> str` | 書いてある承認資料があればその文、無ければ `material(v, q)` を返す |
| `material` | 同上 | 変更 | 変わらない | 「未確認の点」の各項目を引用の行（`> `）にする |
| `present` | `progress/gate/entry.py` | 変更 | `present(st, machine_id, cwd, out, observe, env=None, words: Optional[WordsCheck] = None)` | 質問を作った後・承認資料とリクエストの前に検査し、当たりがあれば `check_failed` の結果を返す |
| `_progress` | 同上 | 変更 | 変わらない | `github_words(cwd)` を渡す |

- `github_words` が返す関数
  - 事前条件: `q.gate` が設計かリリースの承認ゲート。プランの中の承認ゲート（区分なし）では検査しない
  - 副作用: PR を 1 回読み（題・本文・ベース・head）、ベースのブランチと PR の head を 1 回 `git fetch` する。PR の番号が無い質問では文だけを見る
  - 事後条件: 用語集は PR の head のコミットから読む（PR が無ければ `cwd` の HEAD）。設定が無ければ空

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `github_words` が返す関数 | PR を読めない・取得できない | GitHub・git の失敗 | その関数の中 | `model.Refused` を投げる | `refused`（今の観測の失敗と同じ） | 待ちのまま。打ち直せる |
| `present` | 用語集が読めない | `WordsUnreadable` | `github_words` が返す関数 | 資料を書かずに止まる結果を返す | `config_broken` | 待ちのまま。直して打ち直す |
| `present` | 当たりがある | 廃止した語 | 検査 | 資料もリクエストも出さずに止まる結果を返す | `check_failed`（終了コード 1）。`items` に当たり、`summary` に件数 | 待ちのまま。直して打ち直せば承認ゲートになる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `github_words` | `answering.github_observe`・`Git.fetch`・`Git.merge_base` | `gate/` を読んで確かめた | 同じ形（関数を返す関数）で足す。`Target` に題と本文は無いので PR は別に読む |
| `material_text` | `write_material`・`material_path` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `present` | 当たりがあれば資料のファイルもリクエストも無く `check_failed`、直せば `gate` | 2 回目の検査も全部の語・プランの中の承認ゲートでは検査しない・用語集が無い | 用語集が壊れていれば `config_broken`・PR を読めなければ `refused` |
| `material` | 未確認の点が引用の行になる | 未確認の点が無い | — |

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
| `skills/development-workflow/references/approval-request.md` | 「提示を組み立てる前に」へ検査のコマンドと、当たりがあれば提示しないことを足す |
| `skills/cross-review/SKILL.md`・`docs/02-fix-and-rotation.md` | 語の検査・起動し直し・指摘の終わり方・`config_broken` を足す |
| `contracts/data/review-criteria.json` | `fixer_docs` の文を足す（語の一覧は持たない） |
| `.github/workflows/glossary.yml` | 説明のコメントを直す（ジョブは増やさない） |

### 単体テストの観点

| 対象 | 確かめ方 |
| --- | --- |
| 登録と 3 つの検査 | `ndf check`・`ndf records glossary render --check`・`ndf records glossary meanings` を打つ（CI の今のジョブ） |
| テンプレート | 見出しと欄の名前だけの文書に `ndf records glossary words --text` を当てて 0 件（worker の確かめ） |
