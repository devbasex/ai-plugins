---
name: development-workflow
description: "Classify a change into 5 workflow modes and route it to the required steps. Use when deciding how much process a change needs（モード判定・工程の振り分け）."
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "bash \"${CLAUDE_PLUGIN_ROOT}/skills/development-workflow/scripts/workflow-guard.sh\""
          timeout: 10
---

# 開発ワークフローの振り分け

変更内容をモードへ分類し、必要な工程だけを起動する。**全変更にフル工程を課さない。**
判定の結果として返すもの（モード・必須工程・次のコマンド）の形は「3. 判定結果を出力する」にある。

**判定基準を持つのはこの Skill だけである。** 他の Skill とエージェント定義は、判定結果を受け取る側に徹する。
同じ基準を複数箇所へ書くと、モードを追加・変更したときに片方だけが古くなる。

ステップ・パイプライン・セッションなどの語の意味は [references/glossary.md](references/glossary.md) にある。

## 判定する単位

**工程はスプリント単位で 1 回ずつ通す。** スプリントは、1 つの版として出す課題と Pull Request のセットである。
複数の設計と実装を含む。**モードはスプリントで 1 つである。** スプリントが閉じる課題すべての通過記録へ同じ値を書く。
数える対象が 1 つになる。

| 進め方 | モードを判定する単位 | ブランチ |
| --- | --- | --- |
| `normal`・`auto` | スプリントの develop 宛て Pull Request | スプリントブランチ（`sprint/<名前>`）を develop から切り、課題ごとの worktree はそこから切る。課題の Pull Request はスプリントブランチへ集め、develop への Pull Request はスプリントで 1 本にする |
| `fast` | 実装の Pull Request 1 本 | スプリントブランチを作らず、実装の Pull Request が develop へ直接入る（「進め方」の節） |

- **モードの違う課題を 1 つのスプリントへ混ぜない。** 混ざるなら高い方のモードで進めるか、スプリントを分ける
- **スプリントの大きさは、同時に回せる本数（フェーズは 3 本まで）とリリースの間隔（1 版を 1〜2 日）で切る。**
  並列の形と必須ルールは [references/parallel-work.md](references/parallel-work.md) にある

**工程の順序は、要求と受け入れ条件 → モード判定 → 作業場所の用意 になる。** スプリントに何が入るかが
決まらないと、判定できないためである。`light` にも要求と受け入れ条件が掛かる。その費用は受け入れる。
判定の入力が無いまま判定する状態のほうが高くつく。

要求と受け入れ条件は、設計のプラン（`new sprint --design`）が持つ。課題の本文と仕様のコピーが一致すれば飛ばす。
`--design` を渡さないときだけ supervisor で回す。判定の後のフェーズはプランで流す（`agent-layers.md` の表）。

**モード判定は工程表の行を持たない。** ボードへ記録する工程の値を増やさないためである。

## この文書が受け取る値

| 変数 | 値 | 決め方 |
| --- | --- | --- |
| `$SCRIPTS` | プラグインの `scripts/` の絶対パス | [references/scripts-lookup.md](references/scripts-lookup.md)。シェルが変わったら決め直す |

## 判定の手順

### 0. worktree の設定を確かめる

```bash
# 起動したら手順 1 より先に実行する。$SCRIPTS を決めてから。決められなくても止めない
if [ -n "${SCRIPTS:-}" ]; then bash "$SCRIPTS/worktree-setup.sh" check; rc=$?; echo "exit=$rc"; case $rc in 0|2) python3 "$SCRIPTS/project-decl.py" check; echo "decl=$?"; python3 "$SCRIPTS/project-mvv.py" check; echo "mvv=$?";; esac
else echo "exit=判定できない（scripts を解決できない）"; fi
```

