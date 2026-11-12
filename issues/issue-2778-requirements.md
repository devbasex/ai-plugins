# #2778: handoff.py がオーガナイザーの引継ぎ文書 parallel-<計画名> を受け付けず、/ndf:restart で切り替えられない

正は課題の本文（#2778）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 直し方は「`handoff.py` が `parallel-<計画名>` を受け付ける」か「並列の進め方の文書の名を既存の 4 つの形のどれかへそろえる」のどちらか | 課題の本文の受け入れ条件 1（起票者、2026-11-08） | 受け入れ条件 1〜4。前者を採る（理由は前提 1） |
| S2 | オーガナイザーのセッションで `/ndf:restart` がブロックを出して切り替わる | 課題の本文の受け入れ条件 2（起票者、2026-11-08） | 受け入れ条件 6・7 |

## 事象

オーガナイザー（並列の計画を回すセッション）を `/ndf:restart` で切り替えるとき、引継ぎ文書 `parallel-p4` に再開コマンドを置こうとすると、`handoff.py next` と `check` が終了コード 2 で断る（2026-11-08、ndf 10.27.40）。

```
$ printf '%s\n' "/goal /ndf:development-workflow --parallel p4 -- 終わり: [ndf-parallel] finished p4 の行を出したとき" | python3 scripts/handoff.py next parallel-p4
{"tool": "handoff", "status": "stopped", "summary": "名が loop-<名>・sprint-<名>・milestone-<番号>・issue-<番号> の形でない: parallel-p4", ...}
```

2026-11-09 にコミット 451146564（ndf 10.27.49）で `path` でも再現した。

```
$ python3 plugins/ndf/scripts/handoff.py path parallel-p4; echo rc=$?
{"tool": "handoff", "status": "stopped", "summary": "名が loop-<名>・sprint-<名>・milestone-<番号>・issue-<番号> の形でない: parallel-p4", "items": [], "metrics": {}, "run_id": "2a323e4335ae"}
rc=2
```

食い違い:

- 並列の進め方の文書（`skills/development-workflow/references/parallel.md` の「セッションの切り替え」）は、オーガナイザーの引継ぎ文書を `.ndf/handoff/parallel-p1.md` と定める
- `scripts/handoff.py` は名を `loop-`・`sprint-`・`milestone-`・`issue-` の 4 つの形だけ受け付ける（`NAME_RE`）。引継ぎの規則の正本（`references/handoff.md` の「置き場と名」）の表にも `parallel-` の行が無い
- `restart` Skill は `next` が 0 以外ならブロックを出さずに止まる規則のため、オーガナイザーは規則どおりには再起動できない

## 期待する結果

オーガナイザーのセッションで `/ndf:restart` を打つと、引継ぎ文書 `.ndf/handoff/parallel-<計画名>.md` が作られ、「次に実行するコマンド」に起動と同じ 1 行が置かれ、`ndf-next` のブロックが出てセッションが切り替わる。

## 再現手順

1. `.ndf/` のあるリポジトリのメインディレクトリか worktree で、`python3 plugins/ndf/scripts/handoff.py path parallel-p4` を打つ
2. 終了コード 2 と、上の summary が返る（`init`・`next`・`check` も名の検証で同じく 2 を返す）

## 環境

- 観測: ndf 10.27.40（2026-11-08）
- 再確認: ndf 10.27.49、コミット 451146564（2026-11-09）、Linux、Python 3

## 前提

- 前提 1: 直し方は「`handoff.py` と引継ぎの規則の正本が `parallel-<計画名>` の名を受け付ける」を採る。並列の進め方の文書の名を `loop-` などへそろえる直し方は採らない。理由: オーガナイザーは開発ループでもスプリントでもなく、`loop-<名>` に寄せるとレーンの開発ループの引継ぎ文書（例: `loop-lcore`）と同じ名の空間に入り、名が衝突しうる。また計画名と名が 1 対 1 で対応する方が、再開のときに文書を探しやすい
- 前提 2: `<計画名>` の形は、並列の計画の名の規則（`lib/parallel_plan.py` の `PLAN_RE` = `^[a-z0-9][a-z0-9_-]{0,23}$`）に合うものとする。`handoff.py` はこの形に合わない名を、今と同じく終了コード 2 で断る
- 前提 3: オーガナイザーの再開コマンドは、並列の進め方の文書の「セッションの切り替え」のとおり起動と同じ 1 行とする。`/goal` の入力に `--` 以降の終わりの条件が続くため、`restart` の「再開コマンドを決める」の 1 行目（目標の入力に「`<文書のパス>` の続きから」を足す）を当てると、足した文が終わりの条件の一部として読まれる。オーガナイザーのセッションでは足さない
- 前提 4: オーガナイザーのセッションかどうかは、会話の `/goal` の入力が `--parallel <計画名>` を含むことで決める。これで決められないときは `restart` の既存の規則（次の名の行へ進む）に従う

