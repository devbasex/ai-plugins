# NDF の作り直し: 外部ライブラリ・依存の宣言・耐久の記録

NDF の作り直し（#1142）のうち、汎用の処理を外部ライブラリへ任せる決まり、依存の宣言と環境の用意、DBOS Transact の上の
耐久の記録を持つ確定仕様である。決定の番号は作り直しの 6 本の確定仕様を通して一意で、索引と番号の対応表は
[ndf-rebuild.md](ndf-rebuild.md) にある。

## 概要

**例: 2 つのプランを `ndf plan queue impl-a.json impl-b.json --max 2` で流し、途中で `kill -9` する。**

| 順 | 起きること |
| ---: | --- |
| 1 | `bin/ndf` が `plugins/ndf/uv.lock` で固定した環境を用意し（無ければ uv を入れてから）、`python -m ndf` を起動する |
| 2 | キュー（`ndf.core.plan.queue_run`）が 2 本の `触るファイル` を比べる。同じファイルを持てば a の後に b を流し、重ならなければ子のプロセスの `ndf plan next` で同時に流す |
| 3 | 各プランは耐久ワークフロー `plan_workflow`（`ndf.core.plan.runner`）で流れ、ステップは耐久ステップとして DBOS の SQLite に記録される。記録は実行のキーごとのファイル `<git の共通ディレクトリ>/ndf/state/dbos/<キーの名前>.sqlite` |
| 4 | `kill -9` の後に同じコマンドを打つと、DBOS が記録から続ける。済んだステップは流し直さず、前の回の子のプロセスが残っていれば止めてから流れていたステップを打つ |

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| `plugins/ndf/pyproject.toml`・`plugins/ndf/uv.lock` | NDF のプラグインの外部パッケージの宣言と、版とハッシュの固定。グループに分けない 1 つの一覧 |
| `plugins/ndf/bin/ndf` | 版ごとの環境をプラグインの外に用意し、uv が無ければ版とハッシュを固定して入れる。入れられなければ `deps_missing` で止まる |
| `plugins/ndf/hooks/entry.py`・`boot.py` | hook のエントリポイント。用意済みの環境の python で起動し、`uv run` も `bin/ndf` も通らない（hook の契約は `ndf-rebuild.md` の決定 88〜92） |
| `ndf.adapters`（`plugins/ndf/src/ndf/adapters/`） | 外部との接続の層。GitHub（githubkit）・HTTP（httpx）・通知（slack の API・python-dotenv・tenacity）・`claude -p`・git・Markdown（markdown-it-py・pygments）・パスのパターン（pathspec）・重い起動のスロット |
| `ndf.core.durable`（`plugins/ndf/src/ndf/core/durable.py`） | DBOS の殻。`dbos` を import する唯一のモジュール。記録の保存先・実行のキーごとのロック・形の番号・秘密を伏せる serializer・退避と掃除を持つ |
| `ndf.core.plan`・`ndf.progress.plan` | 耐久ワークフローを使う層。プランの実行（`plan_workflow`）とレーン（`lanes_workflow`）の 2 つ |
| リポジトリの根の `pyproject.toml`・`uv.lock` | 根のテストと根の `scripts/` の環境。`[project.optional-dependencies]` にグループを持つ |
| `scripts/lib/ndf_wrappers.py` | 根の `scripts/` の `require(<グループ>...)`。根の uv の環境へ 1 回だけ起動し直す |
| `scripts/lib/` のアダプター（`md.py`・`mdtable.py`・`schema.py`・`procs.py`・`shparse.py`・`versions.py`・`pathmatch.py`・`yamlio.py`） | 根の `scripts/` が外部ライブラリを呼ぶ 1 か所 |
| `scripts/check-script-structure.py` | 根の `scripts/` の検査。`require-groups`（エントリポイントが届く依存のグループを `require` が並べているか）と `hook-deps`（hook の command が `uv run` を挟まないか） |

## 用語

語の定義は用語集にあり、ここでは写さない。

- 耐久ワークフロー・耐久ステップ・耐久キュー・耐久の記録・実行のキー・実行の回: [docs/glossary/ndf-durable.md](../glossary/ndf-durable.md)
- 作り直し・プラグインの入れ替え・旧いプラグイン・子の表: [docs/glossary/ndf-rebuild.md](../glossary/ndf-rebuild.md)
- アダプター: [docs/glossary/ndf-code-structure.md](../glossary/ndf-code-structure.md)。用語集の定義は 10 系の `lib/` を指したままで、この文書では根の `scripts/lib/` のアダプターと、NDF の `ndf.adapters` の層を指す

## 背景

2026-09-26 に利用者が「自作の方針は撤回する。汎用的で車輪の再発明になる処理を抽出し、外部ライブラリを使うように設計を
調整する」と決めた。汎用の処理（業務に固有でなく、どのプロジェクトでも同じ形で要る処理）の自作は、当時のスクリプトに
69 項目・20 種類あった。最も大きいのはシェルの字句解析（約 1,960 行）とプランの実行とキュー（約 2,300 行）である。

