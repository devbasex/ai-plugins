# #2206 の設計: 問い合わせの手順の書き換えの一覧（AC13）

[issue-2206-design.md](issue-2206-design.md) から分けた節である。NDF の Skill とエージェントの本文（`plugins/ndf/skills/**/*.md`・`plugins/ndf/agents/*.md`）のうち、利用者へ問う手順を集め、各箇所をどう書き換えるかを決める。

## 集め方

`AskUserQuestion`・「利用者（に|へ）確認」・「確認を取る」・「同意を得て」・「同意を得る」・「人（へ|に）問う」・「承認を求める」・「利用者に聞く」・「ユーザーに確認」・「尋ねる」・「選んでもらう」・「判断を仰ぐ」で 82 行（35 ファイル）、その言い換え（「承認を待」「同意を求め」「利用者の承認」「承認を得」「承認を取」「確認後」「頼む」など）でさらに 162 行を ripgrep で集めた（2026-10-31）。問いの手順でない行（承認ゲートの説明・「問わない」の禁止・スクリプトの置き場を尋ねる説明など）を除いた 66 行を、問いごとにまとめて下の表にした。

## 書き換えの規則

| 問いの種類 | 書き換え先 | 区分 |
| --- | --- | --- |
| 承認ゲート 1・2 の承認と、その承認の委任（スプリント MVV の承認） | `AskUserQuestion` の `metadata.source` に区分を付ける | `ndf-gate:1` / `ndf-gate:2` |
| レッドライン（共通原則の C1〜C8・プロジェクトの P・スプリントの R）に当たる操作の承認 | 同上 | `ndf-ask:redline:<番号>` |
| 組み込みの 4 区分に当たる問い（費用・取り消せない外部への操作・人にしかできない操作・規約と契約） | 同上 | `ndf-ask:cost` / `irreversible` / `human-action` / `legal` |
| 上のどれにも当たらない問い | 問わずに AI が決め、`ask.py decide --question` で記録して続ける | なし |
| 問わずに止めて報告するだけの手順 | 変えない（AI の 2 つの行動の「止めて人へ戻す」に当たる） | なし |

承認ゲートを持たない問いを「区分を付けて問う」へ寄せる理由は、設計の決定 2（[issue-2206-design.md](issue-2206-design.md)） と要求の「人へ回す基準」にある。費用・取り消せない操作・レッドラインのどれかに当たるかが中身で変わる手順（例: `requirements-design` の質問）は、区分ごとの場合分けを書き、どれにも当たらなければ AI が決める。

## 承認ゲート 1・2

| 箇所 | 問い | 書き換え |
| --- | --- | --- |
| `skills/development-workflow/SKILL.md`（承認ゲートの節・305 行の付近） | 2 つの承認ゲートの前で `AskUserQuestion` で承認を待つ | `ndf-gate:1` / `ndf-gate:2` を付ける |
| `skills/development-workflow/references/approval-request.md`（1〜9・57・139・191〜193 行の付近） | 承認資料を示して承認を求める | 同上。問いの出し方の節に区分の付け方を足し、正本 `asking.md` を指す |
| `skills/development-workflow/references/agent-layers.md`（110・278・287 行の付近） | `関門` の報告を受けた conductor が承認を求める | 同上 |
| `skills/development-workflow/references/relay.md`（9・139〜141 行の付近） | 例の手順のゲート 1 の問い、本文の待ちの差し戻し | ゲート 1 に `ndf-gate:1`。差し戻しの説明に区分の付け方を足す（`WAIT_REASON` と合わせる。AC14） |
| `skills/development-workflow/references/loop.md`（67・102〜104 行の付近） | `gate2_returned` で止まったときの問い | `ndf-gate:2` |
| `skills/development-workflow/references/pace.md`（194・383 行の付近） | fast・auto で `関門` を返した PR の承認、レッドラインに当たったときの承認 | 194 は `ndf-gate:1`。383 は承認ゲートの場で問うため `ndf-gate:<番号>` |
| `skills/development-workflow/references/pace.md`（145〜147 行の付近） | スプリント MVV の説明の直しと `mvv.md` の承認 | `ndf-gate:1`（承認ゲート 1 の委任の承認として扱う） |
| `skills/development-workflow/references/operation-run.md`（87〜96 行の付近）・`production-merge.md`（17〜31 行の付近）・`waiting.md`（141・168 行の付近） | 本番系へ届く操作の承認 | `ndf-gate:2` |
| `skills/development-workflow/references/stage-notes.md`（72 行の付近） | ドキュメントレビューのマージの承認 | `ndf-gate:1` |
| `skills/release/SKILL.md`（56・67・132〜142・237〜245 行の付近）・`references/release-steps.md`（91・108〜117・136・189 行の付近） | 本番リリースの承認と、資料を直したときの取り直し | `ndf-gate:2` |
| `skills/release/references/form-slide.md`・`form-document.md`・`form-spreadsheet.md`・`form-page.md`（制作物承認の節）・`form-service.md`・`form-desktop.md`・`distribution-forms.md` | 制作物承認と公開の同意 | `ndf-gate:2` |
| `skills/merged/SKILL.md`（132〜147 行の付近） | マージの承認を得てから `merge-when-green --gate-approved` | `ndf-gate:1` / `ndf-gate:2` |
| `skills/layout-review/SKILL.md`（71〜73 行の付近） | 画像を読めないとき人が見て承認する | `ndf-gate:2`（制作物承認の資料に載せる） |
| `agents/director.md`（110・332 行の付近） | 設計案のユーザーの承認 | `ndf-gate:1` |