## 対象範囲

含む:
- `handoff.py` の名の検証（`path`・`init`・`find`・`next`・`check`・`remove` が共通に使うもの）に `parallel-<計画名>` を足す。断るときの summary の名の一覧にも足す
- `handoff.py find` の一致の規則に `parallel-<計画名>` の行を足す
- 引継ぎの規則の正本（`references/handoff.md`）の「置き場と名」の表と、`find` の一致の規則の表に `parallel-<計画名>` の行を足す
- `restart` Skill の手順 2 の名の一覧と、再開コマンドを決める表に、オーガナイザーのセッションの扱いを足す
- `handoff.py` のテストに `parallel-<計画名>` の場合を足す

含まない:
- 並列の進め方の文書の名（`parallel-<計画名>`）の変更
- 引継ぎ文書の節の形（テンプレート）と行数の上限の変更
- 中継（ラッパー）と `parallel.py` の振る舞いの変更
- 既存の 4 つの形の名の受け付け方の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | オーガナイザーがカットポイントに達した | 文脈の残量・利用者の `/ndf:restart` | — | — |
| E2 | `restart` が引継ぎ文書の名を `parallel-<計画名>` に決めた | E1 の後に `restart` を打った | 計画名を決められない → 次の名の行へ進む（前提 4） | E1 |
| E3 | `handoff.py init` が引継ぎ文書を作った（在れば変えない） | E2 | 2 → ブロックを出さずに理由を報告する。3 → 文書なしで続ける | E2 |
| E4 | `handoff.py next` が「次に実行するコマンド」に起動と同じ 1 行を置いた | E3 | 0 以外 → ブロックを出さずに理由を報告する | E3 |
| E5 | `handoff.py check --trim` が形と行数を確かめた | E4 | 1 → 正本の「作る・更新する」に従って直す | E4 |
| E6 | `restart` が `ndf-next` のブロックを出し、中継が新しいセッションへ切り替えた | E5 | 中継の外 → 再開コマンドを示して終える | E5 |
| E7 | 新しいセッションが `find` か `path` で引継ぎ文書を読み、オーガナイザーを再開した | E6 の再開コマンド | `find` が 10 → 候補を示して利用者へ聞く | E6 |

## 用語

| 用語 | 意味 |
| --- | --- |
| オーガナイザー | 並列の計画を回し、レーンからの問いとリリースの提示を受けるセッション（`docs/ndf-parallel-model.md`） |
| 引継ぎ文書 | `.ndf/handoff/<名>.md`。セッションを切り替えるときに次のセッションへ現在地と次のコマンドを渡す文書 |
| 並列の計画 | 同時に進めるレーンの一覧と共通の決まりを書いた 1 つの文書。名（計画名。例: `p4`）を持ち、`parallel.py init <計画名>` で作る |

## 受け入れ条件

