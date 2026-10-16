# 依存の欠けの終了コード: `deps.require` が外部パッケージを用意できずに止まると 69 で終わり、supervise は「飛ばしてよい」と読まない

## 目的

- **依存の欠けと「飛ばしてよい」を、終了コードだけで区別できる。** `deps.require` が外部パッケージを用意できずに
  止まると 69 で終わる。3（`refactor.py assess` などの「飛ばしてよい」）とは別の値である
- **依存の欠けは工程に失敗として現れる。** supervise の run のステップも計画の `実行の条件` も、69 を `skip_code`
  と読まず、`on_fail` か `結果: 止まった` へ進む

例: 外部パッケージの無い `/usr/bin/python3` で、起動し直した後の印（`NDF_DEPS_REEXEC`）を自分のパスにして assess を打つ。

```bash
NDF_DEPS_REEXEC="$PWD/plugins/ndf/skills/cross-refactoring/scripts/refactor.py" \
  /usr/bin/python3 plugins/ndf/skills/cross-refactoring/scripts/refactor.py assess --base origin/develop; echo "exit=$?"
```

| 時点 | 振る舞い |
| --- | --- |
| `refactor.py` の import | `deps.require("md", "mdtable")` が import できず、印が自分のパスなので起動し直さずに止まる |
| 標準エラー | `❌ [ndf deps] uv の環境へ起動し直したが md / mdtable のパッケージ（…）を import できない。…` |
| 終了コード | 69 |
| supervise | assess のステップ（`skip_to: review` / `on_fail: refactor`）は `skipped` を記録せず、`on_fail` へ進む |

**終了コードの表と assess の読み方は正本が持つ。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| 共通の終了コードの表（0・1・2・3・10〜19・20〜29・69 と `status` の対応） | [`plugins/ndf/scripts/lib/README.md`](../../plugins/ndf/scripts/lib/README.md) の終了コードの表 |
| `deps.require` の動く順（import できる・起動し直す・uv を入れる・止まる） | [`plugins/ndf/scripts/lib/deps.py`](../../plugins/ndf/scripts/lib/deps.py) の docstring |
| assess の打ち方と終了コード（0・3・2・69・1）ごとの読み方 | [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md) の「前提」 |

この文書が扱うのは、Skill と表に書かない決定の理由、常に成り立つ条件、部品の契約、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「依存の欠け」「結果 JSON」「承認ゲート」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| `deps.require` が依存を用意できずに止まる 3 つの経路の終了コード（NDF のエントリポイントと、`scripts/lib/ndf_wrappers.py` を通す根の `scripts/`） | 終了コード 3 の「前提が無い」と「正常な否定の結果」の、共通の契約での分離 |
| 共通の終了コードの表と `step_result.code_matches` | assess の「飛ばしてよい」の値（3）と、supervise の `skip_code` の既定（3） |
| supervise の run のステップと `実行の条件` が 69 を飛ばさないこと | 依存の入れ方（uv の入れ方・環境の置き場所） |
| ラッパー（ndf-relay）の環境が無いときの終了コードを 3 のまま保つこと | `deps.require` を呼ばない hook とラッパー、mcp-serena の `serena_lsp/env.py`、実験版の `deps-trial.py`・`hook-trial.py`（どれも依存の欠けを 3 で返す別の実装） |

## 背景

3 は共通の契約で「前提が無い」と「正常な否定の結果（飛ばしてよい）」の両方を指し、supervise の `skip_code` の
既定も 3 である。依存の欠けが同じ 3 で終わると、素の python3 や壊れた venv では assess の前に `deps.require` が
止まり、プロダクションコードの差分があってもリファクタリングが毎回黙って飛ばされる。cross-review の `drive.py` も
`state.py merge-fix` の 3 を自前の意味（取り込めない）で読むため、依存の欠けを取り違えて最終スイープへ進む。
依存の欠けに専用の値を当てると、この 2 つの取り違えが起きない。

## 決定と理由

- **依存の欠けは 69 にする。** 69 は `sysexits.h` の `EX_UNAVAILABLE`（必要なものが使えない）で、意味が慣習として
  通じる。配布物の終了コードで使われておらず、共通の契約（0〜3・10〜29）、エントリポイントが自分で使う値
  （`refactor.py` の 4 など）、supervise の打ち切り（124・125）、シェルの予約（126 以上）のどれとも重ならない。
  小さい空き番号は各スクリプトが 4 から順に足す値と衝突しやすく、意味も運ばない
