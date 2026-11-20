# 開発の文書の役割と書き方

NDF が実装の前に書く 5 つの文書（要求仕様・設計書・詳細設計書・実装計画・開発計画）について、
文書ごとの読み手と判断からセクションの順を決め、当たらないセクションを消し、前の文書にある事実を
リンクで渡す。あわせて、文書を省いてよい条件を数えれば決まる値で置き、読み手役に課題と解き方を
言い直させて読めたかを確かめる。この文書は、その形をそう決めた理由と、手順書をまたぐ契約を持つ。

**書き方と手順は Skill が正である。** 表・テンプレート・手順をここに書き写さない。

| 何を | どこが正か |
| --- | --- |
| 文書ごとの読み手・判断・読み方・セクションの順、変更の種類ごとの中心、要求仕様の種類ごとの見出しの順、読み手役の言い直しの問いと照合先、省いてよい文書と条件、詳細設計を別ファイルにする条件、どの文書にも当たる書き方 | [requirements-design の references/document-roles.md](../../plugins/ndf/skills/requirements-design/references/document-roles.md)（文書の役割の表） |
| 要求仕様のテンプレートと、ラベルの必須の項目を足す規則 | [requirements-design の references/spec-template.md](../../plugins/ndf/skills/requirements-design/references/spec-template.md) と [SKILL.md](../../plugins/ndf/skills/requirements-design/SKILL.md) |
| 設計書のテンプレート・承認する人が読む欄・`legacy-refactor` の書き方 | [design の references/design-template.md](../../plugins/ndf/skills/design/references/design-template.md) |
| 成果物の要否・決定の形・詳細設計 | [design の references/deliverables.md](../../plugins/ndf/skills/design/references/deliverables.md)・[decisions.md](../../plugins/ndf/skills/design/references/decisions.md)・[detailed-design.md](../../plugins/ndf/skills/design/references/detailed-design.md)・[detail-template.md](../../plugins/ndf/skills/design/references/detail-template.md)、課題をまとめる条件は [design の SKILL.md](../../plugins/ndf/skills/design/SKILL.md) |
| 実装計画のフォーマット（レビューの単位・リファクタリングの時機を含む） | [implementation-plan の SKILL.md](../../plugins/ndf/skills/implementation-plan/SKILL.md) と [references/pre-refactoring.md](../../plugins/ndf/skills/implementation-plan/references/pre-refactoring.md) |
| 実行計画の `目標:` の行と、2 つの計画の読み手 | [issue-plan-strategy の references/execution-plan.md](../../plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md)・[parallel-plan.md](../../plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md) |
| 工程「詳細設計」を別ファイルにするか畳むか | [development-workflow の SKILL.md](../../plugins/ndf/skills/development-workflow/SKILL.md) と [references/stage-notes.md](../../plugins/ndf/skills/development-workflow/references/stage-notes.md) |
| 承認資料の項目の出所と、設計文書が 2 本以上のときの組み方 | [development-workflow の references/approval-request.md](../../plugins/ndf/skills/development-workflow/references/approval-request.md)。設計 PR の本文は [pr の SKILL.md](../../plugins/ndf/skills/pr/SKILL.md) |
| 実装と設計の突き合わせ（畳んだ詳細設計・省いた文書の照合） | [quality-gates の references/design-match.md](../../plugins/ndf/skills/quality-gates/references/design-match.md) |
| 読み手役の言い直しの合否と回数 | [document-rebuild の SKILL.md](../../plugins/ndf/skills/document-rebuild/SKILL.md) |
| Issue の記載項目の名前と区分 | [ndf-issue-items.md](ndf-issue-items.md) |

## 概要

文書の形は「誰が・何を判断するために・どう読むか」から決まる。要求仕様は承認する人が頭から 1 回通して
読み、「この問題をこの終わりの条件で解いてよいか」を決める。設計書は承認する人とレビュー担当が決定で
立ち止まりながら読み、「この解き方でよいか」を決める。詳細設計書と実装計画は実装する人が要る箇所を引く。
開発計画はオーケストレーター（conductor）が工程ごとに引く。テンプレートのセクションはこの判断の順に並ぶ。