標準ライブラリだけで書く前提をやめたため、外部パッケージを宣言・固定し、どの起動からも同じ環境で読める仕組みが要る。
プランの実行とキューは、落ちた後に同じコマンドで続けられる耐久の記録の上へ移した。

11.0.0 のプラグインの入れ替え（#3304）で、10 系の `plugins/ndf/scripts/`（`lib/` のフラットなモジュール・`deps.py`・
バージョンディレクトリのラッパー・`supervise.py` ほか）は無くなった。NDF は `plugins/ndf/src/ndf/` のパッケージになり、
根の `scripts/` は 10 系の形（`scripts/lib/` のアダプターと `require`）を自分の分だけ残す。

## 仕様

### 採ったライブラリと置き場

| 種類 | ライブラリ | NDF のプラグインで読む所 | 根の `scripts/lib/` のアダプター |
| --- | --- | --- | --- |
| GitHub の呼び出し | githubkit | `ndf.adapters.github` | — |
| プランの実行と再開 | dbos（SQLAlchemy） | `ndf.core.durable` だけ | — |
| Markdown の構造 | markdown-it-py（宣言にある mdit-py-plugins は今どこも import しない） | `ndf.adapters.markdown`・`ndf.domains.materials` | `md.py` |
| Markdown の表 | tabulate | — | `mdtable.py` |
| 形の検証 | jsonschema（NDF）・pydantic（根） | `ndf.contracts`・`ndf.checks` ほか | `schema.py` |
| JSON Schema の参照の解決 | referencing | `ndf.domains.samples` | — |
| プロセス | psutil | `ndf.domains.relay`・`ndf.testing` | `procs.py` |
| ファイルロック | filelock | `ndf.slot_pool`・`ndf.adapters.launch_slot`・`ndf.domains.worktree` ほか | — |
| シェルの構文 | tree-sitter・tree-sitter-bash | `ndf.domains.hooks.context`・`ndf.domains.worktree.write_target` | `shparse.py` |
| 版数 | semver | `ndf.domains.release` | `versions.py` |
| Python のバージョンの条件（PEP 440） | packaging | `ndf.checks.toolchain` | — |
| パスのパターン | pathspec | `ndf.adapters.paths` ほか | `pathmatch.py` |
| unified diff | unidiff | `ndf.domains.crossrefactor` | — |
| コードのコメント | pygments | `ndf.adapters.markdown` | — |
| frontmatter と YAML | ruamel.yaml | `ndf.domains.manifests` | `yamlio.py` |
| 待ちと再試行 | tenacity | `ndf.adapters.notify` | — |
| HTTP | httpx | `ndf.adapters.http`・`ndf.adapters.github`・`ndf.adapters.notify` | — |
| `.env` | python-dotenv | `ndf.adapters.notify` | — |
| 擬似端末 | ptyprocess | `ndf.domains.relay.wrapper` | — |
| `claude -p` | なし（CLI を直に起動） | `ndf.adapters.claude` | — |

### 依存の宣言

| 対象 | 宣言 | 環境 | 足りないとき |
| --- | --- | --- | --- |
| NDF のプラグイン | `plugins/ndf/pyproject.toml` の `dependencies` の 1 つの一覧と `uv.lock` | `bin/ndf` が `<キャッシュのホーム>/ndf/venv/<バージョン>` に `UV_PROJECT_ENVIRONMENT` で用意する | 結果 JSON の `stopped`・`deps_missing` と、終了コードの表の `deps_missing` の値（[ndf-deps-missing-exit-code.md](ndf-deps-missing-exit-code.md)） |
| NDF の hook | 同じ一覧 | SessionStart が用意した環境の python。無ければシステムの `python3` で `hooks/boot.py` を起動し、本体を読めなければ出力なし・終了コード 0 | Tool の呼び出しを止めない（`ndf-rebuild.md` の決定 89・91） |
| 根の `scripts/` | 根の `pyproject.toml` の `[project.optional-dependencies]` のグループと `uv.lock`。グループの名前は `ndf_wrappers.GROUPS` と同じ | `require(...)` が根の `.venv` へ 1 回だけ起動し直す。全体テストの中ではそのまま戻る | uv が無ければ NDF と同じ版の uv を入れ、入れられなければ終了コード 69 |

- 外部パッケージを足す・版を上げるのは、宣言と lock を同じ PR で変えるときだけである
- 版の選び方は `ndf-rebuild.md` の決定 106、依存の更新の仕組みは `ndf-rebuild-review-scope.md` の決定 118 にある
- NDF のコードが読めるのは、自分のパッケージ・標準ライブラリ・`pyproject.toml` が宣言した依存だけである（`ndf check` の不変条件 `I3`。`plugins/ndf/src/ndf/checks/invariants.py`）
- 根の `pyproject.toml` のグループ `durable`（dbos・filelock）は、11.0.0 の入れ替えの後に根の `scripts/` で import する所が無い