- **値の持ち主は `deps.py` の 1 つにし、`step_result.py` はそれを参照する。** 依存の欠けを出すのは `deps._stop` の
  1 か所だからである。逆向き（`deps.py` が `step_result.py` を import する）は、ラッパーにも使われる最下層の
  `deps.py` に `proc` を引き込む。両方に 69 を書くと値が割れうる
- **supervise の判定は変えない。** `RunStep.is_skip` と `check_condition` は終了コードが `skip_code` と等しいとき
  だけ飛ばす・完了にするため、69 は既存の分岐で `on_fail` か `止まった` へ進む。69 を名指しする分岐を足すと、
  終了コードの意味を supervise にも持たせることになる。報告には `exit=69` と `❌ [ndf deps]` の行が既に載る
- **ラッパーの環境が無いときは 3 のまま保つ。** `relay_lib/runtime.py` の 2 か所（環境を作れない・環境が無い）は
  ラッパー自身の「前提が無い」で、`deps.require` の経路に入らない。値は 3 のまま、`runtime.py` が自分の定数
  `EXIT_PRECONDITION = 3` を持つ。`step_result.py` を import しないのは、ラッパーのバージョンディレクトリへ写す
  `lib/` のファイル（`version_dir.LIB_FILES`）に `step_result.py` が無いためである
- **`uv run` 自身の失敗は 69 へ揃えない。** `deps.require` は `os.execve` で `uv run --frozen` に置き換わるため、
  uv が環境を作れない（ネットワークが無い・lock を解決できない）ときは uv の終了コード 1 で終わる。1 も
  `skip_code` と一致しないため、黙って飛ばされる現象は起きない。69 へ揃えるには子プロセスで待つ形へ変え、
  シグナルと標準入出力の受け渡しを作り直すことになる。読み方（69 と同じに扱う）は SKILL.md に書く

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | `deps.require` の 3 つの止まり方（起動し直した後も import できない・`pyproject.toml` と `uv.lock` が無い・uv を入れられない）は、どれも `deps.EXIT_DEPS_MISSING` で終わり、標準エラーに `❌ [ndf deps]` で始まる 1 行を出す | テストが落ちる |
| I2 | `EXIT_DEPS_MISSING` は 0〜3 と 10〜29 のどれとも重ならない | テストが落ちる |
| I3 | `step_result.code_matches("stopped", 69)` は真、`"ok"` と `"gate"` では偽 | テストが落ちる |
| I4 | `step_result.EXIT_DEPS_MISSING` は `deps.EXIT_DEPS_MISSING` を参照し、値を写さない | テストが落ちる |
| I5 | `skip_code` を書かない run のステップは、69 で終わっても `skip_to` へ進まず `skipped` を記録しない。`実行の条件`（`skip_code: 3`）のコマンドが 69 で終わると `結果: 止まった` | テストが落ちる |
| I6 | 依存がそろった環境の assess は、差分なしで 3、差分ありで 0、`<base>` を解けなければ 2 で終わる | テストが落ちる |
| I7 | ラッパーの環境を作れないときの `install` は終了コード 3 で止まる | テストが落ちる |

### `deps.require` の止まり方

| 経路 | 判定 | 終了コード |
| --- | --- | --- |
| 起動し直した後も import できない | `NDF_DEPS_REEXEC` が自分のスクリプトのパスと同じで、足りないグループがある | 69 |
| 宣言が無い | 根（既定はプラグインの根。`project=` を渡せばその根）に `pyproject.toml` か `uv.lock` が無い | 69 |
| uv を入れられない | `ensure_uv` が uv を見つけられず、入れることもできない | 69 |
| `uv run` 自身の失敗 | `os.execve` の後に uv が環境を作れない | uv の値（1）。`❌ [ndf deps]` の行は出ず、uv の `error:` の行が出る |

3 つの経路はすべて `deps._stop(msg)` を通り、`_stop` が `❌ [ndf deps] <msg>` を標準エラーへ出して
`sys.exit(EXIT_DEPS_MISSING)` する。標準出力には何も書かない。

### 定数

