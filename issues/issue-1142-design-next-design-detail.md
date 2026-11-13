# #1142 の詳細設計: 別のディレクトリでの作り直しの配置・順番・入れ替え

設計文書 [issue-1142-design-next.md](issue-1142-design-next.md) を、ファイル配置・子 Issue・流す順番・入れ替えの手順へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。

**部品の中の関数の仕様は、材料にする設計の詳細設計が持つ。** この文書は、新しいディレクトリの全体の配置と、子をまたぐもの（自分の名前の求め方・工程から作業を呼ぶ層・入れ替え）だけを書く。材料の節は、下の「既存の部品の再利用」の表で子ごとに引く。

**旧い実装計画は使わない。** [issue-1142-design-detail.md](issue-1142-design-detail.md) の「実装計画」「流す順序」「レーンへの割り当ての案」と、[issue-1142-design-labels.md](issue-1142-design-labels.md) の行き先の列は、この文書の表に置き換わる。

## ファイル配置

子 Issue ごとにサブパッケージを分け、触るファイルが子どうしで重ならないようにする。Python のパッケージ名は `ndf` である（プラグイン名から切り離す。決定 75）。

| パス | 中身 | 作る子 |
| --- | --- | --- |
| `plugins/ndf-next/.claude-plugin/plugin.json` | Claude Code 用の定義。名前 `ndf-next`・版 `11.0.0-dev.N` | X0 |
| `plugins/ndf-next/.codex-plugin/plugin.json` | Codex 用の定義（同じ名前と版） | X0 |
| `plugins/ndf-next/pyproject.toml` | 依存の宣言（DBOS・jsonschema・httpx ほか。前の企画の libraries の選択） | X0 |
| `plugins/ndf-next/uv.lock` | 依存の lock | X0 |
| `plugins/ndf-next/bin/ndf` | 入口 1 本。依存の環境を用意して `python -m ndf` を呼ぶ | X0 |
| `plugins/ndf-next/src/ndf/__main__.py` | `cli.main` を呼ぶだけ | X0 |
| `plugins/ndf-next/src/ndf/cli.py` | 副命令の表から各パッケージの `main` を呼ぶ | X0 |
| `plugins/ndf-next/src/ndf/identity.py` | 自分のディレクトリ・プラグイン名・Skill の前置き・状態の置き場を求める | X0 |
| `plugins/ndf-next/src/ndf/checks/` | 検査 1 本（構文木を 1 回作り、規則を当てる） | X1 |
| `plugins/ndf-next/contracts/facts/` | 事実の表 | D1 |
| `plugins/ndf-next/contracts/causes.json` | 止まった理由の表 | D1 |
| `plugins/ndf-next/contracts/schemas/` | 結果 JSON のスキーマ（`cause` を必須） | D1 |
| `plugins/ndf-next/contracts/limits.json` | 上限の宣言 | D1 |
| `plugins/ndf-next/contracts/domains/` | 領域の表（ランタイム・収束ループ・宣言・環境変数・PR の見出し・指示書） | D2 |
| `plugins/ndf-next/src/ndf/contracts/` | 表の読み手 | D1 |
| `plugins/ndf-next/src/ndf/adapters/github.py` | GitHub の読み書き（まとめたクエリ） | AD1 |
| `plugins/ndf-next/src/ndf/adapters/git.py` | git の操作 | AD1 |
| `plugins/ndf-next/src/ndf/adapters/ci.py` | CI の結果の読み取りと落ちたチェックの分類 | AD1 |
| `plugins/ndf-next/src/ndf/adapters/markdown.py` | Markdown のコードブロック・コメントの扱い | AD2 |
| `plugins/ndf-next/src/ndf/adapters/paths.py` | パスの照合 | AD2 |
| `plugins/ndf-next/src/ndf/adapters/notify.py` | 通知（httpx） | AD2 |
| `plugins/ndf-next/src/ndf/adapters/llm.py` | LLM の呼び出し（答えをスキーマで縛る） | AD3 |
| `plugins/ndf-next/src/ndf/adapters/cli.py` | 外部 CLI の起動と監視・実行の識別子 | AD3 |
| `plugins/ndf-next/src/ndf/core/loop.py` | 判断のループと判断の関数の型 | C1 |
| `plugins/ndf-next/src/ndf/core/durable.py` | DBOS の殻（DBOS を import するのはここだけ） | C1 |
| `plugins/ndf-next/src/ndf/core/plan/` | プランの実行（遷移・キュー・組み立て・再開・待ち） | C2 |
| `plugins/ndf-next/src/ndf/core/route.py` | 行き先の選び方 | C3 |
| `plugins/ndf-next/contracts/templates/` | プランの雛形 | C3 |
| `plugins/ndf-next/src/ndf/core/refs.py` | 起動の外の成果の解決 | C4 |
| `plugins/ndf-next/src/ndf/core/worker.py` | worker の規則文と受け渡し | C5 |
| `plugins/ndf-next/contracts/workflow/` | 工程の定義（モードと工程）とスプリントの文脈の鍵のスキーマ | P1 |
| `plugins/ndf-next/src/ndf/progress/model.py` | 進行表の型と純粋な判断 | P1 |
| `plugins/ndf-next/src/ndf/progress/store.py` | 進行表の保存（SQLite と PostgreSQL） | P1 |
| `plugins/ndf-next/src/ndf/progress/run.py` | `next` と `answer` の実行 | P1 |
| `plugins/ndf-next/src/ndf/progress/invoke.py` | 工程から作業を呼ぶ層 | P1 |
| `plugins/ndf-next/src/ndf/progress/gate.py` | 承認ゲート | P2 |
| `plugins/ndf-next/src/ndf/progress/plan.py` | 計画の進行表 | P3 |
| `plugins/ndf-next/src/ndf/progress/lane.py` | Lane の進行表 | P3 |
| `plugins/ndf-next/src/ndf/progress/reshape.py` | 組み換え | P4 |
| `plugins/ndf-next/src/ndf/domains/loop/` | 開発ループとスプリント | M1 |
| `plugins/ndf-next/src/ndf/domains/release/` | リリース（段階ごとのモジュール） | M2 |
| `plugins/ndf-next/src/ndf/domains/relay/` | 中継 | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/` | hook の判定 | M3 |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/` | cross-refactoring | M4 |
| `plugins/ndf-next/src/ndf/domains/crossreview/` | cross-review | M5 |
| `plugins/ndf-next/src/ndf/domains/backlog/` | 棚卸し | M6 |
| `plugins/ndf-next/src/ndf/domains/records/` | 記録（確定仕様化・引継ぎ・用語集・使用量・MVV） | M7 |
| `plugins/ndf-next/src/ndf/testing/` | テスト基盤（隔離・揺れない待ち・同時実行の枠） | T1 |
| `plugins/ndf-next/tests/` | テスト（子ごとに `tests/<パッケージ>/`） | X0 |
| `plugins/ndf-next/skills/` | Skill 本文 | S1 |
| `plugins/ndf-next/agents/` | エージェント定義 | S1 |
| `plugins/ndf-next/hooks/` | hook の入口 | S1 |
| `plugins/ndf-next/manifests/` | 配る Skill の表 | S1 |
| `.github/workflows/ndf-next.yml` | 新しいディレクトリのテストと検査 | X0 |

