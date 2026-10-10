# #1594: worker の定義が SKILL.md を読ませず、Skill の手順を委ねた worker ごとに読み方がぶれる（plan-to-spec の並列で章立てがそろわない）

正は課題の本文（#1594）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> supervisor が確定仕様化（plan-to-spec）の手順 2〜7 を、課題ごとに 3 つの ndf:worker へ並べて委ね、起動指示で plan-to-spec の SKILL.md を読むよう指示した。1 つの worker は「worker の定義が SKILL.md を読むことを禁じている」として読まずに進め、設計文書の章立てをそのまま移した仕様書を返した（標準の章立てで組み直しを頼み、1 往復の手戻り）。別の worker は起動指示を優先して読んだ。worker の定義と、Skill の手順を委ねる起動指示の扱いが噛み合っていない。
>
> ## どこで見つけたか
>
> takemi-ohama/novel の PR #41（#11 #12 #13 の確定仕様化）。
>
> ## なぜこの変更の範囲外なのか
>
> 3 層の規約（worker の定義と、supervisor が手順を委ねるときの渡し方）の問題で、開発対象リポジトリでは直せない（MVV なし: 上流）。
>
> ## 直さないと何が起きるか
>
> Skill の手順を worker へ委ねると、worker ごとに手順の読み方がぶれ、並べた成果物の体裁がそろわない。手戻りが生じる。
>
> ## 現状と直し方の絞り込み（ndf 10.17.68 で確認）
>
> - 起動指示へ手順の要点を写す規約は既にある（`agent-layers.md` の起動指示の表の「手順」の行。`references/excerpt.md` をコピーし、無い Skill は supervisor が要る段落をコピーする。#828）
> - 抜粋（`references/excerpt.md`）を持つ Skill は progress-tracking だけで、plan-to-spec には無い
> - `new close` は確定仕様化を全課題で 1 ステップに回す（`supervise_lib/sprint.py` の `_spec_pr_steps`）が、`normal` では supervisor が回し、課題ごとの worker が残る（`agent-layers.md:84`）
>
> 直し方は次の 2 つ: (a) plan-to-spec に `references/excerpt.md`（章立てと引継ぎの表）を置く / (b) `normal` でも確定仕様化を 1 件の worker で回すと `agent-layers.md` の「取り込み」の行に書く。worker に SKILL.md を読ませる案は、#828 の抜粋の規約と逆向きになるため採らない。
>
> ## 由来
>
> takemi-ohama/novel の PR #35 の振り返り（PR #41）。提案: worker が SKILL.md を読まない規則を保つなら、supervisor が委ねる手順の要点（plan-to-spec なら章立てと引継ぎの表）を起動指示へ写すことを agent-layers.md に書く。あるいは、起動指示が名指しした references／SKILL.md の読み取りは許す、と worker の定義に書く。
>
> ## 追記: 確定仕様化の worker の数（スプリント m1385 の振り返り、PR #1732、2026-10-04）
>
> ai-plugins の確定仕様化（3 層の「取り込み」フェーズ）でも、supervisor が課題ごとに worker を 1 件ずつ立てている（m1693: 3 件、m1385: 3 件）。`skill-stats --agents` は 2 スプリント続けて「取り込み」に `worker を使いすぎ` の目印を付けた（m1385: supervisor の実作業 30,202・worker 3 件・固定費の合計 152,157）。m1693 の振り返り（https://github.com/devbasex/ai-plugins/pull/1718#issuecomment-5976476551 ）で「次も付けば worker を 1 件へ束ねる」と決めていた。
>
> 束ねると、worker ごとの読み方のぶれ（この課題の本題）と固定費の両方が減る。直し方の候補に「確定仕様化は 1 件の worker が全課題を書く」を加える。

## 目的

- 3 層で確定仕様化を worker へ委ねたとき、どの worker も同じ手順（plan-to-spec の標準の章立てと引継ぎの表）で仕様書を書き、組み直しの往復が起きない状態にする
- 「取り込み」フェーズの確定仕様化で立てる worker を 1 件にし、`skill-stats --agents` が「取り込み」に `worker を使いすぎ` の目印を付けない状態にする

