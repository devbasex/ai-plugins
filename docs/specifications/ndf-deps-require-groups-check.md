# 依存の宣言の検査: 関数の中の import が届く外部パッケージも `deps.require` で宣言し、欠けを構造チェックで落とす

## 目的

- **LLM を呼ぶ副命令も、ほかの NDF のスクリプトと同じく依存の仕組みを通る。** `project-mvv.py propose` / `vet` は、
  psutil の無い python3 で打っても `deps.require` が uv の環境へ起動し直して最後まで進む
- **同じ形の欠けを、利用者の手元で落ちる前に継続的統合で落とす。** 副命令の中で遅れて読むモジュールが外部
  パッケージへ届くのに、その経路で `deps.require` がそのグループを並べていない形を、構造チェックの
  `require-groups` が違反として出す

例: 直す前の木では、psutil の無い python で次を打つと `lib/procs.py` の `import psutil` で `ModuleNotFoundError`
（終了コード 1）になった。今は `project_lib/mvv_llm.py` の `call_llm_json` が `deps.require("procs", "locks")` を
呼んでから `supervise_lib.claude` を読むため、uv の環境で同じ副命令を最初から打ち直して進む。

```bash
NDF_SUPERVISE_CLAUDE=/bin/true <psutil の無い python> plugins/ndf/scripts/project-mvv.py propose --root . --materials m.json
```

| 時点 | 振る舞い |
| --- | --- |
| `cmd_propose` | 材料・宣言・理由を読む（外へ書かない） |
| `call_llm_json` | `deps.require("procs", "locks")`。揃っていれば戻り、足りなければ同じ argv で起動し直す |
| 起動し直した後 | 揃っているので戻り、`supervise_lib.claude.call_claude` で LLM を 1 回呼ぶ |
| 終了コード | psutil の入った python3 で同じ引数を打ったときと同じ |

**規則の細部と `deps.require` の動く順は正本が持つ。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| `require-groups` の対象・たどる import の範囲・`require` が覆う範囲・任意の依存の形・例外リストの形 | [`scripts/check-script-structure.py`](../../scripts/check-script-structure.py) の docstring の `require-groups` の段落 |
| 依存グループの表 | [`plugins/ndf/scripts/lib/deps.py`](../../plugins/ndf/scripts/lib/deps.py) の `GROUPS` と `plugins/ndf/pyproject.toml` の extra |
| `deps.require` の動く順（import できる・起動し直す・uv を入れる・止まる） | `plugins/ndf/scripts/lib/deps.py` の docstring |
| 依存の欠けの終了コード 69 | [ndf-deps-missing-exit-code.md](ndf-deps-missing-exit-code.md) と [`plugins/ndf/scripts/lib/README.md`](../../plugins/ndf/scripts/lib/README.md) の終了コードの表 |
| MVV の策定の手順（手順 0 の打ち方） | `plugins/ndf/skills/development-workflow/references/project-mvv.md` |

この文書が扱うのは、Skill と docstring に書かない決定の理由、常に成り立つ条件、違反の形、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「エントリポイント」「構造チェック」「依存グループ」「未宣言のグループ」「任意の依存」「依存の欠け」である。
いずれも NDF の開発ワークフロー（`ndf-workflow`）の 1 つのコンテキストに収まる。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| `project-mvv.py propose` / `vet` が psutil の無い python3 で依存の仕組みを通ること | `project-mvv.py` の `check` / `collect` / `approve` / `show` / `context` / `signals` / `schema` の振る舞い（変えない） |
| 構造チェックの `require-groups` の対象と規則 | `deps.require` の起動し直しの手順・終了コード 69・uv の入れ方 |
| 検査が落とした既存のエントリポイントの直し方と、例外リストへ載せる形 | `lib/procs.py` の最上位の `import psutil` で `procs` を単独で import できない件（#1330） |
| | `procs.py` に psutil の代わりの手段（`/proc` の読み取り・`os.kill(pid, 0)`）を持たせること |
| | hook の経路の依存（`deps.require` を呼ばない。`hook-deps` が持つ） |

## 背景

2026-09-29 に carmo-cdk でプロジェクト MVV を策定したとき（ndf 10.17.41、`/usr/bin/python3` 3.14.4、psutil 無し）、
手順 0 の案内どおりに打った `propose` が `mvv_llm.call_llm_json` → `supervise_lib.claude` → `lib/procs.py` の
`import psutil` で止まった。`check` と `collect` は最上位で読むモジュールが外部パッケージへ届かないため通っていた。

