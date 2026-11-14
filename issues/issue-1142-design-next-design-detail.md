# #1142 の詳細設計: 別のディレクトリでの作り直しの配置・順番・入れ替え

設計文書 [issue-1142-design-next.md](issue-1142-design-next.md) を、ファイル配置・子 Issue・流す順番・入れ替えの手順へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。

**部品の中の関数の仕様は、材料にする設計の詳細設計が持つ。** この文書は、新しいディレクトリの全体の配置と、子をまたぐもの（自分の名前の求め方・工程から作業を呼ぶ層・入れ替え）だけを書く。材料の節は、末尾の「既存の部品の再利用」の表で子ごとに引く。

**旧い実装計画は使わない。** [issue-1142-design-detail.md](issue-1142-design-detail.md) の「実装計画」「流す順序」「レーンへの割り当ての案」は、この文書の表に置き換わる。[issue-1142-design-labels.md](issue-1142-design-labels.md) の行き先の列も同じである。

> **#3347:** この文書の「実装計画」「流す順番」「レーンの割り当て」は、[issue-3347-design-detail.md](issue-3347-design-detail.md) の同じ名前の節が置き換える（P1 を P1a・P1b に分け、書かれていない依存を足し、P4 を lrebuild へ移した。決定 97〜99）。

## 実装計画

| 子 | Issue | 中身 | 取り込む旧い子・Issue | 先に要るもの | 順番 | レーン |
| --- | --- | --- | --- | --- | ---: | --- |
| X0 | #3282 | 初期構成（`plugin.json`・依存の lock・エントリポイント・`identity.py`・CI のジョブ・テストの置き場） | 無し | 無し | 1 | lrebuild |
| X1 | #3283 | 検査 1 本（兆候 0 件・構造の規則・輪・事実の重複・禁止語・ループの中の外部呼び出し・I1〜I3） | A0・A2（規則）・DC2・RF2・RF3・W8 | X0 | 2 | lrebuild |
| D1 | #3284 | 契約と読み手（事実の表・止まった理由の表・結果 JSON で `cause` を必須・上限の宣言） | DC1・SF1a・SF1b・DC5 | X0 | 2 | lr-data |
| C1 | #3285 | 判断のループ・判断の関数の型・DBOS の殻 | RF1 | X0 | 2 | lr-flow |
| C1+ | #3285 の追補（#3344） | DBOS の殻の直し（鍵ごとの記録・形の番号・退避・待ちの一覧・DBOS 3.4.0。設計の決定 81〜87） | 無し | C1 | 2 | lr-flow |
| T1 | #3286 | テスト基盤（隔離・揺れない待ち・同時実行の枠） | N9・N12 | X0 | 2 | lr-fix-b |
| D2 | #3287 | 領域の表（ランタイム・収束ループ・宣言・環境変数・PR の見出し・指示書） | DC3・DC4・DC6 | D1 | 3 | lr-data |
| AD1 | #3288 | GitHub・git のアダプターと CI の結果の監視・落ちたチェックの分類 | A2（クエリ）・SF3（#3153）・SF4 | D1 | 3 | lr-fix-a |
| AD2 | #3289 | テキストと通知のアダプター | K1・K2・K3 | X0 | 3 | lr-data |
| AD3 | #3290 | LLM（答えをスキーマで縛る）と外部 CLI のアダプター・実行の識別子 | SF6・N10 | D1 | 3 | lr-fix-b |
| C2 | #3291 | プランの実行（遷移・キュー・組み立て・再開・重なり・待ちの `next`） | RF4・RF5a・SF7・N2b | C1・C1+・D1 | 3 | lr-flow |
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

**C1+ は #3344 の設計の差分で足した。** C1 は閉じているため Issue を開き直さず、PR から `Refs #3285 #3344` で指す。受け入れ条件は下の「#3344 で子へ足す受け入れ条件」の A 系にある。

**Issue の列は承認の後に起票した番号である（2026-11-13）。** P1・P3・P4 は既存の Issue を使い、ほかの 23 本にはラベル `rebuild-1142` を付けた。

**PR の単位は子ごとに 1 本**で、revert 1 回で 1 つ前に戻る。消す古いテストは無い（今のテストは S2 で `plugins/ndf/` ごと消える）。
## 流す順番

| 順番 | 子 | 同時の本数 |
| ---: | --- | ---: |
| 1 | X0 | 1 |
| 2 | X1・D1・C1・T1・C1+ | 5 |
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
| lr-flow | C1・C1+・C2・C3・M5 | 無し（X0 のマージを lrebuild が知らせる） |
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
| `plugins/ndf-next/pyproject.toml` | 依存の宣言（DBOS・jsonschema・httpx ほか。前の企画の libraries の選択）。DBOS の版は C1+ が 3.4.0 へ上げ、11-18 以降に 3.5.0 へ上げる（決定 87） | X0・C1+ |
| `plugins/ndf-next/uv.lock` | 依存の lock | X0・C1+ |
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
| `plugins/ndf-next/src/ndf/core/durable.py` | DBOS の殻（DBOS を import するのはここだけ）。チェックアウトの置き場・鍵ごとの記録・退避・待ちの一覧（#3344） | C1・C1+ |
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
| `core/durable.py`（C1+） | `store_root(name, cwd) -> Path`、`store_lock(root, exclusive)`（文脈マネージャ）、`prepare_store(root, now) -> StoreReady` | チェックアウトの置き場を求め、置き場のロックを取り、形の番号を確かめて用意する（決定 85・I11） |
| `core/durable.py`（C1+） | `run_name(run_key) -> str`、`run_url(root, run_key) -> str`、`run_lock(root, run_key)`（文脈マネージャ） | 実行の鍵から記録の名前・URL・排他を求める（決定 81・I6） |
| `core/durable.py`（C1+） | `launch(database_url, run_key) -> None`（C1 の `launch(database_url, executor_id, name)` を置き換える） | DBOS を鍵の名前と形の番号の版で起動する（決定 81・84） |
| `core/durable.py`（C1+） | `check_file(path) -> bool`、`retire(root, run_key) -> Path`、`sweep(root, now) -> SweepResult`、`sweep_url(url, now) -> SweepResult`、`pending(root_or_url) -> list[PendingRun]` | 壊れたファイルの確かめ・退避・消去・待ちの一覧（決定 82・83・86） |
| `progress/store.py`（P1） | `open_store(url, root) -> Engine`、`backup(engine, root, now) -> Path \| None`、`restore(root, backup) -> Path` | 進行表の DB を開く。SQLite なら WAL で開き、24 時間に 1 回の `quick_check` と控えと戻しを行う。PostgreSQL ならどれも行わない（決定 82） |

