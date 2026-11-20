# #3909 の詳細設計: 外で上がった導入を読み、子とラッパーを今の導入のバージョンで動かす

設計文書 [issue-3909-design.md](./issue-3909-design.md) の構成要素と決定を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 4 つ（`same_cache_rows`・`with_launched_ndf`・`installed_entry` と、`entry()` から切り出す内部の関数 `_fallback_entry`）あるので別ファイルにした。シグネチャを変える既存の関数は無い。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設の関数を持つ）:

```text
plugins/ndf/
├── src/ndf/
│   ├── adapters/claude.py                 * same_cache_rows（新設）
│   ├── domains/relay/
│   │   ├── plugin_update.py               find_origin（変更）・MANY_CACHED（新設の定数）
│   │   ├── session_env.py                 * with_launched_ndf（新設）
│   │   └── wrapper.py                     _before_start・_start・_log_update（変更）
│   └── progress/lane/launch.py            * installed_entry・_fallback_entry（新設）・entry・spawn（変更）
├── skills/development-workflow/references/relay.md   記録の読み方・自動更新の止め方・確かめる手順
└── tests/
    ├── domains/relay/test_relay_plugin_update.py
    ├── domains/relay/test_relay_parts.py
    ├── domains/relay/test_relay_wrapper.py
    └── progress/test_lane_launch.py
```