当時の `require-groups` には外し方が 2 つあり、`project-mvv.py` はその両方に当たって見落とされていた。

- 対象が、モジュールの最上位で `deps.require(…)` を呼ぶエントリポイントだけだった（`project-mvv.py` は最上位で呼ばない）
- たどる import が読み込みの時に走るものだけで、関数の中の import を見なかった（`supervise_lib.claude` は `call_llm_json` の中で読む）

同じ形の欠けは `project-mvv.py` の 1 本に限らなかった。規則を広げた試作（2026-10-30）では、エントリポイント 33 本に
宣言の無い依存グループへ届く経路が 60 件あった。

## 決定と理由

- **`propose` / `vet` の直しは、`call_llm_json` の中で `supervise_lib.claude` を読む前に `deps.require("procs", "locks")`
  を呼ぶ形にする。** psutil が要ると分かるのはそこで、`propose` / `vet` のほかにこの関数を呼ぶ経路が増えても同じ
  宣言が効く。呼び出し元（`cmd_propose`・`vet_body`）はこの関数の手前で読むだけで外へ書かないため、起動し直しても
  書き込みが 2 度起きない
  - 副命令のハンドラー（`cmd_propose` / `cmd_vet`）で呼ぶ形は採らない。検査は呼び出しの流れを追わないため、
    直しても違反が消えない
  - `project-mvv.py` の最上位で呼ぶ形は採らない。`check` と `collect` まで起動し直すようになり、uv もネットワークも
    無い環境では今は通る `check` が 69 で止まる
  - `supervise_lib/claude.py` の最上位で呼ぶ形は採らない。import しただけで `os.execve` が走り、ライブラリの最上位に
    `require` を置かない約束事から外れる
  - 同じ考えで、`pydantic` を遅れて読む `lib/project_mvv_decl.py` の `decl_models` も、読む前に
    `deps.require("schema")` を呼ぶ（呼ぶのは既に `require("schema")` を呼ぶ `cmd_schema` だけで、振る舞いは変わらない）
- **`procs.py` を遅延 import にして代わりの手段へ落とす案と、欠けを検知して `uv run --with psutil` を案内して止まる案は
  採らない。** psutil を呼ぶのは `procs.py` だけと決めてあり、代わりの手段を持つと同じ役割の処理が 2 つになる。
  案内して止めると利用者の手が止まり、依存の欠けの終了コードは `deps.py` の 69 が既に持つ
- **検査は呼び出しの流れを追わず、モジュールの import と `require` の位置だけで未宣言のグループを求める。**
  呼び出しの流れを追うと、属性の呼び出し・引数で渡す関数・動的な振り分けで解決できない箇所が残り、規則が当て推量に
  なる。import と `require` の位置なら構文木から決まり、モジュールごとに 1 回求めて覚えられる。代わりに、関数の中の
  import はその関数が呼ばれるかどうかに関わらず読む側へ持ち越す（多めに出す）
- **関数の中の `require` は、同じモジュールのほかの関数の import を覆わない。** 覆うことにすると、`lib/monitor.py` の
  `main` の `require("procs", "locks")` が、`supervise_lib.claude` から読まれたときにも `monitor` の遅れた `import procs`
  を覆ってしまう（ほかのモジュールから読まれたとき `main` は走らない）。例外は `main` の本体の最上位で呼ぶ `require`
  で、エントリポイントとして起動したときは副命令へ振り分ける前に必ず走るため、エントリポイントとして検査するときだけ
  遅れて読む依存を覆う（`estimate.py` の前例）
- **`require-groups` は新しい種類を足さず、対象を「最上位で `require` を呼ぶエントリポイント」から
  「`if __name__ == "__main__":` の文を最上位に持つ `.py` すべて（hook の経路と `lib/deps.py` を除く）」へ広げて
  1 つの種類のまま持つ。** 種類を足すと同じ問い（宣言の無い依存へ届くか）を 2 つの種類が別の範囲で答え、例外リストの
  項目も分かれる。対象をスクリプトの置き場のパスで決める形は、`lib/monitor.py` のように `lib/` に置いて直接起動する
  モジュールが外れるため採らない
- **検査が落とした既存のエントリポイントは、宣言を足す場所を 2 つの形に限って直す。** どちらにも当たらない違反と、
  即時修正の 4 条件を満たさない違反は、例外リストへ理由と後続の課題の番号つきで載せる

  | 形 | 当てるとき |
  | --- | --- |
  | エントリポイントの最上位（か `main` の先頭）の `require` へグループを足す | すでに最上位か `main` の先頭で `require` を呼んでいて、足すグループで起動し直すようになっても副命令の出力と終了コードが変わらない |
  | 遅れて import する関数の中で、import の前に `require` を呼ぶ | その関数と呼び出し元の手前が外へ書かない。同じ宣言が複数のエントリポイントの違反を一度に消すときを優先する |

  `require` を呼ばないエントリポイントに最上位の `require` を新しく置く形は採らない。今は依存無しで通る副命令まで
  起動し直すようになる