## レッドライン

| 箇所 | 問い | 書き換え |
| --- | --- | --- |
| `skills/development-workflow/references/project-mvv.md`（14・56・58〜59・71 行の付近） | MVV の候補の決め所・「判定できない」の引き受け・本文の承認 | `ndf-ask:redline:C7`（`.ndf/mvv.*` を書く） |
| `skills/retrospective/SKILL.md`（117〜120 行の付近） | 毎回「MVV を見直すか」を問う | 見直すかは AI が `signals` から決めて記録する。改訂の承認だけを `ndf-ask:redline:C7` で問う |
| `skills/backlog-refinement/references/ranking.md`（140 行の付近） | `.ndf/backlog.json` の書き換えの承認 | `ndf-ask:redline:C7` |
| `skills/merged/SKILL.md`（57・120 行の付近） | `-d` が拒んだブランチを `-D` する同意 | `ndf-ask:redline:C4` |
| `skills/merged/SKILL.md`（126 行の付近）・`skills/release/SKILL.md`（95 行の付近）・`references/release-steps.md`（170・223〜230 行の付近） | 退避先（worktree-trash）の削除の承認 | `ndf-ask:redline:C4` |
| `skills/out-of-scope/SKILL.md`（66・78〜80・118〜120 行の付近）・`references/issue-target.md`（31・42〜43 行の付近） | 起票先の候補の選択と、起票・`note` の同意 | 他のリポジトリへ出すときは `ndf-ask:redline:C5`。同じリポジトリでは AI が決める（`--approved` は AI が付けて打ち直す） |
| `skills/pr/SKILL.md`（36〜39・78 行の付近） | 暗黙に起動したときの push の同意 | fork・他のリポジトリへ出すときは `ndf-ask:redline:C5`。同じリポジトリでは AI が決める |
| `skills/google-workspace/SKILL.md`（26・31〜36 行の付近） | `npm install -g` で gws を入れる同意 | `ndf-ask:redline:C6` |
| `agents/data-analyst.md`（115 行の付近） | DELETE・DROP など破壊的な操作の前の確認 | 利用者のデータなら `ndf-ask:redline:C3`、戻せない操作なら `ndf-ask:redline:C4` |

## 組み込みの 4 区分

| 箇所 | 問い | 書き換え |
| --- | --- | --- |
| `skills/google-workspace/SKILL.md`（44〜48 行の付近）・`skills/document-systems/references/system-gdrive.md`（81 行の付近） | `gws auth login`・`gws auth setup` を頼む | `ndf-ask:human-action` |
| `skills/chrome-cdp/SKILL.md`（47・98・159 行の付近）・`references/local-side-setup.md`（50 行の付近） | PC 側の設定・接続の張り直し・目視を頼む | `ndf-ask:human-action` |
| `skills/official-skills-autoloader/SKILL.md`（4・12・41〜67・94〜95・121 行の付近） | 公式 Skill を取得して入れる前の 4 点の同意 | `ndf-ask:legal`（取得する Skill のライセンスへの同意） |
| `skills/progress-tracking/references/board.md`（85〜86 行の付近） | Project ボードを作る前の承認 | `ndf-ask:irreversible`（組織から見える場所を作り、公開範囲を広げる） |
| `skills/external-ai/SKILL.md`（51 行の付近） | 機密を含むコードを外部の API へ送ってよいか | `ndf-ask:legal`（組織の規約と契約の扱い） |
| `skills/requirements-design/SKILL.md`（114 行の付近） | 安全性・課金・外部公開に関わる点の質問 | 費用なら `ndf-ask:cost`、外部へ出すなら `ndf-ask:irreversible` かレッドラインの番号。どれにも当たらなければ AI が決めて要求の「起票者の推定」に記録する |