### 耐久の記録

| 項目 | 今の形（`plugins/ndf/src/ndf/core/durable.py`） |
| --- | --- |
| 保存先 | SQLite では実行のキーごとのファイル `state/dbos/<キーの名前>.sqlite`。PostgreSQL の URL では 1 つの DB に置き、`application_name` でキーごとに分け、起動したキーを `ndf_runs` に書く（`ndf-rebuild.md` の決定 81） |
| 実行の識別 | `executor_id` と `application_name` はどちらもキーの名前（`run_name`） |
| アプリの版 | `format-<FORMAT>-<キーの名前>`。NDF の版が上がっても途中の実行を回復する（`ndf-rebuild.md` の決定 84） |
| DBOS の読み込み | 起動するか記録を読むときに初めて import する（`_load`）。`workflow`・`step` のデコレータは `_Deferred` が覚え、`launch` の前に当てる |
| 秘密 | 秘密は耐久ワークフローの入力・耐久化するステップの結果・メッセージに入れず、要るステップの中で環境変数から読む。serializer（`_RedactingSerializer`）は記録へ書く前の安全網で、伏せるのは文字列（秘密の環境変数の値とトークンの形。`ndf.adapters.run_log.redact` の文字列の規則）と、辞書の値・配列・タプル（名前つきタプルを含む）・例外の引数と属性の中の文字列だけである。辞書のキー・バイト列・集合・dataclass などほかの型の値は伏せずにそのまま書く。伏せるのは記録だけで、流れている呼び出しには元の値が渡るが、回復で記録から読んだ値は伏せた後の値になる。伏せた値を認証に使わない |
| 片付け | 終わったキーのファイルは `dbos/retired/`、壊れたファイルは `dbos/broken/` へ移し、上限の設定の `durable.keep`（14 日）で消す |
| 使う層 | 耐久ワークフローは `plan_workflow`（`ndf.core.plan.runner`）と `lanes_workflow`（`ndf.progress.plan.run`）。打ち直しの待ちと承認の回答の待ちは `durable.recv` |

### 常に成り立つ条件

記号は作り直しの不変条件の記号と重ならない。

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I16 | `dbos` を import するのは `ndf.core.durable` だけで、ほかのモジュールは殻の関数とデコレータだけを使う | `ndf check` の `durable-boundary` が落とす（`plugins/ndf/src/ndf/checks/rules.json` の `structure.durable_module`・`durable_imports`） |
| I17 | 1 つの SQLite の記録のファイルを開くプロセスは同時に 1 つだけで、`executor_id` は実行のキーの名前と同じ。作り直しの I5・I6 が同じ条件を記録の分け方の側から持つ | 2 つ目の起動は `run_lock` を上限の設定の `durable.lock-wait`（300 秒）まで待ち、取れなければオーナー（ホスト・pid・時刻）を添えて `StoreInUse` で止まる |
| I18 | 耐久ワークフローの中の DBOS の呼び出しの順序は、形の番号（`durable.FORMAT`）が同じ間は変えない。変えたら形の番号を上げる | 耐久ワークフローの関数のソースの sha256 と `FORMAT_SOURCE_HASH` を比べるテストが落とす。形の番号の違う記録の扱いは `ndf-rebuild.md` の決定 84 と I7 |
| I20 | 同じキューの同じステップで、`触るファイル` が同じファイルを持つ 2 本は同時に流れない。`触るファイル` の無いプランはすべてのプランと重なるものとして扱う。`/` で終わる値（ディレクトリ）は重なりに数えない | キューの判断（`ndf.core.plan.queue_decide`）が組を分け、後の組を前の組の後に流す |

## 決定と理由

### 決定 1: 外部 CLI の起動と文書の検査を、収束ループとプランの実行から独立した置き場に持つ

外部 CLI の起動は `ndf.adapters.cli`・`ndf.adapters.agents` に、文書の検査（用語集・仕様）は `ndf.domains.records` と
`ndf.checks` にある。どちらも収束ループとプランの実行の両方から呼ばれ、片方に入れると片方の語で名前を付けることになる。
置き場は層（下から `ndf.contracts`・`ndf.adapters`・`ndf.core`・`ndf.progress`・`ndf.domains`）とドメインのサブパッケージで
表し、`ndf check` の `layer` が下の層から上の層を読む import を落とす。

### 決定 2: 根の `scripts/` のライブラリは `scripts/lib/` のフラットなモジュールに置く

根のエントリポイントは `sys.path` に `scripts/lib` を足して `import md` の形で読む。パッケージにすると既存の import を
すべて書き換えることになる。NDF のプラグインは 11.0.0 の入れ替えで `src/ndf` のパッケージになり、`bin/ndf` と
テストが `src` をパスへ足して読む（`[tool.uv] package = false`）。