## 仕様

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 |
| --- | --- | --- |
| 依存グループの表 | `lib/deps.py` の `GROUPS` と `plugins/ndf/pyproject.toml` の 1 組 | `GROUPS` |
| 依存の宣言 | `deps.require(…)` を書く各モジュール（エントリポイントと、遅れて import する関数を持つモジュール） | `deps.require` の呼び出し |
| 依存の宣言の検査 | `scripts/check-script-structure.py` の `require_groups`（`_DepScan`・`_entries`） | 違反 |
| 例外リスト | `scripts/script-structure-allow/` の `*--require-groups.json` | 項目のファイル |

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | エントリポイントから import をたどって届く外部パッケージは、その依存グループが覆う `require` のどれかに並ぶか、任意の依存である | `require-groups` の違反を出し、終了コード 1 |
| I2 | 任意の依存（`except ImportError` / `ModuleNotFoundError` を受ける `try` の本体の import と、`find_spec` / `deps.importable` の確かめが成り立つときだけ通る枝の import）は違反にしない。確かめた後に枝を通らず読む import は任意の依存にしない | 確かめた結果を見ずに無条件に読む import を見逃す |
| I3 | 関数の中で `deps.require` を呼んでから import する依存グループは、そのモジュールを読む側の未宣言のグループに入れない | 読む側に違反を出さない |
| I4 | `require` が覆う範囲は、最上位の呼び出し・`main` の本体の最上位の呼び出し（エントリポイントとして検査するときだけ）・ほかの関数の中の呼び出しの 3 つで決まる（細部は docstring）。モジュールの最上位の import が読み込みの時に届く依存は、`main` の `require` で覆わない（`main` へ着く前に落ちる） | 別の関数の `require` や、読まれる側の `main` の `require` を覆いに数えない |
| I5 | 関数の中で起動し直しうる `require` は、そのプロセスが外へ書く（ファイル・標準出力・投稿）より前に呼ばれる | 起動し直した後に同じ書き込みが 2 度起きる。構造チェックは見ないため、足すときに呼ぶ位置と呼び出し元の手前を読んで確かめる |
| I6 | 依存の揃った python3 では `require` は何もせず戻り、起動し直さない | `propose` / `vet` が毎回 2 プロセスになる |
| I7 | hook の経路（`HOOK_ENTRIES` から import でたどれるモジュール）は `require-groups` の対象にしない | hook は `require` を呼べないため直せない違反が出る（`hook-deps` が持つ） |
| I8 | `require-groups` の例外リストの項目は、`reason` に理由と課題の番号（`#` と数字）を持つ | テストが落ちる |

### 違反の形

構造チェックの起動・結果 JSON の形・終了コード（0 / 1 / 2）・例外リストのファイル名の決め方は他の種類と同じである
（docstring）。`require-groups` の違反は、エントリポイントと足りないグループの組ごとに 1 件である。

| 鍵 | 値 |
| --- | --- |
| `kind` | `require-groups` |
| `path` | エントリポイントのパス（根からの相対） |
| `function` | 足りない依存グループの名前。例外リストの `name` に当たる。1 つの import の名前が 2 つのグループに属するとき（`filelock` は `locks` と `durable`）は、どちらかが並べば満たし、違反の名前は `GROUPS` で先に出るもの |
| `detail` | `<import の名前>（<書いたモジュール>）へ届くのに deps.require() にグループ <名前> が無い` |

例外リストの項目が違反に当たらなくなったら、他の種類と同じく `unused-allow` で落ちる。

### 未宣言のグループの求め方

モジュールごとに「読み込みの時に届く」側と「遅れて読む（関数の本体の中で読む）」側の 2 つに分けて覚え、
エントリポイントの違反はその和から出す。import の解決の置き場は、エントリポイントの置き場・`plugins/ndf/scripts/`・
`plugins/ndf/scripts/lib/` の順（`IMPORT_ROOTS`）と相対 import である。互いに import し合うモジュールは、たどっている
途中のものを空として扱う。文ごとの扱いは docstring が正である。

### 例外リストに残した項目

