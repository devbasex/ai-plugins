# PR #57 Codex ビジネス意義レビュー

| メタ | 値 |
|---|---|
| **PR** | [#57 — feat(ndf)!: playwright-scenario-test v0.3.0 — pure pytest-playwright 完全移行 (v4.2.0)](https://github.com/takemi-ohama/ai-plugins/pull/57) |
| **レビュアー** | OpenAI Codex CLI (`codex exec`) |
| **観点** | 事業・GTM・市場戦略 (技術レビューは別途完了済) |
| **実行日** | 2026-04-26 |
| **実行時間** | 約 8 分 / tokens 129,705 |
| **背景** | 技術レビュー (Codex 3 ラウンド + Amazon Q + Gemini) 完了後、事業観点のみに絞って依頼 |
| **結論** | ⚠️ **コード品質ではなく、事業的な梱包がまだ追いついていない**。power-user 向け merge は合理的、新看板機能として広報するなら周辺メッセージ修正を同時に。 |
| **直近 1ヶ月の最重要アクション** | (1) `marketplace.json` / `plugin.json` / `plugins/ndf/README.md` の storefront 文言更新 / (2) `browser-test` と `playwright-scenario-test` の役割分担を docs で明確化 / (3) 10分で動くデモケースの公開 |
| **PR コメント** | https://github.com/takemi-ohama/ai-plugins/pull/57#issuecomment-4321871998 |

---

# PR #57 ビジネス意義レビュー (Codex)

## エグゼクティブサマリー
この PR は、`playwright-scenario-test` を「独自 DSL を覚えないと使えない社内向け道具」から、「pytest-playwright を使う開発者がそのまま乗れる AI 時代向けの拡張レイヤー」へ再定義した、事業上かなり重要な転換です。  
結論として、**市場戦略としては正しい方向**です。特に、Claude Code のようなエージェントがテストを“書く側”に回る世界では、独自 DSL より標準 pytest に寄せた方が普及確率は高いです。  
一方で、この PR 単体では **プロダクトの価値は上がったが、見つかりやすさ・伝わりやすさ・買われやすさはまだ弱い** です。  
最大の論点は実装ではなく、**「これは誰の何の業務を、どれだけ短縮する製品なのか」を marketplace と README レベルでまだ言い切れていないこと** です。  
したがって、技術的には前進、事業的には **merge 後 1 か月で GTM メッセージと導線整備をやり切れるか** が勝負です。

## 1. 市場ポジショニング

### 1.1 強み
この skill の本質は、Playwright そのものの代替ではなく、**Playwright を AI エージェント運用に最適化した“実務レイヤー”** にあります。  
Playwright 公式はブラウザ自動化基盤として強いですが、`/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/SKILL.md` を読む限り、本 skill はそこに **テスト設計の方法論、page role 分類、証跡標準化、Drive 共有、Markdown レポート** を足しています。  
つまり競争軸は「ブラウザを動かせるか」ではなく、**“AI が再現性の高い E2E を短時間で作り、レビュー可能な証跡まで揃えられるか”** です。  
この位置づけは、Playwright / Selenium / Puppeteer のような実行基盤とは競争しつつも、実は真正面からは競合しません。  
むしろ「それらをどう現場の開発運用に落とすか」の層を取ろうとしている点が重要です。  

Cypress と比べたときの強みは、**Python / pytest エコシステムへの自然な接続**です。  
Cypress は体験の一貫性とプロダクト完成度が高い一方、NDF は Claude Code から自然言語で指示し、pytest ベースでそのまま CI・既存 Python テスト文化に乗せやすい。  
これは JS 中心組織には刺さりにくいですが、**バックエンド主導の Python チーム、QA 専任が薄い SaaS チーム、データ/業務アプリ系チーム**にはかなり実用的です。  

Selenium と比べると、差別化は「新しさ」ではなく **運用思想のパッケージ化**です。  
Selenium は広い互換性と歴史がありますが、NDF は `docs/01-methodology.md` と `docs/02-page-roles.md` に見られるように、**何をテストすべきかまで同梱**している。  
単なる自動化ツールではなく、**AI にテスト観点を教え込む教材兼ランタイム**になっている点は独自です。  

Puppeteer と比べると、NDF は「細かいブラウザ操作の自由度」では勝負していません。  
代わりに、**pytest fixture と marker で“チームが再利用できる品質運用”にした**のが価値です。  
これは OSS 開発者向けには十分説明可能な違いです。  

TestRail と比べると、競合ではなく補完です。  
TestRail はテスト管理と可視化の製品です。  
NDF の強みは、**手で作るテストケース管理ではなく、AI と開発者が一緒に“今必要な E2E と証跡”を素早く作ること**です。  
したがって、NDF は TestRail の代替ではなく、**TestRail に入る前の生成・実行・証跡収集レイヤー**として語るべきです。  

Browserbase と比べると、差別化軸はインフラではなく**ワークフロー所有**です。  
Browserbase はクラウドブラウザとエージェント実行基盤です。  
NDF はその上に載せられる可能性があるが、現状は **ローカル/既存 CI に近い導入の軽さ**が利点です。  
逆に言えば、将来は競合より提携余地が大きいカテゴリです。  

最大の強みは、AI コーディングエージェント時代における **“テストを書く主体が人からエージェントに移る” ことを前提にした設計**です。  
`SKILL.md` の page role、checklist、template、`conftest.py.template` は、全部「人間 QA の教育コスト」を下げるだけでなく、**AI が迷いなくテストを書ける文法と足場**になっています。  
これは単なる E2E ツールではなく、**AI に品質業務を外注するための最小オペレーティングシステム**という見方ができます。  

### 1.2 弱み / 競合との差
弱みは明確で、**今のままだと“何でも入った NDF の一機能”に見えてしまい、単独価値として発見されにくい**ことです。  
`plugins/ndf/.claude-plugin/plugin.json` の description には機能が多く並びますが、焦点が広すぎます。  
利用者は「PR ワークフロー」「Codex 連携」「Slack 通知」「Google Drive」「E2E テスト」のどれを主価値として認識すべきか迷います。  

`plugin.json` の `keywords` に `playwright` `pytest` `e2e` `qa` `accessibility` `testing` が入っていないのも機会損失です。  
ビジネス的には、検索で見つからないプロダクトは存在しないのと同義です。  

さらに `.claude-plugin/marketplace.json` の `ndf` description はまだ v4.0.0 ベースで、**36 skills や scenario test の価値が表に出ていません**。  
これは実装以前に、**店頭 POP が古い**状態です。  
このままでは PR #57 の価値を merge しても市場に伝わりません。  

また、Cypress は「全部入りの体験」と「わかりやすい UI/Cloud」を売れますが、NDF はそこに対して **学習・運用・導入後の価値訴求が文章頼み**です。  
OSS では普通でも、事業観点では **“最初の 10 分で価値がわかるデモ” がまだ不足**しています。  

Browserbase のようなエージェント基盤は「AI エージェント向け」を非常に前面に出しています。  
一方 NDF は、実際にはかなり agent-native なのに、外から見ると「pytest skill の一つ」に見えます。  
ここはポジショニングの負けです。  

TestRail のようなエンタープライズ QA 製品と比べると、NDF は **監査、権限、実行履歴、カバレッジ管理、組織導入の説明責任**が弱いです。  
そのため現状の主要ターゲットは enterprise QA 本体ではなく、**開発チーム主導の品質活動**に限定されます。  
この限定自体は悪くありませんが、誰向けかを曖昧にしたまま広く売ろうとすると失敗します。  

### 1.3 評価
**独自 DSL 廃止は、市場戦略として正しかった**です。  
理由は単純で、AI エージェントがコードを書く世界では、差別化は DSL ではなく **“標準の上にどう勝つか”** に移るからです。  

独自 DSL は短期的には差別化になります。  
しかし普及フェーズでは、学習コスト、IDE 連携、CI 接続、外部 contributor 参加、AI による自動生成のしやすさで不利になります。  
PR 本文でも触れている通り、pytest-native 化によって pytest ecosystem をそのまま使えるのは、事業上かなり大きいです。  

これは「差別化を捨てた」のではありません。  
**差別化の場所を、記法から運用知に移した**のです。  
その意味で、この PR はプロダクトの軸を正しく変えています。  

ただし今後の差別化は、もう「pytest の上で動く」だけでは足りません。  
勝ち筋は **role-based test planning、AI 自動起動、証跡パッケージ、Drive/PR/CI 連携、リリース判定の型化** にあります。  
ここを前面に出せれば強いです。  
出せなければ「ちょっと便利な pytest fixture 集」に見えて埋もれます。  

## 2. ターゲットユーザーと採用シナリオ

### 2.1 主要ターゲット (推定)
現状の一次ターゲットは、**Claude Code をすでに使っている個人開発者と 2〜15 名規模の開発チーム**です。  
理由は、配布チャネルが Claude Code plugin marketplace であり、導入動線が `/plugin marketplace add` → `/plugin install ndf@ai-plugins` に依存しているからです。  
これは市場の母数を絞りますが、同時に「AI 活用に前向きな層」に絞れる利点もあります。  

二次ターゲットは、**QA 専任が弱い SaaS / 内製プロダクトチーム**です。  
特に PM、EM、テックリードが「リリース前確認を人海戦術で回している」組織に向いています。  
`SKILL.md` が提供する動画、trace、HAR、a11y、CWV、report.md は、まさにその手の組織が雑にやっている品質確認を型化できます。  

三次ターゲットは、**受託・制作会社や複数案件を持つ小規模ベンダー**です。  
page role 別 checklist は、案件ごとにゼロからテスト観点を起こす工数を減らせます。  
これは「毎回誰かの経験に依存していた確認作業」を半製品化できます。  

現時点でメインターゲットではないのは、**大企業の中央 QA 組織**です。  
彼らが重視するのは、標準化だけでなく、証跡保管、承認フロー、監査、権限分離、既存テスト管理ツール連携、SLA です。  
NDF にはそこまでのパッケージングがまだありません。  
したがって enterprise を狙うなら、現段階では QA 本部より **開発部門の先行導入**から入るべきです。  

### 2.2 解消する pain point
この skill が解消する最大の pain は、**「E2E をやるべきとわかっていても、設計と証跡が面倒で継続できない」** です。  
多くのチームは Playwright 自体は知っていても、何をテストすべきか、どこまで証跡を残すべきか、どう共有すべきかで止まります。  
`docs/01-methodology.md` と `docs/02-page-roles.md` は、そこを強く埋めています。  

2 つ目の pain は、**AI にテストを書かせても品質観点が抜けやすい**ことです。  
普通の LLM は UI を触るテストを書けても、role 別のチェック観点までは安定しません。  
この skill は、**AI の行動範囲を狭めることで品質を上げる**タイプの製品です。  
それは agent 時代の非常に正しいプロダクト設計です。  

3 つ目の pain は、**テスト失敗時の説明責任**です。  
動画、HAR、trace、console、a11y、CWV をまとめて `report.md` 化し、場合によっては Drive 共有できるのは、単なる「テスト実行」ではなく「関係者に説明できること」を売っています。  
ここはビジネス価値が高いです。  
特に PM やデザイナーや CS を巻き込む現場では、テストコードより証跡の方が価値になります。  

4 つ目の pain は、**認証ありフローの再利用の面倒さ**です。  
これは技術実装には触れませんが、事業上は「ログインが必要な業務アプリでも回せる」という採用可能性に直結します。  
一般論として、認証をまたぐと E2E は一気に面倒になります。  
そこを超えられる体験は中小チームに効きます。  

5 つ目の pain は、**a11y と Core Web Vitals が“やるべきだが後回し”になりがち**なことです。  
この skill はそれをテスト本体に隣接させています。  
つまり、別施策として予算化しにくい品質観点を、**E2E 導入予算の中に潜り込ませる**設計です。  
これは B2B QA 製品として強い発想です。  

### 2.3 採用障壁
最大の採用障壁は、**「だったら素の pytest-playwright を直接使えばよいのでは？」という問い**です。  
これは避けられません。  
したがって、NDF は“pytest を置き換えるもの”ではなく、**pytest 導入後に必ず発生する面倒ごとを減らすもの**として語る必要があります。  

この問いに対する優位性は、現状だと 5 つあります。  
1 つ目は、**role-based planning** です。  
2 つ目は、**証跡の標準化** です。  
3 つ目は、**AI 自動起動との相性** です。  
4 つ目は、**a11y/CWV の近接配置** です。  
5 つ目は、**Drive 共有まで含めた関係者コミュニケーション** です。  

逆に言うと、この 5 点がドキュメントや LP で明確でなければ、利用者は「自前 fixture の方がコントロールできる」と判断します。  
特に上級者はその傾向が強いです。  
上級者を説得するには、「自由度」ではなく **導入後 2 週間の運用負債をどれだけ減らせるか** を見せる必要があります。  

導入障壁の 2 つ目は、**NDF が大きすぎること**です。  
Playwright scenario test だけ欲しい人にも、8 agents / 36 skills / hooks / Slack などの文脈がついてきます。  
統合プラグインとしては合理的でも、単機能導入の観点では重いです。  
これは将来的に **standalone plugin 化** を検討すべき論点です。  

導入障壁の 3 つ目は、**配布チャネルの限定**です。  
Claude Code plugin marketplace に乗っていることは強みでもありますが、Claude Code を使わない組織には届きません。  
Cursor、GitHub Copilot、Devin を使うチームには、そのままでは市場が閉じています。  
OSS として広げるなら、**“Claude Code plugin でありつつ、中身は普通の pytest package”** という二重の売り方が必要です。  

導入障壁の 4 つ目は、**`browser-test` skill との役割重複の見え方**です。  
`browser-test` は「ブラウザで動作確認」、`playwright-scenario-test` は「理論ベース E2E と証跡収集」です。  
しかし利用者の自然言語では両者はしばしば同じ依頼に見えます。  
このままだと簡易確認の skill に流れてしまい、本命 skill が使われない可能性があります。  

### 2.4 評価
ターゲットは広く見積もらない方がよいです。  
この PR 時点で最も勝ちやすいのは、**“AI を使っているが QA 専任がいないプロダクト開発チーム”** です。  
そこでは、この skill は「E2E フレームワーク」ではなく、**リリース前確認を省力化する品質ワークフロー**として売れます。  

現時点では enterprise QA に正面から行くより、**開発チーム起点で横展開される道具**を狙うべきです。  
その方が product-market fit を作りやすいです。  

## 3. Skill のディスカバラビリティと活性化

### 3.1 description / Triggers の妥当性
`/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/SKILL.md` の `description` は情報量が豊富で、技術的にはかなり良いです。  
ただし business 観点では、**少し盛り込みすぎ**です。  
利用者に価値を伝える文章としては強いが、AI の auto-invoke trigger としては信号が散っています。  

良い点は、`pytest-playwright` `a11y` `Core Web Vitals` `Google Drive` `video` `page role` など、差別化要素がほぼ全部入っていることです。  
悪い点は、それにより **skill の中心価値がぼやけやすい**ことです。  
AI にとっても人にとっても、「これは E2E planning skill なのか、動画証跡 skill なのか、品質ゲート skill なのか」が一読で定まりにくい。  

`when_to_use` で Triggers を分離した設計自体は正しいです。  
`plugins/ndf/CLAUDE.md` でもその改善意図が記録されており、方向性は良いです。  
ただし trigger phrase はまだ改善余地があります。  

現状の trigger は、技術者寄りの言葉に少し偏っています。  
実務で出やすい自然言語は、もっとこうです。  
「リリース前に主要導線だけ見て」  
「フォームまわりの回帰確認して」  
「E2E を自動で書いて」  
「動画つきで不具合報告ほしい」  
「UI の受け入れ確認を回したい」  
「サインアップ導線の smoke test して」  
こうした表現が今の `when_to_use` には十分に入っていません。  

### 3.2 自動起動トリガーの抜け
抜けている重要語は、**`smoke test` `受け入れテスト` `UI回帰` `主要導線` `動作確認` `release signoff` `サインアップ` `購入フロー` `チェックアウト` `証跡` `録画` `デモ用動画`** あたりです。  
特に `browser-test` が「動作確認」を取りに行くため、scenario-test は **“継続的 E2E / 証跡付き / pytest 化” の文脈に強く寄せた trigger 設計**が必要です。  

もう一つの抜けは、**役割ベースの具体シーン**です。  
今の `page role` 言及はテスト設計者には刺さりますが、一般利用者は page role という言葉を使いません。  
代わりに「一覧ページ」「申込フォーム」「管理画面」「ログイン」「設定画面」などの平易な語を trigger にもっと入れた方が自動起動しやすいです。  

さらに、「AI にテストを書かせたい」という意図も前面に出し切れていません。  
今の文章は skill の内容説明としては優秀ですが、**エージェントに“これは自分が使うべきものだ”と気付かせるタグ付け**としては、もう少し直接的でよいです。  

### 3.3 インストール導線
導線は概ねシンプルです。  
`README.md` と `docs/project-overview.md` の通り、マーケットプレイス追加と `ndf` インストールで入るのは摩擦が小さいです。  
Claude Code plugin モデルと相性は良いです。  

ただし問題は、**scenario-test 単体の価値に最短で到達できない**ことです。  
利用者はまず NDF 全体を理解させられます。  
`plugins/ndf/README.md` も大きな統合プラグインの説明が先に来るため、testing use case の人には情報密度が高すぎます。  

`marketplace.json` の `ndf` description が古いのは、導線上かなり痛いです。  
ここは marketplace 上の第一印象なので、**PR #57 の価値を最初に伝える場所**です。  
ここが古いままだと、いくら skill が良くても click されません。  

また `plugin.json` の description は良くなっていますが長いです。  
長い description は詳細説明にはよくても、storefront では読み飛ばされます。  
一文目に **“AI が pytest-playwright テストと証跡を自動生成する”** と言い切る短い表現が必要です。  

### 3.4 評価
discoverability は **60 点**です。  
skill 自体の設計は良いです。  
しかし市場に見つけてもらう設計はまだ弱いです。  

最優先でやるべきなのは 3 つです。  
1 つ目は `marketplace.json` と `plugin.json` の storefront 文言更新です。  
2 つ目は `keywords` に testing 系語彙を足すことです。  
3 つ目は `browser-test` との役割分担を README 上で明確にすることです。  

この 3 つはコードよりはるかに小さい作業ですが、採用率には大きく効きます。  

## 4. コミュニティ & エコシステム

### 4.1 OSS 持続可能性
MIT であることは良いです。  
ただし、持続可能性はライセンスだけでは決まりません。  
現状の repo は **内部導入 acceleration の色が強く、外部 contributor がどこから参加してよいかがやや見えにくい**です。  
これは `README.md` と `AGENTS.md` の位置づけからも感じます。  

単独 maintainer 色が強いプロジェクトで、かつ大きい PR が続くと、外部 contributor は入りにくくなります。  
PR #57 自体も大規模で、しかも高度です。  
技術的にはよいのですが、コミュニティ形成の観点では **「後から人が追いつける粒度」** をどう作るかが課題です。  

OSS として伸ばしたいなら、外部参加者に必要なのは実装の正しさ以上に **参加しやすい入口**です。  
具体的には、`good first issue`、サンプルプロジェクト、導入事例、サポート範囲、安定 API 面の宣言、破壊的変更の方針です。  
今は skill の中身は強いが、コミュニティ運営の型はまだ薄いです。  

### 4.2 pytest-playwright 公式との関係
この skill は、pytest-playwright 公式と競合するより、**その上の opinionated companion** として共生すべきです。  
それが一番筋がよいです。  

上流に送れるものは、コードより **知見・事例・パターン** の方が多そうです。  
たとえば fixture 運用、AI との相性、artifact 管理、role-based template などは、公式に「こうすべき」と入れるより、周辺エコシステムとして存在した方が自然です。  

したがって、戦略は「上流を置き換える」ではなく、**“Playwright/pytest-playwright を採用したチームが、その翌日に必要になる運用レイヤー”** として位置づけるべきです。  
これは敵を作らないし、導入時の説明も容易です。  

Browserbase などの agent/browser infra とも競合一辺倒にしない方がよいです。  
彼らは実行基盤とスケールを売り、NDF は test authoring と evidence workflow を売る。  
この分業は成立します。  
将来的には「Browserbase 上で NDF scenario-test を動かす」物語も作れます。  

Anthropic 公式 Skill との関係は、基本的に**補完**です。  
`mcp-builder` のような公式 skill は汎用の土台です。  
NDF の `playwright-scenario-test` は、**品質/QA という垂直ドメインに深く刺した skill** です。  
重複より、公式エコシステムを埋める実務 skill として見せる方がよいです。  

### 4.3 評価
OSS としての種はあります。  
ただし今のままだと、**“よくできた個人/チーム用 power tool” で止まる確率が高い**です。  
それ自体は悪くありません。  
ただ、もし事業化やコミュニティ拡大を狙うなら、次に必要なのはコードではなく **運営のインターフェース**です。  

## 5. 収益化 / 商用展開の余地

### 5.1 現状の SaaS / Enterprise 化余地
現状の OSS 単体でそのまま SaaS になるわけではありません。  
しかし、**商用化の芽は明確にある**と思います。  

売れる可能性があるのは、ランナーそのものではなく **証跡・標準化・運用連携**です。  
具体的には、動画、trace、HAR、a11y、CWV、report.md、Drive 共有の束は、開発チームにとって「実行結果」ではなく **リリース可否を議論する資料**です。  
この資料化こそ B2B でお金になる部分です。  

SaaS 化するなら、有望なのは **Evidence Hub** です。  
各 test run の証跡を保管し、PR、Jira、Slack、Drive、GitHub Releases と紐づけ、リリース単位で履歴比較できる。  
これなら OSS ランナーの上に有料レイヤーを被せられます。  

Enterprise 機能として売りやすいのは、**権限、保持期間、PII マスキング、承認フロー、監査ログ、品質ゲート、SAML/SSO、Jira/TestRail 連携**です。  
現状 skill はそこまで持っていませんが、方向性としては自然です。  

コンサル販売も現実的です。  
特に「AI エージェント時代の品質ワークフロー設計」「Claude Code + pytest-playwright の導入テンプレート」「page role ベースの test design 内製化」は、組織導入支援として売りやすいです。  
多くの組織はツールより **型**にお金を払います。  
NDF はその型をすでに持っています。  

### 5.2 提案
収益化の順序は、**SaaS 直行よりも、まず導入支援と premium workflow から**が堅いです。  
最初の商材としては、次の 3 本が考えやすいです。  

1 本目は、**“AI E2E Starter Pack”** です。  
対象は中小の SaaS チーム。  
Claude Code / pytest-playwright / NDF を 1〜2 週間でセットアップし、主要導線 10 本程度を自動テスト化する導入支援。  

2 本目は、**“Release Evidence Pack”** です。  
動画、a11y、CWV、HAR、trace を PR や Slack に整形して流す運用をテンプレ化し、品質会議の時間を削減する。  
これは PM、QA、EM が直接価値を感じやすいです。  

3 本目は、**“Enterprise Governance Add-on”** です。  
有料機能として証跡保管、権限、承認、チーム別テンプレート、監査を追加する。  
この線なら OSS コアを殺さずに monetization できます。  

Drive 共有、HUD 動画、a11y/CWV は、単体では売りにくいです。  
しかし **「非エンジニアにも読めるリリース証跡パッケージ」** として束ねると売り物になります。  
要するに、機能ではなく **会議資料を自動生成する製品**として売るべきです。  

## 6. ロードマップ提案

### 6.1 v0.4.0 で優先すべきこと (Top 3)
**Top 1: プロダクトの見せ方を刷新すること**  
`marketplace.json`、`plugin.json`、`plugins/ndf/README.md` の scenario-test 訴求を更新し、  
「AI が pytest-playwright テストを書き、証跡を残し、共有まで行う skill」  
と一文で伝わるようにするべきです。  
これは最優先です。  
実装を足すより効果が大きいです。  

**Top 2: 単独導入の黄金導線を作ること**  
今の quickstart は悪くありませんが、もっと「10 分で動く」に寄せるべきです。  
サンプルアプリ、1 コマンド bootstrap、1 本の smoke test、1 つの動画レポートまでを最短導線にする。  
ここができると adoption が跳ねます。  

**Top 3: “証跡から意思決定へ” を完成させること**  
report.md はあるので、次は「PR コメント」「Slack 要約」「リリース判定テンプレ」に接続するべきです。  
単なる artifact ではなく、**誰が何を判断するための出力か** を明確にする。  
ここが製品価値を一段引き上げます。  

### 6.2 v1.0.0 までに必要なこと
v1.0.0 の条件は、機能網羅ではなく **ポジションの固定**です。  
つまり「この製品は何者か」を市場に一言で説明できる状態です。  

必要なのは 5 点です。  
1 つ目は、**standalone plugin または package としての切り出し検討**です。  
NDF 全体の一機能のままだと、testing use case の採用が伸びにくいです。  

2 つ目は、**導入事例または benchmark case**です。  
「主要導線 5 本を何分で作れたか」  
「リリース前確認が何時間減ったか」  
「a11y/CWV の見落としをどれだけ拾えたか」  
この種の数字が必要です。  

3 つ目は、**CI/PR 連携の business story**です。  
人がローカルで使えるだけでは v1.0.0 として弱いです。  
PR 単位、release 単位で結果が消費されるところまでつなげるべきです。  

4 つ目は、**チーム運用テンプレートの充実**です。  
role ベースの test 雛形、プロジェクト種別別テンプレート、導入チェックリスト、失敗時 runbook まで整うと、製品として厚みが出ます。  

5 つ目は、**商用の布石になる evidence registry の原型**です。  
SaaS でなくてもよいですが、「成果物がどこにあり、何を意味し、誰が承認したか」を残せる基盤が欲しいです。  
これが enterprise への橋になります。  

### 6.3 やるべきでないこと
**独自 DSL を復活させること**はやるべきではありません。  
これは戦略的に逆戻りです。  
AI 時代の勝ち筋は標準の上に乗ることです。  

**テスト管理プラットフォーム全部入りを目指すこと**も避けるべきです。  
TestRail 的な領域に正面から行くと、プロダクトが散ります。  
NDF は test management ではなく **AI-assisted test execution workflow** に集中すべきです。  

**page role を増やしすぎること**も罠です。  
分類は増えるほど賢く見えますが、導入コストと説明コストも増えます。  
まずは採用される role に絞り、使用頻度で伸ばすべきです。  

**Claude Code 専用性を過度に強めること**も避けたいです。  
配布は Claude Code plugin でよいですが、価値自体は `pytest package` として外でも読める形を維持すべきです。  
そうしないと市場が狭すぎます。  

**機能の多さを売りにしすぎること**も危険です。  
今の NDF は全体として多機能です。  
しかし testing 領域で勝つには、「多機能」より **“これを入れると release signoff が速くなる”** の方が効きます。  

## 7. 総括 (Go / No-Go ではなく、What's Next)
- このまま merge してよいか: ⚠️
- 理由はコード品質ではなく、**事業的な梱包がまだ追いついていない**からです。
- ただしこれは release block ではありません。
- **power-user 向けに merge するのは合理的**です。
- 逆に、これを「広く採用される新看板機能」として出すなら、周辺メッセージ修正を同時にやるべきです。

- 直近 1 ヶ月の最重要アクション 1:
- `marketplace.json` `plugin.json` `plugins/ndf/README.md` を更新し、scenario-test の価値を storefront レベルで伝える。
- ここで「AI が pytest-playwright テストと証跡を自動生成する」という一文を明示する。

- 直近 1 ヶ月の最重要アクション 2:
- `browser-test` と `playwright-scenario-test` の役割分担をドキュメント上で明確化する。
- 「簡易動作確認」と「再利用可能 E2E + 証跡収集」を分けて説明しないと活性化が鈍る。

- 直近 1 ヶ月の最重要アクション 3:
- 1 つのデモケースを作る。
- 例として「ログイン → 一覧 → フォーム送信 → report.md + 動画 + Drive リンク」までを 10 分で体験できる導線を公開する。
- これは README を 100 行足すより効きます。

- 6 ヶ月後の理想ポジション:
- 「Claude Code 向けの testing skill」ではなく、
- **“AI エージェント時代の release evidence workflow for pytest-playwright”**
- として認知されている状態です。
- そのときの主顧客は、QA 専任の厚い大企業ではなく、
- **開発チーム主体で品質活動を回す SaaS / 内製チーム**であるはずです。
- そこから、証跡保管、承認、監査、CI 統合を有料レイヤーに伸ばすのが自然です。

## 補足評価
この PR の事業上の意義は、単に DSL を捨てたことではありません。  
**「AI が読むための独自記法」から「AI も人も既存 ecosystem で扱える標準記法」へ移ったこと**です。  
これは配布チャネルが Claude Code plugin であっても、中身の市場を広げる判断です。  

より厳しく言うと、ここで捨てたのは DSL ではなく、**“閉じた賢さ”**です。  
その代わりに得たのは、**“開いた普及可能性”**です。  
事業としては後者の方が圧倒的に重要です。  

この PR は、技術の完成より先に、**売れる形に近づいた PR** と評価します。  
残っているのは、売り方です。  

## 参照したローカル資料
- [README.md](/work/ai-plugins/README.md)
- [AGENTS.md](/work/ai-plugins/AGENTS.md)
- [CLAUDE.md](/work/ai-plugins/CLAUDE.md)
- [docs/project-overview.md](/work/ai-plugins/docs/project-overview.md)
- [docs/ndf-plugin-reference.md](/work/ai-plugins/docs/ndf-plugin-reference.md)
- [plugins/ndf/README.md](/work/ai-plugins/plugins/ndf/README.md)
- [plugins/ndf/CLAUDE.md](/work/ai-plugins/plugins/ndf/CLAUDE.md)
- [plugins/ndf/.claude-plugin/plugin.json](/work/ai-plugins/plugins/ndf/.claude-plugin/plugin.json)
- [.claude-plugin/marketplace.json](/work/ai-plugins/.claude-plugin/marketplace.json)
- [plugins/ndf/skills/playwright-scenario-test/SKILL.md](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/SKILL.md)
- [plugins/ndf/skills/playwright-scenario-test/docs/README.md](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/docs/README.md)
- [plugins/ndf/skills/playwright-scenario-test/docs/01-methodology.md](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/docs/01-methodology.md)
- [plugins/ndf/skills/playwright-scenario-test/docs/02-page-roles.md](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/docs/02-page-roles.md)
- [plugins/ndf/skills/playwright-scenario-test/docs/06-pytest-playwright.md](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/docs/06-pytest-playwright.md)
- [plugins/ndf/skills/playwright-scenario-test/pyproject.toml](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/pyproject.toml)
- [plugins/ndf/skills/playwright-scenario-test/templates/conftest.py.template](/work/ai-plugins/plugins/ndf/skills/playwright-scenario-test/templates/conftest.py.template)

## 外部比較で参照した公式情報
- Playwright Python: https://playwright.dev/python/
- Playwright pytest plugin reference: https://playwright.dev/python/docs/test-runners
- Cypress docs: https://docs.cypress.io/app/core-concepts/testing-types
- Cypress product site: https://www.cypress.io/
- Selenium: https://www.selenium.dev/
- Puppeteer docs: https://pptr.dev/
- Browserbase docs: https://docs.browserbase.com/introduction
- Browserbase agent browser: https://docs.browserbase.com/integrations/agent-browser/introduction
- TestRail: https://www.testrail.com/
- Anthropic Claude Code overview: https://docs.anthropic.com/en/docs/claude-code/overview
- Anthropic Claude Code plugins announcement: https://www.anthropic.com/news/claude-code-plugins
- Anthropic Claude Code product page: https://www.anthropic.com/product/claude-code
- Cursor Background Agents: https://docs.cursor.com/background-agents
- GitHub Copilot: https://github.com/features/copilot
- Devin docs: https://docs.devin.ai/get-started/first-run
- Devin Knowledge: https://docs.devin.ai/product-guides/knowledge