| 終了コード | 次に行うこと | 判定結果の出力の `宣言:` の行 |
| --- | --- | --- |
| 0 | `decl=` を [references/project-analysis.md](references/project-analysis.md)、`mvv=`（判断の基準のプロジェクト MVV。2 でも止めない）を [references/project-mvv.md](references/project-mvv.md) の「手順 0 での扱い」に従って扱い、手順 1 へ進む | `宣言: あり。解析: <結果>` |
| 2 | `worktree` の「0. 設定ファイルを用意する」を通し、`check` が 0 を返してから、`decl=` を同じく扱って手順 1 へ進む | `宣言: 作成した（起点 <名前> / 本番 <名前>。未宣言なら既定ブランチ）` |
| 3 | **先へ進まない。** `init --force` を実行せず、`check` の出力を示して利用者に直してもらう | 出さない（判定まで進まない） |
| 1、または `$SCRIPTS` を決められない | 止めずに手順 1 へ進む | `宣言: 判定できない（<理由>）` |

**拒否しない。** 起動の時点には止める引き金が無い（hook は次のコマンドでしか働かない）。
4 ランタイムで同じに働かせるには、本文に置くしかない。frontmatter の `hooks` は発火していない疑いもある。

### 1. 変更対象を確認する

```bash
git status --short
git diff --stat            # 変更済みなら
```

依頼文だけで判定しない。**実際に触るファイルと、触る理由**で決める。

### 2. 上から順に条件を判定する

最初に該当したモードを採る。複数に当てはまる場合は**上のモードが勝つ**。

| 順 | モード | 該当条件（いずれか 1 つで該当） |
| --- | --- | --- |
| 1 | `operation` | **プロダクションコードも文書も変えず、外部の系の状態だけを変える**（権限や保護の規則の設定、反映先の構成の変更、データの投入・修正、外部サービスへの操作など） |
| 2 | **`documentation`** | **読み手へ渡すビジネス文書を作る・改訂する**（提案資料・稟議書・定例報告・指標の定義・運用マニュアル・説明資料など）。リポジトリの説明文書はこれに当たらない |
| 3 | `standard` | 公開インタフェース（API・イベント・コマンド）の追加・変更・削除 / 既存データの移行を伴うスキーマ変更（列の削除・改名・型変更など） / 認証・認可の変更 / 複数モジュールにまたがる変更 / 重要なドメインルールの追加・変更 / 本番の振る舞いの追加・変更、バグ修正 / 本番の振る舞いを変えない構造変更で、対象にテストが十分にある |
| 4 | `legacy-refactor` | 本番の振る舞いを変えずプロダクションコードの構造を変える変更で、対象にテストがない・少ない |
| 5 | `light` | **本番の振る舞いもプロダクションコードの構造も変えない局所変更**（文言・書式・コメント・ドキュメント・静的な設定値、テストの追加、ログ出力の追加など） |

| 判定の決まり | 内容 |
| --- | --- |
| 括弧内は例示 | `light` と `operation` の括弧内は**例示で、ここに挙げたものに限らない**。`light` の基準は「本番の振る舞いもプロダクションコードの構造も変えない」ことで、例示に無い変更もこの条件を満たせば `light` として扱う |
| **`operation` を 1 番に置く** | 権限の設定の変更は「誰が何をできるか」を変えるため、条件だけを見れば `standard` に当たる。しかし変えるのは外部の系の状態で、プロダクションコードは変えないため、**テスト駆動は当たらない**。**設計は触る領域に当たるときだけ通る**（条件は `design` の「触る領域を決める」の表）。重大さは工程を増やさず、**実行前の承認**で受ける（「人手の承認を求める承認ゲート」） |
| 機能名を根拠にしない | **判定の根拠に、特定のホスティングやサービスの機能名を使わない。** 使うと、その機能を持たない対象では判定が成り立たない。機能名を出すのは例としてだけである |
| 見るのは変更の目的物 | 工程が生む記録は判定に含めない。「プロダクションコードも文書も変えず」は、変更しようとしている対象を指す。実行の記録・確定仕様・振り返りは工程の産物であり、これらを書いても `operation` の条件を外れない |
| 移行が不要なスキーマの追加 | インデックスの追加、既定値付き NULL 許容列の追加は `standard` として扱う |
| **`documentation` のタイプ** | 決まったら、続けて 6 つのタイプのうち 1 つを選ぶ（条件と境界事例は [references/document-types.md](references/document-types.md)）。**`README.md` と `docs/` の変更はこのモードに当たらない。** 判定を分けるのは読み手で、リポジトリの外にいる人へ渡すものだけが当たる |

