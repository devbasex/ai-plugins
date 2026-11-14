# #1142 の詳細設計: 別のディレクトリでの作り直しの配置・順番・入れ替え

設計文書 [issue-1142-design-next.md](issue-1142-design-next.md) を、ファイル配置・子 Issue・流す順番・入れ替えの手順へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。

**部品の中の関数の仕様は、材料にする設計の詳細設計が持つ。** この文書は、新しいディレクトリの全体の配置と、子をまたぐもの（自分の名前の求め方・工程から作業を呼ぶ層・入れ替え）だけを書く。材料の節は、末尾の「既存の部品の再利用」の表で子ごとに引く。

**旧い実装計画は使わない。** [issue-1142-design-detail.md](issue-1142-design-detail.md) の「実装計画」「流す順序」「レーンへの割り当ての案」は、この文書の表に置き換わる。[issue-1142-design-labels.md](issue-1142-design-labels.md) の行き先の列も同じである。

## 実装計画

| 子 | Issue | 中身 | 取り込む旧い子・Issue | 先に要るもの | 順番 | レーン |
| --- | --- | --- | --- | --- | ---: | --- |
| X0 | #3282 | 初期構成（`plugin.json`・依存の lock・エントリポイント・`identity.py`・CI のジョブ・テストの置き場） | 無し | 無し | 1 | lrebuild |
| X1 | #3283 | 検査 1 本（兆候 0 件・構造の規則・輪・事実の重複・禁止語・ループの中の外部呼び出し・I1〜I3） | A0・A2（規則）・DC2・RF2・RF3・W8 | X0 | 2 | lrebuild |
| D1 | #3284 | 契約と読み手（事実の表・止まった理由の表・結果 JSON で `cause` を必須・上限の宣言） | DC1・SF1a・SF1b・DC5 | X0 | 2 | lr-data |
| C1 | #3285 | 判断のループ・判断の関数の型・DBOS の殻 | RF1 | X0 | 2 | lr-flow |
| T1 | #3286 | テスト基盤（隔離・揺れない待ち・同時実行の枠） | N9・N12 | X0 | 2 | lr-fix-b |
| D2 | #3287 | 領域の表（ランタイム・収束ループ・宣言・環境変数・PR の見出し・指示書） | DC3・DC4・DC6 | D1 | 3 | lr-data |
| AD1 | #3288 | GitHub・git のアダプターと CI の結果の監視・落ちたチェックの分類 | A2（クエリ）・SF3（#3153）・SF4 | D1 | 3 | lr-fix-a |
| AD2 | #3289 | テキストと通知のアダプター | K1・K2・K3 | X0 | 3 | lr-data |
| AD3 | #3290 | LLM（答えをスキーマで縛る）と外部 CLI のアダプター・実行の識別子 | SF6・N10 | D1 | 3 | lr-fix-b |
| C2 | #3291 | プランの実行（遷移・キュー・組み立て・再開・重なり・待ちの `next`） | RF4・RF5a・SF7・N2b | C1・D1 | 3 | lr-flow |
| C3 | #3292 | 行き先の選び方とプランの雛形 | SF2・N11 | C2 | 4 | lr-flow |
| C4 | #3293 | 参照の部品 | N1 | C2・AD1 | 4 | lr-fix-a |
| C5 | #3294 | worker の規則文と受け渡し | N2a | C2・AD3 | 4 | lr-fix-b |
| P1 | #3092 | 工程の定義・スプリントの進行表・工程から作業を呼ぶ層 | #3092・DC7・SF8・SF10・SF5 | C2・D2 | 4 | lr-rf |
| P2 | #3295 | 承認ゲート（答えごとの行き先・問いの区分） | SF13 | P1 | 5 | lr-rf |
| M2 | #3296 | リリース（段階ごとのモジュール） | RF8 | P1・AD1 | 5 | lr-fix-a |
| M3 | #3297 | 中継と hook | N4・SF12 | D2・AD3 | 5 | lr-fix-b |
| M4 | #3298 | cross-refactoring | N5 | C3・AD3 | 5 | lr-data |
| M5 | #3299 | cross-review | N6 | C3・AD1・AD3 | 5 | lr-flow |
| P3 | #3097 | 計画とレーンの進行表 | #3097・RF6a・RF6b・SF9・N3 | P1・P2 | 6 | lr-rf |
| M1 | #3300 | 開発ループとスプリント | RF7・RF5b・SF11 | P1・M2 | 6 | lr-fix-a |
| M6 | #3301 | 棚卸し | N7 | P1 | 6 | lr-data |
| M7 | #3302 | 記録（確定仕様化・引継ぎ・用語集・使用量・MVV） | N8a・N8b | P1・P2 | 6 | lr-fix-b |
| P4 | #3098 | 組み換えの工程 | #3098 | P3・M1 | 7 | lr-rf |
| S1 | #3303 | Skill 本文・エージェント定義・hook のエントリポイント・配る Skill の表 | 無し | P4・M3〜M7 | 8 | lrebuild |
| S2 | #3304 | 入れ替え・volareinc/nd-factory#183・ラベルの Issue がすべて閉じたかの確かめ | A1a の前提の分 | S1 | 9 | lrebuild |