## 関数の仕様

| 関数 | 種類 | 入力 | 出力 | 副作用 | 前提と後条件 |
| --- | --- | --- | --- | --- | --- |
| `identity.load()` | 読み取り | 環境変数 `CLAUDE_PLUGIN_ROOT`（無ければ `__file__` から 3 つ上） | `PluginIdentity` | 無し | `plugin.json` の `name` を読む。`prefix` は `name + ":"` |
| `identity.prepare_state_dir(ident)` | 書き込み | 識別子 | 状態の置き場のパス | 旧い置き場の名前を変える・新しい置き場を作る | 置き場に `format.json` が無く、ほかのファイルがあれば、`<name>.v10-<日付>` へ名前を変えてから作り直す |
| `invoke.resolve_inputs(stage, context)` | 純粋な関数 | 工程の定義（`metadata.ndf.inputs` の鍵の列）と文脈 | 引数の辞書 | 無し | 鍵が文脈に無ければ `StageInputMissing(key)` を投げる |
| `invoke.invoke(stage, context, cwd)` | 外部呼び出し | 工程・文脈・作業ディレクトリ | `StageResult`（結果 JSON＋`outputs`） | 作業（プラン・LLM・子プロセス）を起動する | `kind` ごとに呼び分ける（`plan` はプランの実行、`skill` は LLM の起動、`script` は子プロセス） |
| `invoke.record_outputs(table, row, result)` | 書き込み | 進行表・行・結果 | 無し | 進行表へ事象を 1 件追記する | 事象 `outputs_recorded` を 1 件追記する。鍵は工程の定義の `outputs` に列挙したものだけ |
| `durable.store_root(name, cwd)` | 外部呼び出し | プラグイン名・作業ディレクトリ | `<git の共通ディレクトリ>/<name>/state` | 無し | `git rev-parse --git-common-dir` で求める。どの worktree から打っても同じパス |
| `durable.store_lock(root, exclusive)` | 書き込み | 置き場・排他か | 文脈マネージャ | `<root の親>/state.lock` を `fcntl.flock` で取る（`exclusive` なら `LOCK_EX \| LOCK_NB`、でなければ `LOCK_SH`） | 置き場を使うプロセスは共有で取り、プロセスが終わるまで持つ。排他は待たずに試し、取れなければ `StoreInUse` を投げる（I11） |
| `durable.maint_lock(root, wait)` | 書き込み | 置き場・待つか | 文脈マネージャ（`wait` が偽で取れなければ `None`） | `<root の親>/maint.lock`（`state.lock` の隣。`state/` の外）を `fcntl.flock` の排他で取る（`wait` なら `LOCK_EX`、でなければ `LOCK_EX \| LOCK_NB`） | 共有の目印（`progress.checked`・`dbos/.swept`）の期限の確かめから、検査・控え・走査と目印の更新までを 1 プロセスに限る。目印ごとに分けず、この 1 つで兼ねる。持ったまま別のロックを待たない（`run_lock` は `wait=False` で試すだけ） |
| `durable.prepare_store(root, now)` | 書き込み | 置き場・時刻 | `StoreReady`（`root`・`retired_to`・`pending_count`） | 置き場を作る・退避する・`format.json` を書く | 決定 85 の表のとおり。手順は次の順で、番号を読む時点と退避する時点を同じ排他の下に置く。(1) `store_lock(root, exclusive=False)` を取り、`format.json` を読む。番号が自分と同じなら共有を持ったまま返す。自分より大きければ `StoreFormatNewer` を投げ、何も書かない (2) 退避が要るなら共有を放し、`store_lock(root, exclusive=True)` を待たずに取る（取れなければ `StoreInUse`） (3) 排他の下で `format.json` を読み直す。ほかのプロセスが既に退避して番号が自分と同じなら退避せずに (5) へ進む (4) 旧い置き場の `dbos/*.lock` と `progress.sqlite.lock` をすべて排他で待たずに取ってから名前を変え、新しい置き場と `format.json` を書く。どれかが取れなければ `StoreInUse` を投げ、何も変えない（I11） (5) 排他を放して共有で取り直し、`format.json` をもう一度読む。番号が自分と同じでなければ (1) からやり直す（3 回まで。超えたら `StoreInUse`）。`flock` の排他から共有への切り替えは不可分でないため、取り直した後の確かめで割り込みを見つける |
| `durable.run_name(run_key)` | 純粋な関数 | 実行の鍵 | `"run-" + sha256(run_key)[:32]` | 無し | DBOS のアプリ名の規則（3〜256 文字・英小文字・数字・`-`・`_`）に収まる。同じ鍵には同じ名前 |
| `durable.run_url(root, run_key)` | 純粋な関数 | 置き場・実行の鍵 | `sqlite:///<root>/dbos/<run_name>.sqlite` | 無し | — |
| `durable.run_lock(root, run_key, database_url=None, wait=True)` | 書き込み | 置き場・実行の鍵・URL・待つか | 文脈マネージャ（`wait` が偽で取れなければ `None`） | SQLite: `<root>/dbos/<run_name>.lock` を `fcntl.flock` の排他で取る。URL: PostgreSQL のセッションの advisory lock `pg_advisory_lock(RUN_LOCK_SPACE, <run_name から求めた 32 ビットの値>)`（`wait` が偽なら `pg_try_advisory_lock`）を取る | `wait` なら 2 つ目は待つ（I6）。`wait=False` は片付け（`sweep`・`sweep_url`）が使う。ファイルのロックは、取った後にパスの inode と開いたファイルの inode が同じかを確かめ、違えば（片付けが `.lock` を消した）開き直して取り直す。URL の鍵のロックは DB 側の排他で、別の機械の起動と片付けも同じ鍵で直列にする（I13） |
| `durable.launch(database_url, run_key)` | 外部呼び出し | URL・実行の鍵 | 無し | DBOS を起動する。URL なら台帳 `ndf_runs` へ 1 行を書く | 呼び手は `run_lock(root, run_key, database_url)` を持った状態で呼び、DBOS を閉じるまで持つ。`name`・`executor_id` は `run_name(run_key)`、`application_version` は `app_version(run_key)`（`f"format-{FORMAT}-{run_name(run_key)}"`）。SQLite の URL なら、鍵ごとの目印 `dbos/<run_name>.checked` が `CHECK_INTERVAL` より古いか無いときだけ、起動の前に `check_file` を打ち、`ok` なら目印の時刻を今にする（鍵ごとの目印は `run_lock` の下で読み書きするため、別の排他は要らない）。PostgreSQL の URL なら、鍵のロックの下で、起動の前に台帳へ `run_name`・`run_key`・起こした時刻を書く（既にあれば書かない。I12・I13） |
| `durable.check_file(path)` | 読み取り | SQLite のファイル | `bool` | 無し | `PRAGMA quick_check` が 1 行の `ok` を返せば真。ファイルが無ければ真 |
| `durable.retire(root, run_key)` | 書き込み | 置き場・実行の鍵 | 移した先のパス | 鍵のファイルの本体・`-wal`・`-shm`・`-journal` をそろえて `dbos/retired/` へ移し、鍵ごとの目印 `dbos/<run_name>.checked` を消し、最後に `dbos/<run_name>.lock` を消す | 呼び手が `run_lock` を持ち、DBOS を閉じた後に呼ぶ（起動したプロセスが終わるときと、`sweep` が `wait=False` で取れた鍵）。耐久ワークフローがすべて `SUCCESS`・`ERROR`・`CANCELLED` のときだけ移す。待ちがあれば何も移さず消さない。移す前に `PRAGMA wal_checkpoint(TRUNCATE)` で WAL を本体へ書き戻す。`.lock` はロックを持ったまま消し、消した後に同じ鍵を待っていたプロセスは `run_lock` の inode の確かめで開き直す |
| `durable.sweep(root, now)` | 書き込み | 置き場・時刻 | `SweepResult`（`retired`・`deleted`・`skipped`・`busy`） | 終わったファイルを移し、`retired/`・`broken/` の `KEEP_DAYS` より古いファイルを消す（本体と `-wal`・`-shm`・`-journal` をそろえて） | `maint_lock(root, wait=False)` を取り、取れなければ何もせず `skipped` を真にして返す。取れたら目印 `dbos/.swept` を読み直し、`CHECK_INTERVAL` より新しければ何もせず `skipped` を真にして返す。生きている置き場の鍵ごとに `run_lock(root, run_key, wait=False)` を試し、取れた鍵だけ状態を読んで `retire` を呼ぶ。取れない鍵（開いているプロセスがいる）は移さず `busy` に数える。本体の無い `.checked` は消し、本体の無い `.lock` は `run_lock` を試して取れたときだけ消す。走査したら目印の時刻を今にしてから `maint_lock` を放す。待ちを抱えたファイルは消さない（I8） |
| `durable.sweep_url(root, url, now)` | 外部呼び出し | 置き場・URL・時刻 | `SweepResult` | 台帳 `ndf_runs` に載った鍵のうち、実行がすべて終わって `KEEP_DAYS` を過ぎた鍵の実行と台帳の行を消す | 目印と排他は `sweep` と同じ（`root` の `maint_lock` と `dbos/.swept`）。台帳の鍵ごとに `run_lock(root, run_key, url, wait=False)`（DB 側の鍵のロック）を試し、取れない鍵は `busy` に数えて触らない。取れた鍵は、ロックを持ったまま `DBOSClient.list_workflows(application_name=<鍵の名前>)` で実行（子を含む）を集め、1 つでも `PENDING`・`ENQUEUED`・`DELAYED` か、`updated_at` が `now - KEEP_DAYS` より新しいものがあれば消さない。すべて条件を満たせば集めた ID をすべて並べて `delete_workflows(ids)` を呼び（`delete_children` は使わない）、台帳の行を消してからロックを放す（I8・I12・I13） |
| `durable.pending(root_or_url)` | 読み取り | 置き場か URL | `list[PendingRun]`（`run_name`・`workflow_id`・`status`・`updated_at`） | 無し | `PENDING`・`ENQUEUED`・`DELAYED` の実行を集める |
| `store.sqlite_path(url)` | 純粋な関数 | URL | 実ファイルのパスか `None` | 無し | SQLite の URL なら `os.path.realpath` で解いた実ファイルのパスを返し、PostgreSQL の URL なら `None`。`open_store` と `restore` は同じこの関数で対象のファイルを決める。URL は #3092 の設計のまま `NDF_PROGRESS_DB` か、無ければ `sqlite:///<root>/progress.sqlite` |
| `store.db_lock(path, exclusive)` | 書き込み | 実ファイル・排他か | 文脈マネージャ | 実ファイルが置き場の中でも外でも、`<実ファイル>.lock` を `fcntl.flock` で取る（`exclusive` なら `LOCK_EX \| LOCK_NB`、でなければ `LOCK_SH`） | 同じ実ファイルは、どのチェックアウトから・どちらの URL の書き方で開いても `sqlite_path` で同じパスに解け、同じ `<実ファイル>.lock` を取る。`state.lock` はチェックアウトごとに分かれるため、それだけでは接続が無いことを確かめられない。置き場の中のファイルだけ省くと、チェックアウト A の既定の DB を B が `NDF_PROGRESS_DB` で開いている間に、A の `restore` が通る（I11） |
| `store.open_store(url, root)` | 外部呼び出し | URL・置き場 | SQLAlchemy の `Engine` | 置き場のロックを共有で取る。SQLite なら WAL と `busy_timeout` 30 秒を設定し、検査の後に控えを取る | **SQLite の URL のとき:** `sqlite_path(url)` の実ファイルへ接続する前に `db_lock(path, exclusive=False)` を取り、`Engine` の全接続を閉じる（`dispose`）まで持つ。取ってから実ファイルへ接続する。接続の後に取ると、その間に別のチェックアウトの `restore` が排他を取り、接続した DB を置き換える（I11）。目印 `state/progress.checked` が `CHECK_INTERVAL` より古いか無ければ、`maint_lock(root, wait=True)` を待って取り、目印を読み直す。まだ古ければ `quick_check` を打ち、`ok` なら `backup` を呼んで目印の時刻を今にしてから `maint_lock` を放す。待っていたプロセスは読み直しで新しい目印を見て検査しない。`ok` でなければ `StoreBroken` を投げ、書かない（I9）。書き込みが `SQLITE_CORRUPT` を受けたら目印を消す。**PostgreSQL の URL のとき:** `PRAGMA`・`quick_check`・`backup` を打たず、#3092 の設計のまま `pg_try_advisory_lock` で書き手を 1 つにする。壊れたときの検出と控えは DB の運用に任せる |
| `store.backup(engine, root, now)` | 書き込み | Engine・置き場・時刻 | 控えのパスか `None` | `backup/progress-<UTC の日時>.sqlite` を書き、8 本目より古い控えを消す | SQLite のときだけ呼ばれる。最新の控えが 24 時間以内なら `None`。表の版を上げる前は日時に依らず取る |
| `store.restore(url, root, backup)` | 書き込み | URL・置き場・控えのパス | 退避した壊れたファイルのパス | 壊れた DB の本体・`-wal`・`-shm`・`-journal` を `<実ファイル>.broken-<日時>`（と同じ接尾辞）へそろえて移し、控えを置く | 副命令 `ndf progress restore --from <控え>` から、`open_store` と同じ URL（`NDF_PROGRESS_DB` か既定）を渡して呼ぶ。自動では呼ばない。SQLite のときだけ動く。対象は `sqlite_path(url)` の実ファイルで、`root` の下の `progress.sqlite` に決め打ちしない。手順は次の順で、途中で止まれば以降を行わない。(1) `store_lock(root, exclusive=True)` と `db_lock(path, exclusive=True)` を取る（取れなければ `StoreInUse`。接続が残っている）(2) 控えを実ファイルと同じディレクトリの一時名 `.<実ファイルの名前>.restoring` へ写し、`quick_check` を打つ (3) 本体と `-wal`・`-shm`・`-journal` をそろえて退避の名前へ移す (4) 一時名を `os.replace` で実ファイルの名前にする (5) 目印 `progress.checked` の時刻を今にする。(4) の時点で実ファイルの `-wal`・`-shm` が残っていないことを確かめ、残っていれば止まる |

