# #3569: ndf-next: 閉じた子（X0・C2・D2+・M3b・P1a・S1b）の 10 本のスクリプトが bin/ndf に無く、Skill が旧いプラグインを呼ぶ

正は課題の本文（#3569）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 種類

不具合

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめる（MVV 判定が承認ゲートを省いたときはその判定が確かめた記録になる）まで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

この課題の本文は、利用者の文を引用していない。題・本文はすべて起票者（T2 #3411 の試走をした AI）が書き、「進め方の決まり」の節はオーガナイザー（並列の計画 p6 のレーン lr-t2 を回す AI）が書いた。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 閉じた 6 つの子（X0・C2・D2+・M3b・P1a・S1b）の 10 本を 1 つの課題にまとめ、行き先の子を開き直さない | 起票者の推定（出所: `plugins/ndf-next/src/ndf/checks/children.json` の `closed`） | 前提 1 |
| S2 | 子の表への載せ方と順番はオーガナイザーが決める | 起票者の推定。「進め方の決まり」でオーガナイザーが「子の表へ載せる」と決めた | 受け入れ条件 7。順番は未決 1 |
| S3 | 各スクリプトは `bin/ndf` の副命令にするか、作らない理由を機能の対応表の `not_built` に書く | 起票者の推定（対象範囲の「含む」） | 受け入れ条件 1・4。どれを作らないかは設計で決める |
| S4 | 本文の冒頭の「今の名前のまま残した」の注記から 10 本の名前を外す | 起票者の推定（対象範囲の「含む」） | 受け入れ条件 5 |
| S5 | 「それだけを理由に落ちていた行」は、2026-11-15 の判定で 10 本のどれかだけを理由に落ちている Skill・エージェントの 4 行を指す | 設計者の推定（下の「事象」の判定の結果） | 受け入れ条件 2 |
| S6 | 2 回目の試走の結果（原因ごとの一覧）は、この課題のコメントに残してオーガナイザーへ返す | 設計者の推定（「進め方の決まり」は返す先の置き場を書いていない） | 受け入れ条件 9 |

## 事象

新しいプラグイン（`plugins/ndf-next/`）だけを読む起動（`plugins/ndf-next/bin/ndf-claude`）で `/ndf11:development-workflow #3411` を流すと、手順 0 で `$SCRIPTS` が旧いプラグインの `plugins/ndf/scripts` に決まり、旧い `worktree-setup.sh`・`project-decl.py`・`project-mvv.py` が走った（2026-11-15、T2 #3411 の試走）。新しいプラグインに `scripts/resolve.sh` が無く、探索の次の候補 `plugins/ndf` が当たるためである。

行き先の子が閉じているのに、新しいプラグインの Skill とエージェントの本文がまだ旧いスクリプトを呼ぶ行が 10 本ある（`bin/ndf` に副命令が無いため S1a #3303 が名前のまま残した）。

| 行き先の子 | 状態 | 旧いスクリプト | 呼ぶ本文の数 |
| --- | --- | --- | --- |
| X0 #3282 | 閉じた | `scripts/resolve.sh` | 12 |
| C2 #3291 | 閉じた | `scripts/supervise.py` | 28 |
| C2 #3291 | 閉じた | `scripts/test-run.py` | 3 |
| D2+ #3287 | 閉じた | `scripts/project-decl.py` | 6 |
| M3b #3407 | 閉じた | `scripts/context-policy.py` | 5 |
| M3b #3407 | 閉じた | `scripts/relay.py` | 13 |
| P1a #3369 | 閉じた | `scripts/mode-materials.py` | 1 |
| P1a #3369 | 閉じた | `skills/development-workflow/scripts/stage-check.sh` | 4 |
| S1b #3409 | 閉じた | `scripts/install-official-skills.sh` | 1 |
| S1b #3409 | 閉じた | `scripts/statusline-switch.sh` | 1 |

呼ぶ本文の数は、`plugins/ndf-next/` の `skills/`・`agents/`・`hooks/`・`manifests/` の下で名前を書いたファイルの数である（2026-11-15、`grep -rlE` で数えた）。

機能の対応表の判定（`python -m ndf.parity.trial.probe`、2026-11-15、design/issue-3569 の 81d3baa54）の結果は次のとおり。