**Issue の列は承認の後に起票した番号である（2026-11-13）。** P1・P3・P4 は既存の Issue を使い、ほかの 23 本にはラベル `rebuild-1142` を付けた。

**PR の単位は子ごとに 1 本**で、revert 1 回で 1 つ前に戻る。消す古いテストは無い（今のテストは S2 で `plugins/ndf/` ごと消える）。
## 流す順番

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

先に要るものは、どれも前の順番の子である（逆転は無い）。1 つのレーンが同じ順番に持つ子は 2 本以下で、プラン 2 本に収まる。

## レーンの割り当て

| レーン | 子 | after |
| --- | --- | --- |
| lrebuild | X0・X1・S1・S2（と順番の監視） | 無し |
| lr-flow | C1・C2・C3・M5 | 無し（X0 のマージを lrebuild が知らせる） |
| lr-data | D1・D2・AD2・M4・M6 | 同上 |
| lr-rf | P1・P2・P3・P4 | 同上 |
| lr-fix-a | AD1・C4・M2・M1 | 同上 |
| lr-fix-b | T1・AD3・C5・M3・M7 | 同上 |

lflow・lbr2・lsw は after から外す。lr-* は今 after に lrebuild を持って止めてある。承認の後にレーンの定義を書き直し、X0 がマージされたら lrebuild がオーガナイザーへ知らせる。

## 旧い子の行き先

`keep` は新しい子へそのまま引き継ぐ、`reshape` は形を変えて引き継ぐ、`merge` は他とまとめて引き継ぐ、`drop` は要らなくなる（S2 の PR で理由を添えて閉じる）。

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
| R1 | 引き継ぐ旧い子（keep・reshape・merge）が解消する Issue は、上の表の新しい子へそのまま付け替える | 約 199 |
| R2 | 要らなくなった旧い子（drop）が解消する Issue は、本文が名指す関数・ファイルの持ち主の新しい子へ付け替える（A3d の 5 件は M3、A3g の 4 件は M4〜M7、A3e の 7 件は AD1・M2・T1、A3a・A3c-2 の 4 件は D1・C2、RF1b の 3 件は C2・P3） | 23 |
| R3 | 引き継ぐ旧い子そのもの 51 本は、取り込んだ新しい子の PR の closing keywords で閉じる | 51 |
| R4 | 要らなくなった旧い子そのもの 21 本は、S2 の PR で理由を添えて閉じる | 21 |
| R5 | develop で実装済みの 11 件は、持ち主の新しい子で再現の手順を通して閉じる（R1 に含む） | — |

付け替えは承認の後、新しい子の起票と同時に行い、行き先の表 [issue-1142-design-labels.md](issue-1142-design-labels.md) の新しい列へ書く。新しい子 23 本（P1・P3・P4 を除く）にもラベル `rebuild-1142` を付ける。

## 処理の手順

**子 Issue の PR（S2 の前）**

| # | 手順 | 確かめ |
| ---: | --- | --- |
| 1 | 子の worktree を develop から作る（今の NDF の `worktree` を使う） | — |
| 2 | `plugins/ndf-next/` の担当のサブパッケージと、そのテスト（`tests/<パッケージ>/`）だけを書く。足してよいのは `cli.py` の副命令の表への 1 行と、契約（D1）を変えるときの下流の読み手とそのテストだけ（概要設計の「集約」） | 検査 I2 |
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

## ファイル配置

子 Issue ごとにサブパッケージを分け、触るファイルが子どうしで重ならないようにする。Python のパッケージ名は `ndf` である（プラグイン名から切り離す。決定 75）。