## 異常系の処理

| 関数 | 異常 | 扱い |
| --- | --- | --- |
| `identity.load()` | `plugin.json` が読めない | 起動を止める（終了コードは止まった理由の表の `config_broken`） |
| `identity.prepare_state_dir(ident)` | 旧い状態の置き場の退避先が既にある | 日付に時刻を足した名前で退避する。消さない |
| `invoke.invoke(stage, context, cwd)` | 文脈に入力の鍵が無い | 行を始めない。止まった理由 `input_missing` と、足りない鍵を返す |
| `invoke.invoke(stage, context, cwd)` | 作業が結果 JSON を返さない | 行を失敗にし、止まった理由 `bad_report` を返す |
| `invoke.record_outputs(table, row, result)` | 工程の定義に無い鍵を作業が出力した | 書かずに捨て、結果の `warnings` に載せる |
| 工程の定義の登録（P1） | 同じ範囲を 2 つの工程が受け持つ | 登録で断る（#3092 の順序の決まりに足す） |
| `durable.prepare_store(root, now)` | 形の番号が自分より大きい | 何も書かない。止まった理由 `format_newer`。結果に置き場の番号と自分の番号を載せる |
| `durable.prepare_store(root, now)` | 退避先が既にある | 日付に時刻を足した名前で退避する。消さない |
| `durable.store_lock(root, exclusive)` | 排他を求めたが、ほかのプロセスが共有か排他で持っている | 待たずに `StoreInUse` を投げる。呼び手は何も変えずに `store_in_use` で止まる |
| `durable.store_lock(root, exclusive)` | ロックのファイルを作れない（置き場の親が書き込めない） | 止まった理由 `config_broken`。置き場を使わない |
| `durable.maint_lock(root, wait)` | ロックのファイルを作れない（置き場が書き込めない） | 止まった理由 `config_broken`。検査も走査もせず、置き場を使わない |
| `store.db_lock(path, exclusive)` | 実ファイルの隣に `<実ファイル>.lock` を作れない | 止まった理由 `config_broken`。進行表の DB を開かない |
| `store.db_lock(path, exclusive)` | 排他を求めたが、ほかのプロセスが共有で持っている | 待たずに `StoreInUse` を投げる。`restore` は何も移さずに `store_in_use` で止まる |
| `durable.prepare_store(root, now)` | 退避が要るのに、置き場のロックか、旧い置き場の鍵のロックか `progress.sqlite.lock` が持たれている | 何も変えない。止まった理由 `store_in_use`。結果に取れなかったロックのパスを載せる |
| `durable.launch(database_url, run_key)` | 鍵のファイルが `quick_check` を通らない | 本体と `-wal`・`-shm`・`-journal` をそろえて `dbos/broken/<run_name>-<日時>.sqlite`（と同じ接尾辞）へ移し、止まった理由 `store_broken`。打ち直すと実行の回 1 から流れる |
| `durable.launch(database_url, run_key)` | URL のとき、台帳 `ndf_runs` に書けない | DBOS を起動しない。止まった理由 `store_broken`。台帳に無い鍵は片付けで消せないため、書かずに起動しない |
| `durable.launch(database_url, run_key)` | 記録の `application_version` が今の `app_version(run_key)`（`format-<FORMAT>-<鍵の名前>`）と違う `PENDING` がある | 起動の回復に任せない。呼び手（C2・P1）が #3092 の決定 21 の扱いで古い実行を取り消す |
| `durable.run_lock(root, run_key, database_url=None, wait=True)` | ロックのファイルを作れない（置き場が書き込めない） | 止まった理由 `config_broken`。DBOS を起動しない |
| `durable.run_lock(root, run_key, database_url=None, wait=True)` | 取った後に、パスの inode が開いたファイルと違う（片付けが `.lock` を消した） | 開き直して取り直す。回数は数えない（消すのは終わった鍵だけで、取り直した後は消されない） |
| `durable.prepare_store(root, now)` | 共有で取り直した後の番号が自分と違う（退避の間に別の退避が割り込んだ） | (1) からやり直す。3 回で合わなければ何も変えずに止まった理由 `store_in_use` |
| `durable.retire(root, run_key)` | 移す先に同じ名前がある | 日時を足した名前で移す。消さない |
| `durable.sweep(root, now)` | 1 つのファイルの状態を読めない（壊れている・ロック中） | そのファイルは移さず消さず、`warnings` に載せて次のファイルへ進む。片付けの失敗で実行を止めない |
| `durable.sweep(root, now)`・`sweep_url(root, url, now)` | 鍵の `run_lock` を `wait=False` で取れない（同じ鍵を開いたプロセスがいる） | その鍵は移さず消さず、`busy` に数えて次の鍵へ進む。目印は更新し、次の走査で見る |
| `durable.sweep(root, now)`・`sweep_url(root, url, now)` | `maint_lock` を取れない（ほかのプロセスが検査か走査をしている） | 何もせず `skipped` を真にして返す |
| `store.backup(engine, root, now)` | 控えの書き込みに失敗する（容量不足など） | 書きかけの控えを消し、`warnings` に載せて進む。古い控えは消さない |
| `store.restore(url, root, backup)` | 控えが `quick_check` を通らない | 何も移さずに止まる（`store_broken`）。一時名の写しを消す。結果に次に新しい控えのパスを載せる |
| `store.restore(url, root, backup)` | 置き場のロックか、実ファイルの `<実ファイル>.lock` を排他で取れない（進行表の DB を開いたプロセスがいる。別のチェックアウトのプロセスも含む） | 何も移さずに止まる（`store_in_use`） |
| `store.restore(url, root, backup)` | URL（PostgreSQL）を渡した | 何も移さずに止まる（`restore_unsupported`）。結果に「PostgreSQL の控えと戻しは DB の運用で行う」を載せる |
| `durable.sweep_url(root, url, now)` | DB に届かない | 消さずに `warnings` に載せて進む。片付けの失敗で実行を止めない |
| `store.open_store(url, root)` | `quick_check` が `ok` でない | 止まった理由 `store_broken`。結果に最新の控えのパスと `ndf progress restore --from <控え>` を載せる |
| `store.open_store(url, root)` | `database is locked` が `busy_timeout` を超えた | 止まった理由 `store_busy`（再試行の方針は止まった理由の表） |
| `store.open_store(url, root)` | DB へ繋げない・認証に失敗する・途中で切れる、SQLite のファイルを開けない・ディスクの満杯や I/O の誤り | 止まった理由 `store_unreachable`（再試行の方針は止まった理由の表）。DB は壊れていないので控えからの戻しを案内しない。結果の接続先はパスワードを伏せる |
| `store.sqlite_path(url)` | SQLite の URL がクエリ（`?timeout=30` など）を持つ・ファイルのパスが無い（メモリの DB） | 何も開かずに止まる（`invalid`）。接続・検査・ロック・戻しが別のファイルを指さないようにする |

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

