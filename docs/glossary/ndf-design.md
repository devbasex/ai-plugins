# 用語集: NDF の設計（`ndf-design`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

設計 Pull Request・設計文書・設計の結果・決定の記録・外部の調査・代表のリポジトリの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| モデルレビュー | — | 設計 PR のレビューの 1 ラウンド目。ドメインモデルのセクションだけを見る | モデルの段 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 詳細レビュー | — | 設計 PR のレビューの 2 ラウンド目以降。確定したモデルを前提に残りを見る | 詳細の段 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 仕様のコピー | — | 課題の本文にある要求を、設計 PR と一緒にコミットする `issues/` のファイル | 仕様の写し | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 人へ残す判断 | — | 設計文書のうち、レッドラインに当たるため設計者（AI）が決めずに承認ゲートで人が決める判断。判断ごとに当たるレッドラインの番号となぜ当たるかを書く。番号を挙げられない判断は人へ残さず、設計者が決める | — | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| 系統（決定番号の） | — | 決定番号が一意に決まる設計文書の集まり。#1142 では `decisions`・`dbos`・`smells`・`data`・`scripted-flow`・`fixes`・`next` の 7 つ。重なる番号は `決定 73（fixes）` のように系統を添えて書く | — | — | — |
| 設計 Pull Request | — | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request。変更ファイルに issues/ の要求・設計・決定の記録を含む | — | — | `docs/specifications/ndf-design-phase.md` |
| 設計文書 | — | design が作る成果物。issues/ 配下に置く Markdown | — | — | `docs/specifications/ndf-design-phase.md` |
| 移行ステップ | `migration_step` | 設計の決める移行の順序の 1 つ分。1 本の PR で閉じる。プランの「ステップ」とは別 | — | — | — |
| 統合した課題 | — | 受け入れ内容を別の課題の実装へ含めると決めた課題。設計の結果の表か `new sprint --absorb` で決める。実装のプランは `実行の条件` で飛ばして実行しない。`--absorb` で決めたときは自分の設計のプランを持たない | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| 設計の結果 | — | 設計文書の「設計の結果」のセクションの表。課題ごとに扱い（実装する / マージする / 閉じる）・統合先・触るファイルを書き、実装のステージの前に機械が読む | — | — | — |
| 決定の見出し | — | 設計文書の囲みの外にある、決定 1 件を表す見出しの行。`## 決定の記録` で始まる H2 のセクションの `### ` の行か、H1 に「決定の記録」を含み `## 決定の記録` のセクションを持たない文書の `## 決定 <数字>…` / `### 決定 <数字>…` の行。pr-body-decisions.sh が設計 PR の本文の「決めたこと」へ写す | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 決定を分けたファイル | — | 設計文書から決定の記録のセクションだけを別に置いたファイル。名前は `issue-<番号>-design-decisions.md` | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 決定を読めない文書 | — | 設計 PR で変更した .md のうち、`## 決定の記録` で始まる H2 を持つか決定を分けたファイルであるのに、決定の見出しが 0 件のもの。文書自身の名前が `-design-decisions.md` で終わらないときに限り、決定を分けたファイルの名前だけを示すセクションは除く。pr-body-decisions.sh の check / sync が 1 で止まる | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 適用範囲 | — | 設計した変更が働く範囲。このリポジトリだけで働くのか利用先のリポジトリでも働くのか、プロジェクトごとに違うものを設定か引数のどちらで受けるのか。設計文書のセクションと承認資料の行に書く | — | — | `docs/specifications/ndf-design-phase.md` |
| 目指す姿 | — | 設計に沿った実装を終えた後に成り立つ姿（設計 PR のマージだけで成り立つ姿ではない）。設計文書の「目指す姿の根拠」の直前のセクションに 3〜5 行で書き、要求のコピーへのリンクを添える。承認資料にもそのまま載せる（#3680） | — | — | `docs/specifications/ndf-design-phase.md` |
| 目指す姿の根拠 | — | 変更後の形が適切だと言える根拠。外部の一次情報・実測・利用者の指示の原文のどれか。設計文書のセクションと承認資料の行に書き、無ければ「無し（理由）」。MVV の根拠の項目とは別のもの | — | — | `docs/specifications/ndf-design-phase.md` |
| 設計に名前の出るテスト | — | 設計文書の「決定の記録」「テスト設計」「構成要素」に、バッククォートで囲んで書かれたテストファイルのパス・<パス>::<名前>・「テスト設計」の test で始まる識別子 | — | — | `docs/specifications/quality-gates-design-match.md` |
| 設計の決定 | — | 設計文書の「決定の記録」の 1 件（### 決定 N）。judge のステップが返す「決定」とは別の語 | — | — | `docs/specifications/quality-gates-design-match.md` |
| 外部の調査 | — | 設計の判断の前に、業界の事実上の標準・仕様、主要な製品の解き方、使える既存のライブラリとマネージドサービスを一次情報で調べ、設計文書に残す手順（#2519）。棚卸しのシーズの調査とは別のもの | — | — | `plugins/ndf/skills/design/references/external-survey.md` |
| 自前で作る決定 | — | 既存のライブラリ・サービス・標準の実装を使わず、プロジェクトの中で作ると決めた決定の記録。決定の作り方の行に「自前で作る」を含むもので見分ける（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| 確かめられなかったこと | — | 外部の調査で、公開の資料が無い・取得できなかった・上限で止めた対象と、試した手段（URL か検索の語）と理由を並べた設計文書の欄（#2519） | — | — | `plugins/ndf/skills/design/references/external-survey.md` |
| 作り方 | — | 設計文書の決定の記録の中の 1 行。その決定が機能をどう得るか（自前で作る / ライブラリやサービスを使う / 標準に倣う、またはその組み合わせ）を書く（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| 比べた候補 | — | 設計文書の決定の記録の中の 1 行。外部の調査の表で比べた行の番号（STD<数> / PRD<数> / LIB<数>）を並べる（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| 承認済みのコミット | — | 設計の承認を問うた時点の設計 Pull Request の先頭のコミット。承認した設計のマージはこの HEAD のときだけ行う | 承認した head、承認した先端 | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| マージの結果 | — | 承認した設計 Pull Request をマージ先へマージする 1 回の試みの結果。マージした・マージ済み・HEAD が動いた・マーカーが無い・検査が緑でない・衝突・マージ先が本番・失敗のどれか | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 扱い | — | 設計の結果の列。課題ごとに `実装する` / `マージする` / `閉じる` のどれかを書く | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 統合先 | — | 設計の結果の列。扱いが `マージする` の課題を、どの課題の実装へ含めるかを課題の番号で書く | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 触るファイル | — | 設計の結果の列。扱いが `実装する` の課題が変えるファイルの一覧。実装のプランの触る範囲になる | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 食い違い | — | 同じ課題について、2 本の設計文書が違う設計の結果（扱い・統合先・触るファイルのどれか）を書いていること。設計の結果のステージは食い違いを見つけると止まり、人へ戻す | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 分割した文書 | — | 1 つの課題の設計を複数ファイルに分けたときの、先頭以外の設計文書（`issue-<番号>-design-<名前>.md`） | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| セルの形の誤り | — | 設計の結果の「実装する」の行の触るファイルのセルが、定めた書式から外れていること。設計の結果のステージが止まる | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 詳細設計 | — | 設計の後に置く工程と、その成果物。ファイル配置・クラスと関数の構造・関数ごとの責務と異常系の処理を、実装の前に決める | — | — | `plugins/ndf/skills/design/references/detailed-design.md` |
| 異常系の処理 | — | 関数ごとに起こりうる失敗と、その検出の場所・扱い・利用者に見える出力・後始末。詳細設計が決める | — | — | `plugins/ndf/skills/design/references/detailed-design.md` |
| 汎用性の後退 | — | NDF の変更の後に、代表のリポジトリの種類のどれかを NDF で扱えなくなること。その種類に要る選択肢（運用・リリースの形・テストのツール・CI など）を、代わりの形なしに無くすことで起きる。値の改名・作り直し・既定の変更・代わりの形のある値の削除は当たらない | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| サポートする選択肢 | — | 設定で選べる値（supported options）。リリース方式の値・delivery の種類・リリースの形など。汎用性の後退は、ある種類に要るサポートする選択肢を代わりの形なしに無くすことで起きる | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 代表のリポジトリの種類 | — | NDF が扱えることを保つリポジトリの種類。代表のリポジトリの設定の kinds に名前で書く（ai-plugins では Web サービス・モバイルアプリ・業務文書だけ） | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 代表のリポジトリ | — | 代表のリポジトリの種類ごとに、設計の振る舞いを確かめるために選ぶ、このリポジトリの作るものを使う側のリポジトリ（実物か、設定を仮に置いたもの） | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 代表のリポジトリの設定 | — | .ndf/design.json の representative_repos。代表のリポジトリの種類の一覧・表を求めるパス・扱えなくなる種類があるときに名指すレッドラインの番号を持つ。置いたリポジトリで、触るファイルがそのパスに当たる設計だけに設計の検査が振る舞いの表を求める | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| このリポジトリだけの事情 | — | 設計を書くリポジトリの運用で決めた値。作るものの既定や振る舞いへ入れず、そのリポジトリの .ndf/ の設定に置く。ai-plugins では「ai-plugins だけの事情」と書く | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| ai-plugins だけの事情 | — | ai-plugins の運用で決めた値（ブランチの運用・リリースの形・マーケットプレイスの名前など）。NDF の既定や振る舞いへ入れず、ai-plugins の .ndf/ の設定に置く | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 製品の約束 | — | NDF が利用者の多様なリポジトリへ約束する価値。ai-plugins の MVV の Value 1〜14 と Vision が持つ | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 設計の検査 | — | 設計文書を書いた後に工程が走らせる、言語モデルを呼ばない決まった検査（design-survey.py check。設計のプランのステップ survey-check） | — | — | `docs/specifications/ndf-design-representative-repos.md` |
| 変更の種類 | — | 要求仕様と設計書のテンプレートを選ぶ軸。新規作成・機能追加・不具合の 3 つで、文書の中心に何を置くかを決める。モードとも Issue の種類とも別の軸 | — | — | `docs/specifications/ndf-development-document-roles.md` |
| 今の姿 | — | 変更の前に、その機能が何のためにあり今どう動いているかを、初めて見る読み手向けに短く書いたもの。要求仕様と設計書の最初に置き、SSOT へのリンクを添える。新規作成では今はどうしているかを書く | — | — | `docs/specifications/ndf-development-document-roles.md` |
| 文書の役割の表 | — | 要求仕様・設計書・詳細設計書・実装計画・開発計画ごとの読み手・読み手が下す判断・読み方・セクションの順序と、変更の種類ごとに中心に置くセクションを並べた表。各テンプレートはこの表の順序でセクションを並べる | — | — | `docs/specifications/ndf-development-document-roles.md` |
| リファクタリングの時機 | — | リファクタリングを置く 3 つの時機。実装の前（準備のリファクタリング）・実装の中（TDD のリファクタリングのステップ）・スプリントの終わり（単位をまたぐ cross-refactoring）。実装計画の欄の名前 | — | — | `docs/specifications/ndf-development-document-roles.md` |
| 文書を省く条件 | — | standard の変更で、文書 × 変更の種類ごとに文書を省いてよい条件。課題の数・公開インタフェースに触れるか・触るファイルの数・新設する関数とシグネチャを変える関数の数で決まる | — | — | `docs/specifications/ndf-development-document-roles.md` |