規則を広げたときに直さなかった違反は、21 項目（11 本のエントリポイント）を `scripts/script-structure-allow/` に
載せ、`reason` に後続の課題 #2235 を書いた。理由は 2 つに分かれる。

| 理由 | 例 |
| --- | --- |
| `deps.require` を呼ばないエントリポイントで、関数の中の import が届く（最上位に `require` を新しく置くと依存無しで通る副命令まで起動し直す） | `relay.py`・`project-decl.py`・`training-optout.py`・`usage-report.py`・`test-run.py`・`lib/post_queue.py`・`lib/transcript_agents.py`・`external-ai.py`・`experimental/phase_cost.py` |
| hook から起動され、用意済みの環境を確かめてから読む（`require` を呼べない） | `wait-notify.py`・`experimental/hook-trial/ht_hook.py` |

## 運用

- 構造チェックは継続的統合のジョブ `script-structure-check`（`.github/workflows/script-structure.yml`）で
  `python3 scripts/check-script-structure.py` として動く。所要は変更前の全体 7.13 秒（うち `require_groups` 1.57 秒）に対し、
  広げた規則は試作で単独 2.28 秒で、増え方は 1 秒前後に収まる
- 新しいエントリポイントや遅れた import を足して違反が出たら、決定の 2 つの形のどちらかで宣言を足す。直せないときだけ
  例外リストへ理由と課題の番号つきで載せる
- 呼ばれない関数の import も読む側へ持ち越すため、違反は多めに出る。例外リストの項目が増えすぎたら、呼び出しの流れを
  追う規則を別の課題として起こす

## テスト観点

- psutil の無い python（`-S`）で `propose` / `vet --kind candidate` を打つと、起動し直して差し替えた LLM が呼ばれ、終了コードが
  psutil のある python と同じになること。psutil のある python では起動し直さないこと。`vet` の照合の記録が 1 行だけ
  増えること（起動し直す前に書かない）（`plugins/ndf/scripts/tests/test_project_mvv.py` の
  `test_propose_and_vet_without_psutil_reexec_and_call_the_llm`）
- psutil の無い python で `check` と `collect` を打つと、起動し直さず、psutil のある python と同じ出力と終了コードになること
  （`test_check_and_collect_without_psutil_do_not_reexec`）
- 最上位で `require` を呼ばないエントリポイントから、関数の中の import を経て外部パッケージへ届く木で、パス・グループ・
  届いた名前と書いたモジュールを持つ違反が出ること（`scripts/tests/test_check_script_structure.py` の
  `test_a_late_import_reaching_a_group_without_require_is_a_violation`・`test_require_must_list_groups_reached_through_imports`）
- `except ImportError`（組を含む）の `try` の中の import と、`find_spec` の確かめが成り立つ枝の import は違反にならず、
  確かめた後に枝を通らず読む import は違反になること（`test_optional_imports_are_not_violations`）
- 関数 A の `require` が関数 B と最上位の import を覆わないこと、`main` の本体の最上位の `require` がエントリポイントとして
  検査するときだけ、それより後の遅れた import を覆うこと、呼ばれる側の関数が `require` の後に import する木で読む側に
  違反が出ないこと（`test_require_covers_only_its_own_reach`）
- 読まれる側のモジュールの `main` の `require` が読む側を覆わないこと（`test_main_require_of_an_imported_module_does_not_cover_its_reader`）
- hook の経路のエントリポイントに `require-groups` の違反が出ないこと（`test_hook_entries_are_left_to_hook_deps`）
- `require-groups` の例外リストの項目が `reason` に課題の番号を持つこと（`test_require_groups_allow_rows_name_their_issue`）
- 最終の木で `python3 scripts/check-script-structure.py` が終了コード 0 で通ること（継続的統合）
- `mvv-gate.py`・`parallel-measure.py`・`supervise.py` など既存の `require` を持つエントリポイントの引数・出力・終了コードが
  変わらないこと（既存のテスト）

## 関連リンク

- [`scripts/check-script-structure.py`](../../scripts/check-script-structure.py)（`require-groups` の規則の正本）
- [`plugins/ndf/scripts/lib/deps.py`](../../plugins/ndf/scripts/lib/deps.py)
- [`plugins/ndf/scripts/project_lib/mvv_llm.py`](../../plugins/ndf/scripts/project_lib/mvv_llm.py)
- [ndf-deps-missing-exit-code.md](ndf-deps-missing-exit-code.md)（依存の欠けの終了コード 69）
- [cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md)（構造チェックのファイル指定と、範囲テストで拾えない `require-groups` の違反）
- 課題: #1455（関連 #1330・後続 #2235）