依存の向き: `plugin_update` と `launch` はどちらも `adapters.claude` の `same_cache_rows` を読み、互いを読まない。`progress` は今も `domains` を読まないので、選ぶ規則を `domains.relay` ではなく下の層（`adapters`）に置いて依存の輪を作らない。`wrapper` は `session_env` と `plugin_update` を今のとおり読む。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/adapters/claude.py` | 変更 | `claude plugin list --json` の行から、同じキャッシュの親を指す有効な NDF の行を選ぶ | `identity` | モジュール | `same_cache_rows`（新設） |
| `plugins/ndf/src/ndf/domains/relay/plugin_update.py` | 変更 | 導入元を、根と一致する行 → 同じキャッシュの親の行の順で選ぶ | `adapters.claude` | モジュール | `find_origin`（変更）、定数 `MANY_CACHED`（新設） |
| `plugins/ndf/src/ndf/domains/relay/session_env.py` | 変更 | 子の `PATH` から別のバージョンの NDF の `bin` と仮想環境の `bin` を外し、起動先の `bin` を先頭に置く | — | モジュール | `with_launched_ndf`（新設） |
| `plugins/ndf/src/ndf/domains/relay/wrapper.py` | 変更 | 起動先の根を覚えて子の `PATH` を整え、記録にラッパーのバージョンと外で上がったかの値を足す | `session_env`・`plugin_update` | `Wrapper` | `_before_start`・`_start`・`_log_update`（変更） |
| `plugins/ndf/src/ndf/progress/lane/launch.py` | 変更 | `relay spawn` を今の有効な導入の `bin/ndf` で打つ | `adapters.claude`・`identity` | モジュール | `installed_entry`・`_fallback_entry`（新設）、`entry`・`spawn`（変更） |
| `plugins/ndf/skills/development-workflow/references/relay.md` | 変更 | 記録の `wrapper` のキーと区別の `jq`、自動更新の止め方・戻し方、確かめる手順 | — | — | — |

## 同じキャッシュの導入を選ぶ規則

満たす決定: 決定 1・決定 4、受け入れ条件 1・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `same_cache_rows` | `plugins/ndf/src/ndf/adapters/claude.py` | 新設 | `same_cache_rows(listed: Sequence[Mapping[str, Any]], root: Path, environ: Mapping[str, str]) -> list[Mapping[str, Any]]` | 呼び手の根（`root`）がキャッシュのバージョンのディレクトリであるときだけ、有効な `ndf@` の行のうち同じキャッシュ・同じマーケットプレイスのバージョンのディレクトリを指す行を、一覧の順のまま返す。根がバージョンのディレクトリでなければ空 |
| `find_origin` | `plugins/ndf/src/ndf/domains/relay/plugin_update.py` | 変更 | 変えない（`environ` は `Updater.env` を渡す） | 根と一致する行があれば今のとおり。無ければ `same_cache_rows` の結果がちょうど 1 件のとき、その行を導入元にする |

- `same_cache_rows`
  - 事前条件: `listed` は `list_plugins` が返した並び（`None` は呼び手が先に扱う）。`environ` は一覧を読んだのと同じ環境
  - 事後条件: 次の 3 つをすべて満たす行だけを返す。どれかが欠ければその行は返さない
    1. 行の条件: `enabled` が真・ID が `ndf@<マーケットプレイス>` の形・`scope` と `installPath` が文字列
    2. キャッシュの根の条件: キャッシュの根 `C` を `<environ の CLAUDE_CONFIG_DIR か $HOME/.claude>/plugins/cache` を解決したパスとする（Claude Code の導入の一覧が `installPath` に書く保存先。2026-11-20 の手元で `~/.claude/plugins/cache/ai-plugins/ndf/11.0.10`）。行の `installPath` を解決したパス `P` が `C/<その行のマーケットプレイス>/ndf/<名前>` の形（`P.parent.parent.parent == C`・`P.parent.parent.name` がマーケットプレイス・`P.parent.name == "ndf"`）
    3. バージョンのディレクトリの条件: 根を解決したパス `R` が `P` と同じ親（`R.parent == P.parent`）を持ち、`R.name` が `R` の `.claude-plugin/plugin.json` の `version` と等しい（Claude Code はバージョンのディレクトリをバージョンの名前で作る）。`plugin.json` が読めなければ満たさない
  - 外す配置の例: 実際のキャッシュの `ai-plugins/ndf/` 直下に置いた開発のチェックアウト `dev` を `--plugin-dir` で起動したラッパーは、親は一覧の行と同じでも `dev` が `plugin.json` の `version` と違うので 3 を満たさず、空になる（#3812 の受け入れ条件 6 を保つ）。キャッシュの外のチェックアウト（`tmp_path/"dev"` の形）と、`CLAUDE_CONFIG_DIR` の違う別の保存先の行は 2 を満たさない
  - 代償: キャッシュの中に、ディレクトリの名前を自分の `plugin.json` の `version` とそろえて置いた開発のチェックアウトは、Claude Code の導入と区別できない（Claude Code の保存先を手で作る形で、利用者の通常の配置ではない）
  - 副作用: 根の `plugin.json` を 1 回読む（`identity.version_of`）。ほかは `Path.resolve()` だけ
- `find_origin` の変える前と後

| 一覧の形 | 今 | 直した後 |
| --- | --- | --- |
| 根と一致する有効な行が 1 件 | その行 | 同じ |
| 根と一致する行が 2 件以上 | `SHARED_PATH` | 同じ |
| 一致は 0 件、同じ親の有効な行が 1 件 | `NO_MATCH` | その行を導入元にする |
| 一致は 0 件、同じ親の有効な行が 2 件以上 | `NO_MATCH` | `MANY_CACHED` |
| 一致も同じ親の行も 0 件（キャッシュの外・別のマーケットプレイス） | `NO_MATCH` | 同じ |
| 一覧が読めない | `LIST_UNREADABLE` | 同じ |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `find_origin` | 同じ親の有効な行が 2 件以上 | user と project のスコープで同じマーケットプレイスの NDF が別のバージョンで有効 | `find_origin` | 理由の文を返す（`Updater` が `skipped` にする） | `log.jsonl` の `skipped` の行（`reason` が `MANY_CACHED`）。端末には出さない | 次のカットポイントで読み直す |
| `same_cache_rows` | `installPath` が解決できない | パスが消えた | `Path.resolve()`（`strict=False` なので例外にならない） | 一致しない行として扱う | 無し | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `MANY_CACHED` | 定数 | `plugins/ndf/src/ndf/domains/relay/plugin_update.py` | `"同じマーケットプレイスのキャッシュを指す有効な NDF の導入が複数ある"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `same_cache_rows` | `plugin_update._is_ndf` | `grep -n "_is_ndf" plugins/ndf/src -r` | 有効な NDF の行の判定は同じ。`_is_ndf` を `adapters.claude` へ移して両方から使う（`progress` から `domains` を読まないため） |
| `same_cache_rows` | `launch.entry` の「同じ親の下のバージョン」 | `launch.py:69-83` を読んだ | 親の下のディレクトリを mtime で選ぶ規則で、導入の一覧を読まない。使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `same_cache_rows` | 同じ親のバージョン M の行を返す | 根と同じバージョンの行も返す（一致する行は除かない）。無効な行・`ndf@` でない行は返さない | 別のマーケットプレイスの親・キャッシュの外の根で空。`CLAUDE_CONFIG_DIR` を `tmp_path` の下に向けた実際のキャッシュの形（`<C>/ai-plugins/ndf/N`・`<C>/ai-plugins/ndf/M`）で、根を同じ親の `<C>/ai-plugins/ndf/dev`（`plugin.json` の `version` は N）にすると空。行が `C` の外の同じ形（`<別の場所>/ai-plugins/ndf/M`）を指しても空 |
| `find_origin` | 一致が無く同じ親の 1 件で導入元が決まる | 一致する行と同じ親の別のバージョンの行が両方あるとき一致する行が勝つ | 同じ親に 2 件で `MANY_CACHED`、キャッシュの外の根で `NO_MATCH`。キャッシュの `ai-plugins/ndf/dev` から動くラッパーの `Updater` は `NO_MATCH` の `skipped` で、更新のコマンドを 1 つも打たない |