| パス | 中身 | 作る子 |
| --- | --- | --- |
| `plugins/ndf-next/.claude-plugin/plugin.json` | Claude Code 用の定義。名前 `ndf-next`・版 `11.0.0-dev.N` | X0 |
| `plugins/ndf-next/.codex-plugin/plugin.json` | Codex 用の定義（同じ名前と版） | X0 |
| `plugins/ndf-next/pyproject.toml` | 依存の宣言（DBOS・jsonschema・httpx ほか。前の企画の libraries の選択） | X0 |
| `plugins/ndf-next/uv.lock` | 依存の lock | X0 |
| `plugins/ndf-next/bin/ndf` | エントリポイント 1 本。依存の仮想環境を用意して `python -m ndf` を呼ぶ | X0 |
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
| `plugins/ndf-next/src/ndf/progress/lane.py` | レーンの進行表 | P3 |
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
| `plugins/ndf-next/tests/` | テスト（子ごとに `tests/<パッケージ>/`）。置き場は X0 が作り、`tests/<パッケージ>/` の中身はそのパッケージを作る子が書く | X0（置き場）・各子（中身） |
| `plugins/ndf-next/skills/` | Skill 本文 | S1 |
| `plugins/ndf-next/agents/` | エージェント定義 | S1 |
| `plugins/ndf-next/hooks/` | hook のエントリポイント | S1 |
| `plugins/ndf-next/manifests/` | 配る Skill の表 | S1 |
| `.github/workflows/ndf-next.yml` | 新しいディレクトリのテストと検査 | X0 |

## クラスと関数の構造

| モジュール | 型・関数 | 責務 |
| --- | --- | --- |
| `identity.py` | `PluginIdentity`（`root`・`name`・`prefix`・`state_dir`）、`load() -> PluginIdentity` | 自分の名前を `plugin.json` と環境変数から求める |
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
| `invoke.invoke(stage, context, cwd)` | 外部呼び出し | 工程・文脈・作業ディレクトリ | `StageResult`（結果 JSON＋`outputs`） | 作業（プラン・LLM・子プロセス）を起動する | `kind` ごとに呼び分ける（`plan` はプランの実行、`skill` は LLM の起動、`script` は子プロセス） |
| `invoke.record_outputs(table, row, result)` | 書き込み | 進行表・行・結果 | 無し | 進行表へ事象を 1 件追記する | 事象 `outputs_recorded` を 1 件追記する。鍵は工程の定義の `outputs` に列挙したものだけ |

## 異常系の処理

| 関数 | 異常 | 扱い |
| --- | --- | --- |
| `identity.load()` | `plugin.json` が読めない | 起動を止める（終了コードは止まった理由の表の `config_broken`） |
| `identity.prepare_state_dir(ident)` | 旧い状態の置き場の退避先が既にある | 日付に時刻を足した名前で退避する。消さない |
| `invoke.invoke(stage, context, cwd)` | 文脈に入力の鍵が無い | 行を始めない。止まった理由 `input_missing` と、足りない鍵を返す |
| `invoke.invoke(stage, context, cwd)` | 作業が結果 JSON を返さない | 行を失敗にし、止まった理由 `bad_report` を返す |
| `invoke.record_outputs(table, row, result)` | 工程の定義に無い鍵を作業が出力した | 書かずに捨て、結果の `warnings` に載せる |
| 工程の定義の登録（P1） | 同じ範囲を 2 つの工程が受け持つ | 登録で断る（#3092 の順序の決まりに足す） |

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
| 各副命令の結果 | 結果 JSON を標準出力へ 1 行（今の NDF と同じ規約） |
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

## hook の基盤（#3345）

設計文書の節「hook の契約（#3345）」と決定 88〜96 を、ファイル・関数・データへ下ろした。持ち主の子は M3 #3297（判定と契約表）と S1 #3303（入口と登録）である。M3 が D1 の `limits.json` と D2 の環境変数の表を変える分は、契約を変える PR の規則（概要設計の「集約」）で、同じ PR で読み手とそのテストも直す。

### ファイル配置（hook の基盤）