判定に迷う場合と境界事例は [references/workflow-modes.md](references/workflow-modes.md) を参照する。

### 3. 判定結果を出力する

呼び出し側（エージェント・他の Skill・利用者）が**判定結果だけを受け取れる形式**で返す。

```text
mode: standard
pace: fast
根拠: 注文確定の振る舞いを変更する。公開 API とスキーマは変えない
宣言: あり。解析: 不要（新しい）
必須工程: worktree → requirements-design → implementation-plan → tdd-cycle
  → refactoring → cross-review → quality-gates → pr
  → plan-to-spec（仕様が変わった場合） → merged → release
  → release-verification（マージ前に実施できなかった受け入れ条件がある場合）
  → retrospective
次のコマンド: python3 "$SCRIPTS/supervise.py" new sprint --name m6 --worktree <リポジトリの根>
  --issue 1052 1053 --design 1052 --version 10.18.0-dev.1 --out <作業ディレクトリ>/sprint-m6
```

- 工程の並びは「モードごとに起動する Skill」の表から読む。この例は出力の形だけを示す
- `pace:` の行は `fast` か `auto` を指定したときだけ出す（既定の `normal` では出さない）
- **判定基準の本文を出力へ貼らない。** 呼び出し側が基準をコピーすると、この Skill が唯一の置き場所である前提が崩れる

**`次のコマンド:` の行は、判定の後に conductor が最初に打つコマンドである。** 次の文書からコピーする。

| 進め方 | コピー元 |
| --- | --- |
| `normal` | [references/waiting.md](references/waiting.md) の「スプリントを流すコマンド」の 1 つ目。設計 Pull Request を出さないモードは `--design` を省く |
| `fast`・`auto` | [references/pace.md](references/pace.md) の「スプリントを始める」の、その進め方の 1 つ目 |
| テンプレートが無い | `無し（supervisor で回す: <フェーズ>）` |

## モードごとに起動する Skill

| 工程 | `light` | `operation` | `legacy-refactor` | `standard` | `documentation` |
| --- | --- | --- | --- | --- | --- |
| 要求と受け入れ条件 | `requirements-design` | `requirements-design` | — | `requirements-design` | `requirements-design` |
| 作業場所の用意 | `worktree`（メインディレクトリで編集してよいパスだけなら不要） | `worktree`（メインディレクトリで編集してよいパスだけなら不要） | `worktree` | `worktree` | `worktree` |
| 設計 | `design`（該当時） | `design`（該当時） | `design` | `design` | `design` |
| 素材の収集と出典の確定 | — | — | — | — | `document-sources` |
| ドキュメント再構成 | — | — | `document-restructuring`（設計 Pull Request を分けた場合） | `document-restructuring` | `document-restructuring` |
| ドキュメントレビュー | — | — | `pr` → `cross-review` → `merged`（設計 Pull Request を分けた場合） | `pr` → `cross-review` → `merged` | `pr` → `cross-review` → `merged` |
| 計画 | — | `implementation-plan` | `implementation-plan` | `implementation-plan` | `implementation-plan`（執筆を分担する場合） |
| 実装 | 直接編集 | 実行 → [operation-run.md](references/operation-run.md) | `refactoring` | `tdd-cycle` | `document-drafting` |
| `構造改善` | — | — | `cross-refactoring` | `cross-refactoring` | — |
| `実装レビュー` | `cross-review` | `cross-review` | `pr-review` | `cross-review` | `cross-review` |
| 完了判定 | `quality-gates` | `quality-gates` | `quality-gates` | `quality-gates` | `quality-gates` |
| Pull Request | `pr` | `pr` | `pr` | `pr` | `pr` |
| 確定仕様化 | — | `plan-to-spec`（実行で決まった設定が以後の判断の前提になる場合） | — | `plan-to-spec` | `plan-to-spec`（確定版を残す場合） |
| 後片付け | `merged` | `merged` | `merged` | `merged` | `merged` |
| 配布 | `release` | `release` | `release` | `release` | `release` |
| 体裁レビュー | — | — | — | — | `layout-review` |
| リリース後テスト | — | `release-verification`（実行した経路とは別の経路で確かめられる場合） | `release-verification` | `release-verification` | `release-verification`（提出の後に確かめる経路がある場合） |
| 振り返り | — | `retrospective`（実行の手順そのものを変えた場合） | `retrospective` | `retrospective` | `retrospective` |
| 棚卸し | `backlog-refinement` | `backlog-refinement` | `backlog-refinement` | `backlog-refinement` | `backlog-refinement` |