## 子の `PATH` を起動先のバージョンにそろえる

満たす決定: 決定 3、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `with_launched_ndf` | `plugins/ndf/src/ndf/domains/relay/session_env.py` | 新設 | `with_launched_ndf(env: Mapping[str, str], keep: Path) -> dict[str, str]` | `env` の `PATH` から、`keep` と同じ親の下の `keep` 以外の根の `bin` と、NDF の仮想環境の保存先の下の要素を外し、先頭に `keep` の `bin` を置いた環境を返す |
| `Wrapper._before_start` | `plugins/ndf/src/ndf/domains/relay/wrapper.py` | 変更 | 変えない | 今の振る舞いに加え、更新・確認の結果の起動先の根（`got.target.root`）を `self.launched_root` に覚える。手が無い・起動先が分からなければ `None` |
| `Wrapper._start` | `plugins/ndf/src/ndf/domains/relay/wrapper.py` | 変更 | 変えない | 子の環境を作った後、`self.launched_root` が `None` でなければ `with_launched_ndf` を通す |

- `with_launched_ndf`
  - 事前条件: `keep` は起動先の導入の根（`installPath`。例 `<キャッシュ>/ai-plugins/ndf/M`）
  - 事後条件: `PATH` の各要素 `p` を解決したパスを `q` とし、次のどちらかに当たる要素を外す。ほかの要素は順と重なりを保つ
    - (a) `q.name == "bin"` で、その親 `r = q.parent`（候補の導入の根。例 `<キャッシュ>/ai-plugins/ndf/N/bin` なら `N`）について `r.parent == keep.resolve().parent` かつ `r != keep.resolve()`。`<キャッシュ>/ai-plugins/ndf/N/bin` は `r.parent` が `ndf` で `keep.resolve().parent`（`ndf`）と等しいので外れ、`<キャッシュ>/ai-plugins/ndf/M/bin` は `r == keep.resolve()` なので残る
    - (b) `${XDG_CACHE_HOME:-$HOME/.cache}/ndf/venv/` の下（`env` の `XDG_CACHE_HOME`・`HOME` で解く）
  - 続けて、`keep/bin/ndf` が実行できれば `keep/bin` を `PATH` の先頭に置く（同じ要素が後ろに残っても外さない）。実行できなければ先頭に置かない（外すだけ）
  - 先頭に置く理由: `bin/ndf` が支える「`PATH` の上に置いたシンボリックリンク」（例 `~/.local/bin/ndf` → `<キャッシュ>/ai-plugins/ndf/N/bin/ndf`）は (a) に当たらない。そのディレクトリを外すとほかのコマンド（同じ `~/.local/bin` の `ndf-relay` など）まで使えなくなり、残すと子の claude が末尾に M の `bin` を足しても `command -v ndf` が N のリンクを返す。`keep/bin` を先頭に置けば、`~/.local/bin` は残したまま `ndf` が M に解決する。`keep/bin` にあるのは NDF 自身のコマンド（`ndf`・`ndf-claude`）だけなので、先に置いて隠れるのは同じプラグインの別のバージョンのコマンドだけである
  - `PATH` が無ければ `env` をそのまま写して返す
  - 副作用: 無し（`Path.resolve()` と実行できるかの確かめだけ）