## クラスと関数の構造

| モジュール | 型・関数 | 責務 |
| --- | --- | --- |
| `identity.py` | `PluginIdentity`（`root`・`name`・`prefix`・`state_dir`）、`load() -> PluginIdentity` | 自分の名前を `plugin.json` と環境から求める |
| `identity.py` | `prepare_state_dir(ident) -> Path` | 形の版のファイルを確かめ、旧い置き場を退避する（決定 79） |
| `progress/invoke.py` | `StageRun`（`kind`・`name`・`inputs`・`outputs`・`range`）、`resolve_inputs(stage, context) -> dict` | 工程の定義と文脈から、作業の引数を組み立てる |
| `progress/invoke.py` | `invoke(stage, context, cwd) -> StageResult`、`record_outputs(table, row, result)` | 作業を呼び、出力を文脈へ書く |
| `checks/` | `check(paths, rules) -> list[Finding]` | 規則を当て、指摘を返す |

## 関数の仕様

| 関数 | 種類 | 入力 | 出力 | 副作用 | 前提と後条件 |
| --- | --- | --- | --- | --- | --- |
| `identity.load()` | 読み取り | 環境変数 `CLAUDE_PLUGIN_ROOT`（無ければ `__file__` から 3 つ上） | `PluginIdentity` | 無し | `plugin.json` の `name` を読む。`prefix` は `name + ":"` |
| `identity.prepare_state_dir(ident)` | 書き込み | 識別子 | 状態の置き場のパス | 旧い置き場の名前を変える・新しい置き場を作る | 置き場に `format.json` が無く、ほかのファイルがあれば、`<name>.v10-<日付>` へ名前を変えてから作り直す |
| `invoke.resolve_inputs(stage, context)` | 純粋な関数 | 工程の定義（`metadata.ndf.inputs` の鍵の列）と文脈 | 引数の辞書 | 無し | 鍵が文脈に無ければ `StageInputMissing(key)` を投げる |
| `invoke.invoke(stage, context, cwd)` | 外部呼び出し | 工程・文脈・作業ディレクトリ | `StageResult`（統一 JSON レポート＋`outputs`） | 作業（プラン・LLM・子プロセス）を起動する | `kind` ごとに呼び分ける（`plan` はプランの実行、`skill` は LLM の起動、`script` は子プロセス） |
| `invoke.record_outputs(table, row, result)` | 書き込み | 進行表・行・結果 | 無し | 進行表へ事象を 1 件追記する | 事象 `outputs_recorded` を 1 件追記する。鍵は工程の定義の `outputs` に列挙したものだけ |

