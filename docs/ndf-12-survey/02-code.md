# ndf-next（11.0）のコードの方法論の測定と、12.0 で先に決めること

測った日: 2026-11-16（develop `ccd656a33`）。対象は `/work/ai-plugins/plugins/ndf-next/`、比べる相手は `/work/ai-plugins/plugins/ndf/scripts/`。
測るスクリプト（大きさ・import の向きと輪・判断のモジュールの純度）と出力は調査の作業ファイルで、リポジトリには置かない。
**[事実]** は測った値かコードの行、**[推測]** は解釈。

## 0. 結論

1. **[事実] 「方法論が無かった」は半分だけ正しい。** 層の順・判断の純度・結果 JSON・関数の大きさ・例外の扱いは
   `ndf check` が機械で強制していて、違反は 0 件（CI の `ndf-next-checks`: 「1102 ファイルに違反なし」）。
   決まっていないのは、検査に載らなかった**横断の部品**（JSON の原子的な書き込み・ロック・時計・外部コマンド・
   値の器・直列化の名前・偽物の差し込み）で、ここは 2〜5 通りに割れている。
2. **[事実] 規約の文書は無い。** `plugins/ndf-next/docs/` は生成した契約のページだけで、書き方の規約は
   `src/ndf/checks/README.md` と `rules.json`（検査の説明）にしか無い。検査に載ったものだけが揃った。
3. **[推測] OOP への統一は勧めない。** 今の作りは「値の器＋純粋な判断の関数＋副作用のアダプター（ここだけクラス）」で、
   Python の慣習（functional core / imperative shell）に合い、判断のテストは偽物なしで書ける。大きさの外れ値は
   むしろクラス側に集まる（`GitHub` 43 メソッド・`Wrapper` 30）。**今の形を規約として文書化し、検査で閉じる**方が良い。
4. **[事実] 設計と実装の食い違いが 1 つ大きい。** 依存に `pydantic` を宣言し（`pyproject.toml`）、設計
   （`issues/issue-1142-design-libraries.md:53,93`）は形の検証を pydantic に任せると書くが、`src/` での利用は 0。
   形は JSON Schema（手書き）・dataclass / NamedTuple（手書き）・手書きの取り出し（`isinstance(` 765 回）の 3 重。
5. **[推測] 12.0 で最初に決めるのは「横断の部品は 1 か所」「値の器は 1 種類で、スキーマとの正本をどちらか 1 つ」
   「差し込みは Protocol のポート＋共有の偽物」の 3 つ。** 残りは今の検査を引き継げばよい。

## 1. 層と依存の向き

### 測った値 [事実]

| 指標 | ndf-next | 旧 ndf |
| --- | ---: | ---: |
| モジュール / import の辺 | 351 / 1,707 | 414 / 1,810 |
| 層の逆向き（contracts < adapters < core < progress < domains） | 0 | （層が無い） |
| モジュールの輪（強連結成分） | 0 | 12（最大 16 モジュール） |
| 関数の中の遅延 import | 32 | — |
| `sys.path` を書き換えるファイル | 0（パッケージ） | 80 |
| `# noqa: E402`（`sys.path` の後の import） | 0 | 448 |

- パッケージ間の辺の多い順: `domains→contracts` 161・`domains→adapters` 133・`core→contracts` 60・`progress→contracts` 49・`domains→core` 34。
- ドメイン間の import は 11 組 53 本（`hooks→relay` 10・`relay→records` 9・`project→records` 8 ほか）。ドメイン間の向きの規則は無い。
- 最上位のモジュール（`cli`・`identity`）は層の表の外で、`adapters`・`core`・`contracts` から読まれる（`src/ndf/adapters/notify.py:31`・`src/ndf/core/worker.py:58`・`src/ndf/contracts/cli.py:19`）。パッケージの 2 者の輪が 1 つ（`ndf.cli` ↔ `ndf.contracts`。モジュール単位では輪にならない）。