**チェックアウトの置き場の定数**（#3344。`core/durable.py` が持つ）

| 名前 | 値 | 意味 |
| --- | --- | --- |
| `FORMAT` | `1` | 耐久ワークフローの中の DBOS の呼び出しの順の番号。アプリの版は `format-<FORMAT>-<鍵の名前>`（`app_version(run_key)`。決定 84） |
| `FORMAT_SOURCE_HASH` | 耐久ワークフローの関数のソースの sha256 | `FORMAT` の上げ忘れを落とすテストが比べる値 |
| `STORE_FORMAT` | `1` | チェックアウトの置き場の `format.json` の番号（決定 85） |
| `KEEP_DAYS` | `14` | `retired/`・`broken/` と URL の終わった実行を消すまでの日数（決定 83） |
| `RUN_PREFIX` | `"run-"` | 鍵の名前の接頭辞。名前の形を決めるだけで、URL の片付けの絞り込みには使わない（絞るのは台帳 `ndf_runs`） |
| `RUNS_TABLE` | `"ndf_runs"` | URL のとき、NDF が起こした鍵を書く台帳の表（`run_name` 主キー・`run_key`・`started_at`）。進行表と同じ DB に置き、DBOS の表ではない（I12） |
| `STORE_LOCK` | `"state.lock"` | 置き場のロックのファイル名。`state/` の親に置く（I11） |
| `MAINT_LOCK` | `"maint.lock"` | 共有の目印の検査・控え・走査を 1 プロセスに限るロックのファイル名。`state/` の親に `state.lock` と並べて置く（I11 の退避で `state/` の名前を変えても動かない） |
| `RUN_LOCK_SPACE` | NDF が決める 32 ビットの定数 | URL のときの鍵のロック（`pg_advisory_lock` の 2 引数の形）の第 1 引数。進行表の DB の `pg_try_advisory_lock` と値の空間を分ける |
| `CHECK_INTERVAL` | 24 時間 | `quick_check` と片付けの走査の間隔。目印 `progress.checked`・`dbos/<run_name>.checked`・`dbos/.swept` の更新時刻と比べる（決定 82・83） |