```text
{"tool": "parity-trial", "status": "ok", "summary": "204 行を判定した", "items": [], "metrics": {"failed": 123, "passed": 7, "not_run": 74}}
```

- 10 本の行（`entrypoint:scripts/resolve.sh` など）は 10 行とも `failed`
- 10 本のどれかだけを理由に `failed` の行は 4 行: `skill:install-wrapper`（`relay.py`）・`skill:statusline`（`resolve.sh`・`statusline-switch.sh`）・`agent:supervisor`（`supervise.py`）・`agent:supervisor-waits`（`supervise.py`）
- 10 本のどれかと、開いている子のスクリプトの両方を理由に `failed` の行は 17 行

## 期待する結果

新しいプラグインだけを読む起動で、Skill とエージェントの本文が呼ぶ 10 本の機能がすべて新しいプラグイン（`bin/ndf` の副命令）に届く。旧いプラグインのスクリプトへ解決しない。

## 再現手順

1. develop（10ca124c7）で `cd plugins/ndf-next && PYTHONPATH=src uv run --frozen --project . python -m ndf.parity.trial.probe` を打つ。上の 10 本の行が `failed` になる
2. `plugins/ndf-next/bin/ndf-claude -p "/ndf11:development-workflow #3411 の手順 0 から 3 までだけを行う"` を打つと、報告に `SCRIPTS=.../plugins/ndf/scripts` が出る

## 環境

develop 10ca124c7、ndf-next 11.0.0-dev.8、Claude Code 2.1.x、Linux

## 前提

- 前提 1: 10 本はこの課題 1 つで直す。行き先の子（X0・C2・D2+・M3b・P1a・S1b）は開き直さない
- 前提 2: 開いている子（M1〜M8・P1b〜P4・AD1+・AD3+・C5・S1d）が持つスクリプトの呼び出しは、この課題の後も本文に残る。そのため、新しいプラグインだけを読む起動では、それらの呼び出しが旧いプラグインへ解決せずに失敗する。この失敗は機能の対応表の判定で `failed` として見え、それぞれの子が直す
- 前提 3: 既にある副命令（`relay`・`lanes`・`test-slot` など、`plugins/ndf-next/src/ndf/cli.py` の `COMMANDS`）で同じ機能を持つものは、その副命令へ写す。どの旧いスクリプトをどの副命令へ写すかは設計で決める
- 前提 4: 後方互換は持たない。新しいプラグインは開発版で、旧いスクリプトの名前・引数で呼べる経路を残さない

## 対象範囲

含む:
- 10 本を `bin/ndf` の副命令にする（既にある副命令へ写せるものは写す）か、作らない理由を機能の対応表の `not_built` に書く
- 新しいプラグインの Skill・エージェント・hook の本文の 10 本の呼び出しを、副命令の呼び出しへ置き換える
- 本文の冒頭の「今の名前のまま残した」の注記から 10 本の名前を外す（残る名前が無くなった注記は消す）
- Skill の手順 0 の `$SCRIPTS` の決め方を、旧いプラグインへ解決しない形にする（新しいプラグインに解決のエントリポイントを置くか、`ndf` の名前で呼ぶ形にして要らなくするかは設計で決める）
- 新しい副命令の引数と結果のスキーマを、`ndf check` の規則 io・skill が読む仕様に載せる
- この課題を #1142 の子の表（`plugins/ndf-next/src/ndf/checks/children.json`）へ載せる
- 直した後に T2 #3411 の 2 回目の試走をし、落ちた行を原因ごとに分けた一覧を返す