### 判断（`*_decide.py`）の純度 [事実]

- 判断のモジュールは 70 本・8,281 行（`src/` の 14%）。検査 `decide-pure`（`src/ndf/checks/README.md:80`）が直の import と
  ファイルを触る呼び出しを禁じ、直の違反は 0（`pure.py` の 13 件はすべて `str.replace` の誤検知）。
- **推移では純粋でない。** 21 本が副作用を持つモジュールを読む。多くは事実の表を読む `ndf.contracts`（`src/ndf/contracts/__init__.py:68` の
  `load_json` がファイルを読む。キャッシュつき）。重いものは:
  - `src/ndf/domains/records/close_decide.py:15` が `ndf.progress.run`（991 行、`subprocess` を使う）から定数 2 つを読む
  - `src/ndf/domains/crossreview/scope_decide.py:13` が `store`（書き込みを持つ）を読む
  - `src/ndf/domains/crossrefactor/symbols_decide.py:19` ほか 3 本が `ndf.testing.scoped` を読む
- [推測] 規則が「直の import」だけを見るため、定数の置き場の都合で副作用のモジュールへ依存が生える。#3667（定数の置き場）と同根。

### 外部コマンドの呼び出し [事実]

- `subprocess.run` / `Popen` は 70 箇所・40 ファイルで、層を問わず直に呼ぶ（adapters 6・core 6・domains 18・checks 3・progress 3・testing 4）。
- `git` / `gh` に限っても、アダプター（`adapters/git.py`・`adapters/github.py`）を通らない呼び出しが 6 箇所:
  `src/ndf/domains/project/measure_repo.py:123`・`src/ndf/domains/project/write.py:352`・`src/ndf/domains/project/measure_ci.py:52`・
  `src/ndf/core/durable.py:400`・`src/ndf/core/plan/queue_run.py:49`・`src/ndf/checks/run.py:124`。
- 旧 ndf は 92 ファイル・169 箇所。旧 ndf には包みの外での利用を落とす検査 `wrapped`（`scripts/check-script-structure.py`）が
  あったが、ndf-next の検査には同じ規則が無い（`rules.json` の `structure` は `layers`・`durable`・`decide` だけ）。

## 2. 書き方の揃い方（同じ役割の実装の形の数）

| 役割 | 形の数 | 内訳 [事実] | 検査で縛っているか |
| --- | ---: | --- | --- |
| CLI の入口 | 1（ほぼ） | `def main(argv: list[str]) -> int` が 29/32、argparse 27 ファイル、`cli.COMMANDS` の表から遅延 import（`src/ndf/cli.py:31`） | 規約の規則 spec-io（引数の取り出し） |
| 結果 JSON | 1 | `result.emit(` 142・`result.stopped(` 190。`print(json.dumps` は 1 箇所（`src/ndf/contracts/cli.py`）。全テストの出力を `tests/conftest.py` が契約とスキーマで確かめる | 結果の契約・spec-io |
| 状態ファイルの読み書き | 3 系統＋同じ部品の複製 | JSON ファイル（一時名＋`os.replace` を 13 ファイルで個別に実装。`slot_pool.py:85`・`domains/backlog/state.py:92`・`domains/crossrefactor/store.py:59`・`domains/crossreview/store.py:69`・`core/plan/files.py:26`・`core/worker.py:480`・`adapters/training_cache.py:136` ほか）、SQLAlchemy（`progress/store.py`）、DBOS（`core/durable.py`）。`read_json` 相当の定義は 12 個、`json.loads(...read_text` は 48 箇所 | 無し |
| ロック | 3 | `filelock` 13 ファイル、`fcntl.flock` 直 16 箇所（`domains/hooks/env.py:91`・`domains/relay/signals.py:89`・`identity.py` ほか）、DB の鍵（`core/durable_locks.py`） | 無し（旧 ndf の `wrapped` は fcntl を包みに限っていた） |
| 外部 CLI | 2 | アダプターのクラス（`Git`・`GitHub`）と、各所の直の `subprocess` | 無し |
| 時計 | 5 以上 | `now` 相当の定義 11 個（本体が同じものが `domains/backlog/depth.py:74`・`domains/relay/signals.py:69`・`core/worker.py:438` ほか）、`datetime.now(UTC)` 19・`datetime.now(timezone.utc)` 10・引数なし 1 | 無し |
| 値の器 | 2 | `@dataclass(frozen=True)` 186・`@dataclass` 33・`NamedTuple` 141。TypedDict・pydantic は 0 | 無し |
| 直列化の名前 | 6 以上 | `load` 12・`parse` 4・`of` 4・`from_json` 2・`from_row` 1・`from_dict` 1 / `as_dict` 6・`._asdict()` 9・`asdict(` 6・`to_json` 1 | 無し |
| 依存の差し込み | 4 | 具体クラスを引数で渡す（`gh: GitHub` 40・`hub: GitHub` 37・`github: GitHub` 5 と名前も 3 通り）、`Protocol` 4 個（`domains/worktree/cleanup.py:28` ほか）、既定値つき引数 58、テストの `monkeypatch.setattr` 240 | 無し |