**表の行名は進捗記録の `stage` の値で、スクリプトが同じ字で読む。** 範囲外の課題の起票（`out-of-scope`）は
この表に載らず、モードで要否を決めない（「範囲外の課題を見つけたとき」）。工程の全体像の図とモードごとの経路は
[references/workflow-modes.md](references/workflow-modes.md) の「標準フロー」にある。

**表の工程はスプリントの中で 1 回ずつ動き、中は並列にする。** 工程ごとの単位は
[references/parallel-work.md](references/parallel-work.md) の「工程が動く単位」にある。

| 工程 | 動く単位 |
| --- | --- |
| 設計 | 設計 Pull Request を主題ごとに同時に開いて並列で回す（1 本の設計文書は 1,000 行以下）。ゲート 1 でまとめて 1 回承認する |
| 実装 | 課題ごとの worktree で並列に進め、スプリントブランチへ集める |
| リファクタリング・コードレビュー・完了判定 | スプリントの develop 宛て Pull Request で 1 回通す（`auto` も同じ）。`fast` では完了判定は課題ごと、リファクタリングとコードレビューはトリガーの範囲で通す |

### 工程ごとの決まり

| 工程 | 決まり |
| --- | --- |
| 作業場所の用意 | **モードを判定した後に行う。** 要求と受け入れ条件は `issues/` に書くため、メインディレクトリのままで済む。開発の変更は `.worktrees/<ブランチ名>` の worktree の中で行う。後から移すと、メインディレクトリに差分が残ったまま並行作業が始まる。`light` でも、プロダクションコードの文言やテストを触るなら worktree を使う。`issues/` `docs/` と各ランタイムの設定だけで収まる変更は、メインディレクトリのままでよい |
| ドキュメント再構成 | 書き上げた設計文書を章立てから組み直す。`document-restructuring` が測る・並べ替える・整える・測り直すの 4 つの手順を持つ。**レビューの前に置く。** 後に置くと、レビュー担当が構成の指摘と内容の指摘を同時に出し、どちらの指摘なのかが混ざる |
| ドキュメントレビュー | 設計だけを載せた Pull Request を、実装より先にマージする。新しい Skill は使わず `pr` → `cross-review` → `merged` を順に呼ぶ。**マージには人間の承認が要る**（止まり方は「自走で工程を通す」）。head のブランチ名は `design/` で始め、マージした後は実装用の worktree を作り直す |
| 実装（`operation`） | **実行そのものを指す。** 行は増やさない。呼ぶのは `tdd-cycle` でも `refactoring` でもなく、[references/operation-run.md](references/operation-run.md) が定める手順である。実行の範囲・記録・失敗したときの止め方はそちらが持つ。**「本番系へ届く操作」の承認ゲート（ゲート 2）に当たる** |
| 構造改善 | **必ず通す。** `standard` と `legacy-refactor` のリファクタリングは `cross-refactoring` を通す。`fast` でも通し、時機だけをトリガーへ移す |
| 実装レビュー | **5 つのモードとも通す。** Pull Request を出す以上、その差分は誰かがレビューする。`light` も、**変えないことの確認**が要る。**明示的に呼ぶ**（自然文で「レビューして」と依頼すると、Claude Code では組み込みの `code-review` が起動して判定の投稿経路が変わる） |
| 配布 | `release` は 5 つのモードすべてで通す。**マージは取り込みで、リリースに数えない。** **自動で進めてよいのは検証リリースまでで、本番へのリリースは承認を得るまで進めない** |

工程ごとの理由・条件・例外は [references/stage-notes.md](references/stage-notes.md) にある。
**起動のたびに読むのは判定の基準と工程表で、そちらは条件に当たったときだけ読む。**