含まない:
- 開いている子が持つ行（その子の受け入れ条件で直す）
- 旧いプラグイン（`plugins/ndf/`）の変更
- 時間で区切った負荷試験

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 新しいプラグインだけを読む起動が始まった | 利用者か conductor が `bin/ndf-claude` を打った | 起動が止まる（この課題の範囲外） | — |
| E2 | Skill の手順 0 が、スクリプトの呼び方を決めた | Skill の本文の手順 0 | 決められなければ、旧いプラグインへ倒れずに理由を出して止まる（受け入れ条件 6） | E1 |
| E3 | Skill・エージェントの本文が副命令を呼んだ | 本文の手順 | 副命令が無い・引数が違うときは、終了コードの表の値と結果 JSON で止まる | E2 |
| E4 | 副命令が結果 JSON を返した | E3 | 結果のスキーマに合わなければ `ndf check` が落とす（受け入れ条件 4） | E3 |
| E5 | 機能の対応表の判定が行ごとの結果を出した | `python -m ndf.parity.trial.probe` | 置き場が無い・呼び手が残る行は `failed` | 本文の置き換えが済んでいる |
| E6 | 2 回目の試走の記録が書かれた | 試走をした worker | 試走が手順 0 で止まれば、止まった理由を記録に書く | E5 |
| E7 | 原因ごとの一覧がオーガナイザーへ返った | E6 | — | E6 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 旧いプラグイン | 今配っている NDF のプラグイン（`plugins/ndf/`）。#1142 の作り直しの前の実装 |
| 機能の対応表 | 旧いプラグインの機能 1 つを 1 行に持ち、行き先の子と試走の結果を持つ表（`plugins/ndf-next/src/ndf/parity/features.json`） |
| 子の表 | #1142 の作り直しの子の Issue を、記号・番号・依存・閉じたかで持つ表（`plugins/ndf-next/src/ndf/checks/children.json`） |
| 入れ替えの前の試走 | プラグインの入れ替え（S2 #3304）の前に、新しいプラグインだけを読む起動でスプリントを流し、機能の対応表の行ごとに届くかを確かめること（T2 #3411） |

## 受け入れ条件

- [ ] 1. `cd plugins/ndf-next && PYTHONPATH=src uv run --frozen --project . python -m ndf.parity.trial.probe` の判定で、10 本の行（`entrypoint:scripts/resolve.sh`・`entrypoint:scripts/supervise.py`・`entrypoint:scripts/test-run.py`・`entrypoint:scripts/project-decl.py`・`entrypoint:scripts/context-policy.py`・`entrypoint:scripts/relay.py`・`entrypoint:scripts/mode-materials.py`・`entrypoint:skills/development-workflow/scripts/stage-check.sh`・`entrypoint:scripts/install-official-skills.sh`・`entrypoint:scripts/statusline-switch.sh`）がどれも `failed` でない
- [ ] 2. 同じ判定で、`skill:install-wrapper`・`skill:statusline`・`agent:supervisor`・`agent:supervisor-waits` の 4 行が `failed` でない
- [ ] 3. 同じ判定で `failed` の行の理由に、10 本の名前が 1 つも出ない（開いている子のスクリプトを理由に `failed` の行は残ってよい）
- [ ] 4. 10 本のそれぞれが、副命令になっているか、機能の対応表の行に `not_built` の理由を持つかのどちらかである。新しい副命令は `ndf <副命令> --help` が終了コード 0 で、引数と結果のスキーマが仕様に載り、`ndf check` の規則 io・skill が通る
- [ ] 5. 新しいプラグインの `skills/`・`agents/` の本文の「今の名前のまま残した」の注記に、10 本の名前が 1 つも出ない
- [ ] 6. 新しいプラグインの Skill の本文の `$SCRIPTS` の決め方が、旧いプラグイン（`plugins/ndf`）を候補に持たない。決められないときは、旧いプラグインへ倒れずに理由を出して止まる
- [ ] 7. 子の表に、この課題（3569）の行があり、子の表を見る `ndf check` の規則が通る
- [ ] 8. `plugins/ndf-next/bin/ndf-claude -p` で `development-workflow` の手順 0 を流した報告に、`$SCRIPTS` の値として旧いプラグインのパス（`plugins/ndf/scripts`）が出ない
- [ ] 9. T2 #3411 の 2 回目の試走の記録が機能の対応表の `trial/` に加わり、落ちた行を原因ごとに分けた一覧（原因・行数・直す場所）がこの課題のコメントにある
- [ ] 10. 旧いプラグインに変更が無い（`git diff --stat origin/develop -- plugins/ndf/` が空）
- [ ] 11. 新しいプラグインの既存のテストのうち、変更した副命令・本文・対応表を見るテストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 新しい副命令は、ほかの副命令と同じ結果 JSON の形（`tool`・`status`・`summary`・`items`・`metrics`）と終了コードの表を使う |
| 移行性 | 新しいプラグインは開発版で利用者へ配っていないため、旧いスクリプトの名前で呼ぶ経路を残さない（互換なし） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 新しいプラグインの `bin/ndf` に副命令が加わる。Skill・エージェントの本文の呼び方が変わる。開発版のため互換なし。旧いプラグインは変わらない |
| データ | 機能の対応表の 10 行と、それを呼ぶ行の `trial` の列が書き換わる。子の表に 1 行加わる。試走の記録が 1 件加わる |
| 既存の振る舞い | 新しいプラグインだけを読む起動で、開いている子が持つスクリプトの呼び出しは旧いプラグインへ解決せずに失敗する（前提 2） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `plugins/ndf-next/bin/ndf <副命令> --help` |
| テスト | `cd plugins/ndf-next && uv run --frozen --project . pytest <変更した範囲のテスト> -q`（全体テストは CI が持つ） |
| 静的解析・型検査 | `plugins/ndf-next/bin/ndf check`（規則 io・skill・子の表）、`python -m ndf.parity.trial.probe` |
| 手動確認（マージ前） | 受け入れ条件 8・9: `bin/ndf-claude -p` で手順 0 を流した報告と、2 回目の試走の記録・一覧を、試走をした worker が見て判定する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 変更は `plugins/ndf-next/` の中だけに置く。`plugins/ndf/` に置かない（`AGENTS.md`・「進め方の決まり」） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」と、新しいプラグインの規約（`ndf check`）。`.md` の文言を照合するテストを書かない |
| テスト戦略 | 副命令の振る舞いは単体テストで、本文の呼び出しの置き換えは機能の対応表の判定と `ndf check` の規則 skill で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更した範囲のテスト・`ndf check`・機能の対応表の判定の実行 |
| 確認してから行う | 副命令の名前と引数の決定（設計承認ゲートで人が見る） |
| 行わない | 旧いプラグインの変更、開いている子が持つ行の置き換え、時間で区切った負荷試験 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 子の表でのこの課題の記号・`order`・`needs` | オーガナイザー（設計で案を出す） | 設計承認ゲート |
| 2. `$SCRIPTS` の決め方を、新しいプラグインの解決のエントリポイントにするか、`ndf` の名前で呼ぶ形にして要らなくするか | 設計 | 設計承認ゲート |
| 3. 10 本のうち、既にある副命令へ写すもの・新しく作るもの・`not_built` にするものの振り分け | 設計 | 設計承認ゲート |