- **本体が同じ最上位の関数が 15 組**（`imports` 後の AST が一致）: `read_json` 3 本（`slot_pool.py:93`・`domains/hooks/env.py:178`・`adapters/training_cache.py:65`）、
  `_invalid` 3 本（`domains/hooks/cli.py:84`・`core/route/local_tests.py:36`・`core/route/__init__.py:550`）、
  `progress/command.py:539-549` と `progress/plan/cli.py:431-440` の 3 組ほか。旧 ndf の検査は `same-body` を落としたが、ndf-next の検査には無い。
- **理由のある分岐もある。** hook の経路は標準ライブラリだけで書く決まり（決定 111。`domains/relay/signals.py:1`・`domains/hooks/env.py:1`）で、
  ここの `fcntl` と手書きの書き込みは意図した複製。[推測] ただし「標準ライブラリだけの共有部品」を 1 本作れば複製は要らなかった。
- [推測] cross-refactoring の採用 911 件の上位（consolidate_duplication・extract_test_helper・introduce_named_constant・move_responsibility）は、
  この表の「検査で縛っていない」行の後始末と一致する。セッションごとに部品を作り、後の周回で寄せている。

## 3. 大きさ

| 指標 | ndf-next src | 旧 ndf scripts（テスト除く） |
| --- | ---: | ---: |
| ファイル数 / 行数 | 351 / 58,558 | 434 / 99,978 |
| ファイルの行 p50 / p90 / p99 / 最大 | 120 / 402 / 756 / 991 | 176 / 491 / 920 / 1,425 |
| 関数の行 p50 / p90 / p99 / 最大 | 6 / 16 / 27 / 56 | 8 / 23 / 49 / 155 |
| 60 行を超える関数 | 0 | 27 |
| 引数 p90 / 最大、6 を超える関数 | 3 / 6、0 | 4 / 17、106 |
| クラス / メソッド 0 のクラス / メソッド 5 以上 | 543 / 368 / 26 | 440 / 264 / 50 |
| 構造の検査の例外 | 例外リストなし。境界の印 `# noqa` 193 件（NDF401 108・NDF501 32・NDF502 20・BLE001 8・S110 7・NDF201 7・S112 6 ほか） | 例外リスト 190 件（`scripts/script-structure-allow/`。same-name 85・lines 30・require-groups 23・sleep-loop 22・same-body 16・limit-literal 7・wrapped 7） |

- 上限は `rules.json`: ファイル 1,000 行・関数 60 行・引数 6・分岐 9・入れ子 3・`elif` 4。最大のファイルは `src/ndf/progress/run.py`（991 行）・`src/ndf/adapters/github.py`（880）・`src/ndf/core/durable.py`（825）で上限の直下に張り付く。
- [推測] 関数は短すぎる側に寄る（p50 6 行）。上限を守るための `extract_method` が多く、1 ファイルの関数の数が増え、同名の関数（`check` 26・`_parser` 19・`load` 12）が散る。