## AI が決める

| 箇所 | 問い | 書き換え |
| --- | --- | --- |
| `skills/backlog-refinement/SKILL.md`（108・232・296・301〜323 行の付近）・`references/no-work.md`（58〜60 行の付近）・`references/milestones.md`（59〜66 行の付近） | 「やらない」で閉じる・前倒し・後ろ倒し・親 issue の起票と結び付けの一括の承認 | AI が決めて記録する（課題は開き直せ、C1〜C8 に当たらない）。棚卸しの報告に一覧を残す |
| `skills/retrospective/SKILL.md`（72・232・235・250 行の付近） | 上流のリポジトリ・配布先のブランチ・PR の番号が分からないとき | AI が記録から決める。決まらなければ投稿を止めて報告する |
| `skills/merged/SKILL.md`（250 行の付近）・`skills/cherry-pick-pr/SKILL.md`（145 行の付近） | コンフリクトの解消を確かめてから続ける | AI が解消して続け、解消の内容を報告に書く |
| `skills/chrome-cdp/SKILL.md`（43 行の付近） | 経路 A・B のどちらにも当たらないときの構成 | 問わずに止めて報告する |
| `skills/official-skills-autoloader/SKILL.md`（77・169 行の付近） | 対象の Skill が曖昧なとき・同じ名前の別物があるとき | AI が決める。上書きの前に退避する |
| `skills/implementation-plan/SKILL.md`（32 行の付近）・`skills/plan-to-spec/SKILL.md`（45・67 行の付近） | plan を作るか・候補が複数のとき・plan を残す運用か | AI が決めて記録する |
| `skills/quality-gates/references/definition-of-done.md`（7 行の付近） | どのモードとして扱うか | AI が `development-workflow` のモード判定で決める |
| `skills/release/SKILL.md`（104 行の付近） | スプリントの範囲が分からないとき | AI が状態ファイルから決める。決まらなければ止めて報告する |
| `agents/director.md`（291〜297 行の付近） | 中断から戻ったとき続きから再開するか | AI が決めて記録する |
| `agents/debugger.md`（53 行の付近） | 修正提案を当てる前の確認 | AI が決めて当て、根拠を報告に書く |
| `skills/worktree/SKILL.md`（67〜70 行の付近） | 壊れた設定を直す・消す・作り直す | 変えない（問わずに止めて報告する手順。C7 の設定を AI が書き換えない） |

## 規則を持つ横断の文書

| 箇所 | 今の規則 | 書き換え |
| --- | --- | --- |
| `skills/decision-request/SKILL.md`（3・8〜25 行の付近） | 判断・承認・選択を利用者に求めるすべての場面での材料の示し方 | 「当たる場面」を区分のある問いに絞り、区分の無い判断は材料を記録へ残して自分で決めると書く。区分の表は `asking.md` を指す |
| `skills/development-workflow/references/project-mvv.md`（97 行の付近） | 人へ判断・承認を求める文面の雛形 | 雛形に `metadata.source` の行を足す |
| `skills/AUTHORING.md`（119・177 行の付近） | 実行前確認が要る操作は「対象を提示して同意を取る」手順を本文へ置く | 同意を取る手順には区分を書く。区分に当たらない操作には同意の手順を置かない |

## 件数

| 書き換え先 | 表の行 |
| --- | --- |
| `ndf-gate:1` / `ndf-gate:2` | 14 |
| `ndf-ask:redline:<番号>`（同じリポジトリでは AI が決める行を含む） | 9 |
| 組み込みの 4 区分 | 6 |
| AI が決める・止めて報告する・変えない | 11 |
| 横断の規則 | 3 |
| 計 | 43 |

既定の宣言（`enforce`・4 区分）で、上の書き換えどおりに動いて hook に止められ続ける箇所は残らない。止められるのは区分を付けなかった問いだけで、その問いは「AI が決める」の規則に当たる。