止まった理由 `store_broken`・`store_busy`・`store_unreachable`・`store_in_use`・`restore_unsupported`・`format_newer`・`format_mismatch` は、D1 の止まった理由の表へ足す（P1・C1+・P3 の PR が下流の読み手と同じ PR で足す。集約の表の「止まった理由の表」の行）。

## 並行と排他

| 対象 | 排他の仕方 |
| --- | --- |
| 子 Issue どうしのファイル | 「ファイル配置」のサブパッケージで分ける。重なるのは `cli.py` の副命令の表だけで、1 行ずつ足すので git の取り込みで解ける |
| 進行表の DB | 1 つの DB を使う。SQLite は、開くプロセスが実ファイルへ接続する前に `<実ファイル>.lock`（`db_lock`）を共有で取り、全接続を閉じるまで持つ。WAL で書き手は同時に 1 つ、PostgreSQL は `pg_try_advisory_lock`（#3092 の設計のまま）。DBOS の記録は置かない（決定 81。決定 77 のこの行を置き換えた） |
| DBOS の記録 | 実行の鍵ごとのファイルを `run_lock` で 1 プロセスに限る（I6）。URL のときは鍵ごとの `application_name` で取り出しを分ける（I5） |
| 鍵ごとのファイルの退避（`retire`・`sweep`） | `run_lock` の下で行う。起動したプロセスは持ったまま `retire` を呼び、`sweep` は鍵ごとに `wait=False` で試して取れた鍵だけを移す。取れない鍵は触らない。`.lock` は持ったまま最後に消し、待っていたプロセスは inode の確かめで開き直す（I6） |
| URL の鍵の起動と片付け（`launch`・`sweep_url`） | DB 側の鍵のロック（`pg_advisory_lock(RUN_LOCK_SPACE, <鍵の値>)`）を共通に取る。`launch` は待って取り、台帳の行の書き込みから DBOS を閉じるまで持つ。`sweep_url` は待たずに試し、取れた鍵だけ、実行の一覧から `delete_workflows` と台帳の行の削除までを持ったまま行う（I13） |
| 共有の目印（`progress.checked`・`dbos/.swept`） | `maint_lock` で 1 プロセスに限る。期限の確かめは取った後に読み直す。進行表の DB の検査は待って取り（検査を終えるまで書かないため）、走査は待たずに試して取れなければ飛ばす。鍵ごとの目印 `dbos/<run_name>.checked` は `run_lock` の下で読み書きするため、別の排他を取らない |
| チェックアウトの置き場の全体 | 置き場を使うプロセスは `state.lock` を共有で持つ。置き場ごとの退避は、共有で番号を読み、退避が要るなら共有を放して排他を待たずに取り、排他の下で番号を読み直してから行い、共有で取り直して番号をもう一度確かめる（`prepare_store` の手順）。退避と進行表の DB の `restore` は、`state.lock` を排他で取れ、退避では旧い置き場の `dbos/*.lock` と `progress.sqlite.lock` もすべて、`restore` では実ファイルの `<実ファイル>.lock` も（置き場の中か外かに依らず）取れたときだけ行う（I11）。`state.lock` は `state/` の外にあり、名前の変更で動かない |
| 今の NDF と新しい NDF | 開発の間は同時に有効にしない（決定 74）。状態の置き場はプラグイン名で分かれる（決定 75） |