## 4. テスト

| 指標 | ndf-next | 旧 ndf |
| --- | ---: | ---: |
| テストのファイル / 行 | 300 / 45,133 | 309（tests/ 内の .py）/ 99,607 |
| テストの行 ÷ ソースの行 | 0.77 | 1.00 |
| `def test_` / 集めた数 | 2,891 / 4,298 | 5,671 / 14,668 |
| `monkeypatch.setattr` | 240（85 ファイル） | 1,050 |
| 偽物のクラス（Fake・Stub） | 24、うち `FakeHub` が独立に 8 個 | 36 |
| CI の pytest | 1 ジョブ 237.6 秒（`-n auto`、run 43772686430）、全体のジョブは約 4 分 12 秒 | 2 分割で 636 秒と 825 秒（run 43772686458）、ジョブは約 14 分 |

- 種類（ファイル単位の粗い分類、`tests/` の `test_*.py`）: 実プロセス（`subprocess`・`git_sandbox`・`postgres_server`・`bin/ndf`・tmux）72 ファイル 17,807 行（40%）、
  偽物の差し込み 52 ファイル 11,023 行（26%）、それ以外 108 ファイル 12,948 行（30%）。判断のモジュールを読むテストは 63 ファイル。
- `FakeHub` の 8 箇所: `tests/domains/release/_fakes.py:23`・`tests/domains/backlog/conftest.py:22`・`tests/domains/crossrefactor/_drive_world.py:133`・
  `tests/domains/worktree/test_worktree_merged.py:20`・`tests/domains/worktree/test_worktree_git.py:22`・`tests/domains/crossreview/test_crossreview_repo.py:71`・
  `tests/support/failed_hub.py:27`・`tests/route/test_cli.py:211`。共有の置き場 `tests/support/` は 4 ファイルだけ。
- 良い点 [事実]: 結果 JSON はすべてのテストで契約とスキーマに照らされる（`tests/conftest.py:45` の `record_output`）。ok を 1 度も出さない副命令があれば落ちる。

## 5. 契約とスキーマ

- `contracts/` は 166 ファイル（すべて JSON）: `schemas/` 81（宣言 19・副命令の引数 27・結果 27・ほか 8）、`workflow/` 32、`templates/` 26、`domains/` 18（事実の表）、`facts/` 3、`hooks/` 3、ほか 3。
- **生成するもの [事実]**: 引数のスキーマ 27 本（argparse の呼び出しを止めて取り出す `checks/spec_args.py`）と契約のページ `docs/contracts/*.md` 8 本。`spec-docs` が生成し直して比べる。
- **手書きで一致させるもの [事実]**: 結果のスキーマ 27 本（テストの出力で照らす）、宣言のスキーマ 19 本（`.ndf/` の実物で照らす）、事実の表 21 本（`fact.schema.json` で照らす）。
  コードの側の型（dataclass / NamedTuple）はスキーマから生成されず、スキーマも型から生成されない。読み込みは `jsonschema` で形を見た後に手で取り出す（`isinstance(` 765）。
- [推測] 照合の仕組みは強いが、同じ形を 3 か所に書く。正本を 1 つにしないと、形を変えるたびに 3 か所を同時に直す必要があり、セッションごとに漏れ方が違う。

## 6. 外部ライブラリ

