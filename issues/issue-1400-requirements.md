# #1400: プロジェクト MVV を normal・auto の各工程で従わせる（特に設計と承認ゲートの照合）

正は課題の本文（#1400）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> せっかくMVVを定めたので、MVVを定めているプロジェクトはnormal, autoモードとも各工程でMVVに従うようにしましょう。特に設計と、承認時(MVVに従っているかどうかチェック)かな。

（2026-09-28 利用者）

## 今どうなっているか（2026-09-28 develop で確かめた）

プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`、#1366）が判断の基準として渡るのは次の地点だけである（`plugins/ndf/skills/development-workflow/references/project-mvv.md` の「判断の地点と記録」）。

| 地点 | MVV が渡るか |
| --- | --- |
| 承認ゲートの MVV 判定（`mvv-gate.py`） | `pace: fast` / `auto` だけ（`supervise_lib/mission_waves.py` の `plan_mvv_design` / `plan_mvv_release`）。**`normal` の承認ゲートは MVV 判定を走らせず、承認資料にも載らない** |
| cross-review・cross-refactoring・supervise の judge・範囲外の起票 | 渡る |
| 要求と受け入れ条件・設計・実装の worker（supervise の `work` のステップ） | **渡らない**。`project_mvv.block` を読むのは judge だけ（`supervise_lib/steps.py` の `JudgeStep`） |
| `requirements-design`・`design`・`tdd-cycle` の Skill 本文 | **MVV に触れていない** |

あわせて確かめたこと:

- `mvv-gate.py check` は「従う」でレッドラインが無いと、`mission-state.py gate` で承認ゲートの記録（`by: mvv`）を書いて 0 を返す。**この記録が承認ゲートを自動で通す根拠になる**（`pace.md` の「MVV 判定」）
- `mission-state.py init` がプロジェクト MVV の参照（版・sha256）を状態に書くのは `pace: fast` / `auto` のときだけである
- `project-mvv.py context` の MVV の節は、ai-plugins（版 1）で 87 行・11,271 バイトある

## 目的

- プロジェクト MVV が承認済みのプロジェクトでは、`pace` によらず（`normal` / `auto` / `fast`）各工程が MVV を判断の基準として読む
- 特に **設計** で、決定ごとに根拠にした MVV の項目を残し、MVV に反する決定を人へ戻す
- 特に **承認ゲート** で、`normal` でも MVV に従っているかの MVV 判定を走らせ、その結果を承認資料に載せる（`normal` は MVV 判定の後も人が承認する。自動で通すのは今どおり `auto` / `fast` だけ）

## 前提

- 前提 1: MVV 判定の規則（従う / 反する疑い / 判定できない・レッドライン・機械のチェックの 5 項目）と、判定に渡す MVV の節の中身は変えない
- 前提 2: 承認ゲートは 2 つのまま増やさない。`normal` で MVV 判定を足しても、承認するのは人である
- 前提 3: プロジェクト MVV が無い・未承認・一致しない・壊れているプロジェクトの振る舞いは今と同じ（「MVV なし」で止めずに進む）
- 前提 4: MVV の節の文は `project-mvv.py context` / `project_mvv.block` の 1 か所から作る（地点ごとに写さない）
- 前提 5: `normal` の承認ゲートの MVV 判定は、ミッション状態ファイルがある流れ（`supervise.py new mission`）で走らせる。状態ファイルの無い課題単位の流れ（conductor が工程を手で進める）は、`requirements-design` / `design` / `tdd-cycle` の Skill 本文の手順（AC2・AC3・AC10）だけが MVV を読み、承認ゲートの MVV 判定は足さない
- 前提 6: `normal` の MVV 判定も `mvv-gate.jsonl` の 1 行として改訂の兆候（「判定できない」の数え上げ）に入れる。行に `pace` を残し、後から `normal` の分を分けて数えられるようにする
- 前提 7: 工程の中で「人へ戻す」とは、worker が作業の報告を「結果: 判断が要る」で終え、理由と MVV の項目を書くことである（人へ直接問わない。supervise の承認ゲートで人に届く）

## 対象範囲

含む:
- `supervise_lib` の `work` のステップのプロンプトへの MVV の節
- `normal` の設計と本番のプランへの MVV 判定のステップ（承認ゲートの記録は書かない）
- `mission-state.py init` の `normal` でのプロジェクト MVV の参照の書き込み（MVV 判定に要るなら）
- `requirements-design` / `design` / `tdd-cycle` の Skill 本文
- `approval-request.md`・`project-mvv.md`・`pace.md` の表

含まない:
- `mvv-gate.py` の判定の規則・レッドライン・機械のチェック
- プロジェクト MVV の策定・改訂の手順
- 承認ゲートの数と、`auto` / `fast` の自動で通す条件
- ミッション MVV（`R<番号>`）の扱い
- 状態ファイルの無い課題単位の流れへの承認ゲートの MVV 判定（前提 5）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | プロジェクト MVV の状態を読んだ | 工程の開始（supervise の実行の開始・手順 0 の `project-mvv.py check`） | 無い・未承認・一致しない・壊れている → 「MVV なし」で進む（前提 3） | — |
| E2 | `work` のステップのプロンプトに MVV の節を入れた | supervise が `work` のステップを始めた | E1 で MVV なし → 節を入れず今と同じプロンプト（AC1・AC7） | E1 |
| E3 | 要求と範囲を MVV と突き合わせた | `requirements-design` の実行 | 反する疑い → 要求を書き切らずに人へ戻した（AC3・前提 7） | E2（supervise のとき） |
| E4 | 設計の決定ごとに根拠の項目を記録した | `design` の決定の記録を書いた | 反する疑い → その決定を書かずに人へ戻した（AC2） | E3 |
| E5 | 実装の選択を MVV と突き合わせた | `tdd-cycle` の実装 | 反する疑い → 実装を進めずに人へ戻した（AC10） | E4 |
| E6 | `normal` の承認ゲートの前に MVV 判定を走らせた | 設計 PR のレビューが終わった（承認ゲート 1）／本番の前の検査が終わった（承認ゲート 2） | 機械のチェックで外れた・LLM が読めない → 外れた理由を判定として残し、人の承認で止まる（AC4） | E4（承認ゲート 1）・E5（承認ゲート 2） |
| E7 | MVV 判定の結果を承認資料に載せた | E6 が終わった | MVV 判定が走らなかった（MVV なし） → 「MVV なし」と書いて今の資料で止まる | E6 |
| E8 | 人が承認か差し戻しを答えた | 人 | — | E7 |
| E9 | 覆しを記録した | E8 の答えが E6 の判定と食い違った | 記録を書けない → 今の `mission-state.py gate --by user` の失敗の扱いに従う | E6・E8 |

## 用語

| 用語 | 意味 |
| --- | --- |
| プロジェクト MVV | `.ndf/mvv.md`・`.ndf/mvv.json` に宣言し、利用者が承認した Mission / Vision / Value と `P<番号>` |
| MVV の節 | `project-mvv.py context` / `project_mvv.block` が作る、判断の地点へ渡す文 |
| 根拠の項目 | `Mission` / `Vision` / `Value 3` / `C4` / `P1` / `R2` の形の番号。無ければ「根拠なし」、MVV が無ければ「MVV なし」 |
| 覆し | 承認ゲートで人の答えが直前の MVV 判定と食い違ったこと（`override_pass` / `override_reject`） |

## 受け入れ条件

- [ ] AC1: 承認済みのプロジェクト MVV があるとき、supervise の `work` のステップ（要求・設計・実装・修正）のプロンプトに judge と同じ MVV の節（`project_mvv.block` の出力と同じ文字列）が入る。無いときは入らず、今と同じプロンプトになる
- [ ] AC2: `design` の決定の記録は、決定ごとに根拠にした MVV の項目（`Value 3` / `C4` / `P1` など、無ければ「根拠なし」、MVV が無ければ「MVV なし」）を持つ。MVV に反する疑いのある決定は、設計に書かずに人へ戻す（理由と項目つき。前提 7）
- [ ] AC3: `requirements-design` は、要求・範囲が MVV に反する疑いがあれば人へ戻す（理由と項目つき）。受け入れ条件の文に MVV の項目を書き込むことは求めない
- [ ] AC4: `pace: normal` のミッションで、承認ゲート 1（設計 PR のマージ）と 2（本番への配布）の前に `mvv-gate.py check` 相当の MVV 判定が走り、判定（従う / 反する疑い / 判定できない）・理由・根拠の項目が承認資料（`approval-request.md` の形）に載る。機械のチェックで外れたときは、外れた理由が判定の欄に載る
- [ ] AC5: AC4 の MVV 判定は、判定が「従う」でもミッション状態ファイルに承認ゲートの記録（`by: mvv`）を書かない。`normal` のプランは判定が何であっても承認ゲートで止まり、人の承認（`mission-state.py gate ... --by user`）の後にだけ先へ進む
- [ ] AC6: AC4 の MVV 判定は `mvv-gate.jsonl` に `pace: normal` を含む 1 行として残る。人の承認と MVV 判定が食い違ったとき（「反する疑い」「判定できない」を人が通した → `override_pass`・「従う」を人が差し戻した → `override_reject`）、`project-mvv-signals.jsonl` に覆しが 1 行書かれる
- [ ] AC7: プロジェクト MVV が無い・未承認・一致しない・壊れているプロジェクトでは、AC1〜AC4 の地点がすべて今と同じに動く。`normal` の承認ゲートの前に LLM を呼ばない
- [ ] AC8: `pace: auto` / `fast` の承認ゲートの振る舞い（「従う」でレッドラインに当たらなければ `by: mvv` を書いて自動で通す）と、既存の `pace` のテストの結果は変わらない
- [ ] AC9: `project-mvv.md` の「判断の地点と記録」の表に、足した地点（`work` のステップ・`requirements-design`・`design`・`tdd-cycle`・`normal` の承認ゲート）と、それぞれの記録の置き場が載る。`pace.md` の承認ゲートの表の `normal` の列に MVV 判定が載る
- [ ] AC10: `tdd-cycle` は、実装の選択（依存の追加・公開インタフェースの形・データの扱い）が MVV に反する疑いがあれば、実装を進めずに人へ戻す（理由と項目つき）
- [ ] AC11: ai-plugins で 1 本のミッションを通し、設計の決定の記録に MVV の根拠の項目が入ること・承認ゲートの資料に MVV 判定が載ることを確かめる（リリースの後の確かめ）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `work` のステップ 1 回あたりのプロンプトの増分は MVV の節の 1 回分（ai-plugins で 11,271 バイト）に限る。`normal` のミッションで増える LLM の呼び出しは承認ゲート 1 回につき `mvv-gate.py` の 1 回だけ |
| 運用・保守性 | MVV の節を作る所は 1 か所（前提 4）。判定と覆しの記録は既存の `mvv-gate.jsonl` / `project-mvv-signals.jsonl` に書き、新しい記録の置き場を作らない |
| 移行性 | 既存の `normal` のミッション状態ファイル（プロジェクト MVV の参照を持たない）でもプランが止まらずに動く。MVV 判定の前の機械のチェックで断られたときは AC4 の「機械のチェックで外れた」として資料に載せる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `mvv-gate.py check` に承認ゲートの記録を書かない呼び方が増える（既定の振る舞いは変えない）。`supervise.py new mission` の `normal` のプランに MVV 判定のステップが増える |
| データ | `mvv-gate.jsonl` の行に `pace` が増える（既存の行は変えない）。`normal` のミッション状態ファイルにプロジェクト MVV の参照が増えうる |
| 既存の振る舞い | MVV が承認済みのプロジェクトで、`normal` の承認資料に判定が載る。`work` のステップのプロンプトが MVV の節の分だけ長くなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・`claude plugin validate .` |
| 用語 | `python3 plugins/ndf/scripts/glossary.py check --file <変えた文書>` |
| 手動確認 | AC11: ai-plugins のミッション 1 本で、設計の決定の記録と承認資料を conductor が読み、根拠の項目と判定が載っていることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`（Skill は `plugins/ndf/skills/`、スクリプトは `plugins/ndf/scripts/`。実験版には置かない。既定で働く変更のため安定版の経路） |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`・`AGENTS.md`（`.md` の文言を固定するテストを書かない） |
| テスト戦略 | AC1・AC4〜AC8 は `plugins/ndf/scripts/tests/` の単体テスト（LLM は偽物に差し替える）。AC2・AC3・AC10 は Skill 本文の手順で、文言のテストは書かない。AC11 はリリースの後の実機の確かめ |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、MVV の節を `project_mvv.block` から作ること、記録の既存の置き場への追記 |
| 確認してから行う | `mvv-gate.py` の既定の振る舞い（「従う」で承認ゲートの記録を書く）を変えること、`mvv-gate.jsonl` の既存のキーを変えること |
| 行わない | 判定の規則・レッドラインの変更、`normal` で承認ゲートを自動で通すこと、承認ゲートの追加 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `normal` の MVV 判定に承認ゲートの記録を書かせない方法（`mvv-gate.py` の引数を足すか、`normal` のプランが判定の記録だけを読む別の入口を使うか） | `design` | 設計 PR |
| `normal` のミッション状態ファイルにプロジェクト MVV の参照を書くか（書かなければ `approval_refusal` が MVV 判定を断る） | `design` | 設計 PR |
| `normal` の MVV 判定を改訂の兆候の閾値（`settings.revise_after`）で `auto` / `fast` と同じに数えるか、分けるか（前提 6 は記録を分けられる形にするところまで） | 利用者（振り返り） | AC11 の後 |