### 決定 4: NDF のエントリポイントは `bin/ndf` の 1 本にし、中身を `src/ndf` のパッケージに置く

呼ぶ側はパスを書かず、名前 `ndf` で呼ぶ。Claude Code は有効なプラグインの `bin/` を PATH へ足し、ほかのランタイムは PATH の上に
置いたシンボリックリンクで呼ぶ。副命令の実体は `python -m ndf` の下にあり、呼ぶパスを変えずに中身を移せる。
代償: `bin/ndf` は環境の用意を毎回確かめるので、hook はこれを通らない別の入口を持つ（決定 25・`ndf-rebuild.md` の決定 89）。

### 決定 5: ラッパーの複製は版によらない小さな起動の部品にし、本体はプラグインの側に置く

複製 `~/.claude/ndf/relay.py` は標準ライブラリだけの部品で、隣の `relay.root` が指すプラグインの `bin/ndf relay` を呼ぶ
（`ndf.domains.relay.install`）。シグナルファイルの形の定義はプラグインの 1 か所にあり、hook とラッパーが食い違わない。
動いているラッパーはプラグインの導入先のコードで動き、版はカットポイントで替わる（[ndf-relay-plugin-update.md](ndf-relay-plugin-update.md)）。

採らなかった案: 中身を版と digest ごとのバージョンディレクトリへ複製する 10 系の形（世代の管理の自作が残り、置き換え先の候補が無かった）

### 決定 6: ラッパーの複製は時刻と JSON の処理を持たない

複製はプラグインの根を読んで `bin/ndf` を呼ぶだけで、時刻・JSON・ログの処理はすべてプラグインの `ndf.domains.relay` が持つ。
同じ概念の実装を複製とライブラリの 2 か所に持つと、片方だけ直した不具合がもう片方に残る。

### 決定 7: 根の検査の例外リストは「ファイル:名前:種類」と理由を 1 項目 1 ファイルで持つ

`scripts/check-script-structure.py` の例外リストは `scripts/script-structure-allow/` に 1 項目 1 ファイルで置き、中身は
`{"path", "name", "kind", "reason"}`、ファイル名は `allow_file_name()` が path・name・kind から決める。違反に当たらない項目は
`unused-allow` として落とす。並列の PR は別のファイルを足す・消すため、マージで衝突しない（#1249）。
NDF の `ndf check` は例外リストを持たず、境界の兆候だけを行末の `# noqa: <規則> <境界の種類>: <理由>` で外す
（`plugins/ndf/src/ndf/checks/README.md`）。

### 決定 8: 移行の前後を比べる物差しは直さずに残す

構造の物差し `scripts/measure/structure-baseline.py` は、移行の前の値を測ったときのまま使う。物差しを直すと、前後の値を
比べられなくなる。同じ本体の関数を構文木で比べる規則は今の検査に無く、同じ事実の重複は `ndf check` の `const-fact` が見る。

### 決定 9: 不足 d（収束した結果を打ち直しで失う）は、収束ループが記録から続く形で直す

収束ループ（`ndf.domains.crossreview.drive`）は、ラウンドごとに状態ファイルへ rounds とジャーナルを書き、終わったら
`summary.json` を書く。打ち直すと記録から続き、最後のラウンドが収束で、次の条件をすべて満たせば、相手役を起動せずに前回の結果を返す
（`drive._reusable`）。過去を上書きで失う構造そのものを持たない。

- その head・比較基点・`.ndf/review.json` の重点が、今と同じである
- ベースブランチのプロジェクト MVV が、今と同じである（承認の状態・本文・共通原則から作る識別子で比べる）。承認済みの MVV があるときは、
  文書の根拠の行をその head で確かめ終えていること（`mvv_checked`）も要る。理由は
  [cross-review-project-mvv.md](cross-review-project-mvv.md) にある
- 今回の相手役の全員が、そのラウンドを読んでいる
- 修正担当に渡す未解決の指摘と、組み直しが残した足すべき内容のうち、まだ決めていないものが無い（`Journal.pending`）。
  修正担当に渡さない指摘、つまり収束を止めない軽い指摘と、必ず人の承認が要る操作に当たり人の承認を待つ指摘は、未解決のまま残って
  いても条件を満たす

### 決定 10: 不足 f（使用量の内訳が合計で失われる）は、1 本の使用量ログに応答ごとの値を残して直す

使用量ログは 1 本の JSONL で、書き手（SubagentStop の hook・`claude -p` の呼び手）によらず同じ保存先
（`NDF_USAGE_DIR`、無ければ状態の保存先の `usage`）に書く（`ndf.domains.records.usage`）。キャッシュの書き込みの区別のような
内訳は合計にした時点で戻らないため、集計は読む側で行う。

### 決定 11: 不足 a（即時修正の流れが無い）は、テンプレート `fix` を別の種別として持つ

`plugins/ndf/contracts/templates/fix.json` は即時修正の流れ（手元のテスト → push → PR → merge-when-green）で、worker の
実装のステップを持たない。必須の引数が実装の種別と違い（Issue は要らず、worktree が要る）、種別を分ければ引数の一覧も分かれる。