- 仮想環境の保存先は `bin/ndf` の `cache_home="${XDG_CACHE_HOME:-$HOME/.cache}/$name"` と同じ規則で解く（`plugins/ndf/bin/ndf` の 41 行）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `with_launched_ndf` | `HOME` も `XDG_CACHE_HOME` も無い | 最小の環境で起動した | 関数の中 | (b) を外さず (a) と先頭に置くことだけ行う | 無し | — |
| `with_launched_ndf` | `keep/bin/ndf` が実行できない | 入れ替えの途中 | 関数の中 | 先頭に置かず (a)・(b) だけ行う | 無し | 次のセグメントで作り直す |
| `Wrapper._start` | 起動先が分からない | 一覧が読めない・`skipped` | `self.launched_root` が `None` | `PATH` を変えずに起動する（今の振る舞い） | 無し | 次のセグメントで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `with_launched_ndf` | `<キャッシュ>/ai-plugins/ndf/N/bin` と仮想環境の `bin` が外れ、先頭が `<キャッシュ>/ai-plugins/ndf/M/bin`、ほかの要素は順のまま残る。実際のコマンド解決: `tmp_path` に N と M の `bin/ndf`、`local/bin/ndf` → N の `bin/ndf` のシンボリックリンク、`local/bin/other` を作り、返した環境で `sh -c 'command -v ndf; command -v other'` を打つと `ndf` は M の `bin/ndf`、`other` は `local/bin/other` を返す | 同じ要素が 5 回重なっても全部外れる。別のマーケットプレイスの NDF の `bin` は残る。`PATH` の要素が N の `bin` へのシンボリックリンクでも外れる | `PATH` が無い・`HOME` が無い・`keep/bin/ndf` が実行できない（先頭に置かず、`local/bin/ndf` のリンクが解決先のまま） |
| `Wrapper._start` | 起動先が M のとき `Spawner` が受けた `env["PATH"]` にバージョン N の `bin` が無い | `--resume` の確認の手でも起動先を覚える | 起動先が分からなければ `PATH` が変わらない |

## 記録にラッパーのバージョンを足す