**ロックを取る順序は `state.lock` → `<実ファイル>.lock`（`db_lock`）→ `run_lock` → `maint_lock` の 1 方向にする。** `db_lock` は `open_store` が接続の前に取り、`restore` は `state.lock` の後に待たずに試す。`maint_lock` を持つプロセスが `run_lock` に触るのは `wait=False` の試しだけで、待たない。順序に逆らう待ちが無いため、行き詰まり（デッドロック）は起きない。

## ログと記録の出力

| 出力 | 形 |
| --- | --- |
| 各副命令の結果 | 結果 JSON を標準出力へ 1 行（今の NDF と同じ規約） |
| 状態の置き場・チェックアウトの置き場の退避 | 結果の `items` に退避先のパスを載せる（`prepare_state_dir` と `durable.prepare_store` の両方） |
| 実行の記録の退避と消去 | 結果の `items` に移したファイルを、`metrics` に `retired`・`deleted` の件数を載せる |
| 待ちを抱えた実行の一覧 | 結果の `items` に `run_name`・`workflow_id`・`status`・`updated_at` を 1 件ずつ |
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
| `entry.main(argv)` | 外部呼び出し | `argv[1]` のランタイム・`argv[2]` の事象・標準入力の payload | 終了コード（いつも 0。止めるときも、ランタイムの契約表の形の JSON を標準出力へ出す） | 計測の 1 行・数え手の更新。SessionStart のときだけ `env.mark_root`・`env.touch_used`・`metrics.prune(14)` | 環境の python で動く。`dispatch` と依存を import する前に `strict.check` を呼び、止める答えならそれを返して終わる。import の失敗・例外・締め切り超えは何も出さずに 0 で終わる |
| `boot.main(argv)` | 外部呼び出し | `entry.main` と同じ（`argv[1]` のランタイム・`argv[2]` の事象・標準入力の payload。登録の command が `hooks/entry.py` のパスを `shift` で外してから渡す） | 終了コード（いつも 0） | SessionStart のときだけ `env.mark_root` と `env.kick_prepare`。`.failed` があれば 1 行の案内を出す | システムの `python3` で動き、標準ライブラリだけを import する。`strict.check` のほかの guard は呼ばない。版がシステムの `python3` の下限（`imports.json` の `boot.python`）より古ければ 0 で終わる |
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
| 登録の command | 環境の python が無い | システムの `python3` で `boot.main` を起動する。`hooks/entry.py` のパスは `shift` で外し、ランタイムと事象を `entry.main` と同じ位置で渡す。`python3` も無ければ何もせずに 0 |
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
| S1-H2 | S1 | 登録の command は `sh -c '[ -x "$0" ] && exec "$0" "$@"; command -v python3 >/dev/null 2>&1 \|\| exit 0; b="${1%/*}/boot.py"; shift; exec python3 "$b" "$@"'` で、環境の python があれば `hooks/entry.py` を直に起動し、無ければシステムの `python3` で `hooks/boot.py` を起動する。boot の経路は `hooks/entry.py` のパスを `shift` で外してから渡す。`bin/ndf` と `uv run` を通らない。環境を消した状態で全事象の登録を打ち、`strict` の guard に当たる入力のほかは出力なし・0 を確かめる。`python3` を `PATH` から外した状態でも 0 を確かめる。環境を消した状態から SessionStart の登録で用意が終わるまでを M3-H14 のテストで通す | 決定 89・決定 91・IH2 |
| S1-H3 | S1 | Codex の登録は、契約表で Codex が持つ事象（UserPromptSubmit を含む）を並べる。Codex で働かない guard は契約表に理由を書く | 決定 95 |
| S1-H4 | S1 | 「ndf-next だけを読む起動」のスクリプトで起動したセッションで、計測の記録に ndf-next の行だけが出て、ndf の hook が走らないことを確かめる | 決定 74・指摘 12 |
| S1-H5 | S1 | 登録の command を 3 つの経路で打ち、起動されたスクリプトの `argv` を確かめるテストを置く。環境の python がある経路は `[hooks/entry.py, ランタイム, 事象]`、環境の python が無い経路は `[hooks/boot.py, ランタイム, 事象]`（`hooks/entry.py` のパスを含まない）、`python3` も `PATH` に無い経路は何も起動せずに 0。`boot.main` が読むランタイムと事象が `entry.main` と同じになることを、事象ごとに確かめる | 決定 89・IH2 |

## 単体テストの観点

