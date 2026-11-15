# #3547 の詳細設計: `ndf gws` の副命令（gws の確認とサムネイルの保存）

設計文書 [issues/issue-3547-design.md](issue-3547-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語は設計文書の「用語の対応」に合わせる（gws の確認・サムネイルの保存・導入の同意の印・認証の状態の読み取り・records の gws の部品）。パスは、表の中では `plugins/ndf-next/` から書く。

この文書で「流れ」と書くのは、gws の確認とサムネイルの保存の 2 つのモジュール（`check.py`・`thumbnails.py`）のことである。「受付」は `ndf gws` の受付（`src/ndf/domains/gws/cli.py`）を指す。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── src/ndf/
│   ├── cli.py                      副命令の表に 1 行を足す
│   ├── checks/children.json        S1d の packages を書く
│   ├── parity/features.json        2 行から unowned を外す
│   └── domains/
│       ├── gws/ *
│       │   ├── __init__.py *       止まる理由の例外・未認証の終了コード
│       │   ├── cli.py *            受付（argparse と結果 JSON の出力）
│       │   ├── check.py *          gws の確認の流れ
│       │   ├── thumbnails.py *     サムネイルの流れ
│       │   ├── report_decide.py *  キーの取り出し・末尾の切り出し（純粋）
│       │   └── material.py *       導入の承認資料
│       └── records/gws.py          足すだけの変更 3 つ（判定 authenticated は変えずに使う）
├── contracts/
│   ├── schemas/io/gws.result.schema.json *   手書き
│   ├── schemas/io/gws.args.schema.json *     生成物
│   └── domains/places.json, ndf-files.json   1 行ずつ足す
├── docs/contracts/commands.md, files.md       生成物
├── skills/google-workspace/SKILL.md
└── tests/domains/gws/ *
    ├── __init__.py *
    ├── test_check.py *
    └── test_thumbnails.py *
docs/glossary/glossary.json, docs/glossary.md  （リポジトリの根から）
```

依存の向きは一方向である。受付が 2 つの流れを読み、流れが結果の値の切り出し・導入の承認資料・records の gws の部品（読み取りと判定）・パッケージの `__init__.py` を読む。結果の値の切り出しは標準ライブラリの型のほかを読まない。records の gws の部品は gws のパッケージを読まない。受付だけが結果の部品で結果 JSON を出し、流れは結果の辞書と終了コードを返すか、止まる理由の例外を投げる。流れが副命令の表（`ndf.cli`）を読まないため、`ndf.cli` → 受付 → 流れ の間に輪はできない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | 副命令の表に `"gws": "ndf.domains.gws.cli"` を足す | 無し（読むのは呼ぶときだけ） |
| `plugins/ndf-next/src/ndf/domains/gws/__init__.py` | 新設 | 2 つの流れが共有する止まる理由の例外（`GwsStop`）と未認証の終了コードを持つ | `ndf.contracts.exit_codes`・`ndf.contracts.result` |
| `plugins/ndf-next/src/ndf/domains/gws/cli.py` | 新設 | 下位の命令 `check`・`thumbnails` の引数を読み、流れを呼び、結果 JSON を 1 行出して終了コードを返す | `ndf.cli`・`ndf.contracts.result`・`check.py`・`thumbnails.py`・`__init__.py` |
| `plugins/ndf-next/src/ndf/domains/gws/check.py` | 新設 | gws と npm を探し、認証の状態の読み取り・承認資料の書き出し・npm の全体インストールを行って gws の確認の結果を組む | `report_decide.py`・`material.py`・`__init__.py`・`ndf.domains.records.gws`・`ndf.contracts.limits`・`ndf.contracts.result` |
| `plugins/ndf-next/src/ndf/domains/gws/thumbnails.py` | 新設 | gws と curl を探し、ページの ID の取得・サムネイルの URL の取得・画像の保存を行ってサムネイルの保存の結果を組む | `report_decide.py`・`__init__.py`・`ndf.domains.records.gws`・`ndf.contracts.limits`・`ndf.contracts.result` |
| `plugins/ndf-next/src/ndf/domains/gws/report_decide.py` | 新設 | 認証済みかの判定・`metrics` へ写すキーの取り出し・出力の末尾の切り出しを持つ純粋な判断 | 無し |
| `plugins/ndf-next/src/ndf/domains/gws/material.py` | 新設 | 導入の承認資料の Markdown を組み、状態の置き場の下の置き場 `gws` へ書く | `ndf.identity`・`ndf.domains.records.files`・`ndf.domains.worktree.atomic` |
| `plugins/ndf-next/src/ndf/domains/records/gws.py` | 変更 | 認証の状態の読み取りに、上限の名前の引数・誤りの標準エラー・3 つのキーの公開の名前を足す | `ndf.contracts.limits`（変えない） |
| `plugins/ndf-next/contracts/schemas/io/gws.result.schema.json` | 新設 | 結果の契約を引き、`tool` を `gws` に固定し、`metrics` と `items` の要素を閉じる | `contracts/schemas/result.schema.json` |
| `plugins/ndf-next/contracts/schemas/io/gws.args.schema.json` | 新設 | 生成物。`ndf check --write-spec` が受付の argparse の定義から書く | 受付 |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | 生成物。副命令の一覧に `ndf gws check`・`ndf gws thumbnails` の行が入る | 受付 |
| `plugins/ndf-next/docs/contracts/files.md` | 変更 | 生成物。置き場 `gws` とファイル `gws-install-material` の行が入る | 置き場の表・ファイルの表 |
| `plugins/ndf-next/contracts/domains/places.json` | 変更 | 行 `{"id": "gws", "root": "state", "path": "gws", "tracked": false}` を足す | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | 行 `gws-install-material`（置き場 `gws`・パス `install.md`・種類 `document`・書き手 S1d・消す時点 `replaced`）を足す | 置き場の表 |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | S1d の `packages` を `["ndf.domains.gws"]` にする | 無し |
| `plugins/ndf-next/src/ndf/parity/features.json` | 変更 | gws の確認とサムネイルの 2 行から `unowned` を外す | 無し |
| `plugins/ndf-next/skills/google-workspace/SKILL.md` | 変更 | 流れの手順 1・2 と操作の対応表のサムネイルの行を `ndf gws ...` に書き換え、案内の読み方を `metrics.client_config_exists` と `summary` へ移す | 受付（呼び出しの形） |
| `plugins/ndf-next/tests/domains/gws/__init__.py` | 新設 | テストのパッケージの印 | 無し |
| `plugins/ndf-next/tests/domains/gws/test_check.py` | 新設 | 偽の gws・npm を PATH に置いて gws の確認を確かめる | 受付・流れ |
| `plugins/ndf-next/tests/domains/gws/test_thumbnails.py` | 新設 | 偽の gws・curl を PATH に置いてサムネイルの保存を確かめる | 受付・流れ |
| `docs/glossary/glossary.json` | 変更 | 用語「導入の同意の印」を足す | 無し |
| `docs/glossary.md` | 変更 | 生成物。用語集から作り直す | `docs/glossary/glossary.json` |

## クラスと関数の構造

型の関係は設計文書の「構成要素図」を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/cli.py` | モジュール `ndf.cli` | `COMMANDS`（変更。1 行を足す） |
| `src/ndf/domains/gws/__init__.py` | クラス `GwsStop`（新設） | `GwsStop.__init__`（新設）・`unauthenticated_code`（新設） |
| `src/ndf/domains/gws/cli.py` | モジュール `ndf.domains.gws.cli` | `gws.cli.main`・`gws.cli._check`・`gws.cli._thumbnails`・`gws.cli._emit_stop`（いずれも新設） |
| `src/ndf/domains/gws/check.py` | モジュール `ndf.domains.gws.check` | `check.run`・`check._report`・`check._present`・`check._install`・`check._npm_prefix`（いずれも新設） |
| `src/ndf/domains/gws/thumbnails.py` | モジュール `ndf.domains.gws.thumbnails` | `thumbnails.run`・`thumbnails._gws`・`thumbnails._unauthenticated`・`thumbnails._page_ids`・`thumbnails._thumbnail`（いずれも新設）。内部の例外 `_NeedsLogin`（新設） |
| `src/ndf/domains/gws/report_decide.py` | モジュール `ndf.domains.gws.report_decide` | `report_decide.pick`・`report_decide.tail`（いずれも新設） |
| `src/ndf/domains/gws/material.py` | モジュール `ndf.domains.gws.material` | `material.render`・`material.write`（いずれも新設） |
| `src/ndf/domains/records/gws.py` | クラス `GwsError`・モジュール `ndf.domains.records.gws` | `GwsError.__init__`（変更）・`records.gws.auth_status`（変更）・`STATUS_KEYS`（変更。`_STATUS_KEYS` を公開の名前にする）。`decide`・`authenticated` は変えず、gws の確認とサムネイルの保存が呼ぶ（設計文書の決定 4） |
| `src/ndf/domains/records/cli.py` | モジュール `ndf.domains.records.cli` | 変えない（`_gws_check` は `records.gws.auth_status()` を引数なしで呼び続ける） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `GwsStop.__init__` | `src/ndf/domains/gws/__init__.py` | 新設 | `(kind: str, detail: str, *, summary: str \| None = None, items: list[dict[str, str]] \| None = None, metrics: dict[str, Any] \| None = None) -> None` | 流れが止まる理由（止まった理由の種類・説明・利用者への案内・項目・計測値）を受付へ運ぶ | `kind` は止まった理由の種類の表（`contracts/causes.json`）にある | 渡した値を同名の属性に持つ | 無し |
| `unauthenticated_code` | `src/ndf/domains/gws/__init__.py` | 新設 | `() -> int` | 未認証の承認ゲートの終了コード（承認ゲートの最初の値に 1 を足した値。11）を返す | 無し | 返す値は承認ゲートの範囲（10〜19）の中にある | 契約のファイル（終了コードの表）を読む |
| `report_decide.pick` | `src/ndf/domains/gws/report_decide.py` | 新設 | `(status: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]` | 認証の状態から、`metrics` へ写すキーだけを取り出す | `keys` のキーはすべて `status` にある（読み取りが確かめた） | 戻り値のキーは `keys` と一致し、ほかのキーを持たない | 無し |
| `report_decide.tail` | `src/ndf/domains/gws/report_decide.py` | 新設 | `(text: str \| None) -> str` | 出力の末尾 `TAIL_LINES` 行を切り出す | 無し | 戻り値の行数は `TAIL_LINES` 以下で、前後の空白を除く。`None` と空は空文字 | 無し |
| `records.gws.auth_status` | `src/ndf/domains/records/gws.py` | 変更 | `(limit: str = "gws.auth-status") -> dict[str, Any]` | `gws auth status` を 1 回打ち、上限の名前 `limit` の秒数で打ち切り、3 つのキーがそろった JSON を返す | `limit` は上限の宣言（`contracts/limits.json`）にある名前 | 戻り値は `STATUS_KEYS` をすべて持つ辞書。引数なしの呼び出しの振る舞いは今と同じ | gws の起動（引数は `auth status` だけ） |
| `GwsError.__init__` | `src/ndf/domains/records/gws.py` | 変更 | `(message: str, stderr: str = "") -> None` | 読めない理由に gws の標準エラーを添える | 無し | `str(exc)` は今と同じ文、`exc.stderr` は gws の標準エラー（無ければ空文字） | 無し |
| `check.run` | `src/ndf/domains/gws/check.py` | 新設 | `(tool: str, install: bool) -> tuple[dict[str, Any], int]` | gws の有無・npm の有無・導入の同意の印から次の処理を選び、gws の確認の結果と終了コードを返す | 状態の置き場は受付の前に用意済み（`ndf.cli`） | 戻り値は `ok`・0 か `gate`・10 か `gate`・11 のどれか。ほかは `GwsStop` を投げる | gws・npm の起動、承認資料の書き出し |
| `check._report` | `src/ndf/domains/gws/check.py` | 新設 | `(tool: str) -> tuple[dict[str, Any], int]` | 認証の状態を上限 `google-workspace.call` で読み、`records.gws.authenticated` で `authenticated` か `unauthenticated` の結果を組む | gws が PATH にある | `metrics` は `state` と 3 つのキーだけを持つ。未認証なら `routes` は `retry` → `ndf gws check`・`cancel` → `null` | gws の起動 |
| `check._present` | `src/ndf/domains/gws/check.py` | 新設 | `(tool: str, npm: str) -> tuple[dict[str, Any], int]` | 承認資料を書き、`missing` の承認ゲートの結果を組む | gws が無く、npm が PATH にあり、導入の同意の印が無い | 承認資料のファイルがあり、`presentation_path` がそのパスを指す。`routes` は `approved` → `ndf gws check --install`・`rejected` → `null` | npm の起動（`prefix -g`）、承認資料の書き出し |
| `check._install` | `src/ndf/domains/gws/check.py` | 新設 | `(npm: str) -> None` | `npm install -g @googleworkspace/cli` を上限 `google-workspace.install` で 1 回打つ | 導入の同意の印がある | 戻ったときは npm が 0 で終わっている | npm の起動（利用者の環境への全体インストール） |
| `check._npm_prefix` | `src/ndf/domains/gws/check.py` | 新設 | `(npm: str) -> str \| None` | `npm prefix -g` を上限 `google-workspace.call` で打ち、全体インストールの置き場を読む | 無し | 0 で終わり出力が空でなければその値、ほかは `None` | npm の起動（読むだけ） |
| `material.render` | `src/ndf/domains/gws/material.py` | 新設 | `(*, package: str, package_url: str, command: Sequence[str], where: str) -> str` | 承認資料の Markdown（対象を開くためのもの・承認の判断に使うもの・同意を求めること・戻し方の 4 節）を組む | 無し | 本文は取得元・打つコマンド・入る先・Google の公式サポート外で版は 0.x・戻し方 `npm uninstall -g <package>` を含む | 無し |
| `material.write` | `src/ndf/domains/gws/material.py` | 新設 | `(text: str) -> Path` | 承認資料を状態の置き場の下の `gws/install.md` へ置き換えで書く | 状態の置き場を解ける | 戻り値のパスに `text` が書き終わっている（半端なファイルを残さない） | ファイルの書き出し（ディレクトリの作成を含む） |
| `thumbnails.run` | `src/ndf/domains/gws/thumbnails.py` | 新設 | `(tool: str, presentation_id: str, out_dir: str, size: str) -> tuple[dict[str, Any], int]` | 全ページのサムネイルを `p<番号>.png` で保存し、サムネイルの保存の結果と終了コードを返す | `size` は `SIZES` のどれか（受付の argparse が確かめた） | 戻り値は `ok`・0 か `gate`・11。ほかは `GwsStop` を投げる。`ok` のとき `items` はページ順 | gws・curl の起動、出力のディレクトリの作成と画像の書き出し |
| `thumbnails._gws` | `src/ndf/domains/gws/thumbnails.py` | 新設 | `(gws: str, args: Sequence[str], what: str) -> Any` | gws のスライドの操作を上限 `google-workspace.thumbnails` で打ち、応答の JSON を返す | gws が PATH にある | 戻り値は JSON として読めた値。0 以外で未認証なら `_NeedsLogin` を投げる | gws の起動 |
| `thumbnails._unauthenticated` | `src/ndf/domains/gws/thumbnails.py` | 新設 | `() -> bool` | gws が失敗した後に、認証の状態を上限 `google-workspace.thumbnails` で 1 回読み、未認証かを決める | gws の操作が 0 以外で終わった後だけ呼ぶ | 読めて `records.gws.authenticated` が偽のときだけ真。読めないときは偽 | gws の起動（`auth status`） |
| `thumbnails._page_ids` | `src/ndf/domains/gws/thumbnails.py` | 新設 | `(gws: str, presentation_id: str) -> list[str]` | `gws slides presentations get` でページの ID をページ順に取る | 無し | 戻り値は 1 件以上で、どれも空でない文字列 | gws の起動 |
| `thumbnails._thumbnail` | `src/ndf/domains/gws/thumbnails.py` | 新設 | `(gws: str, presentation_id: str, object_id: str, size: str, dest: Path, no: int) -> None` | 1 ページのサムネイルの URL を取り、curl で `dest` へ保存する | `dest` の親のディレクトリがある | 戻ったときは `dest` に画像があり、curl が 0 で終わっている | gws・curl の起動、画像の書き出し |
| `gws.cli.main` | `src/ndf/domains/gws/cli.py` | 新設 | `(argv: list[str]) -> int` | 引数を読み、下位の命令の流れを呼び、結果 JSON を 1 行出して終了コードを返す | 引数を読む前に副作用を起こさない（`spec_args` が `main([])` で引数の定義を取り出す） | 標準出力の最後の行が結果 JSON で、戻り値はその終了コード | 標準出力への書き出し |
| `gws.cli._check` | `src/ndf/domains/gws/cli.py` | 新設 | `(args: argparse.Namespace) -> tuple[dict[str, Any], int]` | `check.run(TOOL, args.install)` を呼ぶ（副作用は `check.run` が持つ） | 無し | `check.run` の戻り値をそのまま返す | 無し |
| `gws.cli._thumbnails` | `src/ndf/domains/gws/cli.py` | 新設 | `(args: argparse.Namespace) -> tuple[dict[str, Any], int]` | `thumbnails.run(TOOL, args.presentation_id, args.out_dir, args.size)` を呼ぶ（副作用は `thumbnails.run` が持つ） | 無し | `thumbnails.run` の戻り値をそのまま返す | 無し |
| `gws.cli._emit_stop` | `src/ndf/domains/gws/cli.py` | 新設 | `(stop: GwsStop) -> int` | 止まる理由を `stopped` の結果 JSON にして出し、種類の終了コードを返す | 無し | 戻り値は `cause.kind` の終了コード（1・2・3） | 標準出力への書き出し |

- `records.gws.auth_status` の引数 `limit` の既定値は文字列のまま置く。records の側（`ndf records gws-check`）が今の上限の名前を引数なしで使い続けるためである
- `check._report` の `summary` は、未認証で `client_config_exists` が偽のときに案内を持つ（設計文書の決定 10）。案内は「`gws auth login` の前に `gws auth setup` か OAuth クライアントの用意が要る」である

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `unauthenticated_code` | 終了コードの表が読めない | 契約のファイルの欠け・壊れ | `exit_codes.first_code` の `ContractError` | 呼び出し元へ返す | `ndf.cli.main` が `config_broken` の結果 JSON を出す。終了コードは事実の表の読めないときの値 | 後始末は無い。契約を直して打ち直せる |
| `records.gws.auth_status` | gws を起動できない・上限の秒数を超えた | PATH の gws が実行できない・応答が遅い | `subprocess.run` の `OSError`・`SubprocessError` | 呼び出し元へ返す（`GwsError`。`stderr` は空） | 呼び出し元が決める | 後始末は無い。打ち直せる |
| `records.gws.auth_status` | 0 以外で終わった・JSON でない・3 つのキーが欠ける | gws の誤り・版の違い | 終了コード・`json.loads`・キーの照合 | 呼び出し元へ返す（`GwsError`。`stderr` に gws の標準エラー） | 呼び出し元が決める | 後始末は無い。打ち直せる |
| `check.run` | gws も npm も PATH に無い | Node.js が無い | `shutil.which` | 止める（`GwsStop`、種類 `precondition`、`metrics.state` は `uninstallable`） | `stopped`・3。`summary` は npm（Node.js）を入れてから打ち直す案内 | 後始末は無い。npm を入れて打ち直せる |
| `check.run` | 入れた後も gws が PATH に無い | npm の全体インストールの `bin` が PATH に無い | `_install` の後の `shutil.which` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`summary` は `npm prefix -g` の値の `bin` を PATH へ足す案内（値を読めなければ `npm prefix -g` の `bin` と書く） | PATH も設定のファイルも書き換えない。PATH を足して `ndf gws check` を打てる |
| `check._report` | 認証の状態を読めない | `GwsError`（起動できない・上限の秒数を超えた・0 以外・JSON でない・キーが欠ける） | `records.gws.auth_status` | 止める（`GwsStop`、種類 `invalid`）。未認証に倒さない | `stopped`・2。`items` は標準エラーが空でないときだけ 1 件（`name`: gws auth status の標準エラーの末尾・`result`: `stderr`・`detail`: 末尾 20 行） | 後始末は無い。打ち直せる |
| `check._present` | 承認資料を書けない | 状態の置き場への書き込みの失敗 | `material.write` の `OSError` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`summary` は書けなかったパスと理由 | 一時ファイルは付け替えの前に失敗すれば残る（`atomic.write_text` の名前 `.install.md.<pid>`）。打ち直すと同じ名前へ書き直す |
| `check._present` | `npm prefix -g` を読めない | npm の誤り・上限の秒数を超えた | `_npm_prefix` の戻り値 `None` | 既定値で続ける（入る先を「`npm prefix -g` が示す場所（値を読めなかった）」と書く） | 承認ゲートの結果は変わらない（`gate`・10） | 後始末は無い |
| `check._install` | npm を起動できない | PATH の npm が実行できない | `subprocess.run` の `OSError` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`summary` は出力を示して止まる・`sudo` で打ち直さないと書く | 入れていない。原因を直して `--install` で打ち直せる |
| `check._install` | npm が上限の秒数を超えた | 取得が遅い・止まった | `subprocess.TimeoutExpired` | 止める（`GwsStop`、種類 `interrupted`） | `stopped`・1 | `subprocess.run` が npm を止める。途中まで入った状態は npm に任せる。`--install` で打ち直せる |
| `check._install` | npm が 0 以外で終わった | 権限が無い・取得の失敗 | 終了コード | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`items` は 1 件（`name`: npm の出力の末尾・`result`: `failed`・`detail`: 標準出力と標準エラーをつないだ末尾 20 行）。`summary` は `sudo` で打ち直さないと書く | `sudo` を打たない。利用者が原因を直して `--install` で打ち直せる |
| `check._npm_prefix` | npm を起動できない・上限の秒数を超えた・0 以外・出力が空 | npm の誤り | `OSError`・`TimeoutExpired`・終了コード | 既定値で続ける（`None`） | 出力しない（呼び出し元が案内の文を変える） | 後始末は無い |
| `material.write` | 状態の置き場を解けない・書けない | 状態の置き場の権限・容量 | `identity.load` の `IdentityError`・書き込みの `OSError` | 呼び出し元へ返す（`IdentityError` は `OSError` にせずそのまま投げ、`check._present` が両方を捕まえる） | 呼び出し元が決める | 半端なファイルを残さない（付け替えで書く） |
| `thumbnails.run` | gws か curl が PATH に無い | 導入していない | `shutil.which` | 止める（`GwsStop`、種類 `precondition`） | `stopped`・3。gws が無いときの `summary` は `ndf gws check` で確かめる案内 | 後始末は無い。入れて打ち直せる |
| `thumbnails.run` | 出力のディレクトリを作れない | 権限・同名のファイル | `Path.mkdir` の `OSError` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1 | 後始末は無い。別のディレクトリで打ち直せる |
| `thumbnails.run` | gws が未認証で失敗した | `_NeedsLogin` | `thumbnails._gws` | 承認ゲートの結果を組んで返す（止める理由の例外にしない） | `gate`・11。`items` は失敗した操作 1 件（`result`: `unauthenticated`）、`routes` は `retry` → 同じ引数の `ndf gws thumbnails`・`cancel` → `null` | 保存済みの `p<番号>.png` は残す。ログインの後に打ち直すと全ページを書き直す |
| `thumbnails._gws` | gws を起動できない | PATH の gws が実行できない | `OSError` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1 | 保存済みの画像は残す。打ち直せる |
| `thumbnails._gws` | gws が上限の秒数を超えた | 応答が遅い | `TimeoutExpired` | 止める（`GwsStop`、種類 `interrupted`） | `stopped`・1 | 保存済みの画像は残す。打ち直せる |
| `thumbnails._gws` | gws が 0 以外で終わり、未認証でない | gws の誤り（権限・ID の誤りなど）、または認証の状態を読めない | 終了コードと `_unauthenticated` の偽 | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`items` は 1 件（`result`: `gws-failed`・`detail`: 標準出力と標準エラーをつないだ末尾 20 行） | 保存済みの画像は残す。原因を直して打ち直せる |
| `thumbnails._gws` | 応答が JSON でない | gws の版の違い・出力の混入 | `json.loads` | 止める（`GwsStop`、種類 `invalid`） | `stopped`・2。`items` は 1 件（`result`: `unreadable`・`detail`: 出力の末尾） | 保存済みの画像は残す |
| `thumbnails._unauthenticated` | 認証の状態を読めない（上限の秒数を超えたときを含む） | `GwsError` | `records.gws.auth_status` | 既定値で続ける（偽。未認証でないとして元の gws の失敗で止める） | 呼び出し元の `stopped`・1 | 後始末は無い |
| `thumbnails._page_ids` | ページの ID を読めない | 応答に `slides[].objectId` が無い・空・文字列でない・0 件 | 応答の照合 | 止める（`GwsStop`、種類 `invalid`） | `stopped`・2 | 何も保存していない |
| `thumbnails._thumbnail` | 応答に `contentUrl` が無い | gws の版の違い | 応答の照合 | 止める（`GwsStop`、種類 `invalid`） | `stopped`・2 | それまでのページの画像は残す |
| `thumbnails._thumbnail` | curl を起動できない | PATH の curl が実行できない | `OSError` | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1 | それまでのページの画像は残す |
| `thumbnails._thumbnail` | curl が上限の秒数を超えた | 画像の取得が遅い | curl の終了コード 28（`--max-time` に当たった） | 止める（`GwsStop`、種類 `interrupted`） | `stopped`・1 | `-o` の書きかけのファイルが残りうる。打ち直すと同じ名前へ書き直す |
| `thumbnails._thumbnail` | curl が 28 以外の 0 以外で終わった | URL の期限切れ・HTTP の誤り | curl の終了コード | 止める（`GwsStop`、種類 `unknown`） | `stopped`・1。`items` は 1 件（`result`: `download-failed`・`detail`: curl の標準エラーの末尾） | それまでのページの画像は残す。打ち直せる |
| `gws.cli.main` | 引数の誤り（足りない・知らない・3 つ以外の大きさ・下位の命令が無い） | 呼び出しの誤り | argparse の `SystemExit(2)` | 呼び出し元へ返す（捕まえない） | `ndf.cli._call` が `stopped`（`invalid`）・2 を出す。使い方は標準エラー | 何も起動していない |
| `gws.cli.main` | 流れが止まる理由を投げた | `GwsStop` | `try` の `except GwsStop` | `_emit_stop` で結果 JSON にする | 上の各行の `stopped` と終了コード | 流れの行のとおり |
| `gws.cli._emit_stop` | 結果の形が契約に合わない | `metrics`・`items` の項目の誤り（実装の誤り） | `result.emit` の検査 | 呼び出し元へ返す（`result.emit` が誤りを標準エラーへ書き、`invalid` の終了コードを返す） | 結果 JSON を出さず、終了コード 2 | テストの `tests/conftest.py` の検査で実装の前に落とす |

- 受付と流れは `GwsStop` のほかの例外を捕まえない。捕まえない例外は `ndf.cli._call` が `stopped`（`unknown`）・1 にする

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check.run` | gws がある（導入の同意の印は問わない） | `check._report` の結果。npm を打たない |
| `check.run` | gws が無い・npm が無い（印は問わない） | `GwsStop`（`precondition`・`uninstallable`） |
| `check.run` | gws が無い・npm がある・印が無い | `check._present` の結果（`gate`・10） |
| `check.run` | gws が無い・npm がある・印がある・入れた後に gws がある | `check._install` の後に `check._report` の結果 |
| `check.run` | gws が無い・npm がある・印がある・入れた後も gws が無い | `GwsStop`（`unknown`） |
| `check._report` | 読めた・`records.gws.authenticated` が真 | `ok`・0・`state: authenticated` |
| `check._report` | 読めた・`records.gws.authenticated` が偽（`credential_source` が `none` か `client_secret.json`）・`client_config_exists` が真 | `gate`・11・`state: unauthenticated`。`summary` はログインの案内 |
| `check._report` | 読めた・`records.gws.authenticated` が偽・`client_config_exists` が偽 | `gate`・11・`state: unauthenticated`。`summary` は `gws auth setup` か OAuth クライアントの用意を先に置く案内 |
| `check._report` | 読めない | `GwsStop`（`invalid`） |
| `thumbnails._gws` | 0 で終わった・JSON として読める | 応答を返す |
| `thumbnails._gws` | 0 で終わった・JSON として読めない | `GwsStop`（`invalid`） |
| `thumbnails._gws` | 0 以外・`_unauthenticated` が真 | `_NeedsLogin` |
| `thumbnails._gws` | 0 以外・`_unauthenticated` が偽 | `GwsStop`（`unknown`・`gws-failed`） |
| `thumbnails._thumbnail` | curl が 0 | 戻る |
| `thumbnails._thumbnail` | curl が 28 | `GwsStop`（`interrupted`） |
| `thumbnails._thumbnail` | curl がそのほかの 0 以外 | `GwsStop`（`unknown`・`download-failed`） |

- 繰り返しの終わる条件: `thumbnails.run` のページの繰り返しは、`_page_ids` が返した ID の数で終わる。1 ページで止まる理由が出たら、残りのページを打たずに抜ける
- 計算量の注意: gws の起動はページ数に比例する（取得 1 回 + ページごとに 1 回、curl もページごとに 1 回）。成功時に認証の状態を読まない（設計文書の I8）。gws の確認は gws の起動が最大 1 回、npm の起動が最大 2 回（`prefix -g` と `install -g`、または `install -g` と入れた後の `prefix -g`）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `GwsStop` | 型（例外。属性 `kind`・`detail`・`summary`・`items`・`metrics`） | `src/ndf/domains/gws/__init__.py` | — |
| `Outcome` | 型（`tuple[dict[str, Any], int]`。結果の辞書と終了コード） | `src/ndf/domains/gws/__init__.py` | — |
| `_NeedsLogin` | 型（内部の例外。属性 `what`・`detail`） | `src/ndf/domains/gws/thumbnails.py` | — |
| `STATUS_KEYS` | 定数（3 つのキーの唯一の置き場。gws の確認の `metrics` もここから引く） | `src/ndf/domains/records/gws.py` | `("credential_source", "auth_method", "client_config_exists")` |
| `TAIL_LINES` | 定数 | `src/ndf/domains/gws/report_decide.py` | `20` |
| `AUTHENTICATED`・`MISSING`・`UNAUTHENTICATED`・`UNINSTALLABLE` | 定数（gws の状態の値。結果のスキーマの `metrics.state` の列挙と同じ 4 つ） | `src/ndf/domains/gws/report_decide.py` | `"authenticated"` ほか |
| `PACKAGE`・`PACKAGE_URL` | 定数 | `src/ndf/domains/gws/check.py` | `"@googleworkspace/cli"`・`"https://www.npmjs.com/package/@googleworkspace/cli"` |
| `INSTALL_ARGS` | 定数（npm の後ろの引数） | `src/ndf/domains/gws/check.py` | `("install", "-g", PACKAGE)` |
| `CALL_LIMIT`・`INSTALL_LIMIT` | 定数（上限の名前。値は上限の宣言が持つ） | `src/ndf/domains/gws/check.py` | `"google-workspace.call"`（120 秒）・`"google-workspace.install"`（600 秒） |
| `LIMIT` | 定数（上限の名前） | `src/ndf/domains/gws/thumbnails.py` | `"google-workspace.thumbnails"`（120 秒） |
| `SIZES`・`DEFAULT_SIZE` | 定数（受付の argparse の `choices` と既定値もここから引く） | `src/ndf/domains/gws/thumbnails.py` | `("SMALL", "MEDIUM", "LARGE")`・`"LARGE"` |
| `CURL`・`CURL_TIMEOUT_EXIT` | 定数 | `src/ndf/domains/gws/thumbnails.py` | `"curl"`・`28` |
| `FILE_ID` | 定数（ファイルの表の行の ID。パスはファイルの表と置き場の表が持つ） | `src/ndf/domains/gws/material.py` | `"gws-install-material"` |
| `TOOL` | 定数（`cli.tool_of(__name__)` で引く） | `src/ndf/domains/gws/cli.py` | `"gws"` |
| 置き場 `gws` | 設定値 | `contracts/domains/places.json` | 起点 `state`・パス `gws` |
| ファイル `gws-install-material` | 設定値 | `contracts/domains/ndf-files.json` | パス `install.md`・種類 `document`・消す時点 `replaced` |
| 3 つの上限の秒数 | 設定値（変えない） | `contracts/limits.json` | 上の各行の秒数 |

- gws の状態の 4 つの値は、コードの定数（書き手）と結果のスキーマの列挙（読み手が確かめる契約）の 2 か所に現れる。テストの間に出た結果 JSON をすべて結果のスキーマで確かめる（`tests/conftest.py`）ため、片方だけを変えるとテストが落ちる
- curl の `--max-time` には `LIMIT` の秒数を渡し、`subprocess.run` には上限を重ねない。curl が自分で打ち切り、終了コード 28 を返す（2026-11-15 に手元の curl で、1 秒で打ち切る呼び出しが 28 と `Operation timed out` を返すことを確かめた）

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つの `ndf gws check`（導入の同意の印なし） | 承認資料のファイル（状態の置き場の `gws/install.md`） | 取らない。同じディレクトリの一時ファイルへ書いてから付け替える（`atomic.write_text`。一時ファイルの名前にプロセスの ID を含む） | 後に付け替えたほうが残る。中身は入る先の値のほかは同じ。中断しても元のファイルか書き終えたファイルが残る |
| 2 つの `ndf gws check --install` | npm の全体インストールの置き場 | 取らない（npm に任せる） | 全体インストールは同じ版を入れ直すだけで、打ち直してよい。npm の打ち切りの後は `--install` で打ち直す |
| 同じ出力のディレクトリへの 2 つの `ndf gws thumbnails` | `p<番号>.png` | 取らない | 同じ名前へ上書きする。中断すると書きかけの画像が残りうるが、打ち直すと全ページを書き直す |
| `ndf gws` と `ndf records gws-check` | gws の資格情報 | 取らない。どちらも `gws auth status` を読むだけで書かない | 読むだけなので何度打ってもよい |
| 状態の置き場の用意（`ndf.cli._prepare`） | 状態の置き場 | 用意の側が持つロック（`identity.lock_path`）。受付は用意が終わった後に呼ばれる | 受付は用意に関わらない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `gws.cli.main` | 結果 JSON 1 行 | 標準出力の最後の行（結果 JSON） | 資格情報・トークン・`gws auth status` の応答の 3 つのキーのほか |
| `gws.cli._emit_stop` | 止まった結果 JSON 1 行 | 標準出力（結果 JSON） | 同上 |
| `check._report` | `metrics` の `state` と 3 つのキー | 結果 JSON | 応答のほかのキー（`report_decide.pick` が取り除く）。資格情報のファイルは開かない |
| `check._report` | 読めないときの gws の標準エラーの末尾 | 結果 JSON の `items` | 標準出力（応答の本体） |
| `check._install` | npm の出力の末尾 | 結果 JSON の `items` | — |
| `material.write` | 承認資料 | 状態の置き場の `gws/install.md`（ファイルの表の行） | 利用者の環境の値のうち、入る先（`npm prefix -g` の値）のほか |
| `thumbnails._gws`・`thumbnails._thumbnail` | gws・curl の出力の末尾 | 結果 JSON の `items` | `contentUrl` の値（一時的な URL）を結果に載せない |
| `thumbnails.run` | 保存先のパス・ページの ID | 結果 JSON の `items` と `metrics` | — |

- ログのモジュール（`logging`）には書かない。捕まえない例外の記録は `ndf.cli._call` が持つ（`log.exception`）
- 進捗記録は書かない（副命令は工程の記録を持たない）

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `GwsStop.__init__` | 各領域の止まる理由の例外（`crossrefactor.drive` の例外・`backlog.cli_input` の例外など） | `grep -rn "def __init__(self, kind: str, detail: str" plugins/ndf-next/src` | 使えない。どれも各領域の非公開の型で、gws の領域から読むと領域の間の依存が増える。形（種類と説明を持つ）はそろえる |
| `unauthenticated_code` | 終了コードの部品 `exit_codes.first_code` | `grep -n "^def " plugins/ndf-next/src/ndf/contracts/exit_codes.py` | 使う（最初の値を引き、1 を足す） |
| `report_decide.pick` | records の gws の部品の 3 つのキー | 同上 | キーの集まりは `STATUS_KEYS` を使う。取り出しは新設する（records の部品は辞書全体を返す） |
| `report_decide.tail` | 記録の MVV の承認資料の末尾の切り出し（`mvv_material_decide._tail`）・棚卸しの表の `_tail` | `grep -rn "def .*tail" plugins/ndf-next/src --include=*.py` | 使えない。前者は非公開の関数で、行数は MVV の承認資料の決まり（`OUTPUT_LINES`）が持つ。後者は表の行の省略で別の処理 |
| `check.run` | 今のスクリプトの流れ（`plugins/ndf/skills/google-workspace/scripts/gws-check.py`） | ファイルを読んだ | 新しい NDF からは読まない（今の NDF は変えない。設計文書の決定 11）。判定と順序をそのまま移す |
| `check._report` | records の gws の部品 `records.gws.auth_status`・`records.gws.authenticated` | 同上 | 2 つとも使う（読み取りには上限の名前を渡す。判定は新設しない。設計文書の決定 4） |
| `check._present` | 結果の部品 `result.result`・`result.command` | `grep -n "^def " plugins/ndf-next/src/ndf/contracts/result.py` | 使う |
| `check._install` | 新しい NDF の外部のコマンドを打つ部品 | `grep -rln "shutil.which" plugins/ndf-next/src`（`worktree/container.py`・`hooks/env.py` の 2 件。どちらも自分の領域の非公開の処理） | 使えない。共有の起動の部品が無いため、`subprocess.run` を直接呼ぶ |
| `check._npm_prefix` | 同上 | 同上 | 使えない（同上） |
| `material.render` | 承認資料の組み立ての部品 | `grep -rln "承認の判断に使うもの\|対象を開くためのもの\|戻し方" plugins/ndf-next/src`（0 件） | 使えない。新しい NDF に承認資料の組み立ての部品が無い。リリースの承認資料（`release.materials_decide`）は版の材料を並べる別の形 |
| `material.write` | 付け替えで書く部品 `worktree.atomic.write_text`・ファイルの置き場の部品 `records.files.path`・状態の置き場 `identity.load().state_dir` | `grep -rn "os.replace\|def write_text" plugins/ndf-next/src --include=*.py`・`src/ndf/domains/records/files.py` を読んだ | 3 つとも使う。依存は S1d → S1a → M7・M8 で子の表の向きに合う |
| `thumbnails.run` | 今のスクリプト（`slides-thumbnails.py`） | ファイルを読んだ | 新しい NDF からは読まない。判定と順序をそのまま移す |
| `thumbnails._gws` | 外部のコマンドを打つ部品 | `check._install` の行と同じ | 使えない（同上） |
| `thumbnails._unauthenticated` | `records.gws.auth_status`・`records.gws.authenticated` | 上の行 | 2 つとも使う |
| `thumbnails._page_ids` | 無し（gws のスライドの応答を読む部品） | `grep -rn "objectId" plugins/ndf-next/src`（0 件） | 新設 |
| `thumbnails._thumbnail` | 新しい NDF の HTTP の部品（`adapters/http.py`） | `ls plugins/ndf-next/src/ndf/adapters` | 使えない。`contentUrl` の取得は curl の `-fsSL --max-time` と同じ振る舞い（設計文書の入出力の契約）を保つ。部品を替えると上限と失敗の分け方が変わる |
| `gws.cli.main` | ほかの副命令の受付（`records.cli`・`contracts.cli`）の形 | `src/ndf/domains/records/cli.py` を読んだ | 形をそろえる（`TOOL = cli.tool_of(__name__)`、下位の命令の `set_defaults(handler=...)`） |
| `gws.cli._check` | 同上 | 同上 | 同上 |
| `gws.cli._thumbnails` | 同上 | 同上 | 同上 |
| `gws.cli._emit_stop` | 結果の部品 `result.stopped`・`result.emit` | `src/ndf/contracts/result.py` を読んだ | 使う（`metrics` と `items` は `result.stopped` の残りのキーワードで渡す） |

## 単体テストの観点

テストは受付（`gws.cli.main`）か流れ（`check.run`・`thumbnails.run`）から通し、偽の gws・npm・curl を PATH に置く。偽のコマンドの組み方は実装（`tdd-cycle`）で決める。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `unauthenticated_code` | 11 を返す | 承認ゲートの範囲の中にある | 終了コードの表が読めないと `ContractError`（`ndf.cli` の既存のテストが持つ。足さない） |
| `report_decide.pick` | 3 つのキーだけを返す | 余分のキー（トークンの値）を混ぜても返さない | — |
| `report_decide.tail` | 21 行の入力で末尾 20 行 | 20 行ちょうど・空・`None` | — |
| `records.gws.auth_status` | 渡した上限の名前の秒数で打ち切る | 引数なしで今の上限（`gws.auth-status`） | 0 以外・JSON でない・キーの欠けで `GwsError` の `stderr` に標準エラー。起動できない・打ち切りで `stderr` が空 |
| `GwsError.__init__` | `stderr` を持つ | `stderr` を省くと空文字 | — |
| `check.run` | gws があり認証済みで `ok`・0 | 印があり gws が既にあるとき npm を起動しない | npm が無く 3。入れた後も gws が無く 1 |
| `check._report` | 未認証で `gate`・11 と `routes`（`retry`・`cancel`）。トークンの無い状態（`credential_source: none`）と `client_secret.json` だけの状態（#2067）の両方が未認証。環境変数のトークンがあれば認証済み | `client_config_exists` の真と偽で `summary` が変わる | 読めない 4 通り（0 以外・JSON でない・キーの欠け・打ち切り）で `stopped`・2、標準エラーがあれば `items` に末尾 |
| `check._present` | `gate`・10、承認資料に取得元・打つコマンド・入る先・戻し方、`routes` の `approved` が `ndf gws check --install` | npm の `install` が 1 度も起動されない | 承認資料を書けないと `stopped`・1 |
| `check._install` | `install -g @googleworkspace/cli` を 1 回起動 | — | 起動できない・0 以外で `stopped`・1（`items` に末尾）、打ち切りで `stopped`・1（`interrupted`） |
| `check._npm_prefix` | 値が承認資料の入る先に載る | 出力が空 | 0 以外で入る先が「値を読めなかった」になる |
| `material.render` | 4 つの節と 5 つの項目を含む | — | — |
| `material.write` | 状態の置き場の `gws/install.md` に書く | 2 回打ってもファイルは 1 つ | 書けないと `OSError` を呼び出し元へ返す（`check._present` の行で確かめる） |
| `thumbnails.run` | 3 ページで `p1.png`〜`p3.png`、`items` と `metrics`、`auth status` を打たない | 大きさの指定が無いと `LARGE` | gws か curl が無く 3。出力のディレクトリを作れず 1 |
| `thumbnails._gws` | 応答の JSON を返す | — | 未認証で `gate`・11 と同じ引数の `routes`。未認証でない失敗で 1（`gws-failed` と末尾）。起動できない 1。打ち切り 1（`interrupted`）。JSON でない 2 |
| `thumbnails._unauthenticated` | `none` と `client_secret.json` で真 | — | 読めない・打ち切りで偽（元の失敗の 1） |
| `thumbnails._page_ids` | ページ順の ID | 1 ページ | 0 件・`objectId` の欠けで 2 |
| `thumbnails._thumbnail` | `dest` に保存 | — | `contentUrl` の欠けで 2。curl の 28 で 1（`interrupted`）。そのほかの 0 以外で 1（`download-failed`）。起動できない 1 |
| `gws.cli.main` | `check`・`thumbnails` の結果 JSON が結果のスキーマに合う | 下位の命令が無いと 2 | 知らない引数・3 つ以外の大きさで 2（`ndf.cli` を通して確かめる） |
| `gws.cli._check` | `--install` が `check.run` に渡る | — | — |
| `gws.cli._thumbnails` | 位置引数と `--size` が `thumbnails.run` に渡る | — | — |
| `gws.cli._emit_stop` | 種類ごとの終了コード（1・2・3） | `metrics` を持つ止まる理由（`uninstallable`） | — |
