# NDF の起動が入れる uv と venv の変数を子孫へ漏らさない

## 概要

**NDF のエントリポイント（`plugins/ndf/bin/ndf`）と `uv run` が入れる環境変数は、NDF のパッケージを呼ぶ前に呼び出し元の値へ戻る。**
NDF の子孫（cross-review の修正担当・プランの worker・レーンのセッションなど）が利用先のリポジトリで `uv run --project .` を打っても、
NDF の共有の venv を同期し直さない（#4029）。NDF が自分のサブコマンドを子で打つときは、自分の `src` を環境変数ではなく argv で子の
`sys.path` にだけ渡す。

**例: cross-review の修正担当が、利用先のリポジトリで `uv run --project . pytest` を打つ。** NDF を呼んだ側は `UV_PROJECT_ENVIRONMENT` を
指定していないとする。指定していれば、子孫の `uv run` はその値が指す環境を同期する（NDF は呼び出し元の値を戻すだけで、`.venv` に決めない）。

| | 直す前 | 今 |
| --- | --- | --- |
| 修正担当が受け継ぐ `UV_PROJECT_ENVIRONMENT` | NDF の venv（`<キャッシュのホーム>/ndf/venv/<版>`） | 呼び出し元の値（無ければ無い） |
| 修正担当が受け継ぐ `VIRTUAL_ENV`・`PATH` の先頭 | NDF の venv と、その `bin` | 呼び出し元の値 |
| 修正担当が受け継ぐ `PYTHONPATH` | NDF の `src` を頭に足した値 | 呼び出し元の値 |
| `uv run --project .` の結果 | NDF の venv を利用先の依存で同期し直し、NDF の extras が消える | 利用先の `.venv`（呼び出し元が `UV_PROJECT_ENVIRONMENT` を指定していればその環境）を同期して使い、NDF の venv は変わらない |

子が `ndf ...` を打てば `bin/ndf` が毎回 NDF の venv を指し直すので、NDF のコマンドは今と同じく NDF の venv で動く。

10 系のスクリプトの同じ問題の扱い（`deps.require` が `UV_PROJECT_ENVIRONMENT` を戻す形）は [ndf-shared-venv-and-relay-ls-failure.md](ndf-shared-venv-and-relay-ls-failure.md) にある。
この文書は 11 系のエントリポイントの形を持つ。

## 背景

`bin/ndf` は `CLAUDE_PLUGIN_ROOT`・`UV_PROJECT_ENVIRONMENT`・`PYTHONPATH` を export してから `uv run ... python -P bin/start.py` を打ち、`uv run` は
さらに `VIRTUAL_ENV`・`UV_RUN_RECURSION_DEPTH`・`PATH` の先頭の venv の `bin` を足す。これらがそのまま NDF のプロセスの子孫へ渡り、
`uv run` は `UV_PROJECT_ENVIRONMENT` を受け継ぐと `--project` で指した別のプロジェクトの環境としてその置き場を同期し直す（同期は exact で、
NDF の extras が消える）。プロセスを起動する箇所は多く、1 か所ずつ外すと足し忘れが残る。

## 仕様

### 常に成り立つ条件

| # | 対象 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | `python -m ndf` に入った後の `os.environ` | `CALLER_ENV` の 6 つの変数は呼び出し元の値（無かったなら無い）で、写しの変数 `NDF_CALLER_<名前>` は残らない | テストが落ちる |
| I2 | `bin/ndf` を通らずに `start.py` を打った起動 | 写しが 1 つも無ければ環境を変えない | テストが落ちる |
| I3 | NDF が自分のサブコマンドを打つ子 | `src` は argv で子の `sys.path` の頭へだけ入り、子の `PYTHONPATH` は呼び出し元の値のまま | テストが落ちる |

### 戻し方

| 順 | 誰が | 何をする |
| ---: | --- | --- |
| 1 | `bin/ndf` | export の前に、呼び出し元の `UV_PROJECT_ENVIRONMENT`・`VIRTUAL_ENV`・`UV_RUN_RECURSION_DEPTH`・`PATH`・`PYTHONPATH` を `NDF_CALLER_<名前>` へ写す。あれば空でも写し、無ければ写しを消す（外側の `ndf` の写しを残さない）。`CLAUDE_PLUGIN_ROOT` は `NDF_CALLER_PLUGIN_ROOT` へ写す（無ければ空） |
| 2 | `bin/ndf` | 自分の値を export して `uv run ... python -P bin/start.py` を打つ |
| 3 | `bin/start.py` の `restore_caller_env` | 依存の環境の欠けを確かめた後、自分のパッケージを呼ぶ前に、`CALLER_ENV` の写しから呼び出し元の値へ戻し、写しを消す。`CLAUDE_PLUGIN_ROOT` だけは空の写しを「無かった」と読む（`EMPTY_IS_UNSET`） |
| 4 | `ndf.cli.ndf_command` | 自分のサブコマンドを子で打つ argv を `python -I [-X utf8=<値>] -c <起動の 1 行> <src> <引数>...` で組む |

