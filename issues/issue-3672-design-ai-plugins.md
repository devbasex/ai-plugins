# #3672 の ai-plugins の値

[issue-3672-design.md](issue-3672-design.md) の「ai-plugins だけの事情」の値の中身・今の運用から変える点・移行の順序と PR への割り当てを置く。

## ai-plugins の値

ai-plugins のセクションは、種類 `plugin` の既定（設計文書の「代表のリポジトリの種類の既定」）に測った名前を入れたものと一致させる（受け入れ条件 15）。測った名前は、本番ブランチ `main`・プラグイン `ndf`・ランタイム `claude` と `codex`・ワークフロー `release-cd.yml` である。
決めた理由は [issue-3672-design-decisions.md](issue-3672-design-decisions.md) の決定 12 にある。出典は [issue-3672-design-research.md](issue-3672-design-research.md) にある。

| 項目 | 値 | 出典 |
| --- | --- | --- |
| ブランチ戦略 | `trunk`。main だけで開発し、スプリントブランチは main から切って main へ戻す | STD2・STD8・PRD1・PRD2 |
| 版の付け方 | semver を `plugins/<名前>/.claude-plugin/plugin.json` の `version` の 1 か所に持つ。マーケットプレイスの項目に `version` を書かない。開発版の接尾辞は使わない | STD4・STD9 |
| リリースの単位 | 版上げの PR（main から切った `release/v<版>`）のマージ。スプリントの PR のマージは、Claude Code の既存の利用者へ届かない。Codex の利用者には main が進むたびに届く（「残る危険」） | STD4・STD10・PRD3 |
| タグと Release | `ndf--v<版>` のタグと GitHub Release を、main へのマージで CD（`release-cd.yml`）が作る。公開したタグの更新と削除を ruleset で禁じる | STD5・PRD6 |
| 利用者が取得する ref | 既定ブランチ main（今の登録のまま）。Codex の取得元を版上げの PR のマージでだけ進む ref へ移すかは人へ残す（設計文書の「人へ残す判断」） | STD4・STD10 |
| 承認の場所 | 版上げの PR でのリリース承認ゲートの 1 回。main の ruleset の承認の数を 0 にする | 決定 7・PRD1 |
| マージの主体と方式 | NDF・merge queue・merge commit・迂回なし | STD3・PRD1 |
| マージ前に試す場 | 無し | 決定 12 |

### 今の運用から変える点

| 項目 | 今の値 | 新しい値 | 理由 |
| --- | --- | --- | --- |
| ブランチ戦略 | develop→main（`promotion: whole`・`base_branch: develop`） | `trunk`（main だけ） | 長く残るブランチの間で版数と検査の範囲を運ぶ事故（03-release の §2.A）を無くす |
| スプリントの起点と PR の宛先 | develop | main | 同上 |
| 開発版 | `-dev.N` の版数と develop のチャネル（`release-dev.yml`） | 無し | 版上げの PR までは既存の利用者へ届かず、別のチャネルが要らない（STD4） |
| 版上げの PR の起点 | develop から切り、main へ出した後に develop への同期の PR を作る | main から切る。同期の PR は無い | 起点と宛先が同じになる |
| 承認 | リリース承認ゲートと main の PR のレビューの承認 1（`--admin` で迂回） | リリース承認ゲートの 1 回。ruleset の承認の数 0 | 2 つ目の承認による止まりを無くす（I9） |
| マージ | merge commit・管理者の迂回 | merge queue・merge commit・迂回なし | 組み合わせを GitHub で確かめる（要求の前提 4） |
| タグの保護 | 無し | 公開したタグの更新と削除を禁じる | 依存の版の範囲がタグで解決される（STD5） |
| 宣言の置き場 | 4 か所 | `.ndf/project.json` の `branch_strategy` | I1 |
| タグの形・GitHub Release・取得の ref・merge commit | — | 変えない | 公式の形と同じ（STD5）。取得の ref を変えると利用者の操作が要る（P1） |
| リリース前の直しを使う手段 | 開発版（`-dev.N`）を develop から入れる（例: lguard の 10.27.73-dev.2） | 直しを含む正式版の PATCH を、スプリントの終わりを待たずに版上げの PR で出す。実験版の経路は変えない | 実験版は既定で動くものの直しを載せられない。正式版 1 本の費用が承認 1 回になる（決定 16。版を出す単位の変更は人へ残す。P1・C7） |
| 承認の前に確かめる所 | develop から入れる開発版 | 版上げの PR の head（`release/v<版>`）から入れる（`verify-install --ref`） | 開発版が無くなる（決定 17） |
| `pace: auto`・`fast` と開発ループの条件 | 開発版のチャネル（develop）があるので使える | 承認前の試用（版上げの PR の head からの導入）があるので使える。`.ndf/pace.json` の `verify` を `--ref develop` から `--ref {ref}` へ | 開発版をやめても使い続ける（決定 15） |