### `standard` モードで作るもの

設計工程は `design` が担う。`standard` では次を作る。

| 工程 | 何を作るか |
| --- | --- |
| 用語と不変条件 | `requirements-design` の仕様へ書く |
| 設計文書 | `design` が独立したファイルで作る。触る領域に該当する節をすべて埋める |
| 設計判断の記録 | 設計文書の「決定の記録」の節へ書く |
| ドキュメント再構成 | `document-restructuring` が章立てを読み手の問う順へ組み直し、前後の値を並べる |
| ドキュメントレビュー | 設計だけを載せた Pull Request を `cross-review` へ通し、マージしてから実装へ進む |
| 契約・結合テスト | `tdd-cycle` の階層の使い分けに従う |

**判定基準の側は変えない。** モードの定義は確定しており、この節は振り分け先を示す。

## 進め方（`pace`）

**`pace` はモードとは別の軸で、どこで検証し、承認をだれが担うかを決める。** 既定は `normal` で、上の工程表のとおりに動く。

| | `normal` | `auto` | `fast` |
| --- | --- | --- | --- |
| 何のための進め方か | ウォーターフォール。各工程を検証し、人の承認を取って進める | `normal` と同じ工程で、承認だけを MVV で半自動にする | スタートアップ的。実践投入の中で検証しながら MVV で自動に進める |
| 検証の場所 | 各工程で、配布の前（設計レビュー・テスト・リファクタリング・コードレビュー・完了判定） | 同じ | 実践投入の中（開発版の配布と利用）。検査は配布の後にトリガーでまとめて |
| 承認 | 人が 2 回（設計・本番への配布） | MVV 判定が「従う」なら自動、ほかは人 | MVV 判定で自動（ほかは人） |
| 判定と配布の単位 | スプリント（1 つの版として出す課題のまとまり） | スプリント | 課題ごとの Pull Request（develop へ直接入り、開発版も課題ごと） |
| 確定仕様化・振り返り・棚卸し | 各工程の順に | 同じ | スプリントの終わりにまとめて |
| 向く場面 | 影響が大きい・戻しにくい変更、複数の課題を 1 版にまとめる | `normal` と同じ場面で、人の待ち時間を減らしたい | 小さく出して実践で確かめられる変更、開発版の利用者が近い |

**MVV 判定は `mvv-gate.py` が行う。** 「従う」でレッドラインに当たらないときだけ承認ゲートを通し、ほかは人の承認へ戻す。

**`auto` と `fast` は、使ってよい条件を `supervise.py new sprint --pace <値>` が機械で確かめる。** 条件は
開発版のチャネル・インストール確認・`.ndf/pace.json` の許可・モード・MVV の承認で、1 つでも欠ければ断る。
そのときは `normal` で進める。進め方ごとのフロー・`fast` の工程の区分・条件・設定・トリガー・記録の読み方は
[references/pace.md](references/pace.md) にある。

## 人手の承認を求める承認ゲート

**承認ゲートは 2 つで、増やさない。** どちらも取り消せない操作である。増やすほど「承認したこと」の意味が薄れる。
通過の回数が増えるほど、内容を読まずに通す動きが入る。`fast` と `auto` でも数は変えず、MVV の承認を
2 つの承認ゲートの事前の許可として扱う（条件は `AGENTS.md` と [references/pace.md](references/pace.md)）。

| 承認ゲート | いつ | 文書での意味 | 要否の決まり方 |
| --- | --- | --- | --- |
| 設計 Pull Request のマージ | ドキュメントレビューの工程 | **企画承認** | **マージ先のチャネルによらず要る** |
| 本番系へ届く操作 | リリースの工程、`operation` の実装の工程、および自動反映の本番チャネルへの Pull Request のマージ | **制作物承認** | **届く先が本番系かどうかで決まる** |

- **承認ゲートの外で工程の側が実行前確認を足さない。** 取り消せる操作は止めずに行い、消した対象と戻し方を報告する
  （例外の基準は `AUTHORING.md` の「実行前確認の要否を決める 3 つの問い」）