## 異常系の処理

| 関数 | 異常 | 扱い |
| --- | --- | --- |
| `identity.load()` | `plugin.json` が読めない | 起動を止める（終了コードは止まった理由の表の `config_broken`） |
| `identity.prepare_state_dir(ident)` | 旧い状態の置き場の退避先が既にある | 日付に時刻を足した名前で退避する。消さない |
| `invoke.invoke(stage, context, cwd)` | 文脈に入力の鍵が無い | 行を始めない。止まった理由 `input_missing` と、足りない鍵を返す |
| `invoke.invoke(stage, context, cwd)` | 作業が統一 JSON レポートを返さない | 行を失敗にし、止まった理由 `bad_report` を返す |
| `invoke.record_outputs(table, row, result)` | 工程の定義に無い鍵を作業が出力した | 書かずに捨て、結果の `warnings` に載せる |
| 工程の定義の登録（P1） | 同じ範囲を 2 つの工程が受け持つ | 登録で断る（#3092 の順序の決まりに足す） |

## 処理の手順

**子 Issue の PR（S2 の前）**

| # | 手順 | 確かめ |
| ---: | --- | --- |
| 1 | 子の worktree を develop から作る（今の NDF の `worktree` を使う） | — |
| 2 | `plugins/ndf-next/` の担当のサブパッケージとテストだけを書く | 検査 I2 |
| 3 | `.github/workflows/ndf-next.yml` のテストと検査を通す | CI |
| 4 | develop へ入れる。開発版の利用者には何も届かない（マーケットプレイスに載せていない。決定 74） | — |

**入れ替え（S2）**