**残る危険。** 新しく入れる利用者と、入れ直す利用者は、main の先端（版上げの PR の前にマージしたスプリントを含む）を前の版数の表示のまま受け取る（STD4）。
Codex の利用者は、起動時のマーケットプレイスの自動更新で、main が進むたびに版数を比べずに入れ直す（STD10）。取得元が main のままなら、スプリントの PR は版上げの PR を待たずに Codex の利用者へ届く。
スプリントの PR は設計承認ゲート・cross-review・CI を通っている。Codex の利用者へ届く範囲を版上げの PR に合わせるには取得元の ref を変える必要があり、P1 に当たるため人へ残す（設計文書の「人へ残す判断」）。

## 移行の順序

ai-plugins の工程を動かす NDF は、ai-plugins に入れてある版である。旧い版の NDF は旧いキーだけを読み、新しい版の NDF は旧いキーがあると止まる（I4）。
そのため、NDF の版と `.ndf/` の中身がいつも合うように、新しい NDF を出す PR と `.ndf/` を移す PR を分ける。順序は次のとおりである。

| 手順 | 動かす NDF | 出す PR と宛先 | 中身 |
| --- | --- | --- | --- |
| 1 | 入れてある旧い版 | 実装の PR を develop へ、今の出し方（develop→main の PR）で main へ | 新しい NDF のコードと CI の検査。ai-plugins の `.ndf/`・`.github/workflows/release-dev.yml`・`AGENTS.md`・`docs/versioning-and-distribution.md`（手順 2 へ回す）と、`scripts/check-pr-base.sh`・`.github/workflows/pr-base-guard.yml`（手順 4 へ回す）は含めない（削除の一覧の D1〜D3・D27・D28・D36 のうち ai-plugins の分）。`.ndf/` は旧いままなので、旧い NDF の工程は止まらない |
| 2 | 使わない（人がコマンドを打つ） | 移行の PR を main から切って main へ | `branch-strategy migrate` で移した `.ndf/`、手順 1 で手順 2 へ回したファイル。develop は手順 1 の同期の PR で main と同じ中身になっている。人が GitHub で中身を見てマージする |
| 3 | 使わない | — | 人が main の ruleset を変える（C2。設計文書の「人へ残す判断」） |
| 4 | 手順 1 で出した新しい版（入れ直す） | 宛先の検査の PR を main から切って main へ（スプリントの PR） | `scripts/check-pr-base.sh`・`.github/workflows/pr-base-guard.yml` |
| 5 | 同上 | 版上げの PR を main から切って main へ | 変えた後の最初の正式版で、受け入れ条件 24 を確かめる（リリース後テスト） |