- **カットポイントの再起動も承認ゲートに数えない。** `ndf-next` のブロックの前に承認・確認（`AskUserQuestion` を含む）を
  挟まない。**`/goal` の文面が「承認を求める」と書いていても、指すのはこの 2 つの承認ゲートだけである**
- **`documentation` でも承認ゲートは 2 つのままである。** 企画承認は構成案と体裁設計を載せた設計 Pull Request のマージ、
  制作物承認は本番の提出先への操作にそのまま当たる。**新しい承認ゲートを作らない**

**2 つは要否の決まり方が違う。** 並べて書くと片方に掛かる規則が両方に掛かって読めるため、節を分ける。

### 設計 Pull Request のマージ

**承認するのは、この設計で実装へ進んでよいかである。** マージ先がベースブランチであっても要る。
マージした時点で設計は実装の前提になり、後から直すと設計文書とコードの両方を書き直すことになる。
この費用はマージ先のチャネルで変わらない。

| モード | 扱い |
| --- | --- |
| `standard`・`documentation` | 対象 |
| `legacy-refactor` | 設計 Pull Request を分けたときだけ同じ扱いにする |
| `light`・`operation` | **設計工程を条件付きで通る場合でも対象外である。** どちらも独立した設計文書を作らず、設計 Pull Request を出さない。承認ゲートが掛かるのは設計だけを載せた Pull Request のマージで、設計を書くこと自体には掛からない |

**止める場所をドキュメントレビューに限るのは、そこが後から直す費用の変わり目だからである。**
**宛先が自動反映の本番チャネル（下の節）なら、承認資料に本番への反映を載せ、1 回の承認で両方を通す**
（[production-merge.md](references/production-merge.md)）。

### 本番系へ届く操作

**要否は、届く先が本番系かどうかで決まる。** 本番系とは、利用者が現に使っているリリースのチャネル・環境・外部サービスを指す。
**リリースはその一例であり、`operation` の実行も同じ性質を持つ。** どちらも反映した瞬間に効き、取り消しても
「その状態を見た人」は戻らない。**性質が同じものを別の承認ゲートとして数えない。**

| 何が届くか | 何で判定するか | 規則を持つもの |
| --- | --- | --- |
| リリース（マージと公開） | マージ先が本番チャネルか。**チャネルは本番系の一種である** | `release`。検証リリースは提示して進めてよく、本番へのリリースは承認を得るまで進めない |
| `operation` の実行 | 操作する先が本番系か。検証用の系への操作は承認を求めない | [references/operation-run.md](references/operation-run.md)。承認を求めるのは実行の前で、単位ごとの取り消しの手段を添える。**取り消せない単位を含むときは、そのことを先に示す** |
| **文書の提出** | 提出先の `production` が真か | [references/document-destinations.md](references/document-destinations.md) |

`release` の規則には、実装 Pull Request をマージする時点で読む `merged` と `pr` から辿れるようにしてある。

**検証環境や開発版のチャネルへ入れるマージは取り消せるため、承認を求めない。**
**Pull Request のマージ（実装・設計を問わない）が承認ゲート 2 に当たるのは、宛先が自動反映の本番チャネルのときだけである。**
自動反映の本番チャネルは、本番チャネルのうち、`.ndf/project.json` の `delivery` に `kind: auto` でそのブランチを
`branch` に持つ行があるものである。マージで本番系への反映が自動で始まる。それ以外の宛先へのマージは止めない。
判定の表・止める場所・承認の後の続け方は [references/production-merge.md](references/production-merge.md) にある。

**どのブランチが本番チャネルかは、リポジトリの設定が決める。** 読み取りの順序は次の 2 つである。

1. `.ndf/worktree.json` の `production_branch`
2. **設定が無ければ既定ブランチ**（`origin` の HEAD が指すもの）

**`base_branch` は流用しない。** そちらはベースブランチで、worktree の分岐元を決める（`follow_branch: true` のときは
メインディレクトリの追従先にもなる）。このリポジトリの値は `develop` であり、本番チャネルと定める `main` とは別のブランチである。
流用すると、開発版のチャネルへのマージが本番の扱いになる。

### 提示するもの