読み手はその機能を初めて見る人とする。機能追加や不具合の直しが、これまでその機能を担当していた人に
割り当てられるとは限らないためである。そのため要求仕様は「何が起きたか」→「種類」→ 今の姿（新規作成では
「今はどうしているか」）で始まり、設計書は見出しの無い要約の段落の次に今の姿を置く。

- 当たるモードは `standard` である。`legacy-refactor` は設計書の書き方と承認資料の出所を変えない
  （設計書の今の欄の書き方は design-template.md の「`legacy-refactor` の書き方」へ移した）。`light`・`operation`・
  `documentation` は変わらない
- 承認ゲートの数と、承認資料が載せる項目の種類は変えない。項目の出所のセクションだけを付け替えた
- コードは変えない。コードが読む見出しは名前を残す（「常に成り立つ条件」）

## 用語

用語の定義は、用語集の設定（`.ndf/glossary.json` の `document`）が指す [docs/glossary.md](../glossary.md) にある。
この文書で使う語は「変更の種類」「今の姿」「文書の役割の表」「リファクタリングの時機」「文書を省く条件」である。

実装計画の欄の名前「レビューの単位」は、用語集の `ndf-branch-strategy` の同じ字の語の意味を、工程表の見直し
（[#3879](https://github.com/devbasex/ai-plugins/issues/3879)）が欄の意味（1 本の PR にまとめ、1 回のコードレビューを
掛ける変更のまとまり）へ置き換えた。欄の行がプランの単位になる仕組みは [ndf-review-units-and-refactoring-timing.md](ndf-review-units-and-refactoring-timing.md) にある。

## 背景

設計 Pull Request [#3866](https://github.com/devbasex/ai-plugins/pull/3866) の要求仕様・設計文書・詳細設計
（合わせて 1,290 行）を読んだ承認する人が、何が課題で何をしようとしているのかを読み取れなかった
（2026-11-19、[#3868](https://github.com/devbasex/ai-plugins/issues/3868)）。原因はテンプレートの側にあった。

- セクションが書く順に並び、文書ごとの読み手と判断が決まっていなかった。要求仕様の冒頭が「起票者の推定」の表で、
  設計書は 30 を超えるセクションを持った
- `standard` の設計に 15 の成果物を「必須」とし、対象が無ければ「無い」と書かせ、詳細設計は当たらない
  セクションも見出しを残させた。「無し」が並び、小さな直しでも 1,000 行を超えた
- 3 つの文書が同じ事実を粒度を変えて言い直し、関係の薄い課題を 1 本の設計にまとめた
- 読み手役の確認は文体を見るだけで、初めて読む人が課題と解き方を言えるかを見なかった

### 一次資料と採った形

文書ごとに備える内容は一次資料と、このリポジトリの過去の文書（種類ごとに最近の 8 件、合わせて 50 件）の
数で確かめた。結論だけを残す。

| 出典 | 採った形 | 採らなかった形と理由 |
| --- | --- | --- |
| [Design Docs at Google](https://www.industrialempathy.com/posts/design-docs-at-google/) | 文脈を設計の前に置き、採らなかった案を残す。要求を繰り返さない。小さい変更の文書は小さくする | 設計書に目標と非目標を置く（要求仕様の「目的」「対象範囲」が持つ） |
| [ADR](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)・[MADR](https://adr.github.io/madr/) | 決定ごとに問い・選んだ案・採らなかった案・代償。任意のセクションは消す。1 件 1 問題 | 決定を 1 件 1 ファイルにする（承認資料が決定の見出しを 1 つの文書から並べる） |
| [arc42](https://faq.arc42.org/questions/B-1/)・[C4 model](https://c4model.com/) | 全部を埋めない。図は変更の位置が分かる水準だけ | 12 セクションの並び（システム全体の文書で、変更ごとの文書ではない） |
| [Diátaxis](https://diataxis.fr/) | 引く文書（詳細設計）と通して読む文書（要求・設計）を分け、言い直さずリンクする | — |
| [Amazon の PR/FAQ](https://www.aboutamazon.com/news/workplace/an-insider-look-at-amazons-culture-and-processes) | 要求仕様を場面（何が起きて誰が困ったか）から始める | 結果の姿から書き始める（今の姿を知らない読み手には前後の差が読めない） |
| [Mozilla の Bug Writing Guidelines](https://bugzilla.mozilla.org/page.cgi?id=bug-writing.html) | 不具合を事象・期待する結果・再現手順に分ける。1 報告 1 件 | 再現手順を事象の前に置く順（初めて見る読み手には何が起き本来どう動くかが先に要る） |
| [Rust の RFC](https://github.com/rust-lang/rfcs/blob/master/0000-template.md)・[PEP 1](https://peps.python.org/pep-0001/)・[KEP](https://github.com/kubernetes/enhancements/blob/master/keps/NNNN-kep-template/README.md) | 提案本文の早い位置に要約を置く。小さな変更・不具合には設計の文書が要らない | 要約に見出しを付ける（下の決定の表） |
| [IEEE 1016](https://seng.cankaya.edu.tr/wp-content/uploads/sites/53/2024/09/IEEE-SDD-1016-2009.pdf)・[CMS の SDD](https://www.cms.gov/files/document/sddpdf) | 詳細設計の観点は当たるときだけ使い、構成要素ごとに満たす要求を持つ。ソースのコメントにある情報は参照で足りる | 12 の観点を節にする（変更ごとの文書には多すぎる） |
| [Google の Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html) | 変更を依存で分けた自己完結の単位にし、1,000 行を超える単位を作らない | 目安を 100 行にする（cross-review の呼び出しが増える） |
| [Scrum Guide](https://scrumguides.org/scrum-guide.html)・[Shape Up](https://basecamp.com/shapeup/1.5-chapter-06) | 実行計画に目標を 1 つ置く | かける時間の上限（NDF の工程は時間でなく工程で区切る） |
| [ISO/IEC/IEEE 29148](https://standards.ieee.org/ieee/29148/6937/)・[IPA の要件定義ガイド](https://www.ipa.go.jp/archive/publish/tn20191220.html)・ユーザーストーリー | 受け入れ条件を観測でき・テストできる形に保つ（今の形と同じ） | SRS のセクションの並び（製品全体を書く文書で、1 つの変更には大きすぎる） |

実例の数では、課題 1 つの設計書 8 件の節は中央値 35・最大 45 で、要約を持つものは 0 だった。詳細設計 8 件は
「事前条件」の列の 49%、「副作用」の列の 30% が「無し」か「—」だった。

## 仕様

### 決めたことと理由

| 決めたこと | 理由 |
| --- | --- |
| 5 つの文書の読み手・判断・順序の表（文書の役割の表）を `requirements-design` の参照に 1 つ置き、`design`・`implementation-plan`・`issue-plan-strategy`・`document-rebuild` は表を指して写さない | 各 Skill に自分の行だけを書くと、言い直しをやめる決まりそのものが 4 か所に割れる。変更の種類を決めるのは最初の文書（要求仕様）なのでその Skill に置く。`document-style` は工程に依らない書き方で、`development-workflow` の文書タイプの表は `documentation` の軸で、どちらに置いても軸が混ざる |
| 変更の種類を新規作成・機能追加・不具合の 3 つにし、課題の本文の `## 種類` の 1 行目に書く。Issue の種類（ラベル `bug`・`enhancement`）は変えない | ラベルを作り直すと記載項目の定義と起票の検査まで広がる。調査は設計を書かず、運用と `legacy-refactor` は書き方を別に持つ。不具合と機能追加の境目は、満たそうとする約束が今の SSOT（コマンドの契約か手順書）にあるかで決める |
| どのテンプレートもセクションを「必ず書く」と「当たるときだけ」に分け、当たらないセクションは見出しごと消す。設計書で必ず書くのは 今の姿・文脈と制約・解き方の全体像・決定の記録・影響と危険・テスト設計・設計の結果・外部の調査 の 8 つ | 「必須」で対象が無いときに理由を書かせると「無し」が並び、読み手が書くことの無いセクションを読む。例外は外部の調査の `省く: <理由>` と、実装計画のリファクタリングの時機の「しない」の行で、判断したことを残すための 1 行である |
| 設計書を 要約の段落 → 今の姿 → 文脈と制約 → 解き方の全体像 → 決定 → 影響と危険 の順にし、決定を 問い・選んだ案・採らなかった案・代償・根拠 の形にする | 名前を残して順だけ変えると「目的」「目指す姿」が要求の目標を繰り返す。機能をどう得るかを選んだ決定の `作り方:` と `比べた候補:` の 2 行は、代償の行と根拠の行の間に置き、自前で作るときは候補を必ず並べる（今の決まりのまま） |
| 設計書の頭に、見出しを付けない 4 文以内の要約の段落を置く | 今の姿から始めると、承認する人は図と構造を読み終えるまで何を解くのかが分からない。見出しを付けると承認資料の出所とコードが読む見出しが 1 つ増え、「今の姿から始まる」順に割り込む。見出しを付けない形は出典でなく NDF の判断である |
| 文書ごとに持つ事実を決め、後の文書は前の文書のセクションをリンクで指す。実装計画は要求と設計にあるセクション（目的と非目的・前提・受け入れ条件・代替案など）を持たず、受け入れ条件を番号で指す | 要点を写すと片方だけが直され食い違う。1 文書だけでは全体を読めない代償は、最初の段落の前の文書へのリンクで補う |
| 詳細設計を別ファイルにするのは、新設する関数が 2 つ以上か、既存の関数のシグネチャを変えるときだけ。それ以外は設計書の「どこを変えるか」に畳む | 判定を 2 つの数だけで決め、書き手の判断を挟まない。要求と設計を 1 ファイルにする形は採らない（要求の正は課題の本文で、承認の材料のコードが設計書を `issues/issue-<番号>-design*.md` から読む）。行数の上限も置かない（大きさの指標は触る範囲である） |
| 1 本の設計文書は 1 つの問題を扱い、複数の課題をまとめてよいのは根本原因が共通のときだけ。設計 PR を 1 本にまとめるのは許し、課題ごとの設計文書を並べる | #3866 は関係の薄い 3 課題を 1 本にまとめ、題が抽象になった。設計 PR も 1 課題 1 本にすると承認の回数が増える |
| 読み手役に、要求仕様なら「何が起きて誰が困ったか・何を満たせば終わりか」、設計書なら「どう解くか・何を採らなかったか、なぜか」を言い直させ、問いごとに照合先と照らす。不合格なら書き手が直して 1 回だけ読み直させ（最大 2 回）、2 回目も不合格なら報告に残す | 言えるまで繰り返すと費用の上限が無くなる。設計書には組になる要求仕様のコピーを渡し、それ以外のリンク先は読ませない（SSOT を読めば言える文書が合格し、今の姿の書き足りなさを拾えない） |
| 要求仕様の見出しは今の記載項目の名前（事象・期待する結果・再現手順・環境・目的・対象範囲など）を使い、新しいセクションだけを「該当時」の項目として [ndf-issue-items.md](ndf-issue-items.md) に足す。原文のセクションは定義どおり「依頼（原文）」と書く | 名前を言い換えると、定義・フォーム・骨格・テンプレートと移し替えの後の検査を同時に直すことになる。足すセクションを必須にすると、人がフォームで起票するときにも求めることになる |
| 課題のラベルが必須とする項目は、変更の種類によらず書く。種類の順に無い項目は、種類ごとの中心の後ろ・「なぜ困るか」の前に、記載項目の定義の順で置く | ラベル `bug` の課題を機能追加として書くと、事象・期待する結果・再現手順・環境が本文から落ち、本文の検査で欠けになる |
| 実行計画の `状態` `開始` の行に続けて `目標:` の 1 行を置き、行を足す・外す見直しの理由を目標に照らして書く。並列の計画（JSON）は変えない | 目標が無いと、見直しの基準が行の依存だけになる。並列の計画に欄を足すとスキーマとコードの変更になり、レーンの目標はレーンが流すスプリントの実行計画が持つ |
| 実装計画に「レビューの単位」の欄を置く。1 課題を 1 単位とし、見込みの差分が 1,000 行を超える課題は依存の向きで分ける。目安は 1 単位 400 行以下で、1,000 行を超える単位は作らない | スプリントの PR で 1 回だけ通すと、レビュー担当が読み切れない大きさになる（#3870 は 4,127 行）。行数だけで機械的に切ると、単位が単独でテストを通らない |
| 実装計画に「リファクタリングの時機」の欄を置き、実装の前（準備）・実装の中（TDD のステップ）・スプリントの終わり（単位をまたぐ `cross-refactoring`）の 3 つに分ける | 実装の後に 1 回だけだと、単位ごとのレビューにしたとき単位をまたぐ重複を見る場所が無くなる。単位ごとに `cross-refactoring` を通すと局所の最適になり、呼び出しも増える |
| 文書を省いてよい条件を、課題の数・公開インタフェースに触れるか・触るファイルの数・新設する関数とシグネチャを変える関数の数の 4 つの値だけで書く。要求仕様は省かず、新規作成は設計書と実装計画を省かず、設計書と実装計画は両方を省かない | 言葉の条件（軽微な修正など）は書き手ごとに判断が割れる。4 つの値は今ある文書のセクションから数えられ、実装の後は差分から数え直せる。両方を省くと、不具合の根本原因と直す場所を書く文書が無くなる |

### 常に成り立つ条件

- **コードが読む見出しの名前を保つ。** 課題の本文の「種類」と「影響」の表の「公開インタフェース」の行は承認の
  材料のコード（`plugins/ndf/src/ndf/domains/materials/collect.py`）が読み、設計書の「設計の結果」（4 列）・
  「決定の記録」・「テスト設計」・「構成要素」と、決定の中の「採らなかった案」で始まる行は、設計に出るテストの検査
  （`ndf loop design-tests`）が読む。テンプレートの見出しを変えても既存のテストはコードの中の文字列で見出しを
  作るため CI は落ちない。残っていることは実装の完了判定でテンプレートを `grep` して確かめる
- **承認資料の項目の種類を保つ。** 出所のセクションを付け替えても、項目（目的・用語・適用範囲・目指す姿・
  目指す姿の根拠・起票者の推定・決めたこと・作るもの・満たすこと・未確認のまま残ること）は変えない。出所が
  当たるときだけのセクションで無いときは、`approval-request.md` の出し方（「無し」など）で出し、止まらない
- **「承認する人が読む欄」のセクションの名前を保つ。** supervisor の規則（設計の工程をこの欄に従わせる）と
  `pr` の手順がこの名前のアンカーを指すため、名前を残して中身を新しい欄に書き換えた
- **省いたことは 1 か所に残す。** `省いた文書: <文書>（<満たした条件の値>）` の行を、設計書があれば設計書の頭、
  設計書を省いたときは実装計画（`issues/issue-<番号>-plan.md`）の頭に書く。設計書と実装計画は両方を省かない
  ので、置き先は必ず 1 つある
- **数え直して条件を外れたら完了にしない。** 実装の完了判定が差分のファイル数と新設した関数の数を省いた条件と
  照らし、外れていれば完了にしない。設計書を省いた変更はその課題を設計の工程と設計承認ゲートへ戻し、実装の
  差分は捨てない

### 設計文書を課題番号で引く読み方

まとめた設計文書（`issue-<番号1>-<番号2>-design.md`）は、名前に含む番号のどれで引いても見つかるものとする。
`-` で区切った番号のどれかが指定と一致するものを読み、番号の一部（`78` に対する `7-8`）とは合わせない。
`ndf loop design-tests` は既にこの読み方を持つ（`plugins/ndf/src/ndf/domains/loop/design_tests.py` の
`DESIGN_FILE`）。手順書（`design-match.md`・詳細設計の名前）は同じ言い方で書く。

承認の材料のコード（`collect.py` の `DESIGN_FILE`）は `issue-(\d+)-design` で、番号が 2 つ以上の名前には
一致せず、まとめた設計文書の「設計の結果」の触るファイルを材料に出さない。直すまでは conductor が判定の根拠の
ファイルへ触るファイルを設計文書から書き足す。

## 運用

- **工程表との分担。** この仕様は文書の側（文書の役割の表と実装計画の欄）だけを持つ。工程の並び・動く単位・
  プランのテンプレート・文書を省いたときの工程と承認ゲートの扱い・単位ごとに PR を分けるかは、工程表の見直し
  （[#3879](https://github.com/devbasex/ai-plugins/issues/3879)）が決め、[ndf-review-units-and-refactoring-timing.md](ndf-review-units-and-refactoring-timing.md) が持つ
- **しきい値の見直し。** 文書を省く条件のしきい値（触るファイル 4・新設する関数 0）は、直す 2 ファイルとその
  テストが入る数として置いた値である。省いた変更が cross-review や承認ゲートで差し戻されたかを振り返りで見て
  直す
- **記載項目の検査が戻るとき。** 足した「該当時」の項目は、記載項目の定義の文書（[ndf-issue-items.md](ndf-issue-items.md)）
  にだけある。検査が読む `issue-items.json` と一致の検査を戻す移し替えの作業が、項目を登録し、実体の JSON で
  テンプレートを検査して未知の見出しが 0 件で通ることを確かめる（`scripts/ndf-next-parity/parity/features.json`
  の 2 つの行に書いてある）

## テスト観点

`.md` の文言を照らすテストは置かない。確かめは CI の検査と、実装の完了判定・cross-review・承認する人が持つ。

- 文書の役割の表のセクションの順と、各テンプレートの見出しの順が一致すること
- どのテンプレートも、セクションが「必ず書く」と「当たるときだけ」に分かれ、当たらないセクションを消す決まりを
  持つこと
- コードが読む 7 つの見出し（「種類」「影響」「公開インタフェース」「設計の結果」「決定の記録」「テスト設計」
  「構成要素」）と「採らなかった案」の行の書き出しが、新しいテンプレートに残ること
- 仕様のテンプレートの見出しが、すべて [ndf-issue-items.md](ndf-issue-items.md) の項目の一覧にあること
- 文書を省く条件が 4 つの値だけで書かれ、`implementation-plan` の「必要なケース」「不要なケース」がその表を
  指す 1 行であること
- 実装計画のフォーマットが「レビューの単位」と「リファクタリングの時機」の欄を持ち、文書の役割の表の実装計画の
  行と順が一致すること
- 次の設計 PR の `document-rebuild` の報告に、問いごとの言い直しと合否と、不合格の理由（回答なし・欠落・
  食い違い）が載ること（リリース後テストで確かめる）
- CI の必須のチェック（`check-skill-frontmatter`・`check-markdown-links`・`check-doc-line-limit` を含む）が通ること

## 関連リンク

- [#3868](https://github.com/devbasex/ai-plugins/issues/3868) — 開発の文書の書き方を、読み手と判断から決めたセクションの順へ直す
- [#3879](https://github.com/devbasex/ai-plugins/issues/3879) — 工程表の見直し（レビューの単位・リファクタリングの時機・文書の要否を工程にそろえる）
- 関係する確定仕様: [ndf-design-phase.md](ndf-design-phase.md)（承認する人が読む欄）・[ndf-detailed-design-stage.md](ndf-detailed-design-stage.md)（詳細設計の工程）・[ndf-document-rebuild.md](ndf-document-rebuild.md)（読み手役）・[ndf-issue-items.md](ndf-issue-items.md)（記載項目）