### 決定 12: 計測のスクリプトはリポジトリの根の `scripts/measure/` に置く

`structure-baseline.py`・`claude-p-usage.py`・`ai-branching.py` ほかは、配るプラグインの外に置く。計測は作り直しの
前後を同じコマンドで数えるためのもので、プラグインの入れ替えで消えると比べられなくなる。決め打ちのパスは引数で渡す。

### 決定 13: 不足 c（他のプラグインの版が上がらない）は、差分のあったプラグインを単位ごとに列挙して上げる

リリースは、範囲の起点からの差分に変更のあった単位（プラグイン）ごとに次の版を決める（`ndf.domains.release.gather.plan_bumps`）。
上げる単位を宣言で 1 つに決め打たないので、手で版上げのステップを足さずに済む。

### 決定 14: シェルの字句解析は Python の hook の中で tree-sitter-bash に任せる

元の形（シェルを Python へ書き換えない）は、hook を 1 本の Python のエントリポイントへまとめる決定で改め、今は
`ndf-rebuild.md` の決定 89 が持つ。シェルの構文は `ndf.domains.hooks.context` と `ndf.domains.worktree.write_target` が
tree-sitter-bash で読む。シェルのスクリプトとして残るのは、環境を用意する前に動く起動の部品（`bin/ndf`・`bin/ndf-claude`）と根の `scripts/*.sh` である。

### 決定 15: 語の移行は、中身を移す変更と別の PR で行う

中身を移す差分と語を替える差分を 1 つの PR に混ぜると、移しただけの行と意味が変わった行をレビューで見分けられない。
語の移行の対象は用語集の廃止語（`docs/glossary/glossary.json` の `deprecated`）で、`ndf check` の `banned-word` と CI の
用語の検査が、語が戻るのを止める。読む側が新旧の両方を受ける期間は置かない（決定 65）。

### 決定 16: 不足 g（GitHub の上限に当たる）は、呼び出しを 1 か所に集め、2 つの枠を使い分け、読み直しを ETag で数えさせない

2026-09-26 の実測で、GraphQL と REST の上限はどちらも 1 時間に 5000 で、NDF の呼び出しはすべて GraphQL の枠に偏っていた。
`ndf.adapters.github` は githubkit の上で次のとおりに使い分ける。

| 呼び出し | 枠 |
| --- | --- |
| 単発の読み書きと読み直し | REST。`http_cache` の ETag 付きの読み直しは、変わっていなければ上限に数えられない |
| 番号やブランチごとの読み取り | GraphQL のエイリアスで 1 回のクエリにまとめる |
| 片方の枠が上限のとき | 読み替えるのは 2 つの向きだけである。`GitHub.pulls`（番号ごとの PR のまとめ読み）は GraphQL が上限のとき、件数が `github.rest-fallback`（10 件）以下なら REST で 1 件ずつ読み替え、超えれば読み替えずに上限で返す。`GitHub.pull`（PR 1 件）は REST が上限のとき GraphQL で読み替え、読み替え先も上限ならそこで返す |
| 代われない操作・読み替え先も上限のとき | `github.client()` は既定で上限を待たずに `GitHubError`（`retry_after` 付き）で呼び手へ返す。呼び手が `remaining`（待ちに使える残りの秒）を渡したときだけ、解除までがその残り以内なら待ってやり直し、越えれば待たずに返す（`RetryWithin`）。回復の時刻まで無条件には待たない |

### 決定 17: 不足 h（外部パッケージを使えない）は、1 つの宣言と lock で固定し、起動が uv の環境を用意する

宣言と lock は配る単位ごとに 1 組で（「依存の宣言」の表）、版とハッシュは lock が固定する。NDF は `bin/ndf` が版ごとの環境を
用意して起動し、根の `scripts/` は `require` が根の環境へ起動し直す。デプロイ先の SDK のように無いと始まらない依存が増えても、
上限・再試行・認証を外部パッケージより弱い自作で持ち続けずに済む。NDF はグループに分けない（`ndf-rebuild.md` の決定 77・78）。

採らなかった案: パッケージのファイルをプラグインへ同梱する（pydantic-core のようなバイナリはアーキテクチャと Python の版ごとに要る）
採らなかった案: スクリプトごとに PEP 723 で宣言する（ライブラリのモジュールを import するエントリポイントのすべてに同じ宣言が要り、版が食い違う）

### 決定 18: ファイルの行数の上限は分け忘れを拾う安全網にし、ファイルは責務で分ける

`ndf check` の `lines` は、`plugins/ndf/src/ndf/checks/rules.json` の `structure.max_lines`（1,000 行）を超えるコードの
ファイル（`src/`・`tests/` の `.py` と `bin/`・`hooks/` のスクリプト）を落とす。上限に合わせて切らず、1 ファイル 1 責務で分ける。
例外リストも基準のラチェットも持たない。値は `ndf-rebuild-review-scope.md` の決定 115 で 1,000 にした。

