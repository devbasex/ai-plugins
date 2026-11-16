# #3672 の ai-plugins の値

[issue-3672-design.md](issue-3672-design.md) の「ai-plugins だけの事情」の値の中身と、今の運用から変える点を置く。

## ai-plugins の値

ai-plugins のセクションは、種類 `plugin` の既定（設計文書の「代表のリポジトリの種類の既定」）に測った名前を入れたものと一致させる（受け入れ条件 15）。測った名前は、本番ブランチ `main`・プラグイン `ndf`・ランタイム `claude` と `codex`・ワークフロー `release-cd.yml` である。
決めた理由は [issue-3672-design-decisions.md](issue-3672-design-decisions.md) の決定 12、出典は [issue-3672-design-research.md](issue-3672-design-research.md) にある。

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

**残る危険。** 新しく入れる利用者と、入れ直す利用者は、main の先端（版上げの PR の前にマージしたスプリントを含む）を前の版数の表示のまま受け取る（STD4）。
Codex の利用者は、起動時のマーケットプレイスの自動更新で、main が進むたびに版数を比べずに入れ直す（STD10）。取得元が main のままなら、スプリントの PR は版上げの PR を待たずに Codex の利用者へ届く。
スプリントの PR は設計承認ゲート・cross-review・CI を通っている。Codex の利用者へ届く範囲を版上げの PR に合わせるには取得元の ref を変える必要があり、P1 に当たるため人へ残す（設計文書の「人へ残す判断」）。