**止まる手段と、そのとき提示するものは [references/approval-request.md](references/approval-request.md) が定める。**
承認資料は 2 層に分かれ、対象を開くためのもの（URL・ブランチ・変更量）だけでは足りない。
**承認するのは中身である。Pull Request の形は問わない。**

**並行して走った Pull Request は、承認ゲートでまとめて 1 回の承認へ載せる。** 1 本ずつ求めない。

## 自走で工程を通す

**工程を続けて通す。** ただし**上の 2 つの承認ゲートの前では 1 度止まり、`AskUserQuestion` で人間の承認を待つ**。
承認を得るまでマージせず、次の工程へも進まない。`fast` と `auto` では、MVV 判定が「従う」を返した承認ゲートだけ止まらない。

| 承認ゲート | 止まり方 |
| --- | --- |
| 設計 Pull Request のマージ（`standard`） | 承認を得るまで実装の工程へ進まない |
| 本番系へ届く操作 | 承認を得るまで進めない。**検証リリースまでは自動で進めてよい**。`operation` の実行も同じで、本番系へ届く単位は承認を得るまで実行しない |

**工程は 3 層（conductor / supervisor / worker）へ出し、既定はプラン（`supervise.py`）である。**
conductor がフェーズごとにプランをキューで流す（[references/waiting.md](references/waiting.md) の「スプリントを流すコマンド」）。
テンプレートか要る設定が無いときだけ、`Agent` で supervisor を起動する
（[references/agent-layers.md](references/agent-layers.md) の「プランで流すか supervisor で回すか」）。
**承認ゲートで止まれるのは conductor だけである。** 起動の指示・報告の形・モデルの基準・到達点の置き直しも `agent-layers.md` にある。

**supervisor を起動するときは `$SCRIPTS` を解き、起動指示の「進捗記録」へ絶対パスで書く。** supervisor はこの 1 行で
進行を記録し、この Skill も `progress-tracking` も起動しない。規則は `agent-layers.md` の「conductor → supervisor」にある。

### 会話をまたいで続ける

**工程は 1 つの context window で通し切らなくてよい。** 判定したモードと通った工程は会話の外へ残るため、
文脈を捨てても現在地から続けられる。**長い文脈のまま進めると、記録は残っているのに後の工程の判断だけが悪くなる。**
カットポイント・委譲してよい対象・残量の見方は [references/context-window.md](references/context-window.md) にある。

**conductor は、止められたときに次の工程を始める再開コマンドを出す。**

| 項目 | 内容 |
| --- | --- |
| 止められたとき | `context-window.md` の 4 つのカットポイントと、コンテキスト量の hook（`hook.py` の token の guard） |
| 出すもの | 再開コマンド `/ndf:development-workflow #<課題>`（今のセッションを `/goal` で始めていたときだけ先頭に `/goal `）。情報文字列が `ndf-next` のコードブロック 1 つで出す |
| 3 層で出す時点 | conductor がフェーズレポート（`## フェーズの報告`）かキューの done を受け取った時点（supervisor とプランは出さない）。`結果: 関門` なら、承認ゲートの承認と取り込みの後 |
| 出す前に行うこと | 引継ぎ文書（メインディレクトリの `.ndf/handoff/<名>.md`）を作るか更新する。対象の最後の工程（棚卸し。マイルストーンは閉じた後）の後に conductor が消す。規則は [references/handoff.md](references/handoff.md) |

出す時点・アナウンス・新しい会話が状態を戻す手順は、`context-window.md` の「新しい会話で戻す」にある。

**カットポイントの再起動はラッパーが自動で行う（Claude Code だけ）。** 利用者が `claude` と打つと alias がラッパーを挟む。
ラッパーは conductor が出した `ndf-next` のブロックを拾い、`/exit`・プラグインの更新・次のセッションの起動を行う。
始め方・止め方・上限は [references/relay.md](references/relay.md) にある。
**ブロックの前に次の Bash を 1 回実行し、2 行目（アナウンス）をブロックの直前へそのままコピーする。**
1 行目が `outside` か失敗ならラッパーの外である（書き方は `context-window.md` の「新しい会話で戻す」）。

```bash
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'; case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
[ -n "$PLUGIN_ROOT" ] && python3 "$PLUGIN_ROOT/scripts/relay.py" notice || echo outside
```