採らなかった案: 上限を 500 行にし、超えるファイルを例外リストに載せてラチェットで減らす（分ける基準を行数から責務へ移し、上限は安全網にした。#2991）

### 決定 19: 汎用の処理は自作せず、外部ライブラリを使う

業務（NDF の工程・語・プランの形・Claude Code の仕様）に固有でない処理は、外部ライブラリに任せる。標準ライブラリの素直な使用と、
業務に固有の判定は対象から外す。根の `scripts/` は `scripts/lib/` のアダプターの 1 か所がライブラリを呼び、ライブラリの振る舞いと
このリポジトリの契約の差（日本語のエラーメッセージ・表の `\|` のエスケープ・版の形）をそこで吸収する。
NDF のプラグインでは外部との接続を `ndf.adapters` の層に置き、ライブラリは宣言した依存の範囲（`I3`）で使うモジュールが読む。
1 か所に限るのは `dbos` だけである（I16）。

### 決定 21: プランの実行と再開は DBOS Transact の上に置く（不足 i）

2026-09-26 の実験版（PR #1258）で LangGraph（SqliteSaver）・Burr（SQLitePersister）・DBOS Transact（SQLite）の 3 つとも、
サーバーを立てずにプランの雛形・キュー・kill -9 からの再開を保てた。DBOS は同時の本数と待ち（`recv`）をライブラリが持ち、
自作が残らない。今は SQLite と PostgreSQL の URL の両方で動き、宣言の版は `dbos==3.4.0` である。
キュー（`ndf plan queue`）は子のプロセスの `ndf plan next` を並べて流し、プランの回のキー・回復・承認ゲートの待ちはプランの実行が持つ。

採らなかった案: Burr（20.2 MB と軽いが、キューと同時の本数の制御の約 60 行を自作することになる）

### 決定 22: 全体テストは、配る単位ごとの 1 つの環境で、本番と同じパッケージを入れて流す

テストはスクリプトを同じプロセスで import するため、テストの環境に無いパッケージがあると落ちる。根のテストは根の
`pyproject.toml`（playwright_kit・全グループ・pytest・pytest-xdist）の環境で `uv run --frozen --project . --all-extras pytest . -q -n auto`
で流す。NDF のテストは `uv run --frozen --project plugins/ndf pytest plugins/ndf/tests -q` で流し、根の pytest は `plugins/ndf`
を収集しない。CI（`.github/workflows/pytest.yml`）も同じ環境を使う。

採らなかった案: 呼ぶ側のテストを uv の環境で流し直す（テストが二重に流れ、流し直しの仕組みが呼ぶ側の数だけ増える）

### 決定 23: 根の `require()` は複数のグループを 1 回で入れる

1 回の起動し直しで 1 つのグループしか入らないと、2 つ要るエントリポイントの 2 つ目の `require` が、起動し直した後なのに
import できないとみなして止まる。`require("md", "versions")` のように可変長で受け、起動し直すのは 1 回である。
`scripts/check-script-structure.py` の `require-groups` が、エントリポイントから届く依存のグループを `require` が並べているかを見る
（[ndf-deps-require-groups-check.md](ndf-deps-require-groups-check.md)）。

### 決定 24: `claude -p` の起動は CLI の直の起動のまま残す

`ndf.adapters.claude` は `-p --output-format json`（と `stream-json`）で CLI を起動する。実験版（PR #1264・#1267）で
claude-agent-sdk は呼び出しの形を満たしたが、既定のままでは子の CLI が会話タイトルを作る呼び出しを足して費用が 2.77 倍になり、
止める手段は文書に無い環境変数だけだった。置き換えで消える自作は約 130 行で、SDK だけが持つ機能を今の使い方で使わない。

### 決定 25: （廃止）mcp-serena の hook の環境

mcp-serena は Serena 2.1.0 向けの作り直しで自前の hook・`pyproject.toml`・`uv.lock` を捨て、公式の `serena-hooks` を
`uvx --from serena-agent==2.1.0` で起動するようになった。用意済みの環境の python を直に起動する仕組みは無くなった
（[mcp-serena-serena-2-1.md](mcp-serena-serena-2-1.md)）。

### 決定 29: 触るファイルの重なる組は止めずに待たせて直列にする

キューは同じステップのプランを `触るファイル` の重ならない組に分け、重なる組を前の組の後に流す（I20）。止めて知らせる形では、
conductor が切り方を直して打ち直すまで後続がすべて待つ。重なりの関係は推移的でないため、組の分け方で同時の本数が減ることはあるが、
誤りにはならない。ディレクトリの値を重なりに数えないのは、テストのディレクトリのように多くのプランが触る保存先で、別のファイルを
触るプランを直列にしないためである（#1811）。

### 決定 30: 同時に動く重い起動は重みのスロットで数え、GitHub の上限は接続の層が枠の使い分けで受ける