| 対象 | 観点 |
| --- | --- |
| `identity.load` | ディレクトリ名を `plugins/x` に変えても、名前と前置きが `plugin.json` から求まる |
| `identity.prepare_state_dir` | 旧い置き場が退避され、中身が残る。2 度目の起動では退避しない |
| `invoke.resolve_inputs` | 鍵が欠けると `input_missing` で止まる。余分な鍵は渡さない |
| `invoke.record_outputs` | 宣言に無い鍵は書かない。前の工程の `pr` が次の工程の引数に届く |
| 検査 I1〜I3 | `"ndf-next"` の文字・`plugins/ndf/` の import・`plugins/ndf/` の差分を、それぞれ落とす |
| I5（`durable.launch`） | 2 つのプロセスが同じ SQLite のファイルを別の鍵で開き、それぞれのキューへ入れると、どちらも自分の実行だけを流す。`application_name` を同じ値にするよう壊すと落ちる |
| I6（`durable.run_lock`） | 同じ鍵で 2 つ起動すると、2 つ目は 1 つ目が抜けるまで DBOS を起動しない。ロックを外すよう壊すと落ちる |
| I7（`durable.launch`） | 承認ゲートの `recv` で待つ実行を `os._exit` で落とし、`plugin.json` の版だけを上げて同じ鍵で起動し直すと、済んだステップを流さずに続く。`application_version` を NDF の版にするよう壊すと落ちる |
| `FORMAT_SOURCE_HASH` | 耐久ワークフローの関数を 1 行変えると、hash の比べ合いが落ちる |
| I8（`durable.sweep`・`sweep_url`） | 15 日前に終わった実行は消え、15 日前から `PENDING` の実行は残る。URL では、親が 15 日前に終わり子が `PENDING`（または 1 日前に終わった）鍵は親も子も残る。状態を見ずに日数だけで消すよう、または `delete_children=True` で親だけを渡すよう壊すと落ちる |
| I12（`durable.sweep_url`） | 台帳に無い `application_name` `run-report` の 15 日前に終わった実行を同じ DB に置くと、片付けの後も残る。接頭辞 `run-` だけで絞るよう壊すと落ちる |
| 走査の頻度（`durable.sweep`・`sweep_url`） | 目印が 1 時間前なら走査せず `skipped` を返し、25 時間前なら走査して目印を更新する |
| 目印の排他（`store.open_store`・`durable.sweep`） | 目印の無い置き場で 8 つのプロセスが同時に `open_store` と起動を打つと、`quick_check`・控え・走査はそれぞれ 1 回だけ起きる（打った回数を数える）。`maint_lock` を取らないよう壊すと落ちる |
| `durable.sweep` と `run_lock`（I6） | 過去の実行が `SUCCESS` の鍵を、別のプロセスが `run_lock` を持ったまま（まだ `ENQUEUED` を書く前）にしておくと、走査はその鍵のファイルを移さず `busy` に数える。`run_lock` を試さないよう壊すと落ちる |
| `durable.retire`・`sweep` の `.lock`・`.checked` | 終わった鍵を退避すると、生きている置き場に本体・`-wal`・`-shm`・`-journal`・`.lock`・`.checked` のどれも残らない。`.lock` を消した後に同じ鍵で 2 つ起動すると、1 つ目が抜けるまで 2 つ目が待つ（inode の確かめを外すと両方が通って落ちる） |
| I13（`durable.launch`・`sweep_url`） | URL で、15 日前に終わった鍵の DB 側の鍵のロックを別の接続が持つ間（同じ鍵の打ち直し）、`sweep_url` はその鍵の実行と台帳の行を消さない。ロックを試さないよう壊すと落ちる |
| `durable.retire` と打ち直し | 終わった鍵で打ち直すと、流さずに `retired/` の結果を返す |
| I9（`store.open_store`） | 目印の無い、先頭のページを壊したファイルを開くと `store_broken` で止まり、ファイルの大きさと更新時刻が変わらない。目印が 1 時間前なら `quick_check` を打たない（打った回数を数える） |
| `store.open_store`（PostgreSQL） | PostgreSQL の URL で開くと、`PRAGMA` を 1 つも発行せず、控えのファイルを作らない |
| `store.restore` と WAL | 控えを取った後に書き込んで `progress.sqlite-wal` にフレームを残したまま（チェックポイントさせずにプロセスを落とす）`restore` すると、戻した DB の中身が控えと一致し、旧い `-wal`・`-shm` は退避の名前の側にある。`-wal` を移さないよう壊すと落ちる |
| `store.restore` の対象 | `NDF_PROGRESS_DB` に置き場の外の SQLite のファイルを渡して `restore` すると、そのファイルが控えの中身になり、置き場の `progress.sqlite` は変わらない。置き場の外のファイルを別のプロセスが開いている間は `store_in_use` で止まる。置き場の中の既定の DB を別のチェックアウトのプロセスが `NDF_PROGRESS_DB` で開いている間も、元のチェックアウトの `restore` は `store_in_use` で止まる。`db_lock` を置き場の中のファイルで省くよう壊すと、`open_store` が接続の後に `db_lock` を取るよう壊すと落ちる。`root` の `progress.sqlite` に決め打ちするよう壊すと落ちる |
| I11（`durable.prepare_store`・`store.restore`） | 別のプロセスが `state.lock` を共有で持つ間、番号の小さい置き場の退避と `restore` は `store_in_use` で止まり、`state/` の名前と中身が変わらない。旧い置き場の `dbos/*.lock` か `progress.sqlite.lock` を持つプロセス（別のチェックアウトから既定の DB を開いたプロセスを含む）がいる間も同じ。退避の後も `maint.lock` は同じパスにある。番号の小さい置き場を 2 つのプロセスが同時に開くと、退避は 1 回だけ起き、新しい置き場は退避されない（排他の下で読み直さないよう壊すと落ちる）。ロックを確かめないよう壊すと落ちる |
| `store.backup` | 同じ日の 2 度目の起動では控えを取らない。8 本目を取ると最も古い 1 本が消える |
| I10（`durable.prepare_store`） | 番号 0（`format.json` 無し）と自分より小さい番号は退避され、中身が残る。自分より大きい番号は何も変わらず `format_newer` で止まる |
| `durable.pending` | `PENDING`・`ENQUEUED`・`DELAYED` だけが並び、`SUCCESS` は並ばない |

## #3344 で子へ足す受け入れ条件

設計の決定 81〜87 を、子の受け入れ条件へ下ろした表である。**設計承認の後に、各子の Issue の本文の受け入れ条件へこの表の行を写す**（C1+ は C1 #3285 の本文に「追補」の節を足して写す）。番号は設計文書の「DBOS の記録と進行表の置き場」の対応表が指す。