- 宣言 23 個（`plugins/ndf-next/pyproject.toml`）。`src/` での利用ファイル数: filelock 13・sqlalchemy 9・jsonschema 7・httpx 5・psutil 3・pathspec 3・tree-sitter 2・markdown-it 2・dbos 1・githubkit 1・tenacity 1・semver 1・ruamel 1・unidiff 1・dotenv 1・ptyprocess 1・pygments 1。
- **宣言して使っていない**: `pydantic`・`slack-sdk`・`mdit-py-plugins`（どこにも無い）、`tabulate`（`tests/bin/test_start.py` だけ）。
- 任せられそうな自作 [推測]:
  - 形の検証と取り出し → pydantic（宣言済み・githubkit が引き込む）。`model_json_schema()` でスキーマを生成すれば正本が 1 つになる
  - 待ちと再試行 → tenacity（宣言済みだが `adapters/notify.py` だけ。`time.sleep(` が 15 箇所、`testing/waits.py` は自作の待ち）
  - 原子的な書き込み → 標準ライブラリの 1 関数で足りる（ライブラリは要らない。置き場を 1 つにすることが要点）
  - ロック → filelock に寄せる（hook の経路の標準ライブラリだけの制約は、共有の小さな `fcntl` の包み 1 本で受ける）

## 7. 旧 ndf との比較のまとめ

ndf-next は、検査に載せた項目（層・輪・関数とファイルの大きさ・例外の握りつぶし・結果 JSON）で旧 ndf より明確に良い（上の表）。
一方、旧 ndf の検査にあって ndf-next で落ちた規則がある: `same-body`（本体の重複）と `wrapped`（汎用の部品は包みの外で使わない）。
この 2 つの穴がそのまま「2. 書き方の揃い方」の割れになっている [推測]。

## 8. 方法論として決まっていたこと／いなかったこと

| 決まっていて守られた [事実] | 決まっておらずセッションごとに違う [事実] |
| --- | --- |
| 層の順（5 層）と輪の禁止 | 横断の部品の置き場（原子的な書き込み・JSON の読み・ロック・時計・待ち・外部コマンド） |
| 判断のモジュールの命名と純度（直の import） | 値の器の種類（dataclass か NamedTuple か）と、スキーマとの正本 |
| CLI の入口の形と副命令の表 | 直列化のメソッド名 |
| 結果 JSON と止まった理由・終了コード | 依存の差し込み方（具体クラス・Protocol・既定値・monkeypatch）と引数名 |
| 大きさと複雑さの上限、例外の握りつぶしの禁止と境界の印 | テストの偽物の置き場（共有か各ファイルか）、テストの種類の配分 |
| 事実の重複（定数の値の重複）の禁止 | ドメイン間の依存の向き、最上位モジュール（`cli`・`identity`）の層 |
| 外部依存の宣言の外を読まない（I3） | 純度の推移（判断が副作用のモジュールの定数を読む） |

## 9. OOP に統一するか、今の作りを規約にするか

**推奨: 今の「値の器＋純粋な関数＋アダプター（クラス）」を規約として明文化し、検査で閉じる。OOP への統一はしない。** [推測]

根拠:

1. **Python の慣習**: 標準ライブラリと主要なライブラリは、値は dataclass、処理は関数、状態と資源を持つものだけクラスにする。
   全部をクラスにすると、状態を持たないメソッドの集まり（実質は名前空間）が増える。ndf-next でもメソッド 0 のクラスが 368/543 で、
   クラスの大半は値の器と例外である。
2. **テストのしやすさ**: 判断が引数と戻り値だけの関数なら、偽物も monkeypatch も要らない。ndf-next の monkeypatch は旧 ndf の約 1/4（240 対 1,050）で、
   判断の関数への分離が効いている [事実]。OOP に寄せると、振る舞いがインスタンスの状態に乗り、テストごとに組み立てと差し替えが要る。
3. **AI が書くときの揃いやすさ**: 揃ったのは AST で機械に判定できる規則だった [事実]。「関数か・クラスか」「どこまでを 1 オブジェクトにするか」は
   判定しにくく、OOP の設計判断（責務の境界・継承か合成か）はセッションごとにぶれやすい。外れ値もクラスに出ている
   （`adapters/github.py` の `GitHub` 43 メソッド・`domains/relay/wrapper.py` の `Wrapper` 30）。
