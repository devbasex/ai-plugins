# 用語集: NDF のプロジェクトの設定（`ndf-project-settings`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

プロジェクトの設定（.ndf/）を解析で作り、選べる値と旧いキーの改名を扱うときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 設定 | — | リポジトリ側に置く .ndf/<名前>.json。無ければその機能は既定の動きだけになるか、何も動かない。「<対象>の設定」の形で呼ぶ（指示書チェックの設定・リリースの設定・ボードの設定・worktree の設定・用語集の設定）。git で追跡するものを共有設定、追跡しないものを個人設定と呼ぶ | 宣言 | — | `docs/specifications/ndf-instruction-files-check.md` |
| プロジェクトの設定 | — | リポジトリの根の `.ndf/` に置く、そのプロジェクトの形（テスト・CI・ブランチ・デプロイなど）を表すファイルの集まり。機能ごとの JSON と、解析が書く `project.json` からなる | — | — | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| 解析 | — | プロジェクトの設定を作るために、リポジトリと CI を決定論で測り、測った値から設定の中身を決めること | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| 不明 | — | 解析が測れなかった・決められなかった項目の値。ai-plugins の値で埋めない | — | — | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| 測った値 | — | 解析の測定が、ファイル・git・`gh` の出力から決定論で決めた項目の値 | — | — | `docs/specifications/ndf-next-closed-children-subcommands.md` |
| 質問 | `question` | 解析の測定が値を 1 つに決められず、候補と根拠を付けて conductor へ渡す項目 | 問い直、問い返、問い方、問い（単独） | — | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| 回答 | `answer` | 解析の質問ごとに conductor が選んだ値か不明。書き出しが形を確かめて設定へ書く | 答え | — | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| 入力の指紋 | — | 解析が読んだファイルの、HEAD の木での blob の SHA と、ブランチの構成の組。プロジェクトの設定の新しさの判定に使う | — | — | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| ハードコード | — | ai-plugins 固有の名前（リポジトリ名・組織名・ブランチ名・タグの形・マーケットプレイスの登録名など）を、設定から読まずに ndf-next のコード・コントラクト・Skill の本文へ直接書くこと。検査が例外リストの外の件数を数えて落とす | — | — | `docs/specifications/ndf-next-repo-neutral-defaults.md` |
| 設定で選べる値 | — | `.ndf/` の設定ファイルに書ける値の選択肢（リリース方式の表の行・delivery の種類・リリースの形）。コントラクトから数え、Issue の番号のある例外なしに減ると検査が落ちる | — | — | `docs/specifications/ndf-next-repo-neutral-defaults.md` |
| リモート名 | — | ndf が取得・検証・差分の基準・送信に使う git のリモートの名前。`.ndf/worktree.json` の `remote` に書き、省けば `origin` | — | — | `docs/specifications/ndf-next-repo-neutral-defaults.md` |
| 選べる値の基準 | — | 設定で選べる値の集合ごとに、出所と値の一覧を持つファイル（`scripts/ndf-choices.json`）。値を減らすときは Issue の番号と理由を持つ例外を足す | — | — | `docs/specifications/ndf-next-repo-neutral-defaults.md` |
| 設定のキーの改名表 | `ndf_settings_renames` | 利用者の設定（.ndf/）の旧いキーの位置・新しいキーの位置・値の対応を行ごとに持つコントラクトの表（contracts/data/ndf-settings-renames.json）。ブランチ戦略の移行（ndf project branch-strategy migrate）と旧い設定の検査、設定のキーの移行（ndf settings migrate）と設定を読む側の止める検査が、行を分けて同じ表を読む | — | — | `plugins/ndf-next/skills/worktree/references/ndf-settings.md` |
| 移行のコマンド | `settings_migrate` | 設定のキーの改名表に従い、利用者の .ndf/ を新しいキーへ書き換えるサブコマンド。差分と差分のハッシュを示す実行と、利用者が承認した差分のハッシュを渡して書く実行に分かれる | — | — | `plugins/ndf-next/skills/worktree/references/ndf-settings.md` |
| 解析の記録 | — | 解析が書く、解析をどの入力で行ったかの値（解析器のバージョン・入力のファイルのハッシュ・ブランチの構成）と、その解析が書いた設定の指紋。設定が古くなっていないかの判定が読む。git で追跡しないファイルに置き、設定には置かない | — | — | `docs/specifications/ndf-project-analysis-record.md` |
| 書いた値の指紋 | — | 解析が書いた設定の値の SHA-256。今の値の指紋と一致すれば解析が書いた値、違えば人が直した値として扱う。設定の値が変わったときだけ変わるため、設定と一緒に追跡する | — | — | `docs/specifications/ndf-project-analysis-record.md` |