| 定数 | 置き場所 | 値 |
| --- | --- | --- |
| `EXIT_DEPS_MISSING` | `plugins/ndf/scripts/lib/deps.py` | 69（値の持ち主） |
| `EXIT_DEPS_MISSING` | `plugins/ndf/scripts/lib/step_result.py` | `deps.EXIT_DEPS_MISSING`（参照） |
| `EXIT_PRECONDITION` | `plugins/ndf/scripts/lib/step_result.py` | 3（共通の契約の「前提が無い・正常な否定の結果」） |
| `EXIT_PRECONDITION` | `plugins/ndf/scripts/relay_lib/runtime.py` | 3（ラッパーの環境が無い。`deps.require` の経路の外） |

`deps.py` には `EXIT_PRECONDITION` が無い。テストを除く `plugins/ndf` で 69 を数値で書く定数は `deps.py` の 1 行だけである。

### supervise の読み方

| 終了コード | run のステップ（`skip_to` を持つ） | `実行の条件` |
| --- | --- | --- |
| 0 | `next` へ | 流す |
| `skip_code`（既定 3） | `skipped` を記録して `skip_to` へ | `結果: 完了` |
| 10〜19 | 承認ゲート | — |
| 1・2・69・ほか | `on_fail` へ。無ければ `結果: 止まった` | `結果: 止まった`（理由に `exit=<値>`） |

判定は `supervise_lib/steps.py` の `RunStep.is_skip`（`code == step.get("skip_code", 3)`）と
`supervise_lib/engine.py` の `check_condition` が持つ。

### 呼び手への影響

| 呼び手 | 依存の欠けのときの振る舞い |
| --- | --- |
| supervise の検査のプランの assess | 飛ばさず `on_fail`（refactor）へ進む。refactor も同じ依存を要るため、そこで止まる |
| cross-review の `drive.py`（`state.py merge-fix`） | 3 と取り違えず、`must` が `state.py merge-fix が終了コード 69 で止まった` で止める |
| 0 以外を失敗と読む呼び手 | 変わらない |

## テスト観点

- `deps.require` の 3 つの止まり方が、どれも `deps.EXIT_DEPS_MISSING` で終わること
  （`plugins/ndf/scripts/tests/test_deps.py` の `…_stops_as_deps_missing`・`test_uv_that_cannot_be_installed_stops_with_the_manual_command`）
- site-packages を外した python で起動し直した後の `refactor.py assess` が、3 でなく 69 で終わること
  （`test_refactor_assess_without_its_packages_stops_as_deps_missing`）
- `EXIT_DEPS_MISSING` が 0〜3 と 10〜29 に入らないこと（`test_deps_missing_does_not_overlap_the_common_codes`）
- `step_result.EXIT_DEPS_MISSING` が `deps` の値と等しく、`code_matches` が `stopped` だけで真になること
  （`plugins/ndf/scripts/tests/test_step_result.py`）
- `skip_to` と `on_fail` を持つ run のステップが 69 で終わると、`skipped` を記録せず `on_fail` へ進むこと
  （`plugins/ndf/scripts/tests/test_supervise.py` の `test_skip_to_does_not_jump_when_deps_are_missing`）。3 なら今どおり `skip_to` へ進むこと
- `実行の条件`（`skip_code: 3`）のコマンドが 69 で終わると、本体を走らせず `結果: 止まった` になること
  （`plugins/ndf/scripts/tests/test_supervise_pace.py` の `test_condition_that_stops_on_missing_deps_is_not_done`）
- 依存がそろった環境の assess が 3・0・2 を返すこと（`plugins/ndf/skills/cross-refactoring/tests/test_assess.py`）
- ラッパーの環境を作れない `install` が 3 で止まること（`plugins/ndf/scripts/tests/test_relay.py` の `test_install_stops_when_env_cannot_be_made`）
- SKILL.md と help の読み方は `.md` の文言を照合するテストで縛らない。レビューで読む

## 関連リンク

- [`plugins/ndf/scripts/lib/README.md`](../../plugins/ndf/scripts/lib/README.md)（共通の終了コードの表）
- [`plugins/ndf/scripts/lib/deps.py`](../../plugins/ndf/scripts/lib/deps.py)
- [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [cross-refactoring-round-tests-and-assess.md](cross-refactoring-round-tests-and-assess.md)（assess と飛ばした記録）
- 課題: #1654（由来 #725）