| パス | 中身 | 作る子 |
| --- | --- | --- |
| `plugins/ndf-next/contracts/hooks/guards.json` | guard の表（名前・事象・Tool・順序・`strict`・`suggests`・止める鍵の作り方） | M3 |
| `plugins/ndf-next/contracts/hooks/runtimes.json` | ランタイムの契約表（Claude Code と Codex の事象・読む鍵・判定ごとの出力の形・timeout の上限） | M3 |
| `plugins/ndf-next/contracts/hooks/imports.json` | 入口が import してよいモジュールの表（標準ライブラリのほかに許す依存と、NDF の中のモジュール） | M3 |
| `plugins/ndf-next/contracts/limits.json` | 事象ごとの予算と `hook.pushback`・`hook.deny-repeat`・`hook.transcript-tail` を足し、`hook.ask-forward`・`hook.git-*` を締め切りの中へ直す | M3（D1 の契約を変える） |
| `plugins/ndf-next/contracts/domains/`（環境変数の表） | `NDF_HOOK_OFF` の 1 行 | M3（D2 の契約を変える） |
| `plugins/ndf-next/src/ndf/domains/hooks/context.py` | `HookContext`（1 回だけ作る解析） | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/dispatch.py` | guard の表を順に呼び、判定をまとめ、注入を切り、出力を契約表の形にする | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/counter.py` | セッションの数え手 | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/budget.py` | 予算の読み手と順序の検査 | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/metrics.py` | 計測の 1 行と `ndf hooks stats` | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/outbox.py` | 送信待ちへの書き込みと、切り離した送り手の起動 | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/env.py` | lock の hash ごとの依存の環境の用意と、使われない環境の片付け。根の目印と最後に使った時刻の読み書き。標準ライブラリだけで書く（システムの `python3` から呼ぶ） | M3 |
| `plugins/ndf-next/src/ndf/domains/hooks/strict.py` | `strict` の guard（本番チャネルへのマージ）。標準ライブラリだけで書く（決定 91） | M3 |
| `plugins/ndf-next/tests/domains/hooks/samples/<ランタイム>/<版>/<事象>.json` | 本体の版ごとの入力の見本 | M3 |
| `plugins/ndf-next/hooks/entry.py` | 環境の python の入口。標準ライブラリで事象を読み、先に `strict` を呼び、次に `dispatch` を呼び、どの失敗でも終了コード 0 で終わる | S1 |
| `plugins/ndf-next/hooks/boot.py` | 環境が無いときの入口（システムの `python3`）。`strict` を呼び、SessionStart なら `prepare.py` を切り離して起動する。標準ライブラリだけ | S1 |
| `plugins/ndf-next/hooks/prepare.py` | 切り離して動く環境の用意。`env.prepare` と `env.prune` を呼ぶ。標準ライブラリだけ | S1 |
| `<キャッシュ>/<プラグイン名>/venv/<uv.lock の hash>/` | 依存の環境。中の `.last-used` の更新時刻が最後に使った時刻 | `env.prepare` が作る |
| `<キャッシュ>/<プラグイン名>/venv/<uv.lock の hash>.lock`・`.failed` | 用意の排他のファイルと、用意に失敗した理由 | `prepare.py` |
| `<キャッシュ>/<プラグイン名>/roots/<プラグインの置き場の sha256 の先頭 16 字>.json` | 根の目印。導入先 1 つが指す環境 | SessionStart の入口 |
| `plugins/ndf-next/hooks/claude.json`・`codex.json` | 登録。事象ごとに 1 行で、`ndf hooks render` が契約表と予算から作る | S1 |

### 型と関数

| モジュール | 型・関数 | 責務 |
| --- | --- | --- |
| `context.py` | `HookContext`（`runtime`・`event`・`payload`・`tool`・`tool_input`、遅延で 1 度だけ作る `shell_tree`・`transcript_tail`・`last_reply_kind`） | 入力の解析を 1 回にする（IH1） |
| `dispatch.py` | `GuardSpec`（`name`・`events`・`tools`・`order`・`strict`・`suggests`・`deny_key`）、`Verdict`（`kind`: `pass`・`advise`・`deny`・`pushback`、`reason`・`context`・`key`） | guard の表の行と判定 |
| `dispatch.py` | `run(ctx, specs, limits, counter) -> Outcome`、`render(outcome, runtime_table) -> tuple[int, str]` | 表の順に呼び、判定をまとめて出力を作る |
| `counter.py` | `Counter.open(session_id) -> Counter`、`allow_pushback()`・`allow_deny(key)`・`reset(source)` | 利用者の入力ごとの差し戻しと同じ拒否の回数（IH5） |
| `budget.py` | `Budget`（事象ごとの `p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars`）、`check_budget(limits, registrations) -> list[str]` | 予算の順序の検査（IH3） |
| `metrics.py` | `write_line(record)`、`stats(days) -> dict`、`prune(days) -> int` | 計測（IH9）と古い記録の片付け |
| `outbox.py` | `enqueue(kind, payload)`、`kick()` | 通知を送信待ちへ書き、送り手を切り離して起動する（IH7） |
| `env.py` | `prepare(ident) -> Path`、`prune(ident, now) -> list[Path]`、`mark_root(ident, plugin_root)`、`touch_used(ident)`、`kick_prepare(ident)` | 依存の環境の用意と片付け・根の目印と最後に使った時刻（決定 89） |
| `strict.py` | `check(raw_payload, root) -> Verdict \| None` | `strict` の guard。標準ライブラリだけ（決定 91） |
| `hooks/entry.py` | `main(argv) -> int` | 環境の python の入口（IH2・IH4） |
| `hooks/boot.py` | `main(argv) -> int` | 環境が無いときの入口（IH2・IH4） |

### 関数の仕様（hook の基盤）

