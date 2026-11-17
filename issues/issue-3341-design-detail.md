# #3341 の詳細設計: 外からの参照の検査（R0）と、旧い置き場の退避の直し（R1）

設計文書 [issue-3341-design.md](issue-3341-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。書き方は NDF の `design` Skill の `references/detailed-design.md` にある。

**この文書が扱うのは、入れ替えの前に `main` へ入れる 2 つの子である。** 外からの参照の検査と書き換えの表（R0）と、新しい NDF の退避と旧いラッパーの見分けの直し（R1）である。入れ替えそのもの（#3304 の S2-0〜S2-15）はファイルの移動と workflow の書き換えの手順で、新しい関数を持たない。手順と確かめは [issue-3341-design-swap.md](issue-3341-design-swap.md) が持つ。

例: 10 系の利用者が 11.0.0 へ上げた直後に `ndf plan list` を打つと、新しい NDF は状態の置き場 `~/.local/state/ndf` を用意する。今の実装はここで旧い置き場を `ndf.v10-<日付>` へ名前を変える。R1 の直しの後は、名前を変える前に旧いラッパーの錠（`relay/<名前>/relay.lock`）を待たずに取りにいき、1 つでも取れなければ何も変えずに止まる。すべて取れたら、名前の変更と新しい置き場の用意が終わるまで錠を持ち続ける。結果 JSON は `stopped`・`store_in_use` で、`items` に取れなかった錠のパスと `reason: legacy_running` が載る。

## この文書の語

| 語 | この文書での意味 |
| --- | --- |
| 今のツリー | 検査を打った時点の worktree 。入れ替えの前は `plugins/ndf/` が 10 系、入れ替えの後は新しい NDF |
| 入れ替えの後のツリー | 今のツリーの `plugins/ndf-next/` を `plugins/ndf/` と読み替えたツリー。ファイルは動かさず、パスの読み替えだけで判定する |
| 参照の組 | 外からの参照の 1 件。参照を持つファイルと、そこに書かれた `plugins/ndf/` で始まるパスの組 |
| 表の行 | 書き換えの表（`scripts/plugin-refs-swap.json`）の 1 行。参照を持つファイル・書き換える前の文字列・書き換えた後の文字列・判定・行き先を持つ |
| 形の版 | 状態の置き場の `format.json` の番号。`format.json` が無いか読めない置き場は 10 系のもので、形の版を `10` とする |

## ファイル配置

省く: #3341 の「設計の結果」は「閉じる」の 1 行で、触るファイルを持たない（成果は設計 PR だけ）。下のファイルは前の子 R0・R1 の PR が触り、子の設計の結果で列挙する。配置と役割は次のツリーと一覧に置く。

パッケージ・モジュール構成（`*` は新設）:

```text
.github/workflows/
└── script-structure.yml              # R0: script-structure-check に 2 ステップを足す
scripts/
├── check-plugin-refs.py *            # R0: 外からの参照の収集・2 つのモードの検査・表を当てる apply
├── plugin-refs-swap.json *           # R0: 書き換えの表（94 行）
└── tests/
    └── test_check_plugin_refs.py *   # R0
plugins/ndf-next/
├── contracts/causes.json             # R1: 変えない（既存の store_in_use・precondition を使う）
├── src/ndf/
│   ├── identity.py                   # R1: 退避先の名前・旧い錠の確かめ・止まる理由
│   ├── cli.py                        # R1: IdentityError の止まる理由を結果へ写す
│   ├── core/durable.py               # R1: prepare_store の退避を identity の規則へ寄せる
│   ├── core/durable_locks.py         # R1: 止まる理由の例外 2 つ
│   ├── domains/relay/install.py      # R1: relay.version に自分の版を書く
│   └── domains/relay/registry.py     # R1: 錠の確かめを identity から読む
└── tests/
    ├── identity/test_identity.py
    ├── core/test_durable_store.py
    ├── cli/test_cli.py
    └── domains/relay/test_relay_install.py
```

- **R0 の依存の向き**: `check-plugin-refs.py` が呼ぶのは 2 つだけである。参照を持つファイルの一覧を返す `git` と、行き先の副命令の有無を確かめる `plugins/ndf-next/bin/ndf`（入れ替えの後は `plugins/ndf/bin/ndf`）である。10 系の `plugins/ndf/scripts/` も、根の `scripts/lib/` も読まない。検査の対象のファイルから検査を読む向きは無く、循環しない
- **R1 の依存の向き**: `identity` は新しい NDF の最も下の層で、標準ライブラリだけを読む。退避先の名前の規則と旧い錠の確かめは `identity` に置き、`core/durable.py` がそれを読む（`core` → `identity` の一方向）。`domains/relay/install.py` は `identity` と `domains/relay/signals.py` を読む（今と同じ）
- 役割の一覧:
  - `scripts/check-plugin-refs.py`（新設）: 外からの参照を集め、今のツリーと入れ替えの後のツリーで参照先の実在を確かめ、表を当てる。依存する先は `git`・`plugins/ndf-next/bin/ndf`
  - `scripts/plugin-refs-swap.json`（新設）: [issue-3341-design-refs.md](issue-3341-design-refs.md) の「実行する参照」の 147 組のうち「同じパス」を除く 94 組を、判定ごとの行で持つ。依存する先は無し
  - `scripts/tests/test_check_plugin_refs.py`（新設）: 一時のリポジトリで 2 つのモードと `apply` を確かめる
  - `.github/workflows/script-structure.yml`（変更）: `script-structure-check` に `check --tree current` と `check --tree swapped` の 2 ステップを足す。`push` の `paths` に新設の 2 ファイルを足す
  - `plugins/ndf-next/src/ndf/identity.py`（変更）: 退避先の名前（規則 P1）・退避先があれば止まる（P2）・旧い錠の確かめ（P6）。`config-state` を退避の対象から外す
  - `plugins/ndf-next/src/ndf/cli.py`（変更）: 状態の置き場を用意できないときの止まる理由を、例外が持つ理由から取る
  - `plugins/ndf-next/src/ndf/core/durable.py`（変更）: `_retire_store` の退避先を `identity.retired_name` で求める。退避先があれば止まり、旧い錠が取れなければ待ち直さずに止まる
  - `plugins/ndf-next/src/ndf/core/durable_locks.py`（変更）: 止まる理由の例外 `StoreLegacyRunning`・`StoreRetireTargetExists` を足す
  - `plugins/ndf-next/src/ndf/domains/relay/install.py`（変更）: ランチャーを置き換えたとき `relay.version` に自分の版を書く（規則 P3）
  - `plugins/ndf-next/src/ndf/domains/relay/registry.py`（変更）: `lock_busy` の本体を `identity.lock_held` へ移す。`lock_busy` は名前を変えずに `identity.lock_held` を呼ぶ（錠の確かめを 1 か所にする）

## クラスと関数の構造

型の関係は設計文書の「ドメインモデル」の集約の表が持つ。ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `scripts/check-plugin-refs.py` | モジュール | `main`（新設）・`collect_refs`（新設）・`load_table`（新設）・`check_tree`（新設）・`target_exists`（新設）・`command_exists`（新設）・`apply_table`（新設）・`Ref`（型。新設）・`Row`（型。新設）・`Finding`（型。新設） |
| `plugins/ndf-next/src/ndf/identity.py` | モジュール | `retired_name`（新設）・`lock_held`（新設。`registry.lock_busy` の本体を移す）・`hold_legacy_locks`（新設）・`prepare_state_dir`（変更: 止まる理由）・`_retire_all`（変更: 退避先があれば止まる）・`_retire_target`（削除）・`_retire_candidates`（削除）・`OLD_STATE_PLACES`（変更: `config-state` を `KEEP` へ） |
| `plugins/ndf-next/src/ndf/identity.py` | `IdentityError` | `kind`・`reason`・`paths`（属性を足す）・`items`（変更: 理由とパスを載せる） |
| `plugins/ndf-next/src/ndf/cli.py` | モジュール | `_prepare`（変更: 止まる理由を `IdentityError.kind` から取る） |
| `plugins/ndf-next/src/ndf/core/durable.py` | モジュール | `_retire_store`（変更）・`prepare_store`（呼び方は同じ。止まる例外が 2 つ増える） |
| `plugins/ndf-next/src/ndf/core/durable_locks.py` | `StoreLegacyRunning`・`StoreRetireTargetExists` | 新設（`StoreStopped` の子。メソッドを持たない） |
| `plugins/ndf-next/src/ndf/domains/relay/install.py` | モジュール | `install`（変更: `version` 引数を足す）・`VERSION_FILE`（定数。新設） |
| `plugins/ndf-next/src/ndf/domains/relay/registry.py` | モジュール | `lock_busy`（変更: 本体を `identity.lock_held` の呼び出しにする。シグネチャと責務は変えない） |
| `plugins/ndf-next/src/ndf/domains/relay/cli.py` | モジュール | `_do_install`（変更: 自分の版を `install` へ渡すだけ。責務と失敗の扱いは変えない） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` | `scripts/check-plugin-refs.py` | 新設 | `main(argv: list[str] \| None = None) -> int` | 副命令 `check --tree current\|swapped [--strict]` と `apply` を振り分け、結果を標準出力へ 1 行の JSON で出して終了コードを返す | リポジトリの根で打つ（`--root` で変えられる） | 終了コードは 0 = 指摘なし / 1 = 指摘あり / 2 = 表か git を読めない | 標準出力・標準エラーへの書き込み |
| `collect_refs` | `scripts/check-plugin-refs.py` | 新設 | `collect_refs(root: Path) -> list[Ref]` | `SCAN_PATHSPEC` の追跡されたファイルから、`REF_PATTERN` に当たるパスを参照の組として集める | `root` が git の worktree | 戻り値は（ファイル・パス）で重複が無く、ファイル名とパスの順に並ぶ。`SELF_FILES` のファイルを含まない | 外部コマンド `git ls-files`。ファイルの読み込み |
| `load_table` | `scripts/check-plugin-refs.py` | 新設 | `load_table(path: Path) -> list[Row]` | 書き換えの表を読み、行の形を確かめて返す | 無し | 戻り値のすべての行が `TABLE_VERDICTS` の判定を持ち、判定ごとに要る値（下の「内部のデータと定数」）を持つ | ファイルの読み込み |
| `check_tree` | `scripts/check-plugin-refs.py` | 新設 | `check_tree(root: Path, refs: list[Ref], rows: list[Row], tree: str, strict: bool) -> list[Finding]` | 1 つのモードで参照先の実在と表の行の状態を判定し、指摘を返す | `tree` は `current` か `swapped` | 戻り値の各指摘が規則の名前（下の「処理の手順」）を持つ。`tree` が `current` のとき表を読まない | `command_exists` を通した外部コマンド |
| `target_exists` | `scripts/check-plugin-refs.py` | 新設 | `target_exists(base: Path, rel: str) -> bool` | `plugins/ndf/` の下の相対パス（glob と `{}` を含んでよい）が `base` の下に実在するかを返す | `rel` は `plugins/ndf/` を除いた残り | glob を含むときは、最初の glob の文字より前の最も深いディレクトリの実在で判定する | 無し |
| `command_exists` | `scripts/check-plugin-refs.py` | 新設 | `command_exists(plugin: Path, words: tuple[str, ...]) -> bool` | プラグインのエントリポイントがその副命令を知っているかを、`--help` の終了コードで返す | `plugin/bin/ndf` が実行できる | 同じ `words` は 1 回の実行で 1 度だけ打つ（結果を覚える） | 外部コマンド `<plugin>/bin/ndf <words> --help`。状態の置き場は一時のディレクトリへ向ける |
| `apply_table` | `scripts/check-plugin-refs.py` | 新設 | `apply_table(root: Path, rows: list[Row]) -> tuple[list[str], list[Finding]]` | 「向け直す」の行の文字列を置き換え、「ファイルを消す」の行のファイルを消し、「消す」の行を手で直す一覧として返す | 表に「未作成」の行が無い | 指摘が 1 件でもあれば、どのファイルも変えていない | ファイルの書き込みと削除 |
| `retired_name` | `plugins/ndf-next/src/ndf/identity.py` | 新設 | `retired_name(place: Path, form: int \| None, now: datetime) -> Path` | 退避先のパス `<元の名前>.v<形の版>-<YYYYMMDD>` を求める（規則 P1） | `now` はタイムゾーンを持つ | `form` が None なら形の版は `LEGACY_FORM`。日付は UTC で書く。退避先の有無を見ない | 無し |
| `lock_held` | `plugins/ndf-next/src/ndf/identity.py` | 新設 | `lock_held(path: Path) -> bool` | `path` の排他を誰かが持っていれば真を返す（`registry.lock_busy` の本体を移したもの） | 無し | 取れた排他はすぐ放している。ファイルが無ければ偽で、ファイルを作らない | 錠のファイルを開く（読むだけ） |
| `hold_legacy_locks` | `plugins/ndf-next/src/ndf/identity.py` | 新設 | `hold_legacy_locks(place: Path, patterns: tuple[str, ...]) -> ContextManager[list[Path]]` | 旧い置き場の錠を待たずに排他で取りにいき、`with` の中で取れなかった錠のパスを渡す（規則 P6）。すべて取れたときは、`with` を抜けるまで錠を持ち続ける | 無し | 1 つでも取れなければ、取れた錠をすぐ放して空でない一覧を渡す。すべて取れたら空の一覧を渡し、`with` を抜けるときに放す。錠のファイルを作らない | 錠のファイルを開く（読むだけ） |
| `prepare_state_dir` | `plugins/ndf-next/src/ndf/identity.py` | 変更 | `prepare_state_dir(ident: PluginIdentity, now: Optional[datetime] = None) -> StateReady` | 状態の置き場を用意し、旧い置き場を P1・P2・P6 で退避する。旧い錠の確かめから名前の変更と `format.json` の書き込みまでを、1 つの `hold_legacy_locks` の `with` の中で行う | 無し | 旧いラッパーの錠が 1 つでも取れない・退避先が既にある、のどちらかなら何も変えずに `IdentityError`（理由つき） | ディレクトリの名前の変更・`format.json` の書き込み・隣の錠のファイル |
| `_retire_all` | `plugins/ndf-next/src/ndf/identity.py` | 変更 | `_retire_all(old: list[Path], now: datetime) -> tuple[Retired, ...]` | 旧い置き場を順に退避先の名前へ変える | `old` のすべての置き場の旧い錠を、呼び出し元が `hold_legacy_locks` で持っている | 名前を変える前にすべての退避先が無いことを確かめ、1 つでもあれば何も変えずに `IdentityError`（`retire_target_exists`） | ディレクトリの名前の変更 |
| `_prepare` | `plugins/ndf-next/src/ndf/cli.py` | 変更 | `_prepare(ident: PluginIdentity) -> int \| None` | 状態の置き場を用意し、止まったら例外の理由で止まった結果を出す | 無し | 止まった結果の `kind` は `IdentityError.kind`、`items` は退避した置き場と止まった理由のパス | 標準エラーへの書き込み |
| `_retire_store` | `plugins/ndf-next/src/ndf/core/durable.py` | 変更 | `_retire_store(root: Path, found: int, now: datetime) -> Path` | チェックアウトの置き場を P1・P2・P6 で退避する（`found` が 0 なら `identity.retired_name` へ None を渡す）。旧い錠の確かめから名前の変更と `format.json` の書き込みまでを、1 つの `hold_legacy_locks` の `with` の中で行う | 置き場の排他を持っている | 旧い錠が取れなければ `StoreLegacyRunning`、退避先があれば `StoreRetireTargetExists` で、どちらも何も変えていない | ディレクトリの名前の変更・`format.json` の書き込み |
| `install` | `plugins/ndf-next/src/ndf/domains/relay/install.py` | 変更 | `install(environ: Mapping[str, str], plugin_root: Path, missing_code: int, replace_foreign: bool, version: str) -> Installed` | ランチャーと `relay.root` を置き、置き換えたときは `relay.version` に `version` を書く（規則 P3） | `version` は `plugin.json` の `version` | ランチャーを書いたときに限り `relay.version` の中身が `version` と改行 | ファイルの書き込み（`relay.py`・`relay.root`・`relay.version`・`ndf-relay`） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | 引数の誤り | 知らない副命令・`--tree` の値 | `argparse` | 止める | 使い方を標準エラーへ・終了コード 2 | 何も変えていない。直して打ち直せる |
| `collect_refs` | git を読めない | worktree の外で打った・git が無い | `subprocess.run(check=True)` の例外 | 呼び出し元へ返す（`main` が 2 で止める） | `{"status": "stopped", "summary": "git ls-files を打てない: ..."}`・終了コード 2 | 何も変えていない。根を直して打ち直せる |
| `collect_refs` | ファイルを読めない | 追跡されているのに worktree に無い・UTF-8 でない | ファイルの読み込みの例外 | 既定値で続ける（そのファイルを飛ばし、`warnings` に載せる） | 結果の `warnings` にパス。終了コードは判定どおり | 何も変えていない |
| `load_table` | 表を読めない・形が違う | JSON の構文の誤り・判定の語が違う・判定に要る値が無い | `json.loads` と行の形の確かめ | 呼び出し元へ返す（`main` が 2 で止める） | `stopped` と行の番号と欠けた値・終了コード 2 | 何も変えていない。表を直して打ち直せる |
| `check_tree` | 参照先が実在しない | 表に無い参照を足した・表の行き先が無い・「未作成」の行き先ができたのに表が古い・`from` が参照を持つファイルに無い | 判定の表（「処理の手順」） | 指摘として返す（`main` が 1 で止める） | `{"status": "failed", "items": [{"rule": ..., "file": ..., "ref": ...}]}`・終了コード 1 | 何も変えていない。表か参照を直して打ち直せる |
| `command_exists` | エントリポイントを起動できない | uv が入れられない・依存を解決できない（`bin/ndf` が `deps_missing` で止まる） | `bin/ndf` の終了コードが副命令の誤り（2）以外の 0 でない値 | 止める（判定せずに `main` が 2 で止める） | `stopped`・`bin/ndf` の標準エラーの最後の行・終了コード 2 | 一時の状態の置き場を消す。環境を直して打ち直せる |
| `apply_table` | 表と worktree が合わない | 「向け直す」の行の `from` がファイルに無い・「ファイルを消す」の行のファイルが無い・「未作成」の行が残っている | 書き込みの前の確かめ | 止める（1 つも書かない） | `failed` と合わない行・終了コード 1 | 何も変えていない。表を直して打ち直せる |
| `apply_table` | 書き込みの途中で失敗 | 書き込みの権限が無い・ディスクが満杯 | 書き込みの例外 | 止める | `stopped` と書き終えたファイルの一覧・終了コード 2 | 書き終えたファイルは `git checkout -- <パス>` で戻して打ち直す |
| `lock_held` | 錠のファイルを開けない | 権限が無い | `os.open` の例外（無いファイルは偽で返す） | 呼び出し元へ返す（`OSError`） | 呼び出し元の行のとおり | 何も変えていない |
| `hold_legacy_locks` | 錠のファイルを開けない | 権限が無い | `os.open` の例外（無いファイルは除く） | 呼び出し元へ返す（`IdentityError`。理由 `config_broken`） | `prepare_state_dir` の行と同じ | 何も変えていない |
| `prepare_state_dir` | 旧いラッパーが動いている | 10 系のラッパー・リレーハブ・耐久の実行が錠を持つ | `hold_legacy_locks` | 止める | `{"status": "stopped", "kind": "store_in_use", "items": [{"reason": "legacy_running", "path": "<錠>"}]}`・`store_in_use` の終了コード | 何も変えていない。10 系を止めてから打ち直せる（入れ替えの手順 U1） |
| `prepare_state_dir` | 退避先が既にある | 同じ日に退避した後に、10 系がまた置き場を作った | `_retire_all` の名前を変える前の確かめ | 止める | `stopped`・`precondition`・`items` に `{"reason": "retire_target_exists", "path": "<退避先>"}`・`precondition` の終了コード | 何も変えていない。人が 2 つの置き場を確かめ、片方を動かしてから打ち直す |
| `_retire_all` | 名前を変えられない | 権限が無い・別のファイルシステム | `Path.rename` の例外 | 止める（今と同じ） | `config_broken` と退避できた元と先 | 退避できた置き場は退避先に残る。原因を直して打ち直せる |
| `_prepare` | 置き場を用意できない | `prepare_state_dir` の上の 3 行 | `IdentityError` と `OSError` | 止める | 例外の `kind` で止まった結果。`OSError` は `config_broken` | 上の行のとおり |
| `_retire_store` | 旧い錠が取れない | 10 系の耐久の実行（`dbos/*.lock`）か進行表の DB の錠を持つプロセスが居る | 錠を待たずに取る | 止める（待ち直さない。`prepare_store` は `StoreLegacyRunning` を再試行しない） | `stopped`・`store_in_use`・`items` に `legacy_running` と錠のパス | 何も変えていない。10 系の実行を終えてから打ち直せる |
| `_retire_store` | 退避先が既にある | 同じ日の 2 回目の退避 | 名前を変える前の確かめ | 止める | `stopped`・`precondition`・`items` に `retire_target_exists` と退避先 | 何も変えていない。人が確かめてから打ち直す |
| `install` | ファイルを書けない | 設定ディレクトリの権限 | `signals.replace_text` の例外 | 呼び出し元へ返す（今と同じ） | `relay install` の止まった結果（今と同じ） | 一時のファイルは同じ置き場に残る場合がある。打ち直せる（書き込みは置き換えで冪等） |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check_tree`（`current`） | 参照先が今のツリーの `plugins/ndf/` の下に実在する | 指摘なし |
| `check_tree`（`current`） | 参照先が実在しない | `missing` |
| `check_tree`（`swapped`） | 参照の組がどの表の行にも当たらず、参照先が `plugins/ndf-next/` の下に実在する | 指摘なし（「同じパス」） |
| `check_tree`（`swapped`） | 参照の組がどの表の行にも当たらず、参照先が実在しない | `unlisted` |
| `check_tree`（`swapped`） | 表の行の `from` が、その行のファイルに 1 か所も無い | `stale_row`（表が古い） |
| `check_tree`（`swapped`） | 「向け直す」の行で、行き先が実在する | 指摘なし |
| `check_tree`（`swapped`） | 「向け直す」の行で、行き先が実在しない | `target_missing` |
| `check_tree`（`swapped`） | 「未作成」の行で、行き先が実在しない・`strict` が偽 | 既知の失敗として `metrics.unbuilt` に数え、指摘にしない |
| `check_tree`（`swapped`） | 「未作成」の行で、行き先が実在しない・`strict` が真 | `unbuilt` |
| `check_tree`（`swapped`） | 「未作成」の行で、行き先が実在する | `unbuilt_exists`（行を「向け直す」へ書き換える） |
| `check_tree`（`swapped`） | 「消す」「ファイルを消す」の行 | 指摘なし（入れ替えの PR が消す。消し忘れは入れ替えの後の `current` が `missing` で落とす） |
| `apply_table` | 「未作成」の行が 1 つでもある | 何も変えずに `unbuilt` |
| `apply_table` | すべての行の確かめが通る | 「向け直す」を置き換え、「ファイルを消す」を消し、「消す」の行を `items` に `manual` で並べる |
| `prepare_state_dir` | 退避する旧い置き場が無い | `format.json` を置いて返す（今と同じ） |
| `prepare_state_dir` | 退避する置き場があり、ほかの根から呼ばれた | `config_broken` で止まる（今と同じ） |
| `prepare_state_dir` | 退避する置き場があり、旧い錠が 1 つでも取れない | `legacy_running` で止まる |
| `prepare_state_dir` | 退避する置き場があり、錠はすべて取れ、退避先がある | `retire_target_exists` で止まる |
| `prepare_state_dir` | 退避する置き場があり、錠はすべて取れ、退避先が無い | 名前を変えて `format.json` を置く |

- 参照の組が表の行に当たるとは、組のファイルが行の `file` と同じで、組のパスが行の `from` の中に含まれることを指す。行番号では当てない（ほかの PR が行を足すと番号がずれ、関わりの無い PR が落ちるため）
- 繰り返しの終わる条件: `collect_refs` は追跡されたファイルの一覧を 1 回だけ回る。`prepare_store` の再試行は今と同じ `PREPARE_ATTEMPTS` 回で、新しい 2 つの例外は再試行しない
- 計算量の注意: `command_exists` は 1 回 0.4 秒ほど（2026-11-17 に `plugins/ndf-next/bin/ndf release-steps approval-check --help` で 0.37 秒）。副命令の語の並びごとに 1 回だけ打ち、表の行き先の並びは 30 通りほどなので、検査全体で 15 秒ほどに収まる見込み（未確認。R0 の PR の CI で測る）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `SCAN_PATHSPEC` | 定数 | `scripts/check-plugin-refs.py` | [issue-3341-design-refs.md](issue-3341-design-refs.md) の 147 組を数えるコマンドの pathspec と同じ並び（`.github`・`.ndf`・指示書 6 本・`pyproject.toml`・`conftest.py`・`plugins/mcp`・`tests`・`scripts/check-*`・`scripts/lib`・`scripts/contracts_lib`・`scripts/structure_checks`・`scripts/token-usage.py`・`scripts/validate-runtime-plugins.sh`、除外 `.ndf/project.json`）。表の文書のコマンドはこの定数をコピーしたもので、正本はこの定数 |
| `REF_PATTERN` | 定数 | `scripts/check-plugin-refs.py` | `plugins/ndf/[A-Za-z0-9_./*{},-]*`（末尾の `.` と `,` を除く） |
| `SELF_FILES` | 定数 | `scripts/check-plugin-refs.py` | `scripts/check-plugin-refs.py`・`scripts/tests/test_check_plugin_refs.py`（検査が自分の定数を参照として数えない） |
| `TABLE_VERDICTS` | 定数 | `scripts/check-plugin-refs.py` | `redirect`（向け直す）・`unbuilt`（未作成）・`drop`（消す）・`delete_file`（ファイルを消す） |
| `Row` | 型 | `scripts/check-plugin-refs.py` | `id`（`R9` など設計の表の番号）・`file`・`verdict`・`from`（置き換える前の文字列）・`to`（`redirect` で必須。置き換えた後の文字列）・`target`（`redirect` と `unbuilt` で必須。`{"path": "<plugins/ndf/ からの相対>"}` か `{"command": ["release-steps", "publish"]}`）・`feature`（`unbuilt` で必須。機能の対応表の行の `id`） |
| `Ref` | 型 | `scripts/check-plugin-refs.py` | `file`・`path` |
| `Finding` | 型 | `scripts/check-plugin-refs.py` | `rule`・`file`・`ref`・`row`（表の行の `id`。当たらなければ None） |
| `plugin-refs-swap.json` | 設定値 | `scripts/plugin-refs-swap.json` | `{"summary": ..., "rows": [<Row>, ...]}`。94 行（向け直す 73・未作成 9・消す 8・ファイルを消す 4） |
| `LEGACY_FORM` | 定数 | `plugins/ndf-next/src/ndf/identity.py` | `10`。`RETIRED_SUFFIX` はこの値から作り（`f".v{LEGACY_FORM}-"`）、別の値を持たない |
| `LEGACY_LOCKS` | 定数 | `plugins/ndf-next/src/ndf/identity.py` | 状態のホーム: `relay/*/relay.lock`・`relay/hub/hub.lock`。チェックアウトの置き場の錠は `durable.py` の今の一覧（`dbos/*.lock`・`progress.sqlite.lock`）をそのまま使う |
| `VERSION_FILE` | 定数 | `plugins/ndf-next/src/ndf/domains/relay/install.py` | `relay.version`（10 系の `version_dir.py` の `copy_version_path` と同じ置き場と名前） |
| 止まる理由 | 結果 JSON の値 | 既存の `contracts/causes.json` | 旧いラッパーが動いている → `store_in_use`、退避先がある → `precondition`。どちらも `items[].reason` に `legacy_running`・`retire_target_exists` を載せる。理由の表に行を足さない |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 新しい NDF の 2 つのコマンドが同時に状態の置き場を用意する | 状態のホーム `~/.local/state/ndf` | 今と同じ隣の錠（`ndf.lock`）の排他の下で、錠の確かめ・退避先の確かめ・名前の変更・`format.json` を行う | 後の処理は先が置いた `format.json` を見て退避しない。止まった処理は何も変えないため、打ち直せる |
| 新しい NDF の置き場の用意と、10 系のラッパー・リレーハブ | 旧い置き場の `relay/*/relay.lock`・`relay/hub/hub.lock` | 待たずに排他で取りにいき、すべて取れたら、名前の変更と新しい置き場の `format.json` の書き込みが終わるまで持ち続ける（隣の錠も同じ間持つ） | 錠を持つ間に起動した 10 系は錠を取れず、退避する置き場へ書かない（不変条件 I3）。10 系の錠は `filelock` で、待つ間も試すたびにパスから錠のファイルを開き直すため、錠を放した後に取れる錠は新しい置き場のものである。その 10 系の記録は新しい置き場だけへ書かれ、退避先と新しい置き場へ分かれない |
| 新しい NDF の耐久の実行と、10 系の耐久の実行 | チェックアウトの置き場の `dbos/*.lock`・`progress.sqlite.lock` | 置き場の排他を取ったうえで、旧い錠を待たずに取る。取れなければ待ち直さない。すべて取れたら、名前の変更と新しい置き場の `format.json` の書き込みが終わるまで持ち続ける | 止まった処理は何も変えない。10 系の実行が終われば打ち直せる。錠を持つ間に起動した 10 系の耐久の実行は、上の行と同じ理由で新しい置き場だけへ書く |
| 10 系と 11.0 の SessionStart | 共有の設定ディレクトリの `relay.py`・`relay.version` | 錠を取らない。`relay.version` の版の大小で 10 系が置き直さない（規則 P3） | `install` の書き込みは一時のファイルからの置き換えで、同じ中身を何度書いてもよい |
| `check-plugin-refs.py apply` と、ほかの作業 | worktree のファイル | 取らない（入れ替えの PR の作業ディレクトリで 1 回打つ） | 確かめが通るまで何も書かない。途中で落ちたら書いたファイルを戻して打ち直す |
| `command_exists` の `bin/ndf` と、利用者の新しい NDF | 状態の置き場 | `NDF_STATE_HOME` を検査ごとの一時のディレクトリへ向け、利用者の置き場に触れない | 一時のディレクトリは検査の終わりに消す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `main` | 指摘の一覧・モード・既知の失敗の数（`metrics.unbuilt`）・`apply` の書き換えたファイルと手で直す行 | 結果 JSON（標準出力の 1 行） | 無し（秘密を扱わない） |
| `command_exists` | 起動できなかったときの `bin/ndf` の標準エラーの最後の行 | 結果 JSON の `summary` | 環境変数の値 |
| `prepare_state_dir` | 止まった理由と、取れなかった錠のパスか退避先のパス | `log.warning` と、`cli._prepare` が出す結果 JSON の `items` | 錠のファイルの中身（ホスト名・pid） |
| `_retire_store` | 同上と、退避した置き場の待ちを抱えた実行の数（今と同じ） | `log.warning` と結果 JSON の `items` | 耐久の記録の中身 |
| `install` | 書いた版 | 結果 JSON の `items`（`relay install` の今の出力に `relay.version` の行を足す） | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `main` | `scripts/check-markdown-links.py` の `main` | 読んだ（Markdown のリンクだけを見る。設計文書の LIB2） | 使えない理由: workflow の `run:` のコマンドと JSON の値のパスを見ない。文書のリンクは引き続きそちらが見る |
| `collect_refs` | `scripts/check-markdown-links.py` の `_git_markdown_files` | `grep -n "^def " scripts/check-markdown-links.py` | 使えない理由: `*.md` に絞った一覧を返す。pathspec を受けない |
| `load_table` | `scripts/lib/allowlist.py` | `ls scripts/lib` | 使えない理由: 検査の例外の一覧の形で、判定ごとの値の確かめを持たない |
| `check_tree` | lychee（設計文書の LIB1） | 設計文書の「外部の調査」 | 使えない理由: 入れ替えの後のツリーでの読み替えを持たない |
| `target_exists` | `pathlib.Path.glob` | 標準ライブラリ | 使えない理由: `{a,b}` の形を展開しない。最初の glob の文字より前のディレクトリの実在で判定する |
| `command_exists` | 新しい NDF の `cli.py` の `COMMANDS` | `sed -n 28,62p plugins/ndf-next/src/ndf/cli.py` | 使えない理由: 1 段目の副命令しか持たず、`records glossary check` のような 2 段目以降を判定できない（2026-11-17 に `bin/ndf records glossary check --help` が終了コード 2） |
| `apply_table` | `sed -i` | 無し | 使えない理由: 書き込みの前にすべての行を確かめて 1 つも書かずに止まる形を持たない |
| `retired_name` | `identity._retire_candidates`・`durable_sweep._free_name` | `grep -rn "_free_name\|_retire_target" plugins/ndf-next/src` | 使えない理由: どちらも退避先があれば名前を足して続ける（規則 P2 と逆）。`identity` の 2 つは消し、`_free_name` は鍵ごとのファイルの片付け（`durable_sweep`）だけが使い続ける |
| `lock_held` | `domains/relay/registry.lock_busy` | `grep -rn "def lock_busy" plugins/ndf-next/src` | 使う（本体を移す）。`registry` は `identity` より上の層にあり、`identity` から読むと依存が逆向きになるため、本体を `identity` へ移して `registry.lock_busy` から呼ぶ |
| `hold_legacy_locks` | `identity.lock_held`・`core/durable_locks.try_flock` | `grep -rn "def try_flock" plugins/ndf-next/src` | `lock_held` を使う。`try_flock` は開いたファイルを受ける形で、`core` にあり `identity` から読めない |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `collect_refs` | 一時のリポジトリの workflow とテストから参照の組を集める | 同じファイルで同じパスを 2 行で名指すと 1 組。末尾の `.` を除く。`SELF_FILES` を数えない | 追跡されたファイルが worktree に無いと `warnings` に載せて続ける。git の外で打つと終了コード 2 |
| `load_table` | 4 つの判定の行を読む | 行が 0 件の表 | 判定の語が違う・`redirect` に `to` が無い・`unbuilt` に `feature` が無いと終了コード 2 |
| `check_tree` | `current` で全参照が実在し 0 | 「未作成」の行が残ると `swapped` は 0、`--strict` は 1 | 表から 1 行消すと `unlisted`。workflow に `plugins/ndf/scripts/` の参照を足すと `unlisted`。行き先を作って表を書き換えないと `unbuilt_exists`。`from` を消すと `stale_row`。行き先を実在しないパスにすると `target_missing` |
| `target_exists` | 実在するファイルとディレクトリ | `plugins/ndf/**`・`plugins/ndf/{a,b}/x` は最初の glob の前のディレクトリで判定する | 実在しないパスで偽 |
| `command_exists` | 実在する副命令で真 | 同じ語の並びを 2 回渡すと `bin/ndf` は 1 回だけ打たれる | 知らない副命令で偽。`bin/ndf` が依存の欠けで止まると終了コード 2。利用者の状態の置き場に何も作らない |
| `apply_table` | 「向け直す」を置き換え、「ファイルを消す」を消す | 「消す」の行は書き換えず `manual` に並ぶ | 「未作成」が残ると何も変えずに 1。`from` が無い行があると何も変えずに 1 |
| `retired_name` | `format.json` の無い置き場で `ndf.v10-20261117` | `form` が 1 なら `.v1-`。日付をまたぐ時刻でも UTC の日付 | 無し |
| `lock_held` | 誰も持たない錠で偽 | ファイルが無いと偽で、ファイルを作らない | 別のプロセスが持つと真 |
| `hold_legacy_locks` | 錠を持つプロセスが居なければ空 | 錠のファイルが無い置き場で空。錠のファイルを作らない | 別のプロセスが `relay/<名前>/relay.lock` か `relay/hub/hub.lock` を持つとそのパスを返す |
| `prepare_state_dir` | 旧い置き場を `ndf.v10-<日付>` へ退避する | `config-state` だけがある利用者で何も退避しない | 錠を持つプロセスが居ると `legacy_running` で止まり何も変えない。退避先があると `retire_target_exists` で止まり何も変えない。名前を変えられないと `config_broken` |
| `_prepare` | 退避した元と先を標準エラーに出す | 退避が無いとき何も出さない | `IdentityError.kind` が結果の `kind` になる |
| `_retire_store` | `format.json` の無い置き場を `state.v10-<日付>` へ退避する | `_read_format` が 0（`format.json` が無いか読めない）なら `.v10-`。番号は 1 から始まるため、0 を形の版として使わない | 10 系の `dbos/*.lock` を持つプロセスが居ると `StoreLegacyRunning` で、`prepare_store` が再試行しない。退避先があると `StoreRetireTargetExists` |
| `install` | ランチャーを書いたとき `relay.version` が自分の版 | 印の無い 10 系の複製を `replace_foreign` が偽で残すとき `relay.version` を書かない | 書けないと今と同じ止まり方 |