## 依頼（原文）

次は課題の起票時の本文（題を除く）である。

（書き手: 起票者、日付: 2026-11-15。書き手が利用者でない文は、利用者の指示の原文として扱わない）

> ## 種類
>
> 不具合
>
> ## 事象
>
> ndf-next だけを読む起動（`plugins/ndf-next/bin/ndf-claude`）で `/ndf11:development-workflow #3411` を流すと、手順 0 で `$SCRIPTS` が旧いプラグインの `plugins/ndf/scripts` に決まり、旧い `worktree-setup.sh`・`project-decl.py`・`project-mvv.py` が走った（2026-11-15、T2 #3411 の試走）。ndf-next に `scripts/resolve.sh` が無く、探索の次の候補 `plugins/ndf` が当たるためである。
>
> 行き先の子が**閉じている**のに、ndf-next の Skill とエージェントの本文がまだ旧いスクリプトを呼ぶ行が 10 本ある（`bin/ndf` に副命令が無いため S1a #3303 が名前のまま残した）:
>
> | 行き先の子 | 状態 | 旧いスクリプト |
> | --- | --- | --- |
> | X0 #3282 | 閉じた | `resolve.sh` |
> | C2 #3291 | 閉じた | `supervise.py`・`test-run.py` |
> | D2+ #3287 | 閉じた | `project-decl.py` |
> | M3b #3407 | 閉じた | `context-policy.py`・`relay.py` |
> | P1a #3369 | 閉じた | `mode-materials.py`・`stage-check.sh` |
> | S1b #3409 | 閉じた | `install-official-skills.sh`・`statusline-switch.sh` |
>
> ## 期待する結果
>
> ndf-next だけを読む起動で、Skill とエージェントの本文が呼ぶコマンドがすべて ndf-next（`bin/ndf` の副命令）に届く。旧いプラグインのスクリプトへ解決しない。
>
> ## 再現手順
>
> 1. develop（10ca124c7）で `cd plugins/ndf-next && PYTHONPATH=src uv run --frozen --project . python -m ndf.parity.trial.probe`（T2 #3411 の PR で入る確かめ）を打つ。上の 10 本の行が `failed` になる
> 2. `plugins/ndf-next/bin/ndf-claude -p "/ndf11:development-workflow #3411 の手順 0 から 3 までだけを行う"` を打つと、報告に `SCRIPTS=.../plugins/ndf/scripts` が出る
>
> ## 環境
>
> develop 10ca124c7、ndf-next 11.0.0-dev.8、Claude Code 2.1.x、Linux
>
> ## 影響
>
> 入れ替え（S2 #3304）の判定が読む試走の記録（T2 #3411）で、上の行と、それを呼ぶ Skill の行が通らない。11.0.0 の受け入れ条件 2（全行が通る）を満たせない。開いている子（M1〜M8・P1b〜P4・AD1+・AD3+・C5・S1d）が持つ行は、それぞれの子が直す。この Issue は行き先の子が閉じた行だけを持つ。
>
> ## 対象範囲
>
> - 含む: 上の 10 本を `bin/ndf` の副命令にする（か、作らない理由を対応表の `not_built` に書く）。ndf-next の Skill・エージェントの本文の呼び出しを置き換え、冒頭の「今の名前のまま残した」の注記から外す
> - 含まない: 開いている子が持つ行（その子の受け入れ条件で直す）・`plugins/ndf/` の変更
>
> ## 受け入れ条件
>
> - [ ] `python -m ndf.parity.trial.probe` で、上の 10 本の行と、それだけを理由に落ちていた行が `failed` でなくなる
> - [ ] `bin/ndf-claude` で `development-workflow` の手順 0 を流すと、`$SCRIPTS` が旧いプラグインへ解決しない
>
> ## 起票者の推定
>
> - 1 つの Issue にまとめた。どれも「閉じた子の副命令が無い」が原因で、行き先の子を開き直すより新しい子にする方が #1142 の子の表を壊さない（出所: `plugins/ndf-next/src/ndf/checks/children.json` の `closed`）
> - 子の表（`children.json`）への載せ方と順番はオーガナイザーが決める
>
> ## 由来
>
> issue #3411（T2 の試走）。直すのは行き先の子の仕事で、T2 は試走で見つけた不具合を直さない（#3411 の受け入れ条件 I2）
>
> ## 関連
>
> - 親: #1142・#3395
> - 見つけた試走: #3411
> - 同じ形の残り（開いている子が持つ）: #3300 #3296 #3297 #3298 #3299 #3301 #3302 #3408 #3092 #3295 #3097 #3288 #3290 #3294 #3547