| 関数 | 種類 | 入力 | 出力 | 副作用 | 前提と後条件 |
| --- | --- | --- | --- | --- | --- |
| `entry.main(argv)` | 外部呼び出し | `argv[1]` のランタイムと事象・標準入力の payload | 終了コード（いつも 0。止めるときも、ランタイムの契約表の形の JSON を標準出力へ出す） | 計測の 1 行・数え手の更新。SessionStart のときだけ `env.mark_root`・`env.touch_used`・`metrics.prune(14)` | 環境の python で動く。`dispatch` と依存を import する前に `strict.check` を呼び、止める答えならそれを返して終わる。import の失敗・例外・締め切り超えは何も出さずに 0 で終わる |
| `boot.main(argv)` | 外部呼び出し | `entry.main` と同じ | 終了コード（いつも 0） | SessionStart のときだけ `env.mark_root` と `env.kick_prepare`。`.failed` があれば 1 行の案内を出す | システムの `python3` で動き、標準ライブラリだけを import する。`strict.check` のほかの guard は呼ばない。版がシステムの `python3` の下限（`imports.json` の `boot.python`）より古ければ 0 で終わる |
| `strict.check(raw_payload, root)` | 読むだけ | payload・作業ディレクトリ | 止める答えか `None` | 無し | シェルの字句は `shlex` で読む。字句を読めない・宣言（`.ndf/project.json` の `delivery`）を読めないときは止める答えを返す |
| `dispatch.run(ctx, specs, limits, counter)` | 純粋に近い（guard が副作用を持つときは表に書く） | 文脈・表・予算・数え手 | `Outcome`（まとめた判定・guard ごとの所要と判定） | 無し（数え手は `counter` を通す） | 締め切りの残りが余白を下回ったら、残りの guard を呼ばずに通す。`deny` が上限を超えたら `advise` へ下げる。複数の判定は `deny` > `pushback` > `advise` > `pass` の順で 1 つにまとめる |
| `dispatch.render(outcome, runtime_table)` | 純粋な関数 | 判定・契約表 | 終了コードと標準出力 | 無し | 注入の文が上限の字数を超えたら、`<状態の置き場>/hooks/notes/` へ書いたファイルのパスと先頭の要点に置き換える |
| `counter.allow_pushback()` | 書き込み | 無し | 真偽 | 状態のファイルの回数を 1 増やす | 今の入力の後の回数が `hook.pushback` 未満なら真 |
| `counter.reset(source)` | 書き込み | 入力の出どころ（`human`・`relay`） | 無し | 回数を 0 にする | `relay` の印があれば印を消すだけで、回数は変えない |
| `budget.check_budget(limits, registrations)` | 純粋な関数 | 予算と登録 | 破れの一覧 | 無し | 部品の上限 ≤ 締め切り − 余白、締め切り ≤ 登録の timeout、timeout の無い登録が 0 件 |
| `outbox.kick()` | 外部呼び出し | 無し | 無し | 送り手を `start_new_session` で起動する | 待たない。送り手が走っていれば起動しない |
| `env.prune(ident, now)` | 書き込み | 識別子・時刻 | 消した置き場 | 環境の置き場と、指す先の導入先が無くなった根の目印を消す | `prepare.py` だけが呼ぶ。`roots/` の目印のうち `plugin_root` が今もある目印の `lock_hash` の置き場と、`.last-used` の更新時刻が 7 日以内の置き場は消さない。排他のファイルを持たれている置き場も消さない |
| `env.mark_root(ident, plugin_root)` | 書き込み | 識別子・導入先 | 無し | 根の目印を書く | SessionStart の入口（`entry`・`boot`）だけが呼ぶ。中身が同じなら書かない |
| `env.touch_used(ident)` | 書き込み | 識別子 | 無し | `.last-used` の更新時刻を今にする | SessionStart の `entry` と、用意が終わったときの `env.prepare` だけが呼ぶ。予算の経路（PreToolUse など）では呼ばない |
| `env.kick_prepare(ident)` | 外部呼び出し | 識別子 | 無し | `prepare.py` を `start_new_session` で起動する | 待たない。排他のファイルを持たれていれば起動しない |
| `metrics.prune(days)` | 書き込み | 日数 | 消したファイルの数 | `hooks/<日付>.jsonl` のうち `days` 日より古いものを消す | SessionStart の `entry` だけが呼ぶ |

### 異常系の処理（hook の基盤）

