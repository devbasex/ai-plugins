# 用語集: NDF の課題の棚卸し（`ndf-issue-upkeep`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

backlog-refinement と out-of-scope が課題を分類し、起票するときの語。区分・現象レイヤー・修正レイヤー・起票先

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 棚卸し | — | 既存の課題の本文・マイルストーン・ラベルを現状に合わせ、着手の順位を決め直すこと。スクラムのバックログリファインメントに当たる。工程表の行名（進捗記録の stage の値）でもある | 手入れ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 区分 | — | backlog-refinement が課題ごとに決める 8 つ（そのまま・追記が要る・書き直しが要る・閉じてよい・やらない・重複・ルートコーズ・要判断） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 現象レイヤー | — | 課題が実際に現れている場所。ファイル・クラス・レイヤーのいずれかで書く | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正レイヤー | — | 原因を直すべき場所。その責務を持つべき場所までさかのぼる | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| ルートコーズ | — | 修正レイヤーが現象レイヤーと違う課題に付ける区分。現れている場所では直さない | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| クラスタ | — | 同じ修正レイヤーを指す課題の集合 | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 親 issue | — | クラスタの修正レイヤーを直すために新しく作る課題 | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 子 issue | — | クラスタに属する既存の課題。親 issue を作った後も閉じない | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正方針 | — | 修正レイヤーへの直し方。移動 / 統合 / 新設 / 向きの修正 / 分離 の 5 つ | 採る手 | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| やらない | — | 課題そのものは成り立つが、抱える費用が直す費用を下回ると決める区分の値 | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 再検討条件 | — | 「やらない」で閉じた課題を再び考える条件 | 再燃の条件 | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 範囲外の課題 | — | この変更の受け入れ条件にも直す対象にも入らない課題。見つけたその場で issue にする | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 起票先 | — | issue-file.py create が課題を作るリポジトリ | — | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 由来 | — | 範囲外の課題を見つけた元。PR #<番号> か issue #<番号> の形で書く。Pull Request がまだ無ければ起点の issue | — | — | `plugins/ndf/skills/out-of-scope/SKILL.md` |
| 本文の骨格 | — | 範囲外の課題の本文が持つ見出し。Issue の記載項目の定義（plugins/ndf/scripts/data/issue-items.json）の項目名で書き、種類ごとの必須の項目・起票者の推定・由来を含む | — | — | `docs/specifications/out-of-scope-issue-file.md` |
| 提示の要約値 | — | 承認資料に載せた内容から作る sha256。issue-file.py の create では起票先・題・本文・ラベル、note では打つ先・番号・1 行から作り、同意の後の --approved に渡して、示した内容と打つ内容が同じことを確かめる | — | — | `docs/specifications/out-of-scope-issue-file.md` |
| 上流リポジトリ | — | NDF の Skill・エージェント・hook の実体を持つリポジトリ | 配布元のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 開発対象リポジトリ | — | NDF を使って開発している側のリポジトリ。gh repo view が返すもの | 開発対象のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 品質のクォータ | — | 棚卸しの順位付けで、品質向上の Issue（定義したラベルの Issue）に先に割り当てる容量の割合。最低の件数を持ち、残りの容量を今の順位で埋める。.ndf/backlog.json の quality セクションで定義したときだけ効く | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| プロダクトバックログ | — | open の課題を着手の順に並べた全体。マイルストーンの順（上の層）→ マイルストーンの中の順位（下の層）の辞書順で読む | — | — | — |
| スプリントバックログの候補 | — | 1 つのマイルストーンの中の課題を順位で並べたもの。次のスプリントはその先頭から容量の分を切り出す | — | — | — |
| 遅延コスト | — | 課題の着手を遅らせると失う量の相対値。実害の大きさ・時間的重要度・リスク低減の 3 つの見積の段階の和。略して CoD | — | — | — |
| 実害の目安 | — | 実害の大きさの見積の段階の帯を影響範囲の区分で決める表。既定は全体の停止・データの損失・安全機構の欠落が 13〜20、一部の機能の誤動作・手戻りが 5〜8、表示や終了コードの誤り（結果は正しい）が 2〜3、どれにも当たらなければ 1。帯の中の値はアンカー（段階を比べる基準の課題）と比べて決める。設定で変えられる | — | — | — |
| 退避策の状態 | — | 退避策・回避策が実際に止まるのを防いでいるか（防いでいる / 部分的 / 無い）。防いでいるときだけ実害の大きさとリスク低減を下げる根拠になる。実害の大きさを下げてよいのは実害の目安の区分を 1 つ下げるまでである | — | — | — |
| WSJF | — | 遅延コストを大きさの見積の段階で割った値。人が実施する前提の一般的な形で、順位の値の大きさの指数が 1 のときに当たる | — | — | — |
| 見積の段階 | — | 遅延コストの 3 つの要素と大きさに付ける相対値。設定の尺度（既定は 1, 2, 3, 5, 8, 13, 20）の中から選ぶ。大きさは AI エージェントが実施したときの費用（所要時間・LLM の費用・変更量）で付け、人の工数では付けない | — | — | — |
| 切り出しの境界 | — | マイルストーンの中の順位を、大きい課題のクォータに当てた 1 切れを除いて先頭から、大きさの見積の段階の和が容量の残りに収まるまで取ったときの、最後の課題 | — | — | — |
| 前倒しの候補 | — | 後ろのマイルストーンか未設定にあり、遅延コストが直近のマイルストーンの切り出しの境界の課題より余白以上に高い課題 | — | — | — |
| 後ろ倒しの候補 | — | 直近のマイルストーンのうち、同じマイルストーンの課題が依存していない課題の中で遅延コストが最も低い 1 件。前倒しの候補の最小の遅延コスト以上のものと、順序で次のマイルストーンが無いときは出さない。前倒しの候補と対にして示す | — | — | — |
| 容量 | — | 1 つのスプリントへ切り出す大きさの見積の段階の和の上限。設定か引数で受け、既定値を持たない | — | — | — |
| 余白 | — | 前倒しの候補にするために、遅延コストが切り出しの境界の課題を上回るべき尺度の段階の数。既定は 1 | — | — | — |
| 自動の閾値 | — | 前倒しを承認なしに反映するために、実害の大きさかリスク低減のどちらかが届くべき段階。既定は 8 | — | — | — |
| バックログの設定 | — | 尺度・余白・自動の閾値・容量・被依存の対応表・ラベルの下限・大きさの指数・大きい課題の閾値とスロットの割合・実害の目安・アンカーを持つ `.ndf/backlog.json` | — | — | — |
| 順位の値 | — | 遅延コストを大きさの見積の段階の α 乗で割った値（α は大きさの指数）。マイルストーンの中の順位を決める | — | — | — |
| 大きさの指数 | — | 順位の値で大きさの見積の段階に掛ける指数 α。0 以上 1 以下で、既定は 0.3（AI エージェントが実施する前提）。1 で WSJF、0 で遅延コストだけになる | — | — | — |
| 大きい課題 | — | 大きさの見積の段階が設定の閾値（既定は尺度の上から 2 番目。既定の尺度では 13）以上の課題 | — | — | — |
| 大きい課題のクォータ | — | 容量のうちスロットの割合（既定 0.5）の分を、マイルストーンの中で遅延コストが最大の大きい課題の 1 切れへ先に当てる取り決め。容量が無い回は出さない | — | — | — |
| 分割が要る | — | 大きい課題のクォータに当たったが、スロットに収まるサブイシューが無く課題そのものもスロットを超える状態。報告に出し、自動で分割しない | — | — | — |
| 人の判断待ち | — | 着手の前に人の判断（needs-decision・承認ゲート）を要する状態。費用に数えず、順位の値を変えずにマーカーとして出す | — | — | — |
| 候補の上限 | — | 1 回の棚卸しで区分を決める候補の数の上限。candidates の --limit、.ndf/backlog.json の candidate_limit、既定 10 の順に決まる | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 持ち越し | — | 候補の上限を超えたため、その回で区分を決めずに残した候補。candidates の metrics.deferred に番号だけが載る | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 原因の型 | — | 課題を原因で分けた A〜I の区分。A 失敗の判定・B 記録とログ・C 回復と再開・D 待ちと上限・E 解決の時点・F 並行の衝突・G 機能と方針・H 文書・I その他（#2335） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 柱 | — | 大方針の子で、根本原因の仕組みごとに直す課題。#2335 では P0〜P8（#2336〜#2344）（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 差分の棚卸し | — | 開発ループでは数回のループの 1 回に 1 回（既定 3 回。`.ndf/backlog.json` の `depths.diff.every`）、ループの外では毎スプリントのまとめで行う棚卸しの深さ。振り返りの範囲の変更で食い違った本文の訂正・直ったのに開いたままの課題を閉じる・閉じた親の子の見直し・その範囲で起票した課題（振り返りの範囲の始まりの時刻より後に作られた課題）への見積の段階の付与だけを行う（#2315・#2914） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 振り返りの範囲 | — | 1 回の振り返りと差分の棚卸しが扱うループの 1 回の並び。まだどの範囲にも入っていない見送ったループの 1 回（深さの記録の `diff` の `not_due` の行）と、今のループの 1 回からなる。ループの外では今のスプリントだけで、見送ったループの 1 回は次に開発ループで走らせる回まで残る。深さの記録の `diff` の `due` の行の `values.scope` に残る（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 起動の判定 | — | 棚卸しの深さごとに、その回で走らせるかを決める判定。`upkeep.py depth due <深さ>` が終了コード 0（起動する）か 3（起動しない）を返し、深さの記録へ判定の行を残す（#2315・#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 見直し | — | 数スプリントに 1 回行う棚卸しの深さ。後回しを除く open の課題の枝刈り・順位付け・マイルストーンの再編と、ニーズ・シーズの調査からの起票を行う（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 大整理 | — | 十数スプリントに 1 回行う棚卸しの深さ。未完了の課題すべてを原因の型で分け、Issue 単位では直らない根本原因を探し、見つかれば大方針を立てて課題とマイルストーンを組み直す。後回しの再評価を含む（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 後回しの再評価 | — | 後回しのマイルストーンの課題を、前倒し・やらない・据え置きに分けること。大整理の一部（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| ニーズの調査 | — | 定義した利用の記録と市場・競合から、利用者が求めている変更を探すこと（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| シーズの調査 | — | 定義した技術の構成・ライブラリの競合・周辺環境の変化から、取り入れられる変更を探すこと（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 調査の記録 | — | 調査の項目ごとに出典と調べた日時を持つ結果。見直しのフェーズだけが読んで起票に使う（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 材料の設定 | — | プロジェクトがニーズとシーズの調査で読む先（プロダクトの種類と利用者・利用の記録と読む範囲・市場と競合・技術の構成・ライブラリの競合・周辺環境・後回しのマイルストーン）を書いたもの（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 見直しのオーナー | — | 見直しのフェーズ（待ち時間の調査を含む）を受け持つ 1 つのセッション。設定をロックの中で取り、終わったら解放する（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 大方針 | — | 大整理で見つけた根本原因を直す親の Issue。柱と受け入れ条件を持つ（例 #2335）（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 棚卸しの深さ | — | 1 回の棚卸しが扱う範囲。差分の棚卸し・見直し・大整理の 3 つ。`upkeep.py` の `--depth diff\|review\|overhaul`（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 調査の項目 | — | 調査を小さく分けた単位。側面と対象の組（例: `seeds.library:pytest`）。1 項目が 1 つの調査の記録になる（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 付け直しの記録 | — | 大整理が課題の親とマイルストーンを変える前に残す、課題ごとの前の状態。`upkeep.py revert` が読んで戻す（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 深さの記録 | — | 棚卸しの深さごとの起動の判定と実行を 1 行ずつ残す記録。メインディレクトリの `.ndf/backlog/depths.jsonl` で、コミットしない。差分の棚卸しの行は、棚卸しのステップの秒・ドル・言語モデルの名前を持つ（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| Issue の種類 | — | 不具合・機能・調査・運用など、記載すべき項目の組を決める Issue の区分 | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載項目 | — | Issue の本文（または課題の保存先の欄）に書く 1 つの事柄。例: 受け入れ条件・再現手順・担当者 | — | — | `docs/specifications/ndf-issue-items.md` |
| 必須の項目 | — | その種類の Issue で、欠けると起票の前の検査で止まる項目 | — | — | `docs/specifications/ndf-issue-items.md` |
| 該当時の項目 | — | 条件に当たるときだけ書く項目。当たる条件を定義に書く | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載の水準 | — | 記載項目に何を書けば満たしたことになるかの基準 | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載項目の定義 | — | 種類ごとの記載項目・区分・記載の水準をまとめた 1 か所の SSOT | — | — | `docs/specifications/ndf-issue-items.md` |
| 起票の前の検査 | — | AI が Issue を作る前に、本文が記載項目の定義を満たすかを判定する検査 | — | — | `docs/specifications/ndf-issue-items.md` |
| 起票の後の検査 | — | 作られた open の Issue の本文が記載項目の定義を満たすかを判定する検査 | — | — | `docs/specifications/ndf-issue-items.md` |
| 課題の保存先 | — | Issue を管理するシステム（GitHub・Redmine・リポジトリの中の markdown など）。.ndf/project.json の issues が示す | — | — | `docs/specifications/ndf-issue-items.md` |
| 一致の検査 | — | 記載項目の定義と、フォーム・out-of-scope の骨格・仕様のテンプレートが食い違っていないかを確かめる検査。CI で動く | — | — | `docs/specifications/ndf-issue-items.md` |
| 工程が足すセクション | — | 起票の後に工程が Issue の本文へ書き足すセクション（進行・承認ゲートの回答・閉じた理由・再発など）。起票者は書かない | 工程が足す節 | — | `docs/specifications/ndf-issue-items.md` |
| 保存先ごとの表し方 | — | 記載項目を、課題の保存先ごとにどの欄・セクション・ラベルで表すかの対応 | — | — | `docs/specifications/ndf-issue-items.md` |
| 見積の入力 | — | ndf issues rank --scores に渡す、課題ごとの見積の段階と根拠の JSON | — | — | `docs/specifications/ndf-issues-rank-estimate-input-and-sources.md` |
| 見積の根拠 | — | 見積の入力の列ごとの why。段階を付けた理由の 1 行 | — | — | `docs/specifications/ndf-issues-rank-estimate-input-and-sources.md` |
| 実害の区分 | — | 見積の入力の ubv.impact。UBV の帯（実害の目安）を引く区分 | — | — | `docs/specifications/ndf-issues-rank-estimate-input-and-sources.md` |