自分の import の道（`sys.path`）は起動のときに決まっているので、手順 3 で `PYTHONPATH` を戻しても自分の import は変わらない。

`ndf_command` の子は `-I` で起動するので、呼び出し元の `PYTHONPATH` とその道の `sitecustomize` を import の道に入れない。`-I` は `PYTHONUTF8`・
`PYTHONIOENCODING` も無視するため、子の標準出力・標準エラーは起動の 1 行で UTF-8 に決め（`CHILD_ENCODING`）、読む側（`plan queue`・
`invoke.run_command`）は `child_encoding` で UTF-8 と分かる。`PYTHONUTF8` は `-X utf8=` で写す。ステップと外部スクリプトの出力は従来どおり
ロケールで読む。`os.execv` で自分を置き換える `ndf answer work` は、呼び出し元が置き換える前の文字コードで読むので、今の標準出力・
標準エラーの文字コードを写す（`keep_streams`）。

### 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | 戻すのはプロセスの入口（`start.py`）の 1 か所にする | 子を起動する箇所（プランのステップ・`plan queue` の子・`ndf answer work`・工程の作業・`progress` の worker・cross-review の相手役と修正担当など）は多く、1 か所ずつ外すと足し忘れが残る | 子を起動する箇所ごとに変数を外す |
| 2 | 呼び出し元の値は `bin/ndf` が写しの変数で渡す | `bin/ndf` と `uv run` が値を上書きするため、`start.py` の時点では呼び出し元の値が分からない | `start.py` で NDF の値と見分けて消す（利用者が自分で設定した値まで消える） |
| 3 | 自分のサブコマンドの子へは `src` を argv で渡し、`PYTHONPATH`（`ndf_env`）に入れない | 環境変数に入れると、その子の下のステップへ NDF の `src` が残る（#4035） | 子の `PYTHONPATH` に `src` を足す |

## データ・設定

写しの変数は `plugins/ndf/contracts/domains/env.json` の 6 行（`NDF_CALLER_PLUGIN_ROOT`・`NDF_CALLER_UV_PROJECT_ENVIRONMENT`・`NDF_CALLER_VIRTUAL_ENV`・
`NDF_CALLER_UV_RUN_RECURSION_DEPTH`・`NDF_CALLER_PATH`・`NDF_CALLER_PYTHONPATH`）が持つ。どれも `child: drop` で、中継が起動するセッションへ渡さない。
一覧は生成物の [docs/contracts/env.md](../../plugins/ndf/docs/contracts/env.md) にある。

## テスト観点

| 観点 | 縛るテスト |
| --- | --- |
| 写しから呼び出し元の値へ戻り、写しが消えること（I1）。ほかのプラグインの根が呼び出し元の値として戻ること | `plugins/ndf/tests/bin/test_start.py` の `test_the_callers_values_come_back_and_the_held_copies_go`・`test_another_plugins_root_comes_back_as_the_callers_value`・`test_the_package_runs_after_the_environment_is_restored` |
| `bin/ndf` を通らない起動は環境を変えないこと（I2） | 同 `test_a_start_not_through_the_entrypoint_keeps_the_environment` |
| 自分のサブコマンドの子の argv と文字コード、子の `PYTHONPATH` に `src` が残らないこと（I3） | `plugins/ndf/tests/cli/test_cli.py`・`tests/core/plan/test_queue_run.py`・`tests/progress/test_invoke.py`・`tests/progress/gate/test_gate_work.py` の #4029 で足したテスト |

## 関連リンク

- [issue #4029](https://github.com/devbasex/ai-plugins/issues/4029) — 要求と受け入れ条件
- [issue #4035](https://github.com/devbasex/ai-plugins/issues/4035) — 自分のサブコマンドの子孫に残る `PYTHONPATH`
- [Pull Request #4034](https://github.com/devbasex/ai-plugins/pull/4034) — 実装
- [ndf-shared-venv-and-relay-ls-failure.md](ndf-shared-venv-and-relay-ls-failure.md) — 10 系のスクリプトの同じ問題