| 場所 | 異常 | 扱い |
| --- | --- | --- |
| 登録の command | 環境の python が無い | システムの `python3` で `boot.main` を起動する。`python3` も無ければ何もせずに 0 |
| `entry.main` | 依存の import に失敗した | `strict.check` は import の前に済んでいる。ほかは入口の `except` で何も出さずに 0 で終わる |
| `prepare.py` | `uv` が無い・依存を取得できない・作れない | 一時の置き場を消し、`<hash>.failed` に理由を書いて終わる。次の SessionStart の `boot.main` が 1 行の案内を出し、用意を起動し直す |
| `entry.main` | payload が JSON でない・契約表に無い事象・知らない Tool | 何も出さずに 0。計測に `input_shape` か `unknown_event` と書く |
| `dispatch.run` | guard が例外を投げた | `strict` でなければその guard を `pass` にし、計測に例外の種類を書く。`strict` なら `deny` と固定の理由 |
| `dispatch.run` | 締め切りの残りが余白を下回った | 残りの guard を呼ばずに通す。計測に `deadline` と書く |
| `counter` | 排他を締め切りまでに取れない・状態のファイルが壊れている | 数えずに通す（差し戻さず、拒否は `advise` へ下げる。`strict` は除く） |
| `metrics.write_line` | 書けない | 判定を変えずに捨てる |
| `budget.check_budget` | SessionStart で破れを見つけた | 1 行の案内を出し、続ける |

### データと定数（hook の基盤）

**`limits.json` に足す鍵**（値は設計文書の決定 90 の表と判断 (h)）

| 鍵 | 中身 |
| --- | --- |
| `hook.budget.<事象>` | `p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars` |
| `hook.pushback` | `attempts: 2`（利用者の入力ごとの Stop の差し戻しの合計） |
| `hook.deny-repeat` | `attempts: 2`（同じ guard・理由・対象の拒否） |
| `hook.transcript-tail` | 会話の記録の末尾を読む大きさ（`bytes`。最後の応答と文脈の量が入る大きさ） |
| `hook.budget.<事象>.<ランタイム>` | ランタイムで値が違う事象の予算（Codex の SessionEnd）。あれば `hook.budget.<事象>` より先に読む |

**セッションの数え手**（`<状態の置き場>/hooks/sessions/<session_id>.json`）

| 鍵 | 型 | 中身 |
| --- | --- | --- |
| `turn` | 整数 | 利用者の入力の番号。利用者の入力で 1 増える |
| `pushbacks` | 整数 | 今の入力の後の差し戻しの数（理由をまたぐ） |
| `denies` | 辞書 | 拒否の鍵（guard・理由・対象の hash）ごとの数 |
| `relay_mark` | 真偽 | 中継が次の入力を送る前に立てる印 |

**計測の 1 行**（`<状態の置き場>/hooks/<日付>.jsonl`）: `at`・`runtime`・`event`・`tool`・`path`（`entry` か `boot`）・`total_ms`・`guards`（名前・所要・判定の列）・`verdict`・`inject_chars`・`error`・`pushbacks`。14 日より古いファイルは SessionStart の `entry` が `metrics.prune(14)` で消す。

**根の目印**（`<キャッシュ>/<プラグイン名>/roots/<プラグインの置き場の sha256 の先頭 16 字>.json`）

| 鍵 | 型 | 中身 |
| --- | --- | --- |
| `plugin_root` | 文字列 | 導入先のプラグインの置き場（`CLAUDE_PLUGIN_ROOT` か入口の `__file__` から求める） |
| `lock_hash` | 文字列 | その導入先の `uv.lock` の hash（登録が指す環境の置き場の名前） |
| `at` | 文字列 | 書いた時刻 |

書き手は SessionStart の入口（`entry`・`boot`）だけで、1 セッションに 1 回である。読み手は `env.prune` だけである。導入先ごとに 1 ファイルなので、版の違う導入先（プラグインのキャッシュに残る旧い版・`--plugin-dir` の作業ディレクトリ）が同じキャッシュを使っても、互いの目印を上書きしない。

**最後に使った時刻**（`<キャッシュ>/<プラグイン名>/venv/<uv.lock の hash>/.last-used` の更新時刻）: 書き手は SessionStart の `entry` と、用意が終わったときの `env.prepare` である。読み手は `env.prune` だけである。PreToolUse などの予算の経路は書かない。根の目印から指されなくなった環境（導入先が消えた旧い版）を、7 日の間は消さずに残すために使う。

### 並行と排他（hook の基盤）

| 対象 | 排他の仕方 |
| --- | --- |
| 同じセッションの並列の Tool の呼び出し | 数え手のファイルを `fcntl` の排他で開き、待ちは締め切りの中だけにする。取れなければ数えずに通す |
| 計測の記録 | 1 行を 1 回の追記（`O_APPEND`）で書く |
| 依存の環境 | 置き場は lock の hash ごとに別で、作るときは置き場ごとの排他のファイル（`<hash>.lock`。`fcntl`）を取り、一時の置き場に作ってから名前を変える。排他を取れなければ用意しない。使い中の置き場は作り直さない。`env.prune` は排他を持たれている置き場を消さない |

### 単体テストの観点（hook の基盤）