満たす決定: 決定 2、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._log_update` | `plugins/ndf/src/ndf/domains/relay/wrapper.py` | 変更 | 変えない | `plugin_update` の行に `wrapper`（`self.s.version`）と `outside` を足し、書いた後に `self.known_version` を `launched`（null なら変えない）へ進める。ほかのキーと値は変えない |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Wrapper.known_version` | 属性（`Optional[str]`） | `plugins/ndf/src/ndf/domains/relay/wrapper.py` | ラッパーを作ったときの `self.s.version` |
| `Wrapper.launched_root` | 属性（`Optional[Path]`） | `plugins/ndf/src/ndf/domains/relay/wrapper.py` | `None` |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._log_update` | `{"kind": "plugin_update", "result", "from", "to", "launched", "wrapper", "outside", "reason", "seconds"}` | `log.jsonl` | 導入先のパス（今の決まりのまま `reason` に含めない） |

`outside` の決め方: 外で上がりうる時点は 2 つある。前の行から更新のコマンドまでの間と、更新のコマンドから起動先の確認（`confirm()`）までの間である。2 つの比べを行う。

| 比べ | 比べる値 | 比べられるとき |
| --- | --- | --- |
| 前の比べ | 前に知っていたバージョン `self.known_version`（ラッパーを作ったときは `self.s.version`）と、この行の更新の前のバージョン（`from`。`from` が null の行なら `launched`） | 両方が null でない |
| 後の比べ | 更新のコマンドが返した `to` と、その後の確認で読んだ `launched` | 両方が null でない（`to` が null の行、つまり更新しない手・失敗の行では比べない） |

どちらかの比べで違えば真。比べられた比べがすべて同じなら偽。どちらの比べもできなければ null。`from` が null の行で `launched` を前の比べに使ったときは、後の比べはしない（同じ値を 2 度比べない）。

例: 既知が N のとき、更新のコマンドが `up_to_date`（`from`=N・`to`=N）を返し、その直後に自動更新が M へ上げて `confirm()` が `launched`=M を返した行は、前の比べは同じ・後の比べが違うので真。`known_version` は M へ進み、次の行（`from`=M）は偽になる。

relay.md の「記録の読み方」の `plugin_update` の行の表に `wrapper` と `outside` を足し、区別の例を 1 つ足す。

```bash
cat ~/.local/state/ndf/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "plugin_update" and .outside == true) | {at, result, wrapper, from, to, launched}'
```

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Wrapper._log_update` | `updated`・`up_to_date`・`skipped`・`failed` のどの行にも `wrapper` と `outside` がある。外で上がった後の最初の行は真 | 自分で N から N+1 へ上げた次の行は偽。確認の手（`skipped`）は `launched` で比べる。既知が N で、更新の手が `up_to_date`（`from`=N・`to`=N）を返し、確認の手が M を返す順（更新のコマンドと `confirm()` の間に外で上がった）では真で、`known_version` が M へ進み、続く `from`=M の行は偽 | `from` も `launched` も null の行は null で、`known_version` は進まない |

## 耐久ワークフローがラッパーを今の導入のバージョンで起動する

満たす決定: 決定 4、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `installed_entry` | `plugins/ndf/src/ndf/progress/lane/launch.py` | 新設 | `installed_entry(cwd: Path, listed: Callable[..., Optional[list[dict[str, Any]]]] = claude.list_plugins) -> Path` | `cwd` で導入の一覧を読み、`same_cache_rows(listed, identity.ROOT, os.environ)` がちょうど 1 件で、その根の `bin/ndf` が実行できればそれを返す。ほかは `_fallback_entry()` の選んだパスを返し、一覧による選択を断念した理由と実際に選んだバージョンを標準エラーへちょうど 1 行書く |
| `_fallback_entry` | `plugins/ndf/src/ndf/progress/lane/launch.py` | 新設（内部） | `_fallback_entry() -> tuple[Path, Optional[str]]` | 今の `entry()` の選び方（自分の根が使えれば自分、消えていれば同じ親の下の最も新しい根）をそのまま行い、選んだ `bin/ndf` と、切り替えたときの注記（`バージョンの保存先 <own> が無いので`。切り替えなければ `None`）を返す。何も書かない |
| `entry` | `plugins/ndf/src/ndf/progress/lane/launch.py` | 変更 | 変えない | `_fallback_entry()` を呼び、注記があれば今と同じ 1 行（`ndf lanes: <注記> <選んだ根> のバージョンで打つ`）を標準エラーへ書く。振る舞いと出力は今と同じ |
| `spawn` | `plugins/ndf/src/ndf/progress/lane/launch.py` | 変更 | 変えない | argv の先頭を `entry()` から `installed_entry(cwd)` に替える。`stop` などほかの呼び出しは `entry()` のまま |