| # | 手順 | 確かめ |
| ---: | --- | --- |
| 1 | 今の NDF で動いているスプリントとプランが無いことを確かめる。あれば終わるのを待つ | `parallel.py status`・`supervise.py` の状態 |
| 2 | `git rm -r plugins/ndf` の後に `git mv plugins/ndf-next plugins/ndf` を打つ | — |
| 3 | `plugin.json` 2 本の `name` を `ndf`、版を `11.0.0` にする | `claude plugin validate .` |
| 4 | `.ndf/project.json` の suite のパスと `scripts/*-allow/` の許可ファイルを新しい中身に合わせて作り直す（旧い許可は消す） | 既存の検査のスクリプト |
| 5 | ルートの `pyproject.toml`・`conftest.py` の今の NDF 向けの記述を新しい側へ寄せ、`.github/workflows/ndf-next.yml` を消す | 既存の CI のジョブ |
| 6 | `CHANGELOG.md` に入れ替えを書き、手順 2〜6 を 1 本の PR にする | — |
| 7 | `claude --plugin-dir plugins/ndf` で主要な Skill を 1 回ずつ起動する | 振る舞いの確認 |
| 8 | develop へ入れて開発版としてデプロイし、リリース承認ゲートを通して main へ出す | リリース後テスト |
| 9 | ラベル `rebuild-1142` の開いている Issue が S2 自身を除いて 0 件であることを確かめる。残れば理由を添えて閉じるか、オーガナイザーへ送る | `gh issue list --label rebuild-1142 --state open` |

戻すときは、入れ替えの PR を revert する。

## 内部のデータと定数

**スプリントの文脈の鍵**（工程の定義の `inputs`・`outputs` に書ける語。閉じた語彙で、`contracts/workflow/` のスキーマが持つ）

| 鍵 | 型 | 書く工程 |
| --- | --- | --- |
| `issues` | 整数の列 | 進行表の作成 |
| `mode`・`pace` | 文字列 | モード判定 |
| `worktree` | パス | 作業場所の用意 |
| `branch` | 文字列 | 作業場所の用意 |
| `design_pr`・`pr` | 整数 | 設計の PR・PR |
| `delivery` | `.ndf/project.json` の `delivery` の 1 行 | デプロイ |
| `version` | 文字列 | デプロイ |

## 並行と排他

| 対象 | 排他の仕方 |
| --- | --- |
| 子 Issue どうしのファイル | 「ファイル配置」のサブパッケージで分ける。重なるのは `cli.py` の副命令の表だけで、1 行ずつ足すので git の取り込みで解ける |
| 進行表とプランの実行の DB | 1 つの DB を使う（決定 77）。SQLite はロックのファイル、PostgreSQL は `pg_try_advisory_lock`（#3092 の設計のまま） |
| 今の NDF と新しい NDF | 開発の間は同時に有効にしない（決定 74）。状態の置き場はプラグイン名で分かれる（決定 75） |

## ログと記録の出力

| 出力 | 形 |
| --- | --- |
| 各副命令の結果 | 統一 JSON レポートを標準出力へ 1 行（今の NDF と同じ規約） |
| 状態の置き場の退避 | 結果の `items` に退避先のパスを載せる |
| 工程の出力 | 進行表の事象 `outputs_recorded`（宣言した鍵だけ） |

## 既存の部品の再利用

**今の `plugins/ndf/` のコードは import も複写もしない**（I3）。読んで参考にするだけにする。関数の仕様は次の文書の節から引く。

| 新しい子 | 引く節 |
| --- | --- |
| X1 | smells の詳細設計の「関数の仕様」（検査の規則）・data の詳細設計の重複の検査・#2963 の詳細設計の RF2 と RF3 |
| D1・D2 | data の詳細設計の「ファイル配置」「内部のデータと定数」・scripted-flow の詳細設計の止まった理由の表 |
| AD1〜AD3 | base の詳細設計（K1〜K3）・scripted-flow の詳細設計（SF3・SF4・SF6）・fixes の詳細設計（N10） |
| C1〜C5 | #2963 の詳細設計（RF1・RF4・RF5a）・scripted-flow の詳細設計（SF2・SF7）・fixes の詳細設計（N1・N2a・N2b・N11） |
| P1〜P4 | #3092 の 4 本の文書。工程から作業を呼ぶ層だけはこの文書 |
| M1〜M7 | #2963 の詳細設計（RF5b・RF7・RF8）・fixes の詳細設計（N4〜N8b）・scripted-flow の詳細設計（SF11・SF12） |
| T1 | fixes の詳細設計（N9・N12） |