この要求の工程の起動指示（書き手: AI の起動指示、日付: 2026-11-15）:

> #3569。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 3569 issues/issue-3569-requirements.md` で写しを作り、コミットする（push しない）。

## 進め方の決まり

並列の計画 p6 のレーン lr-t2 のオーガナイザーの判断（2026-11-15 17:0x JST、T2 #3411 の 1 回目の試走の後）。

- **レーン lr-t2 がこの課題を直す。** 行き先の子は S1d #3547 を除いてすべて閉じており、待っても直す子は来ない
- やること: エントリポイントの `$SCRIPTS` の解決（ndf-next に解決のエントリポイントを置くか、`ndf` の名前で呼ぶ形にして要らなくする。設計で決める）と、旧いスクリプト 10 本を `bin/ndf` の副命令へ（既にある副命令へ写せるものは写す）
- この課題を #1142 の子の表（`plugins/ndf-next/src/ndf/checks/children.json`）へ載せる。入れ替え S2 #3304 の先に要るもの
- 直したら T2 #3411 の 2 回目の試走をし、落ちた行を原因ごとに分けた一覧（原因・行数・直す場所）をオーガナイザーへ返す
- 今の `plugins/ndf/` は変えない。後方互換は意識しない。時間で区切った負荷試験は書かない
- 新しい副命令の引数と結果のスキーマは lspec の仕組み（`ndf check` の規則 io・skill）に載せる
