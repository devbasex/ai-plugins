---
marp: true
theme: default
paginate: true
size: 16:9
header: 'ai-plugins / NDF v4.20.1'
style: |
  section {
    font-family: "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
    font-size: 26px;
    padding: 50px 60px;
    color: #12263a;
  }
  section.lead {
    background: linear-gradient(135deg, #12263a 0%, #1f3a5f 100%);
    color: #ffffff;
  }
  section.lead h1 { color: #ffffff; border: none; font-size: 60px; }
  section.lead h2 { color: #9fc4e8; font-size: 30px; font-weight: normal; }
  section.lead p  { color: #c7d6e4; }
  h1 { color: #1f3a5f; font-size: 40px; border-bottom: 3px solid #4a90d9; padding-bottom: 8px; margin-top: 0; }
  section img { display: block; margin: 0 auto; }
  h2 { color: #1f3a5f; font-size: 30px; }
  h3 { color: #35506b; font-size: 26px; }
  code { background: #eef3f8; color: #12263a; }
  pre  { background: #f7f9fb; border-left: 4px solid #4a90d9; font-size: 22px; }
  table { font-size: 22px; }
  th { background: #eaf2fb; }
  blockquote {
    border-left: 5px solid #f5a623;
    background: #fffaf0;
    padding: 10px 20px;
    font-size: 24px;
  }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
  .small { font-size: 21px; color: #5a6b7a; }
  header { color: #8899aa; font-size: 16px; }
  footer { color: #8899aa; font-size: 16px; }
---

<!-- _class: lead -->
<!-- _paginate: false -->
<!-- _header: '' -->

# ai-plugins

## NDF、実際のところ どう使うのか

社内勉強会 / 2026-08-06
Claude Code・Codex CLI・Kiro CLI 対応

<!--
【0:00-0:45】
NDFが何なのかは前回話したので、今日は「で、結局どのコマンドをいつ叩くのか」だけをやります。
今日持ち帰ってほしいのは1つだけ。PRを1本出すまでの流れが、スキルを繋ぐだけで終わる、という感覚です。
最後にCodexとKiroでのインストールも触りますが、9割はClaude Codeの話をします。
-->

---

# まず全体像を1枚で

![w:820](images/overview.png)

<div class="small">

編集するのは `plugins/ndf-shared/` の **1か所だけ**。そこから3ランタイム分の配布物が生成されます。
ほかに MCP プラグインが **10個**（Serena / BigQuery / Playwright / Chrome DevTools / Redash など）。

</div>

<!--
【0:45-2:00】
構成はシンプルです。スキルの本体は ndf-shared の1か所。ここを直すと、Claude用・Codex用・Kiro用の配布物がビルドで生成されます。
なので「Claudeでは直ってるけどCodexでは古い」が起きない。ここが地味に一番効いてます。
数が違うのは、ランタイムごとに意味のないスキルを外しているからです。Claudeは29、Codexは30、Kiroは28。
あとMCPプラグインが10個。今日は時間の都合で名前だけにします。
-->

---

# インストールは2コマンド（Claude Code）

```bash
# 1. マーケットプレイスを登録（初回だけ）
/plugin marketplace add https://github.com/devbasex/ai-plugins

# 2. NDFを入れる
/plugin install ndf@ai-plugins
```

入れると使えるようになるもの:

| | |
|---|---|
| **スキル 29個** | `/ndf:pr`, `/ndf:review` … スラッシュで直接呼べる |
| **エージェント 8個** | director / corder / qa / debugger / devops-engineer など |
| **フック 2種** | SessionStart（transcript保持を90日に維持）/ Stop（AI要約+Slack通知） |

<!--
【2:00-3:15】
インストールはこれだけです。marketplace add は初回だけ、あとは install。
入るものは3種類。スラッシュで呼ぶスキル、裏で働くサブエージェント、それとフック。
フックはSessionStartとStopの2つで、特にStopは作業が終わったらSlackに要約を投げてくれます。長時間タスクを回してる人はこれだけでも入れる価値があります。
では本題、スキルの話に行きます。
-->

---

# 今日の主役: PRを1本出すまでの流れ

![w:900](images/pr-flow.png)

<!--
【3:15-4:30】
これが今日の地図です。左上から時計回りに見てください。
作る前にプランを書き、セルフレビューしてPRを出す。出したらAIレビューを回して、指摘を分類して直して閉じる。最後に、書いたプランを仕様書として残す。
ポイントは、この矢印1本1本がスキル1個に対応していることです。自分で手順を覚える必要がない。
次のスライドから、この図の四角を左から順に開けていきます。
-->

---

# ① 作る前に書く — `/ndf:implementation-plan`

実装を始めるとき、`issues/` にプランファイルがあるか確認して、なければ作る。

**作るケース**
複数ファイルにまたがる変更 / 新機能 / 既存ロジックの大幅変更 / DBマイグレーション

**作らないケース**
typo修正 / 設定値だけの変更 / 1ファイルで完結する軽微な修正

> PR作成時にプランが無ければ、**会話履歴 + `git log` + `git diff` から自動生成**してからPRを作ります。

<div class="small">

ファイル名に日本語は使いません（Git / CI / 検索ツール互換性のため）。`issues/TASK-1234_add-export-api.md` のように。

</div>

<!--
【4:30-6:00】
まず作る前。implementation-plan は、issues/ の下に実装プランを置くスキルです。
「後任か将来の自分が、なぜこの変更をしたのか追えるように」というのが目的で、全部の変更に要るわけじゃない。typo直しにプランは要りません。判断基準はスライドの通りです。
便利なのは下のブロックで、プランを書かずに実装しちゃった場合でも、PRを作るタイミングで会話履歴とgit logとdiffから逆算して生成してくれます。「あとで書く」が実際にあとで書かれる。
ファイル名に日本語を使わないルールだけ、地味ですがハマるので覚えておいてください。
-->

---

# ② 出す — `/ndf:review-branch` → `/ndf:pr`

<div class="cols">
<div>

### PR を出す前に自分で見る

```bash
/ndf:review-branch
/ndf:review-branch security
/ndf:review-branch performance
/ndf:review-branch tests
```

mainとの差分を、品質・セキュリティ・
パフォーマンス観点でセルフレビュー。

</div>
<div>

### そのままPRにする

```bash
/ndf:pr
/ndf:pr --draft
/ndf:pr "エクスポートAPIを追加"
/ndf:pr qa/staging
```

commit → push → PR作成 まで一括。
**既にPRがあれば本文を最新差分で更新**。

</div>
</div>

> `/ndf:pr` はデフォルトブランチへの直接コミットを拒否します。うっかり main で作業してても止まります。

<!--
【6:00-7:45】
出す前と出す瞬間。この2つはセットで使います。
左のreview-branchは、まだGitHubに上げてない段階のセルフレビュー。引数にsecurityとかtestsとか観点を渡せます。「レビュアーに指摘される前に自分で気づく」用ですね。
右のpr。commitしてpushしてPRを作るまで全部やります。第一引数の解釈が賢くて、--draftならドラフト、ブランチ名っぽい文字列ならベース指定、それ以外はコミットメッセージとして扱われます。
既にPRがある状態で叩くと、新しくは作らずPR本文を今の差分に合わせて書き直します。実装が変わったのに説明文が古いまま、が無くなる。
あと下の注意書き。mainで直接コミットしようとすると止まります。これは何度か救われてます。
-->

---

# ③ 出した後 — `/ndf:review`

```bash
/ndf:review              # 直前のPRを Claude 自身がレビュー
/ndf:review 123          # PR番号を指定
/ndf:review 123 codex    # Codex CLI に委譲
/ndf:review 123 gemini   # Gemini CLI に委譲
```

第二引数でレビュアーを差し替えられるのがポイント。

| | 使いどころ |
|---|---|
| 省略（Claude） | ふだんのレビュー。文脈を持っているので速い |
| `codex` / `gemini` | **第二意見がほしいとき**。自分が書いたコードを別のAIに見せる |

<div class="small">

`/ndf:review-branch` はローカル差分（PR前）、`/ndf:review` は GitHub 上の既存PR。対象が違うだけです。

</div>

<!--
【7:45-9:00】
PRを出したあとのレビュー。基本は引数なしで、Claudeが自分でレビューします。
面白いのは第二引数で、codexやgeminiと書くと、そのCLIに丸ごと委譲します。
なぜこれが要るかというと、自分が書いたコードを自分でレビューしても甘くなるからです。実装したのと同じセッションのClaudeは、その実装を正しいと思っている。別のAIに渡すと普通に知らない指摘が出てきます。
review-branchとreviewの違いは、PR前かPR後か、それだけです。
で、この「第二意見」を極端に振り切ったのが次のスライドです。
-->

---

# ④ 両AIが納得するまで回す — `/ndf:cross-review`

![w:880](images/cross-review.png)

```bash
/ndf:cross-review 123
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に"
/ndf:cross-review 123 --max-rounds 4 --only codex
```

<!--
【9:00-10:45】
cross-reviewです。codexとgeminiの両方にPRレビューを投げて、両者がAPPROVEを返すまで、レビューと修正を自動で回し続けます。
片方がAPPROVEでも、もう片方が指摘を出していれば止まらない。修正はサブエージェントに投げるので、メインの会話は汚れません。
デフォルトは最大12ラウンドで、8ラウンド回っても収束しなければPRをローテーションします。
--focus で観点を足せます。あと、これは言っておくべきなんですが、PRの変更ファイルを見て「これはドキュメントだけのPR」「DBマイグレーションを含む」みたいに自動で分類して、種別ごとのレビュー観点を両方のAIに渡しています。
重い処理なので、単発の第二意見が欲しいだけなら前のスライドの /ndf:review でいいです。使い分けてください。
-->

---

# ⑤ 指摘を捌く3段 — 分類 → 修正 → クローズ

| コマンド | やること |
|---|---|
| `/ndf:review-pr-comments` | **READ-ONLY。** 全コメントを読んで対応可否と優先度を判定。修正は一切しない |
| `/ndf:fix` | 分類結果をもとに実際に修正。`--severity-min` / `--defer-nit` で範囲を絞れる |
| `/ndf:resolve-pr-comments` | 対応済みコメントに返信し、スレッドを resolved にする |

```bash
/ndf:review-pr-comments 123          # まず見るだけ
/ndf:fix 123 --defer-nit             # nit は後回しにして直す
/ndf:resolve-pr-comments 123         # 返信して閉じる
```

> 「読む」と「直す」を分けているのは、**全部直すのが正解とは限らない**から。先に分類だけさせると判断できます。

<!--
【10:45-12:00】
指摘が付いたあとの3段です。
最初のreview-pr-commentsは読み取り専用。コメントを全部読んで、これは直すべき、これは仕様なので直さない、と分類だけします。コードは1行も触りません。
次のfixで実際に直す。--defer-nit を付けるとnitレベルは後回しにしてリストだけ出してくれるので、レビューが荒れてるPRで便利です。
最後にresolve-pr-comments。直したコメントに返信してスレッドを閉じます。これ手でやると本当に面倒なやつです。
なぜ読むのと直すのを分けているかというと、AIに全部渡すと全部直そうとするからです。指摘の中には「それは意図的にそうしてる」ってものが必ずある。先に分類させると、そこで止められます。
-->

---

# ⑥ 書いたものを資産に残す

<div class="cols">
<div>

### `/ndf:plan-to-spec`

実装が終わった `issues/` のプランを、
**現在のコードと一致する確定仕様書**に
書き直して `docs/` へ移す。

消すもの:
開発中の履歴 / TODO / PR分割 /
作業チェックリスト / 未採用案

</div>
<div>

### `/ndf:markdown-writing`

読み手は**会話も検討過程も知らない第三者**、
という前提で書かせるルール。

- 説明文にテーブル名・カラム名などの内部識別子を持ち込まない
- 「案A」「以前は〜だった」を残さない
- 否定的な結論にはエビデンス必須

</div>
</div>

<div class="small">

関連: `/ndf:investigation-rules`（「〜が無い」と書くなら実行結果を添えろ）、`/ndf:problem-solving`（つじつま合わせをせず上流で直す）

</div>

<!--
【12:00-13:15】
最後のフェーズ。書いたものを残す話です。
plan-to-specは、実装が終わったプランを仕様書に変換します。単にファイルを移動するんじゃなくて、書き直す。プランには「まずAをやってからBをやる」みたいな作業順とか、途中でやめた案とかが入っていて、それは仕様じゃないので全部落とします。残るのは今のコードと一致する記述だけ。
右のmarkdown-writingは、v4.20で体裁ルールから可読性ルールに拡張されたやつです。一番効くのは1個目で、説明文にテーブル名やカラム名をそのまま書かせない。書いた側は説明した気になるけど、読む側には何も伝わらないんですよね。PR本文にも効きます。
下の2つは名前だけ。investigation-rulesは「無い」と書くなら実行結果を貼れというルール。AIがコードを読んだだけで「該当なし」と断言する事故を防ぐためのものです。
-->

---

# Codex / Kiro でも同じスキルが使えます

<div class="cols">
<div>

### Codex CLI

```bash
codex plugin marketplace add \
  https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

セッション内では `ndf:` 接頭辞で
**30個**のスキルが使えます。

<div class="small">

Claude版との差: エージェント8個とSessionStart/Stopフックは無し。代わりに Playwright 系スキル5個が入ります。

</div>

</div>
<div>

### Kiro CLI

```bash
git clone \
  https://github.com/devbasex/ai-plugins.git
bash plugins/ndf-kiro/install.sh
kiro-cli chat
```

`.kiro/skills/` に**28個**、
`.kiro/agents/default.json` を生成。

<div class="small">

`--with-slack` でStop時のSlack通知、`--with-codex` でCodex CLI連携を追加。何度実行しても安全（冪等）。

</div>

</div>
</div>

<!--
【13:15-14:15】
CodexとKiroです。ここは「同じものが使える」ということだけ持って帰ってください。
Codexはマーケットプレイス方式で、Claudeとほぼ同じ2コマンド。セッションに入るとndfコロン付きでスキルが並びます。実際に叩いて30個読み込まれているのを確認済みです。
Kiroだけ方式が違って、リポジトリをcloneしてinstall.shを叩きます。.kiro/skills/ 以下にスキルが並んで、エージェント定義も一緒に作られます。--with-slack を付けるとSlack通知が付く。何度実行しても壊れないので、更新したら叩き直せばいいです。
-->

---

# まとめ: 今日から試す3ステップ

1. **入れる** — `/plugin marketplace add …` → `/plugin install ndf@ai-plugins`
2. **次のPRで `/ndf:pr` を叩く** — commit・push・PR作成・本文更新まで任せてみる
3. **レビューが付いたら `/ndf:review-pr-comments`** — まず分類だけさせて、判断は自分でやる

<br>

> スキルは覚えるものではなく、**繋がっている流れを1回なぞってみるもの**です。
> プラン → PR → レビュー → 仕様書、を1本だけ通してみてください。

<div class="small">

リポジトリ: https://github.com/devbasex/ai-plugins ／ 詳細は `docs/ndf-plugin-reference.md`

</div>

<!--
【14:15-15:00】
まとめです。3つだけ。
入れる。次のPRでprを叩く。レビューが付いたらまずreview-pr-commentsで分類させる。
全部のスキルを覚える必要はなくて、さっきの地図の流れを1回なぞってもらえれば、どこで何を呼ぶかは体で分かります。
質問あればどうぞ。
-->