## 前提

- 前提 1: worker の定義（`plugins/ndf/agents/worker.md` と `agent-layers.md` の「worker が守る規則」の 6）の「`SKILL.md` を読まない」は変えない。手順は起動指示の「手順」で渡す（#828 の抜粋の規約に沿う。依頼文が「採らない」と決めている）
- 前提 2: 確定仕様化の手順の要点（呼び出し・結果の読み方・判断の基準。判断の基準に標準の章立てと引継ぎの表の要点を含む）は、`plugins/ndf/skills/EXCERPTS.md` の上限（40 行以内かつ 2,000 文字以内）に収まる。収まらないときの扱いは設計が決める（未決 1）
- 前提 3: `pace: fast` の `new close` の確定仕様化のステップ（`supervise_lib/sprint.py` の `_spec_pr_steps`。全課題を 1 ステップで回す）は、既に 1 件で回っているので振る舞いを変えない
- 前提 4: 確定仕様化を supervisor 自身が書くか、1 件の worker に委ねるかは supervisor の裁量に残す。委ねるときの件数を 1 件とする（課題ごとに並べない）
- 前提 5: 効果の実測（worker の件数と目印）は、この変更を入れた後の最初のスプリントの `skill-stats --agents` で見る。マージの前には測れない

## 対象範囲

含む:
- 確定仕様化を worker へ委ねるときの件数（全課題で 1 件）を、3 層の規約（`agent-layers.md` の「取り込み」の行）に書く
- plan-to-spec の手順の要点を、起動指示へコピーできる抜粋として用意する（`EXCERPTS.md` の形）
- plan-to-spec の本文から抜粋を指す 1 行

含まない:
- worker の定義の「`SKILL.md` を読まない」の規則を緩めること（前提 1）
- `new close` のプランの確定仕様化のステップの変更（前提 3）
- `normal` のプランへ確定仕様化のステップを足すこと（`agent-layers.md` の表のとおり、`normal` は supervisor で回す）
- plan-to-spec 以外の Skill への抜粋の追加（他の Skill で同じぶれが見つかったら別の課題にする）
- 抜粋と本文の一致を機械で確かめるチェックの新設

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | supervisor が「取り込み」フェーズで確定仕様化の工程に入った | conductor が `normal` のスプリントで取り込みのフェーズを supervisor に渡した | — | スプリントの Pull Request がマージされた |
| E2 | supervisor が全課題の確定仕様化を 1 件の worker へ委ねた（または自分で書いた） | E1 | worker が `結果: 判断が要る` で返したら、supervisor が手順を足して委ね直すか自分で書く | E1 |
| E3 | supervisor が plan-to-spec の抜粋を起動指示の「手順」へコピーした | E2 で worker へ委ねると決めた | 抜粋が無い・読めないとき、supervisor は本文から要る段落をコピーする（`agent-layers.md` の既存の規約） | E2 |
| E4 | worker が抜粋の章立てと引継ぎの表に従って、全課題の仕様書を書き `spec-finalize` でコミットした | E3 の起動指示 | `spec-finalize` が `stopped` を返したら、worker が `summary` を読んで直し打ち直す。直せなければ `結果: 判断が要る` | E3 |
| E5 | `skill-stats --agents` が次のスプリントの「取り込み」の worker の件数と目印を出した | 振り返り | 記録が無いときは測れなかったと振り返りに書く | E4 の後のスプリントが終わった |

## 用語

| 用語 | 意味 |
| --- | --- |
| 確定仕様化 | 用語集のとおり |
| 抜粋 | 用語集のとおり |
| worker | 用語集のとおり |
| supervisor | 用語集のとおり |

## 受け入れ条件