## 単体テストの観点

| 対象 | 観点 |
| --- | --- |
| `identity.load` | ディレクトリ名を `plugins/x` に変えても、名前と前置きが `plugin.json` から求まる |
| `identity.prepare_state_dir` | 旧い置き場が退避され、中身が残る。2 度目の起動では退避しない |
| `invoke.resolve_inputs` | 鍵が欠けると `input_missing` で止まる。余分な鍵は渡さない |
| `invoke.record_outputs` | 宣言に無い鍵は書かない。前の工程の `pr` が次の工程の引数に届く |
| 検査 I1〜I3 | `"ndf-next"` の文字・`plugins/ndf/` の import・`plugins/ndf/` の差分を、それぞれ落とす |

## 実装計画

| 子 | 中身 | 取り込む旧い子・Issue | 先に要るもの | 順番 | Lane |
| --- | --- | --- | --- | ---: | --- |
| X0 | 骨組み（`plugin.json`・依存の lock・入口・`identity.py`・CI のジョブ・テストの置き場） | 無し | 無し | 1 | lrebuild |
| X1 | 検査 1 本（兆候 0 件・構造の規則・輪・事実の重複・禁止語・ループの中の外部呼び出し・I1〜I3） | A0・A2（規則）・DC2・RF2・RF3・W8 | X0 | 2 | lrebuild |
| D1 | 契約と読み手（事実の表・止まった理由の表・結果 JSON で `cause` を必須・上限の宣言） | DC1・SF1a・SF1b・DC5 | X0 | 2 | lr-data |
| C1 | 判断のループ・判断の関数の型・DBOS の殻 | RF1 | X0 | 2 | lr-flow |
| T1 | テスト基盤（隔離・揺れない待ち・同時実行の枠） | N9・N12 | X0 | 2 | lr-fix-b |
| D2 | 領域の表（ランタイム・収束ループ・宣言・環境変数・PR の見出し・指示書） | DC3・DC4・DC6 | D1 | 3 | lr-data |
| AD1 | GitHub・git のアダプターと CI の結果の監視・落ちたチェックの分類 | A2（クエリ）・SF3（#3153）・SF4 | D1 | 3 | lr-fix-a |
| AD2 | テキストと通知のアダプター | K1・K2・K3 | X0 | 3 | lr-data |
| AD3 | LLM（答えをスキーマで縛る）と外部 CLI のアダプター・実行の識別子 | SF6・N10 | D1 | 3 | lr-fix-b |
| C2 | プランの実行（遷移・キュー・組み立て・再開・重なり・待ちの `next`） | RF4・RF5a・SF7・N2b | C1・D1 | 3 | lr-flow |
| C3 | 行き先の選び方とプランの雛形 | SF2・N11 | C2 | 4 | lr-flow |
| C4 | 参照の部品 | N1 | C2・AD1 | 4 | lr-fix-a |
| C5 | worker の規則文と受け渡し | N2a | C2・AD3 | 4 | lr-fix-b |
| P1 | 工程の定義・スプリントの進行表・工程から作業を呼ぶ層 | #3092・DC7・SF8・SF10・SF5 | C2・D2 | 4 | lr-rf |
| P2 | 承認ゲート（答えごとの行き先・問いの区分） | SF13 | P1 | 5 | lr-rf |
| M2 | リリース（段階ごとのモジュール） | RF8 | P1・AD1 | 5 | lr-fix-a |
| M3 | 中継と hook | N4・SF12 | D2・AD3 | 5 | lr-fix-b |
| M4 | cross-refactoring | N5 | C3・AD3 | 5 | lr-data |
| M5 | cross-review | N6 | C3・AD1・AD3 | 5 | lr-flow |
| P3 | 計画と Lane の進行表 | #3097・RF6a・RF6b・SF9・N3 | P1・P2 | 6 | lr-rf |
| M1 | 開発ループとスプリント | RF7・RF5b・SF11 | P1・M2 | 6 | lr-fix-a |
| M6 | 棚卸し | N7 | P1 | 6 | lr-data |
| M7 | 記録（確定仕様化・引継ぎ・用語集・使用量・MVV） | N8a・N8b | P1・P2 | 6 | lr-fix-b |
| P4 | 組み換えの工程 | #3098 | P3・M1 | 7 | lr-rf |
| S1 | Skill 本文・エージェント定義・hook の入口・配る Skill の表 | 無し | P4・M3〜M7 | 8 | lrebuild |
| S2 | 入れ替え・volareinc/nd-factory#183・ラベルの Issue の閉じの確かめ | A1a の前提の分 | S1 | 9 | lrebuild |