4. **ただし「関数で書く」だけでは足りない。** 割れたのは関数か OOP かではなく、**横断の部品と差し込みの決め事が無かった**ことである。
   OOP に統一しても、ロックや時計の置き場を決めなければ同じように割れる。

アダプター（外部と状態を持つもの）はクラスのままでよく、そこは **Protocol のポート＋1 つの実装＋共有の偽物 1 つ**に揃える。

## 10. 12.0 で最初に決めるコードの規約（優先順）

1. **横断の部品は 1 モジュールずつ、外での直の利用を検査で落とす。** 対象: 外部コマンドの起動、原子的な JSON の読み書き、ロック、時計、待ちと再試行、HTTP。
   旧 ndf の `wrapped` 規則を戻す。hook の経路の「標準ライブラリだけ」の制約は、標準ライブラリだけの共有部品のパッケージ（例: `ndf.base`）を 1 つ作って受ける。
2. **形の正本を 1 つにする。** pydantic のモデルを正本とし、結果・宣言・事実の表のスキーマは `model_json_schema()` で生成する（生成物の比較は今の `spec-docs` を流用）。
   内部だけの値は `@dataclass(frozen=True, slots=True)` の 1 種類（NamedTuple は使わない）。直列化は `from_json` / `to_json` の 2 つの名前に固定。
3. **差し込みはポート。** 外部と話すものは `Protocol` を `contracts`（か `ports`）に置き、実装は `adapters`、偽物は `tests/support/` に 1 つ。
   引数名も 1 つ（`gh` / `hub` / `github` を統一）。`monkeypatch.setattr` はポートで足りない境界（環境変数・`sys.argv`）だけ。
4. **純度は推移で見る。** 判断のモジュールが読んでよいのは、判断・値の器・定数だけのモジュール。定数の置き場は #3667 の基準に従い、副作用のモジュールに定数を置かない。
5. **重複の検査を戻す。** `same-body`（本体の重複）と、テストの偽物の重複（同名のクラスが 2 つ以上）。
6. **永続化の選び方を表にする。** JSON ファイル（人が読む・hook の経路）／SQL（台帳・照会）／DBOS（再開の要る作業）の使い分けを 1 表で決め、ほかを禁じる。
7. **層の表に最上位モジュールとドメイン間の向きを入れる。** `cli`・`identity` の層を決め、ドメイン間の依存は表（`children.json` の `needs` に似た形）で宣言する。
8. **テストの配分の規則。** 判断＝純粋なテスト、アダプター＝実プロセスの契約テスト（sandbox）、ドメイン＝偽物のポート。実プロセスは adapters と `bin/` の通しに限る。
9. **引き継ぐもの（今のままでよい）**: 5 層の順・輪の禁止・`main(argv) -> int` と副命令の表・結果 JSON の契約と全テストでの照合・大きさの上限・例外の握りつぶしの禁止と境界の印。
10. **規約を文書 1 本に書く。** 今は検査の説明（`src/ndf/checks/README.md`）が規約を兼ねる。書き手（AI）が書く前に読む規約の文書を置き、各規則に検査の名前を対応させる。

## 11. 確かめられなかったこと

- テストの種類ごとの所要（CI のログに `--durations` が無い）。実プロセスのテストが 237 秒のどれだけを占めるかは不明。
- 揃っていない書き方がどのセッション（どの子 Issue）で生まれたか（`git blame` とセッションの対応は取っていない）。「セッションごとに違う」は形の数からの推測。
- 判断のモジュールが実行時に実際にファイルを読むか（`contracts` のキャッシュの初回だけか）。静的な import だけを見た。
- テストの種類の分類はファイル単位の正規表現で、1 ファイルに種類が混ざる場合は「実プロセス」を優先して数えた。
- pydantic を使わなかった理由の記録（設計の決定から外した記録）は探しきれていない。