LLM と外部 CLI の起動は、テストと同じ保存先の重い起動のスロット（`ndf.slot_pool`・`ndf.adapters.launch_slot`）を
`contracts/limits/` の `launch.weight.<種類>` の重みで取り、空きが無ければ上限まで待つ。GitHub の上限は、
ステップに資源のタグを付けて数える代わりに、`ndf.adapters.github` が枠の使い分けで受ける。待つのは呼び手が渡した予算（`remaining`）の中だけで、予算を越える上限や予算を渡さない呼び出しは待たずに呼び手へ返す（決定 16）。
プランの JSON に資源の宣言を足さずに済む。

### 決定 31: ステップの打ち切りは耐久ステップの中で行う

ステップの子の待ちは耐久ステップの中にあり、上限（ステップの `timeout`、無ければ `contracts/limits/` の `supervise.run-step`）で
打ち切って `interrupted` で止まる（`ndf.core.plan.steps`）。DBOS の上で見張ると、見張りごとに耐久ワークフローが要り、
子のプロセスを持つスレッドの外から止める仕組みを作ることになる。

### 決定 32: 収束ループは耐久ワークフローに載せず、再開の位置を状態ファイルの 1 か所に持つ

`ndf cross-review run` と cross-refactoring の収束ループは、ラウンドごとの状態ファイルとジャーナルから続く（決定 9）。
耐久ワークフローはプランの実行とレーンの 2 つだけである。状態ファイルと耐久の記録を並べると、再開の位置を 2 か所に持つことになる。
1 回の起動でラウンドを上限まで回すため、ラウンドを worker へ出して要約だけを返す形を妨げない。

### 決定 33: 送信を耐久の記録に載せない

hook の通知は 1 件を 1 ファイルとして送信先ごとの送信待ち（`ndf.domains.hooks.outbox`）へ書き、切り離した送り手が送る。
GitHub へ書く本文は `ndf.adapters.publish` の `Publisher` を通す。送信を使うのは短命の CLI と hook で、耐久ワークフローで送らせると
送れない項目を待つ間プロセスを抜けられず、同じプロセスで続ける呼び出し側が続けられない。

### 決定 34: DBOS を呼ぶのは `ndf.core.durable` だけにし、依存は 1 つの一覧に置く

DBOS は SQLite で使っても SQLAlchemy を引き込むが、宣言と lock で固定する。殻の外は `durable.workflow`・`step`・`recv` ほかの
関数だけを使い（I16）、判断の規則は殻に置かず `ndf.core.loop` の判断の関数に置く。DBOS の import は約 0.3 秒の CPU を使うため、
DBOS を開かない副命令は import しない。

### 決定 35: 前の回の子のプロセスを止めてから、流れていたステップを打ち直す

ステップの子は自分のプロセスの組で起動し、pid とホスト名を `steps/<ステップ>.pid.json` に書く。打ち直しの前に前の回の子が
残っていれば `process_group` で止めてから打ち、別のホストの子が残っていれば打たずに止まる（`StepRefused`）。落ちたプロセスの
子が生きたまま同じステップをもう一度流すと、2 つの `claude -p` が同じ worktree を書く。これは揮発ではなく破損である。

### 決定 65: 作り直しの残りの実装は後方互換を置かず、形を最終形へ直接変える（利用者 2026-11-13）

作り直しの実装の間はほかの開発を止めたため、古い形を移行の間だけ受ける経路に守る相手がいない。エントリポイント・プランの JSON・
状態ファイル・結果 JSON の形は一番綺麗な最終形へ直接変え、呼ぶ Skill の手順・hook・プランのテンプレート・テストを同じ子の PR で
書き換える。旧い設定のキーは別名として読まず、改名表（`contracts/data/ndf-settings-renames.json`）で場所を示して止める（`ndf.core.branch_strategy_legacy`）。

採らなかった案: 語の移行のために読み替えの期間を置く（作り直しの間はほかの開発が流れず、守る相手がいない）

### 決定 66: 作り直しの残りの実装はテストを作り直してよく、古いテストを安全網にしない（利用者 2026-11-13）

置き換えた範囲の古いテストは同じ PR で消し、新しい形の観点で書き直す。戻すときは古いテストに照らさず、子の PR の revert 1 つで
前の形へまとめて戻す（呼ぶ側を同じ PR で直しているため）。

採らなかった案: テストの差し替え先を関数の定義元のモジュールへ向け直し、古いテストを差し替え先の変更だけで通す（前の企画の
スプリント 2・2b の形。テストが古い形を固定し、決定 65 の「最終形へ直接変える」と食い違う）
採らなかった案: 古いテストを残して新しい形と両方を通す（同じ理由）

### 決定 67: 作り直しは子の Issue ごとの PR に分け、スプリントを版の区切りにしない（利用者 2026-11-13）