- 手順 1 のリリースより前に始めたスプリントは、手順 1 のリリースまでに develop へ入れ終える。手順 1 のリリースから手順 3 を終えるまでは、新しいスプリントを始めない（新しい NDF は旧いキーで止まり、旧い NDF は新しいセクションを読めず、手順 3 の前は ruleset が承認 1 を求めて I10 で止まるため）
- 手順 2 と 3 は NDF の工程を通さない。新しい NDF の読む部品（`load`）はメインディレクトリの `.ndf/` を読み、移行の PR がマージされるまでそこに旧いキーが残るため、I4 で止まる。worktree の用意（`resolve_base`）・承認の証跡（`trail_writer`）・マージ（`land_approved`）のどれも動かない
- 移行の PR は、人が main から切った worktree で `branch-strategy migrate` を打ち（移行のコマンドは旧いキーを読む。I8）、`gh pr create` で作る。人は GitHub で中身を見てマージし、このマージがリリース承認を兼ねる。手順 2 と 3 で人へ問う承認はこの 1 回である。ruleset はまだ承認 1 を求めるため、承認を満たせなければ人が自分の権限で迂回してマージする
- 移行の PR の宛先の検査（`guard`）は、手順 1 の旧い `check-pr-base.sh` が走る。旧い検査は、宛先の main の `branch_flow.promotion`（`whole`）を読んで選んで集める判定を外し、ベースブランチ（`base_branch`）を PR の中の `.ndf/worktree.json` から読む。移行の PR はそのキーを消すため、ベースブランチが無いとして通す
- 新しい `check-pr-base.sh` を手順 4 へ回すのは、宛先のブランチのセクションを `load_at_ref` で読むためである。手順 1 か手順 2 に入れると、宛先の main にセクションが無いため止まり、develop→main の PR と移行の PR の検査が落ちる。手順 4 では main にセクションがあり、新しい NDF の工程で出せる
- 手順 1 の間、旧い宣言の検査（`check-legacy-branch-decl.py`）は、手順 2・4 へ回したファイルを照らさない。手順 2 の移行の PR と手順 4 の宛先の検査の PR が、それぞれの行を検査の対象へ足す
- 手順 2 から後、develop は更新されない。develop を消すかは人へ残す（設計文書の「人へ残す判断」）

### PR への割り当て

ai-plugins では、NDF の版と `.ndf/` の中身がいつも合うように、詳細設計（[issue-3672-design-detail.md](issue-3672-design-detail.md) ）の「ファイル配置」の表のファイルを 3 本の PR に分ける（上の「移行の順序」の手順 1・2・4）。

| PR | 載せるファイル | 削除の一覧の検査のデータ（`scripts/legacy-branch-decl.json`） |
| --- | --- | --- |
| 実装の PR（手順 1。develop へ出し、今の出し方で main へ） | 「ファイル配置」の表のうち、下の 2 行に挙げる 8 つを除くすべて | 下の 2 行のファイルを照らす行（D1〜D3・D27・D28・D36 のうち ai-plugins の分）を持たない |
| 移行の PR（手順 2。人が main から切って main へ。NDF の工程を通さない） | `.ndf/project.json`・`.ndf/worktree.json`・`.ndf/supervise.json`（`branch-strategy migrate` の出力）、`.github/workflows/release-dev.yml` の削除、`AGENTS.md`・`docs/versioning-and-distribution.md` の 6 つ | この行のファイルを照らす行（D1〜D3・D28・D36 のうち ai-plugins の分）を足す |
| 宛先の検査の PR（手順 4。新しい NDF の工程で main から切って main へ） | `scripts/check-pr-base.sh`・`.github/workflows/pr-base-guard.yml` の 2 つ | この行のファイルを照らす行（D27）を足す |

影響評価（[issue-3672-design-impact.md](issue-3672-design-impact.md) ）で足したファイルのうち、`CLAUDE.md`・`CONTRIBUTING.md`・`plugins/ndf/README.md`（削除の一覧の D36 で足した分）と `.ndf/pace.json`（`verify` の `--ref`）は、上の表の移行の PR に足す。
`.ndf/pace.json` を手順 1 に入れないのは、`{ref}` を置き換える試用のステージが新しい NDF にしか無く、手順 2 の前に旧い NDF が動かす工程で develop からの確かめが止まるためである。

検査のスクリプトに PR の段階を見分ける分岐は持たない。手順 1 のデータに行が無いことだけで、手順 2・4 へ回したファイルを照らさない。
新しい `check-pr-base.sh` は宛先のブランチのセクションを `load_at_ref` で読むため、main にセクションが入る手順 2 のマージより後に出す（上の「移行の順序」）。

移行の PR と宛先の検査の PR をこの課題で出すかは、設計文書の「人へ残す判断」（C2・P1）に依る。#3641 で行う側に決まったときは、2 本の行をそのまま #3641 へ移し、実装の PR の中身は変えない。