- [ ] `plugins/ndf/skills/plan-to-spec/references/excerpt.md` があり、1 行目が `<!-- ndf-excerpt: plan-to-spec -->`、見出しが `## 呼び出し` / `## 結果の読み方` / `## 判断の基準` の 3 つだけ、この順で並ぶ
- [ ] その抜粋が 40 行以内かつ 2,000 文字以内である（`EXCERPTS.md` の「分量を確かめる」のコマンドで測る）
- [ ] その抜粋の「呼び出し」に `spec-finalize` の呼び出し 1 行があり、「結果の読み方」に `ok` と `stopped`（終了コード 1・3）の次にすることがある
- [ ] その抜粋の「判断の基準」に、plan-to-spec 本文の手順 3 の標準の章立ての見出しの並びと、引継ぎの表の各行（plan の項目 → 引継ぎ先の章）が載る。本文に無い規則は載らない
- [ ] `plugins/ndf/skills/plan-to-spec/SKILL.md` に「呼ぶ側へ渡す抜粋は `references/excerpt.md`」を指す 1 行がある
- [ ] `agent-layers.md` の「取り込み」の行（またはそこから指す箇所）に、確定仕様化を worker へ委ねるときは全課題を 1 件の worker に委ね、起動指示の「手順」へ plan-to-spec の抜粋をコピーすると書かれている
- [ ] 退行しない: worker の定義（`plugins/ndf/agents/worker.md`）と `agent-layers.md` の「worker が守る規則」の 6（`SKILL.md` を読まない）の文が変わらない
- [ ] 退行しない: `supervise_lib/sprint.py` の `_spec_pr_steps` の出力（`new close` の確定仕様化は 1 ステップ）が変わらない
- [ ] 既存の検査（`python3 scripts/check-skill-frontmatter.py`・`python3 scripts/check-markdown-links.py`・全体テスト）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 変更の後の最初のスプリントで、`skill-stats --agents` の「取り込み」の worker の件数が 1 以下で、`worker を使いすぎ` の目印が付かない（リリース後に測る） |
| 運用・保守性 | 抜粋は本文の写しであり、本文に無い規則を足さない（正本は本文。`EXCERPTS.md`） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（Skill の呼び方・スクリプトの引数・結果 JSON は同じ） |
| データ | 無し |
| 既存の振る舞い | 3 層の `normal` の取り込みで、supervisor が確定仕様化に立てる worker が課題ごとから 1 件になる。worker が受け取る手順が抜粋の形にそろう |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`・`python3 scripts/check-markdown-links.py`・`python3 scripts/check-cross-skill-refs.py` |
| 分量 | `wc -l < plugins/ndf/skills/plan-to-spec/references/excerpt.md` と、`EXCERPTS.md` の文字数のコマンド |
| 手動確認 | 変更を入れた後の最初の `normal` のスプリントの振り返りで、`skill-stats --agents` の「取り込み」の worker の件数と目印を見る。仕様書の章立てが課題の間でそろっているかを確定仕様化の Pull Request で見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 抜粋は `plugins/ndf/skills/<Skill名>/references/excerpt.md`（`plugins/ndf/skills/EXCERPTS.md`）。3 層の規約の正本は `plugins/ndf/skills/development-workflow/references/agent-layers.md` |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md` と `EXCERPTS.md`。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |
| テスト戦略 | 文書の変更が中心のため、既存の検査スクリプトと全体テストで退行が無いことを見る。効果は振り返りの実測で見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テストと既存の検査スクリプトの実行、抜粋の分量の測定 |
| 確認してから行う | worker の定義の規則の変更（この課題では行わない。前提 1）。承認ゲート 1・2 で人が見る |
| 行わない | plan-to-spec の本文の手順そのものの書き換え、他の Skill への抜粋の追加、`new close` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 抜粋が 2,000 文字に収まらないとき、`EXCERPTS.md` の「上限を超えたとき」の順（入れないものを外す → スクリプトへ出す → それまでは supervisor が段落をコピー）のどこで止めるか | 設計（`design`） | 承認ゲート 1 |
| 2. 委ねる件数の規約を「取り込み」の表の行に書くか、起動指示の節に一般則（同じ Skill の手順を課題ごとに並べない）として書くか | 設計（`design`） | 承認ゲート 1 |