| 対象 | 観点 |
| --- | --- |
| IH1 | 1 回の事象で、payload の解析・構文木・会話の記録の読み込み・最後の応答の分類の関数がそれぞれ 1 回だけ呼ばれる。登録の各事象が 1 行 |
| IH2 | 入口からの import の閉包が `contracts/hooks/imports.json` に収まり、`boot.py`・`prepare.py`・`strict.py`・`env.py` からの閉包は標準ライブラリだけ。環境の python を消した状態で登録の command を打つと、`strict` の guard に当たる入力のほかは出力なし・0。環境を消した状態から SessionStart の登録を打つと、待たずに 0 で返り、用意が終わった後の登録は `entry` で動く |
| IH3 | `check_budget` が、部品の上限が締め切りを超える表・timeout の無い登録を落とす |
| IH4 | guard に例外を投げさせても通す。`strict` の guard だけが止める。環境が無い・依存の import を落とした状態でも、本番チャネルへのマージを止める |
| IH5 | 本文の待ちの応答で Stop を 5 回続けても差し戻しは 2 回。同じ範囲を 5 回読んでも拒否は 2 回で、3 回目から案内。利用者の入力で数え直し、中継の印のある入力では数え直さない |
| IH6 | 全 guard の `suggests` の Tool の見本を全 guard に通して、止める判定が出ない |
| IH7 | socket の生成を禁じた状態で、全 guard を見本の入力で通せる。`subprocess` の呼び出し先が git の許した副命令と NDF の送り手だけ |
| IH8 | 上限の字数を超える文を返す guard の出力が、パスと要点に置き換わる |
| IH9 | 計測の書き込みを失敗させても判定が変わらない |
| IH10 | すべての見本を入口に通して、出力が契約表の形（Codex の Stop は `hookSpecificOutput` を持たない）に合う |

## #3345 で子へ足す受け入れ条件

設計の決定 88〜96 を、子の受け入れ条件へ下ろした表である。**設計承認の後に、M3 #3297 と S1 #3303 の本文の受け入れ条件へこの表の行を写す。** 番号は設計文書の「#3345 の受け入れ条件と、解いた所の対応」が指す。