- [ ] 1. `.ndf/` のあるリポジトリで `handoff.py init parallel-p4 --title "並列の計画 p4"` が終了コード 0 を返し、メインディレクトリの `.ndf/handoff/parallel-p4.md` を雛形から作る
- [ ] 2. 課題の事象の再開コマンドを標準入力に渡した `handoff.py next parallel-p4` が終了コード 0 を返し、文書の「次に実行するコマンド」の節がその 1 行になる。続く `handoff.py check parallel-p4 --trim` が終了コード 0 を返す
- [ ] 3. `handoff.py path .ndf/handoff/parallel-p4.md --exists` が、文書のあるとき 0、無いとき 1 を返す。`remove parallel-p4` が本体と履歴を消す
- [ ] 4. `handoff.py find --text "/goal /ndf:development-workflow --parallel p4 -- 終わり: ..."` が、`.ndf/handoff/parallel-p4.md` だけがあるとき終了コード 0 でそのパスを返す。`parallel-p4` を含まない入力（例: `--parallel p40`）では一致しない
- [ ] 5. 計画名の形（前提 2）に合わない名（例: `parallel-`・`parallel-P4`・`parallel-p4-history`）と、5 つの形のどれでもない名は、今と同じく終了コード 2 で断る。断るときの summary の名の一覧に `parallel-<計画名>` が載る
- [ ] 6. 引継ぎの規則の正本の「置き場と名」の表と `find` の一致の規則の表、`restart` の手順 2 の名の一覧に `parallel-<計画名>` が載り、`restart` の再開コマンドを決める表がオーガナイザーのセッションでは起動と同じ 1 行を使うと定める（前提 3・4）
- [ ] 7. 中継の下のオーガナイザーのセッションで `/ndf:restart` を打つと、`ndf-next` のブロックが出てセッションが切り替わり、新しいセッションが同じ計画のオーガナイザーとして再開する（リリース後テストで確かめる）
- [ ] 8. 既存の 4 つの形（`loop-`・`sprint-`・`milestone-`・`issue-`）の名に対する `handoff.py` の振る舞いが変わらない（既存の `test_handoff.py` が通る）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 名の形の定義は `handoff.py` の 1 か所に置き、副命令ごとに分けない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`handoff.py` が受け付ける名が 1 つ増える。既存の名の受け付け方は変わらない（後方互換） |
| データ | 無し。引継ぎ文書はコミットしない手元の文書である |
| 既存の振る舞い | 断るときの summary の文言が変わる。`find` の候補に `parallel-` の文書が入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_handoff.py -q`、全体は `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | リポジトリの CI の lint（`ruff`）と `python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 受け入れ条件 7。リリース後に、中継の下で動いている並列の計画のオーガナイザーで `/ndf:restart` を打ち、ブロックが出て切り替わり、新しいセッションが `parallel.py start <計画名>` で同じ計画を続けることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`。スクリプトは `plugins/ndf/scripts/`、規則の正本は `plugins/ndf/skills/` の参照文書。開発は worktree で行う |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`handoff.py` の出力は `lib/step_result.py` の形の 1 行の JSON |
| テスト戦略 | `handoff.py` の副命令を一時のリポジトリで呼ぶ単体テスト（既存の `test_handoff.py` に足す）。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、名の形の定義を 1 か所に保つ |
| 確認してから行う | 引継ぎ文書の名の規則を変えること（設計承認ゲートで見る） |
| 行わない | 並列の進め方の文書の名の変更、中継と `parallel.py` の変更、引継ぎ文書のテンプレートの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 名の表で `parallel-<計画名>` の行を置く順（オーガナイザーのセッションが `/goal` のマイルストーンや課題も持つときにどれを先に当てるか）。要求では前提 4 で判定できるときは `parallel-` を先に当てるとし、表の中の位置は設計で決める | 設計（`design`） | 設計承認ゲート |

## 依頼（原文）

> ## 何が起きたか
>
> `/ndf:restart` でオーガナイザーのセッションを切り替えるとき、引継ぎ文書 `parallel-p4` に再開コマンドを置こうとすると、`handoff.py next` と `check` が終了コード 2 で断る（2026-11-08、ndf 10.27.40）。
>
> ## 食い違い
>
> - `skills/development-workflow/references/parallel.md` の「セッションの切り替え」は、オーガナイザーの引継ぎ文書を `.ndf/handoff/parallel-p1.md` と定める
> - `scripts/handoff.py` は名を `loop-`・`sprint-`・`milestone-`・`issue-` の 4 つの形だけ受け付ける
> - `restart` Skill は `next` が 0 以外ならブロックを出さずに止まる規則のため、オーガナイザーは規則どおりには再起動できない
>
> ## 受け入れ条件
>
> - [ ] `handoff.py` の `init`・`next`・`check` が `parallel-<計画名>` を受け付ける（または parallel.md の名を 4 つの形のどれかへそろえる）
> - [ ] オーガナイザーのセッションで `/ndf:restart` がブロックを出して切り替わる

（書き手: 起票者、日付: 2026-11-08。課題を起こした AI が、運用中に観測した事象を記録した本文。利用者の指示の原文は含まない）

> #2778。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-09）