- `installed_entry`
  - 事前条件: `cwd` はレーンの作業ディレクトリ（project スコープの導入はここで決まる）
  - 事後条件: 返すパスは実行できる `bin/ndf`。一覧の導入が自分のバージョンと同じなら `entry()` と同じパスになる
  - 副作用: `claude plugin list --json` を 1 回打つ（環境はこのプロセスのまま）。代わりのときに標準エラーへちょうど 1 行
  - 代わりのときの 1 行: `entry()` を呼ばず `_fallback_entry()` を呼ぶので、`entry()` の切り替えの行は出ない。断念の理由・切り替えの注記・選んだバージョン（選んだ根の `plugin.json` の `version`。読めなければ根のパス）を 1 行にまとめる。形は `ndf lanes: <断念の理由>。[<注記> ]<選んだバージョン> のバージョンで打つ`
    - 例（一覧が読めず、自分の根はある）: `ndf lanes: 導入の一覧を読めない。11.0.9 のバージョンで打つ`
    - 例（一覧が読めず、自分の根が消えていて別のキャッシュを選んだ）: `ndf lanes: 導入の一覧を読めない。バージョンの保存先 <キャッシュ>/ai-plugins/ndf/11.0.9 が無いので 11.0.10 のバージョンで打つ`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `installed_entry` | 一覧が読めない | `claude` が無い・失敗・時間切れ | `list_plugins` が `None` | `_fallback_entry()` で続ける | 標準エラーに 1 行 `ndf lanes: 導入の一覧を読めない。[<注記> ]<選んだバージョン> のバージョンで打つ`（`run.log` に残る） | 次の起動で読み直す |
| `installed_entry` | 同じ親の有効な行が 0 件・2 件以上 | キャッシュの外から動く・スコープが 2 つ | `same_cache_rows` の件数 | `_fallback_entry()` で続ける | 標準エラーに件数を理由に添えた同じ形の 1 行 | 同上 |
| `installed_entry` | 選んだ根の `bin/ndf` が実行できない | 入れ替えの途中 | `_usable` | `_fallback_entry()` で続ける | 標準エラーに同じ形の 1 行 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `installed_entry` | 一覧の同じ親のバージョン M の `bin/ndf` を返す | 一覧のバージョンが自分と同じなら `entry()` と同じ | 一覧が読めない・0 件・2 件・実行できないで `entry()` と同じパスと標準エラーの 1 行。自分の根を消し、同じ親に別のバージョンの根を置いた上で一覧が読めないとき、標準エラーがちょうど 1 行で、断念の理由と選んだ別のバージョンの両方を含む |
| `entry` | 今のテストがそのまま通る（自分の根が消えたときの切り替えの 1 行も今と同じ） | — | — |
| `spawn` | `_fake_run` が受けた argv の先頭がバージョン M の `bin/ndf` | — | 一覧が読めないときの argv の先頭が自分のバージョン |

## 手順書: 自動更新の止め方と確かめる手順

満たす決定: 決定 5、受け入れ条件 7・8

relay.md の「プラグインの更新とラッパーのバージョン」の後ろに小見出しを 1 つ足し、次を載せる。書くコマンドは書く前に手元で打って結果を確かめる（設定ファイルを変える行は、確かめる人の手元で打つ）。

| 載せること | 中身 |
| --- | --- |
| 自動更新の既定と決まる順 | サードパーティのマーケットプレイスは既定で無効。設定ファイルの `extraKnownMarketplaces.<名前>.autoUpdate` → `known_marketplaces.json` の `autoUpdate` → 既定の順。公式へのリンク（設計の外部の調査の PRD1） |
| 止める | `/plugin` の Marketplaces タブで `ai-plugins` の自動更新を切る。または利用者の設定ファイルの `extraKnownMarketplaces.ai-plugins.autoUpdate` を `false` にする（設定ファイルにこの行があれば、`known_marketplaces.json` より優先する） |
| 止まったかを見る | `jq '.["ai-plugins"].autoUpdate' ~/.claude/plugins/known_marketplaces.json` と、設定ファイルの同じキーが `false` |
| 確かめる | リリースの前に止める → リリースの後、ラッパーの下でカットポイントを 1 回通す → `log.jsonl` の `plugin_update` の行が `result: updated`・`from`=旧・`to`=新・`launched`=新・`wrapper`=旧 |
| 戻す | 止めたのと同じ手段で `true` に戻し、上の `jq` で確かめる |