**人がその場にいて指示を変えたいときは、conductor へ伝える。** 次のフェーズから反映する。

## 即時修正

**次の 4 つをすべて満たす不具合は、どの `pace` でも起票せず、その場のプランで直す。** 1 つでも欠ければ
`out-of-scope` で起票する。行数では決めない（契約を変える小さな変更を止められない）。

1. 原因が特定できている。再現するテストを先に書く（書けないときは再現の手順を Pull Request の本文に書く）
2. 既存の契約を壊さない（エントリポイントの引数と出力の形・結果 JSON・プラン JSON・状態ファイル・hook の入出力・
   用語集の形）。任意の項目や副命令を足す後方互換の追加はよい
3. 既存の方針の範囲に収まる。方針・既定値・閾値そのものを変えるなら、課題か実験版へ回す
4. revert 1 回で戻せる

マージ済みの変更の不具合なら、[references/pace.md](references/pace.md) の「流出不具合の記録」に従う。

## 範囲外の課題を見つけたとき

この変更の受け入れ条件にも、直す対象にも含まれない課題は、**見つけたその場で `out-of-scope` が
issue にする**。呼び出し元・3 択の判断・振り返りでの拾い方は `out-of-scope` の SKILL.md にある。

## 途中でモードが変わったとき

判定はやり直してよい。ただし**上げる方向のみ**を既定とする。状況ごとの対応と、下げたいときの扱いは
[references/workflow-modes.md](references/workflow-modes.md) の「途中でモードが変わったとき」にある。

## 進行をボードへ記録する

判定したモードと、いま何番目の工程にいるかは会話の中にしか残らず、セッションが変わると引き継がれない。
**リポジトリが `.ndf/projects.json` を持つ場合に限り**、これを GitHub Projects のボードへ残す。

- 判定の結果（モード）は、作業場所を用意した時点で記録する
- カットポイントを持つ Skill が、自分の工程に入った時点で進行を書き込む
- ボードの値は**この工程表の行名と一致させる**。工程を足したときは、同じ表からボード側も更新する

**この仕組みは任意である。** 設定が無ければ何も起きず、工程はそのまま通る。進行管理が理由で開発が止まってはいけない。
設定と値の一覧は [references/projects-tracking.md](references/projects-tracking.md) にある。

## 工程の飛ばしとマージを機械で見る

| 仕組み | 何をするか |
| --- | --- |
| 進捗記録のスクリプト | 自分で通過工程を積む。記録の無い工程は案内するだけで拒否しない |
| この Skill の frontmatter の `hooks` | 承認ラベル（ラベル `design-approved`）の無い設計 Pull Request のマージだけを拒否する |

判定の 2 つ・有効にする操作・通過記録の読み方・`fast` の工程の出し方は
[references/stage-completeness.md](references/stage-completeness.md) にある。

## 参照

- [references/workflow-modes.md](references/workflow-modes.md) — 判定の境界事例、モード別の詳細、標準フロー（工程の全体像の図）
- [references/projects-tracking.md](references/projects-tracking.md) — 進行を GitHub Projects へ記録する設定と値の一覧
- [references/stage-completeness.md](references/stage-completeness.md) — 通過記録と報告、承認ラベルの作り方
- [references/parallel-work.md](references/parallel-work.md) — 並行開発の 4 つの形、工程が動く単位、任せるうえでの必須ルール
- [references/pace.md](references/pace.md) — 進め方（`pace`）のフロー・`fast` と `auto` の条件・設定・プランのステージ・検査のトリガー・MVV 判定
- [references/approval-request.md](references/approval-request.md) — 承認を求めるときに提示するもの
- [references/operation-run.md](references/operation-run.md) — `operation` の実行の範囲・記録・失敗したときの扱い
- [references/context-window.md](references/context-window.md) — context window のカットポイント、委譲する対象としない対象、残量の見方。引継ぎ文書の置き場・節の形・作る／読む／更新する／消す規則は [references/handoff.md](references/handoff.md)
- [references/agent-layers.md](references/agent-layers.md) — 3 層（conductor / supervisor / worker）の責務、フェーズ、報告の形