**PR の単位は子ごとに 1 本**で、revert 1 回で 1 つ前に戻る。消す古いテストは無い（今のテストは S2 で `plugins/ndf/` ごと消える）。

### 流す順番

| 順番 | 子 | 同時の本数 |
| ---: | --- | ---: |
| 1 | X0 | 1 |
| 2 | X1・D1・C1・T1 | 4 |
| 3 | D2・AD1・AD2・AD3・C2 | 5 |
| 4 | C3・C4・C5・P1 | 4 |
| 5 | P2・M2・M3・M4・M5 | 5 |
| 6 | P3・M1・M6・M7 | 4 |
| 7 | P4 | 1 |
| 8 | S1 | 1 |
| 9 | S2 | 1 |

先に要るものは、どれも前の順番の子である（逆転は無い）。1 つの Lane が同じ順番に持つ子は 2 本以下で、Plan 2 本に収まる。

### Lane の割り当て

| Lane | 子 | after |
| --- | --- | --- |
| lrebuild | X0・X1・S1・S2（と順番の見張り） | 無し |
| lr-flow | C1・C2・C3・M5 | 無し（X0 のマージを lrebuild が知らせる） |
| lr-data | D1・D2・AD2・M4・M6 | 同上 |
| lr-rf | P1・P2・P3・P4 | 同上 |
| lr-fix-a | AD1・C4・M2・M1 | 同上 |
| lr-fix-b | T1・AD3・C5・M3・M7 | 同上 |

lflow・lbr2・lsw は after から外す。lr-* は今 after に lrebuild を持って止めてある。承認の後に Lane の定義を書き直し、X0 がマージされたら lrebuild がオーガナイザーへ知らせる。

## 旧い子の行き先

`keep` は新しい子へそのまま写す、`reshape` は形を変えて写す、`merge` は他とまとめて写す、`drop` は要らなくなる（S2 の PR で理由を添えて閉じる）。