| # | 子 | 受け入れ条件 | 決定・不変条件 |
| --- | --- | --- | --- |
| M3-H1 | M3 | `domains/hooks/` の `dispatch.run` が guard の表（`contracts/hooks/guards.json`）の順に guard を呼び、payload・シェルの構文木・会話の記録の末尾・最後の応答の分類は `HookContext` が 1 回の事象で 1 度だけ作る。テストで各解析の関数の呼び出しが 1 回であることを確かめる | 決定 88・IH1 |
| M3-H2 | M3 | AskUserQuestion の判定と中継へのシグナルファイルの作成、Stop の待ちの知らせ・中継の印・人のいない実行の差し戻しを、同じプロセスの中の関数として呼ぶ。別のプロセスで判定し直す経路を作らない | 決定 88・IH1 |
| M3-H3 | M3 | 入口から import でたどれるモジュールが `contracts/hooks/imports.json` に収まることを構文木で確かめるテストを置く。`bin/ndf`・依存を入れ直す処理・通信のアダプター・`core/durable.py` を含まない。`hooks/boot.py`・`hooks/prepare.py`・`strict.py`・`env.py` からの閉包は標準ライブラリだけであることも同じテストで確かめる | 決定 89・決定 91・IH2・IH7 |
| M3-H4 | M3 | `limits.json` に事象ごとの予算（`hook.budget.<事象>`）を足し、`hook.ask-forward`・`hook.git-rev-parse`・`hook.git-remote` ほか部品の上限を `締め切り − 余白` 以下にする。`check_budget` を CI のテストと SessionStart で打つ | 決定 90・IH3 |
| M3-H5 | M3 | 会話へ入れる文を事象ごとの `inject_chars` で切り、超える分はファイルへ書いてパスを入れる | 決定 90・IH8 |
| M3-H6 | M3 | 例外・締め切り超え・知らない事象と Tool・入力の形の違いで、出力なし・終了コード 0 になる。`strict` は本番チャネルへのマージを見る guard だけで、その guard は自分の失敗で止める側の答えを返す。`strict` の guard（`strict.py`）は標準ライブラリだけで書き、入口が `dispatch` と依存の import より先に呼ぶ。環境を消した状態と依存の import を落とした状態で、本番チャネルへのマージを止めることをテストで確かめる | 決定 91・IH4 |
| M3-H7 | M3 | セッションの数え手を作る。Stop の差し戻しは理由をまたいで利用者の入力ごとに `hook.pushback`（2）回まで、同じ guard・理由・対象の拒否は `hook.deny-repeat`（2）回の後に案内へ下げる。利用者の入力で数え直し、中継が送った入力では数え直さない。今の文脈の上限の拒否と同じ範囲を読む拒否も、この規則に乗せる | 決定 92・IH5 |
| M3-H8 | M3 | guard の表の `suggests` の Tool（sleep の guard が勧める Monitor と背景の起動を含む）の見本の呼び出しを全 guard に通すテストを置く | 決定 92・IH6 |
| M3-H9 | M3 | 入口の処理の間に socket を作らない。通知は `outbox.enqueue` で書き、`outbox.kick` で送り手を切り離して起動する。SessionStart で `git fetch` と学習の設定の読み書きを行わない。AskUserQuestion の転送を hook の中で待たない | 決定 93・IH7 |
| M3-H10 | M3 | 入口の起動ごとに計測の 1 行を書く。14 日より古い記録は SessionStart の入口だけが `metrics.prune(14)` で消し、ほかの事象ではディレクトリを走査しない。`ndf hooks stats` が事象・guard ごとの p50・p95・拒否と差し戻しの数・注入の最大を返す | 決定 94・IH9 |
| M3-H11 | M3 | ランタイムの契約表（`contracts/hooks/runtimes.json`）と、Claude Code 2.1.325・Codex 0.167.0 の見本を置き、すべての見本の解析と出力の形を CI で確かめる。Codex の Stop の出力は `hookSpecificOutput` を持たない。`ndf hooks capture` で見本を足せる | 決定 95・IH10 |
| M3-H12 | M3 | guard の止め方は `NDF_HOOK_OFF` の 1 つで、D2 の環境変数の表へ 1 行を足す。閾値は `limits.json` から読み、環境変数では受けない | 決定 96 |
| M3-H13 | M3 | 見本の入力で入口を事象ごとに 20 回起動し、p95 が予算の 3 倍を超えたら落ちる CI のテストを置く | 決定 90・決定 94 |
| M3-H14 | M3 | 依存の環境を lock の hash ごとの置き場に作り、SessionStart は用意を切り離して起動して待たない。環境が無いときの用意は、システムの `python3` で動く `boot.py` から `prepare.py` を起動する。環境を消した状態から SessionStart の登録を打ち、SessionStart の timeout の中で 0 で返ること・切り離した用意が終わって環境の python ができること・その後の PreToolUse の登録が `entry` で動く（計測の `path` が `entry`）ことを、テストで確かめる（依存の取得は CI が先に温めた uv のキャッシュから `--offline` で行う）。根の目印（SessionStart の入口が書く）から指されず、`.last-used`（SessionStart の `entry` と用意の終わりが書く）が 7 日より古い置き場を消す。予算の経路では目印と `.last-used` を書かない。使い中の置き場を作り直さない | 決定 89・決定 93 |
| S1-H1 | S1 | `hooks/claude.json` と `hooks/codex.json` は事象ごとに登録 1 行で、すべての登録に予算の timeout を付ける。登録は `ndf hooks render` が契約表と予算から作り、CI で作り直した結果と一致することを確かめる | 決定 88・決定 90・IH1・IH3 |
| S1-H2 | S1 | 登録の command は `sh -c '[ -x "$0" ] && exec "$0" "$@"; command -v python3 >/dev/null 2>&1 \|\| exit 0; exec python3 "${1%/*}/boot.py" "$@"'` で、環境の python があれば `hooks/entry.py` を直に起動し、無ければシステムの `python3` で `hooks/boot.py` を起動する。`bin/ndf` と `uv run` を通らない。環境を消した状態で全事象の登録を打ち、`strict` の guard に当たる入力のほかは出力なし・0 を確かめる。`python3` を `PATH` から外した状態でも 0 を確かめる。環境を消した状態から SessionStart の登録で用意が終わるまでを M3-H14 のテストで通す | 決定 89・決定 91・IH2 |
| S1-H3 | S1 | Codex の登録は、契約表で Codex が持つ事象（UserPromptSubmit を含む）を並べる。Codex で働かない guard は契約表に理由を書く | 決定 95 |
| S1-H4 | S1 | 「ndf-next だけを読む起動」のスクリプトで起動したセッションで、計測の記録に ndf-next の行だけが出て、ndf の hook が走らないことを確かめる | 決定 74・指摘 12 |

## 単体テストの観点

| 対象 | 観点 |
| --- | --- |
| `identity.load` | ディレクトリ名を `plugins/x` に変えても、名前と前置きが `plugin.json` から求まる |
| `identity.prepare_state_dir` | 旧い置き場が退避され、中身が残る。2 度目の起動では退避しない |
| `invoke.resolve_inputs` | 鍵が欠けると `input_missing` で止まる。余分な鍵は渡さない |
| `invoke.record_outputs` | 宣言に無い鍵は書かない。前の工程の `pr` が次の工程の引数に届く |
| 検査 I1〜I3 | `"ndf-next"` の文字・`plugins/ndf/` の import・`plugins/ndf/` の差分を、それぞれ落とす |