#1142 は親の Issue で、実装は子の Issue ごとの PR に分ける。子の依存・流す順番・レーンは子の表（`plugins/ndf/src/ndf/checks/children.json`）
が持ち、`ndf check` の `child-deps` が import と照らす。語の移行の子は、同じファイルを触るほかの子の後に置く（語を直す行が 1 回で済む）。

### 決定 68: 自作のまま残った 4 種類を外部ライブラリへ移す

2026-11-13 の時点で、Markdown の構造の読み取り・パスのパターン照合・コードのコメントの除去・HTTP の呼び出しの 4 種類が
自作のまま残っていた（#3217・#3235・#3210 で移した）。今は次のとおりである。

| 種類 | NDF | 根の `scripts/lib/` |
| --- | --- | --- |
| Markdown の構造 | `ndf.adapters.markdown`（markdown-it-py。正規表現で囲みや `#` を数えない） | `md.py` |
| パスのパターン | `ndf.adapters.paths`（pathspec。glob を正規表現へ自分で変換しない） | `pathmatch.py` |
| コードのコメント | `ndf.adapters.markdown` の `mask_comments`（pygments） | — |
| HTTP | `ndf.adapters.http`（httpx だけ。urllib を使わない）。通知は `ndf.adapters.notify` がこれを通す | — |

## テスト観点

テストは `plugins/ndf/tests/core/test_durable*.py`・`plugins/ndf/tests/core/plan/`・`plugins/ndf/tests/checks/test_structure.py`・
`test_invariants.py`、`plugins/ndf/tests/adapters/`、`scripts/tests/test_check_script_structure.py` にある。

- `ndf.core.durable` の外で `dbos` を読む import を `durable-boundary` が落とし、`from pkg import mod` の形でも当たること（I16）
- 同じ SQLite のファイルを別のキーで開いたプロセスが、相手のキューの実行を取り出さないこと。同じキーで 2 つ起動すると 2 つ目が待ち、上限を過ぎるとオーナーを添えて止まり、同じステップが 2 度流れないこと（I17）
- 耐久ワークフローの関数の本体を変えると、`FORMAT_SOURCE_HASH` との比べが落ちること（I18）
- `触るファイル` の無いプランがすべてのプランと直列になり、同じファイルだけが重なりに当たり（`a/b` と `a/bc` は当たらない）、テストのディレクトリを共に持つ別のファイルのプランは同じ組で流れること（I20）
- `kill -9` の後の打ち直しが流れていたステップから続き、済んだステップを流さないこと。前の回の子が残っていれば止めてから打ち、別のホストの子なら打たずに止まること（決定 35）
- 承認の回答を待つプランが、待ちの目印を書いてプロセスを抜け、次の起動が記録から回復すること
- 記録へ書く値（入力・出力・メッセージ・例外）に、秘密の環境変数の値とトークンの形が残らないこと
- DBOS を開かない副命令が `dbos` を import しないこと（決定 34）
- 行数の上限が `src/`・`tests/`・`bin/`・`hooks/` のファイルに当たり、上限を超えたファイルが 1 件の違反になること（決定 18）
- 宣言していない外部パッケージを読む import を `I3` が落とすこと
- 根のエントリポイントが届く依存のグループを `require` が並べていなければ `require-groups` が落とし、hook の command が `uv run` を挟めば `hook-deps` が落とすこと（決定 20 を引き継ぐ `ndf-rebuild.md` の決定 89、決定 23）
- GitHub の読み直しが ETag 付きの REST で行われ、片方の枠が上限のときに代われる読み取りがもう片方の枠で行われること。`pulls` は `github.rest-fallback` を超える件数では REST へ読み替えず、`pull` は読み替え先も上限なら REST へ戻らないこと（決定 16）
- `remaining` を渡さない `github.client()` が上限を待たずに返し、渡したときは解除が残りを越える上限を待たずに `retry_after` 付きで返すこと（決定 16・30）

## 関連リンク

- 索引と番号の対応表・子の記号の表: [ndf-rebuild.md](ndf-rebuild.md)
- 依存が欠けたときの終了コード: [ndf-deps-missing-exit-code.md](ndf-deps-missing-exit-code.md)
- 根の `require` のグループの検査: [ndf-deps-require-groups-check.md](ndf-deps-require-groups-check.md)
- 耐久ワークフローからのレーンの起動: [ndf-parallel-durable-lane-launch.md](ndf-parallel-durable-lane-launch.md)
- 上限の設定と再試行: [ndf-limits-declaration-and-retry-policy.md](ndf-limits-declaration-and-retry-policy.md)
- ラッパーの更新: [ndf-relay-plugin-update.md](ndf-relay-plugin-update.md)
- 検査の規則: `plugins/ndf/src/ndf/checks/README.md`
- 課題: [#1142](https://github.com/devbasex/ai-plugins/issues/1142)・[#3304](https://github.com/devbasex/ai-plugins/issues/3304)・[#3412](https://github.com/devbasex/ai-plugins/issues/3412)