| 旧い子 | 仕分け | 新しい子 |
| --- | --- | --- |
| A0 #3196 | reshape | X1（増やさないラチェットでなく、初日から兆候 0 件） |
| A1a #3199・A1b #3202 | drop | —（旧名と古い値の読み替えは新しいディレクトリに無い。nd-factory#183 は S2 の前提へ） |
| A2 #3200 | merge | X1（規則）・AD1（まとめたクエリ） |
| A3a #3228・A3b #3219・A3c-1 #3214・A3c-2 #3229・A3d #3220・A3e-1 #3230・A3e-2 #3231・A3f-1 #3211・A3f-2 #3212・A3g #3221 | drop | —（旧いコードの兆候直し。解消する Issue は持ち主の新しい子へ） |
| DC1 #3197・SF1a #3198 | keep | D1 |
| SF1b #3203 | reshape | D1（結果 JSON の契約で `cause` を初日から必須に） |
| DC5 #3213 | merge | D1 |
| DC2 #3201・RF2 #2991・RF3 #2996・W8 #3241 | merge | X1 |
| DC3 #3204・DC4 #3205・DC6 #3206 | keep | D2 |
| DC7 #3232・SF8 #3234・SF10 #3208 | merge | P1 |
| SF5 #3224 | reshape | P1（設計の工程の定義に最後の判定を置かない） |
| DC8 #3222 | drop | —（持ち主へ寄せる後始末） |
| SF2 #3215 | keep | C3 |
| N11 #3267 | merge | C3 |
| SF3 #3153 | reshape | AD1（分割ジョブとまとめのチェックを区別する形で作る） |
| SF4 #3223 | merge | AD1 |
| SF6 #3226・N10 #3266 | keep | AD3 |
| SF7 #3225 | keep | C2 |
| RF4 #2963・RF5a #2993 | reshape | C2（判断を純粋な関数で最初から書く） |
| N2b #3257 | merge | C2 |
| SF9 #3207・RF6a #2992・RF6b #3254・N3 #3258 | merge | P3 |
| SF11 #3209 | merge | M1 |
| RF5b #3253・RF7 #2994 | reshape | M1 |
| SF12 #3216 | merge | M3 |
| N4 #3259 | keep | M3 |
| SF13 #3227 | keep | P2 |
| K1 #3217・K2 #3235・K3 #3210 | reshape | AD2 |
| W1 #3238〜W7 #3240（7 本） | drop | —（新しいディレクトリは用語集の語で書く） |
| RF1 #2990 | keep | C1 |
| RF1b #3252 | drop | —（#2640・#2827・#3053 は C2・P3 へ） |
| RF8 #2995 | reshape | M2（段階ごとのモジュールで最初から） |
| N1 #3255 | keep | C4 |
| N2a #3256 | keep | C5 |
| N5 #3260 | keep | M4 |
| N6 #3261 | keep | M5 |
| N7 #3262 | keep | M6 |
| N8a #3263・N8b #3264 | keep | M7 |
| N9 #3265・N12 #3268 | reshape・keep | T1（N9 の残骸と重複のテストを削る部分は drop） |

集計: keep 20・reshape 13・merge 18・drop 21（計 72）。

## ラベルの付け替え

ラベル `rebuild-1142` の開いている Issue は 294 件（2026-11-14。行き先の表の 283 件から外した 6 件を引き、後から起票した子 17 件を足した数）。

| 規則 | 対象 | 件数の見込み |
| --- | --- | ---: |
| R1 | 写す旧い子（keep・reshape・merge）が解消する Issue は、上の表の新しい子へそのまま付け替える | 約 199 |
| R2 | 要らなくなった旧い子（drop）が解消する Issue は、本文が名指す関数・ファイルの持ち主の新しい子へ付け替える（A3d の 5 件は M3、A3g の 4 件は M4〜M7、A3e の 7 件は AD1・M2・T1、A3a・A3c-2 の 4 件は D1・C2、RF1b の 3 件は C2・P3） | 23 |
| R3 | 写す旧い子そのもの 51 本は、取り込んだ新しい子の PR の closing keywords で閉じる | 51 |
| R4 | 要らなくなった旧い子そのもの 21 本は、S2 の PR で理由を添えて閉じる | 21 |
| R5 | develop で実装済みの 11 件は、持ち主の新しい子で再現の手順を通して閉じる（R1 に含む） | — |

付け替えは承認の後、新しい子の起票と同時に行い、行き先の表 [issue-1142-design-labels.md](issue-1142-design-labels.md) の新しい列に写す。新しい子 23 本（P1・P3・P4 を除く）にもラベル `rebuild-1142` を付ける。