| # | 子 | 受け入れ条件 | 決定・不変条件 |
| --- | --- | --- | --- |
| A1 | C1+ | `durable.launch` は実行の鍵を受け、`name` と `executor_id` に `run_name(run_key)` を渡す。C1 の `launch(database_url, executor_id, name)` は残さない | 決定 81・I6 |
| A2 | C1+ | SQLite のときは鍵ごとのファイル `state/dbos/<run_name>.sqlite` を使い、`run_lock` で同時に 1 プロセスに限る | 決定 81・I6 |
| A3 | C1+ | 2 つのプロセスが同じ SQLite のファイルを別の鍵で開いてキューへ入れても、相手の実行を取り出さないことをテストで確かめる | I5 |
| A4 | C1+ | 鍵のファイルは、鍵ごとの目印が 24 時間より古いか無いときに起動の前に `quick_check` を打ち、通らなければ `broken/` へ移して `store_broken` で止まる | 決定 82 |
| A5 | C1+ | 終わった実行の鍵のファイルを `retired/` へ移し、`retired/`・`broken/` の 14 日より古いファイルを、走査の目印が 24 時間より古い起動で消す。待ちを抱えたファイルは消さない。走査は鍵ごとに `run_lock` を待たずに試し、取れた鍵だけを移す。開いているプロセスがいる鍵を移さないことをテストで確かめる | 決定 83・I6・I8 |
| A6 | C1+ | URL を渡したときも、台帳 `ndf_runs` に載った鍵のうち、実行（子を含む）がすべて終わって 14 日を過ぎた鍵の実行を `delete_workflows` で消す。1 つでも待ちか 14 日以内のものがある鍵は消さない。`delete_children=True` に頼らない | 決定 83・I8 |
| A7 | C1+ | `application_version` は `format-<FORMAT>-<鍵の名前>`（`app_version(run_key)`）で、`plugin.json` の版を上げて起動し直しても途中の実行が回復することをテストで確かめる。`FORMAT_SOURCE_HASH` の比べ合いのテストを置く | 決定 84・I7 |
| A8 | C1+ | チェックアウトの置き場の `format.json` を決定 85 の表のとおりに扱う。番号が自分より大きい置き場には書かない | 決定 85・I10 |
| A9 | C1+ | `durable.pending` が置き場か URL から待ちを抱えた実行を集める | 決定 86 |
| A10 | C1+ | `plugins/ndf-next/pyproject.toml` と `uv.lock` の DBOS を 3.4.0 にし（3.5.0 へは 11-18 以降。決定 87）、`plugins/ndf-next/tests/` がすべて通る | 決定 87 |
| A11 | C1+ | 置き場を使うプロセスは `state.lock` を共有で持つ。置き場ごとの退避は、共有で番号を読み、共有を放して排他を待たずに取り、排他の下で番号を読み直し、`state.lock` と旧い置き場の `dbos/*.lock`・`progress.sqlite.lock` をすべて排他で取れたときだけ行い、共有で取り直して番号をもう一度確かめる。取れなければ何も変えずに `store_in_use` で止まること、2 つのプロセスが同時に開いても退避が 1 回だけであることをテストで確かめる | 決定 85・I11 |
| A12 | C1+ | URL のとき `durable.launch` は DB 側の鍵のロックを取ってから台帳 `ndf_runs` へ鍵を書いて DBOS を起動し、片付けは台帳に無い `application_name` の実行を消さないことと、鍵のロックを持つ起動がいる鍵を消さないことをテストで確かめる | 決定 83・I12・I13 |
| A13 | C1+ | 鍵のファイルを `retired/`・`broken/` へ移すときは、本体と `-wal`・`-shm`・`-journal` をそろえて移し、鍵ごとの `.checked` を消し、`.lock` は `run_lock` を持ったまま最後に消す。退避の後に生きている置き場へ鍵のファイルが 1 つも残らないことをテストで確かめる | 決定 82・83・I6 |
| A14 | C1+ | 片付けの走査は、`maint_lock` を待たずに取れ、取った後に読み直した目印 `dbos/.swept` が 24 時間より古いときだけ行う（SQLite と URL の両方） | 決定 83 |
| B1 | C2 | プランの実行は、実行の回ごとに 1 つの実行の鍵で `durable.launch` を呼ぶ。同じプランの別の回・別のプランと鍵を共有しない | 決定 81・I5 |
| B2 | C2 | 承認ゲートの `recv` で待つ間にプロセスが `os._exit` しても、次の `next` が同じ鍵で起動して回復する | 決定 81・I6 |
| B3 | C2 | 20 本のプランの実行と 14 本のラッパー（`next` と `wait` を打ち続けるプロセス）を同時に 30 分流し、`database is locked` が 0 回、1 ステップあたりの上乗せが 1 秒以内であることを測り、値を PR に貼ってからマージする | 決定 81・82 |
| B4 | C2 | 終わった鍵で打ち直すと、`retired/` の結果を返して流さない | 決定 83 |
| B5 | C2 | 待ちを抱えた実行の一覧を出す副命令 `ndf plan pending` を持つ（`durable.pending` を呼ぶ） | 決定 86 |
| B6 | C2 | B3 の負荷の中で、鍵のファイルの `quick_check` と片付けの走査の 1 回あたりの所要時間と、30 分の間に打った回数を測り、値を PR に貼る。目印が効いて、回数が呼び出しの回数に比例しないこと | 決定 82・83 |
| P1-1 | P1 #3092 | 進行表の DB は DBOS の表を持たず、SQLite なら WAL・`busy_timeout` 30 秒で開き、目印 `progress.checked` が 24 時間より古いか無いときに `quick_check` を打つ。通らなければ書かずに `store_broken` で止まる | 決定 81・82・I9 |
| P1-2 | P1 #3092 | SQLite のとき、表の版を上げる前とその日の最初の検査の直後に、backup API で控えを取り、新しい 7 本を残す。`ndf progress restore --from <控え>` で戻せる | 決定 82 |
| P1-3 | P1 #3092 | 34 本のプロセスが進行の事象を同時に書く負荷で、`database is locked` が 0 回、書き込みの待ちの 95 パーセンタイルが 1 秒以内であることを測り、値を PR に貼ってからマージする。超えたらマージせずに設計へ戻す | 決定 82 |
| P1-4 | P1 #3092 | PostgreSQL の URL を渡したとき、`open_store` は `PRAGMA`・`quick_check`・控えを行わず、`pg_try_advisory_lock` で書き手を 1 つにする。`restore` は何もせず `restore_unsupported` で止まる | 決定 82 |
| P1-5 | P1 #3092 | `restore` は `open_store` と同じ URL から `sqlite_path` で解いた実ファイルを対象にし、`state.lock` と実ファイルの `<実ファイル>.lock`（置き場の中のファイルでも）を排他で取れたときだけ動き、控えを一時名へ写して `quick_check` を通してから、本体と `-wal`・`-shm`・`-journal` をそろえて退避し、一時名を本体の名前にする。WAL にフレームを残したまま `restore` して、戻した中身が控えと一致することと、`NDF_PROGRESS_DB` に置き場の外のファイルを渡すとそのファイルが戻ること、置き場の中の既定の DB を別のチェックアウトが開いている間は `store_in_use` で止まることをテストで確かめる | 決定 82・I11 |
| P1-6 | P1 #3092 | 進行表の DB の検査は `maint_lock` を待って取り、目印を読み直してから打つ。P1-3 の負荷の中で、進行表の DB の `quick_check` の所要時間と 30 分の間に打った回数を測り、値を PR に貼る。目印と `maint_lock` が効いて、回数がプロセスの数に比例しないこと | 決定 82 |
| P3-1 | P3 #3097 | レーンの起動は、計画が記録した形の番号（`STORE_FORMAT`・`FORMAT`・表の版）と自分のものを比べ、違えば `format_mismatch` で止まる。NDF の版だけが違えば止まらず、レーンの行の事象と `warnings` に残す | 決定 86 |
| M3-1 | M3 #3297 | プラグインの更新を反映する再起動の前に、`ndf plan pending` の一覧を結果に載せる | 決定 86 |

**#3092 の本文の決定 2・18・21 の扱いの変更**（DBOS の記録を同じ DB に置かない・決定 18 の切り替えを使わない・決定 21 の契機を形の番号にする）も、P1-1 とあわせて #3092 の本文へ写す。
