## 何が起きたか

2026-11-25 の [#4516](https://github.com/devbasex/ai-plugins/issues/4516)（プロジェクトの設定から、実行ごとに変わる値を追跡しない別ファイルへ分ける）の設計承認ゲートで、利用者が、どの仕組みも使っていない計測値について [「使われない情報を取得・保存する必要はないです」と指摘した](https://github.com/devbasex/ai-plugins/pull/4518#issuecomment-7162632854)。

#4516 はファイル数・CI の実行時間などを測るのをやめた。一方で、解析（`ndf project measure` と `ndf project write`）が `.ndf/project.json` に書くほかの項目は、使う側があるかを数えていなかった。

解析が書く項目は 9 つある。このうち `test`（テストの走らせ方）と `ci`（CI の必須のチェック）は、値を読むプログラムがある（`plugins/ndf/src/ndf/testing/declared.py:58`・`means.py:31`）。この 2 つは維持するので調査の対象から外した。起票時の本文の「対象範囲」にも含まれていない。残る 7 項目を調べると、6 項目は、書いた値を読むプログラムも手順も無かった（下の「今の姿」の表）。利用者のリポジトリでは今も、これらの項目が測られ、チームで共有する設定のファイルに書かれる。一部の項目は、AI が回答を作る手間もかかる。

## 種類

機能追加（解析が `.ndf/project.json` に書くと決めている項目を減らす）

## 今の姿

解析は、リポジトリを調べて `.ndf/project.json`（git で追跡し、チームで共有する設定のファイル）に項目を書く。値が機械で決まらない項目は「質問」になり、AI が根拠を読んで回答する。解析は、書いた値のハッシュ（以下「指紋」）も同じファイルの `analysis.written` に残す。次の書き出しは指紋を使って、解析が書いたままの値か、人が直した値かを見分ける。手順の SSOT は [project-analysis.md](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/skills/development-workflow/references/project-analysis.md)、仕組みと理由の SSOT は [確定仕様 ndf-project-analysis-record.md](https://github.com/devbasex/ai-plugins/blob/main/docs/specifications/ndf-project-analysis-record.md) である。

### 項目ごとの読み手

2026-11-27 に `main` のコミット `0cf28737c`（NDF 11.0.49）で数えた。「読み手」は、`.ndf/project.json` に書かれた値を使う次の 3 つを指す。

- 値を読んで振る舞いを変えるプログラム
- 値を読めと指示する手順書
- 手順書や出力に従って、保存した値を見て作業する利用者。ファイルを開けば閲覧できる、というだけの場合は数えない

表は解析が書く 9 項目のうち、`test` と `ci` を除く 7 項目である。パスは `plugins/ndf/` からの位置である。

| 項目 | 中身 | 読み手 | 扱い |
| --- | --- | --- | --- |
| `languages` | 言語の名前・フレームワーク・バージョン | 読み手無し | 消す |
| `services` | コンテナの有無・compose のファイル・データベース | 読み手無し。測る途中で拾う compose のサービス名は、テストの質問の根拠に使う（`src/ndf/domains/project/measure_repo.py:530-533`） | 消す（サービス名を拾う処理は残す） |
| `issues` | 課題の保存先（質問） | 読み手無し | 消す |
| `checks` | 静的解析・整形の Tool の一覧 | 読み手無し | 消す |
| `ndf_policies` | NDF の方針を採るか（質問） | 読み手無し | 消す |
| `instructions` | 指示書のファイル・読み込み・注記 | 設定の値は読み手無し。注記は、設定ではなく測定の結果から取って、書き出しの「案内」に出す（`src/ndf/domains/project/cli.py:96`・`write.py:352`） | 項目は消す（案内は残す） |
| `materials` | 課題の棚卸しで何を調べるかの設定（質問） | `skills/backlog-refinement/references/` の `research.md:13`・`review.md:8`・`review.md:28`・`overhaul.md:15`・`overhaul.md:17`・`overhaul.md:41`・`depths.md:4` | 残す |

「読み手無し」は、`plugins/ndf` の `src`・`skills`・`agents`・`contracts` と、`scripts`・`experimental`・`.github` を、項目の名前と中のキーの名前で `git grep` した結果である。当たったのは、解析自身（測る・書く）、設定の形の定義と説明の文書、キーの名前だけを数える確かめ（`ndf records preflight`）、同じ名前の別の値だけだった。

### 解析が項目のために行っていること

- `issues` の質問の根拠を作るために、GitHub の API で閉じた Pull Request の一覧と課題の一覧を取る。この 2 つの呼び出しは、ほかの項目では使わない
- `issues` と `ndf_policies` は質問なので、解析のたびに AI が回答を作る

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| 解析が書く項目 | `languages`・`test`・`ci`・`services`・`issues`・`checks`・`ndf_policies`・`instructions`・`materials` | `test`・`ci`・`materials` |
| 解析が呼ぶ GitHub の API | 必須のチェック・閉じた Pull Request・課題 | 必須のチェックだけ |
| AI が回答する質問 | `test`・`issues`・`ndf_policies`・`materials`（測定が質問にしたもの） | `test`・`materials`（測定が質問にしたもの） |
| 指示書の案内 | 書き出しの出力に出る | 変わらない |
| 既存の設定に残る 6 項目 | 解析が値を保つ | 次の書き出しで取り除く |

測定が項目を質問にするのは、値を機械で決められないときである。たとえば `test` は、テストを走らせる手がかり（テストのツール、コマンド、指示書の記述）があるときだけ質問になり、無ければ質問にならない。

## 今の利用者への影響と互換

- 既存の `.ndf/project.json` に残る 6 項目は、NDF を新しいバージョンへ上げた後の最初の書き出しで無くなる。利用者は、そのときの差分を 1 回コミットする。人が手で直した値も取り除く（読む仕組みが無いため）
- 古い NDF は、6 項目の無い設定を読める（必須のキーは `version` だけである）。ただし古い NDF が書き出すと 6 項目が書き戻るので、チームで NDF のバージョンをそろえる

## なぜ困るか

- どの仕組みも使わない値が、チームで共有する設定に載る。`.ndf/` の変更はマージの前に人が確かめるので、確かめる量が増える
- 解析のたびに、使わない値のために GitHub の API のクォータ・解析の時間・AI の token を使う
- 設定に載っている値は、書き換えれば NDF の振る舞いが変わるように読める。実際は何も変わらない（例: `ndf_policies` を `false` にしても同じように動く）

## 目的

- 解析が書く設定の項目を、読み手（コード・Skill の手順・利用者）があるものだけにする
- 読み手の無い項目は、測るのも書くのもやめる

## 対象範囲

含む:
- `.ndf/project.json` の解析が書く項目（`languages`・`services`・`issues`・`checks`・`ndf_policies`・`instructions`・`materials`）ごとに読み手を数えること
- 読み手の無い項目と、その取得の処理を消すこと
- 既存の設定に残る値を、次の書き出しで取り除くこと
- 設定の形の定義・NDF が書くファイルの一覧・手順書・説明の文書・確定仕様・このリポジトリの `.ndf/project.json` を、新しい形に合わせること

含まない:
- #4516 が扱った解析の記録・計測値・`test_duration`・`ci.workflows`・`ci.provider`
- `test`・`ci`・`materials` の形と中身を変えること
- 人が書くセクション（`docs`・`workflow`・`toolchain`・`budget`）と、ブランチ戦略の設定（`branch_strategy`）
- 指示書の設定（`.ndf/instructions.json`）。`.ndf/project.json` の `instructions` 項目とは別のファイルである
- [#2239](https://github.com/devbasex/ai-plugins/issues/2239)（別のリポジトリへの起票を、起票先の流儀に従わせる）の実装と本文の書き換え

## 前提

- 前提 1: このリポジトリの外のプログラムは、読み手に数えない。NDF のコントラクト（`plugins/ndf/contracts/`）に、6 項目を外のサービスが読むという定めは見当たらない

## 受け入れ条件

- [ ] 1. 解析が書く 9 項目のうち `test` と `ci` を除く 7 項目ごとに、読み手（ファイルと行）か「読み手無し」を書いた表が課題の本文にある
- [ ] 2. 読み手の無い項目は、`ndf project measure` が測らず、`ndf project write` が書かない
- [ ] 3. `ndf project measure` の結果の項目に `languages`・`services`・`issues`・`checks`・`ndf_policies`・`instructions` が無い
- [ ] 4. 設定の無いリポジトリで書き出した `.ndf/project.json` に、3 の 6 キーが無く、`analysis.written` にもその指紋が無い
- [ ] 5. 測定の間に呼ぶ GitHub の API は必須のチェック（`rules/branches`）だけで、閉じた Pull Request（`pulls`）と課題（`issues`）を呼ばない
- [ ] 6. 同じリポジトリに対する `test` と `materials` の測定の結果（値・候補・根拠）と `ci` の値は、変更の前と同じになる。compose のサービス名は、テストの質問の候補に今と同じく載る
- [ ] 7. 指示書が 1 つも無いリポジトリと、`NDF_PLUGIN_GUIDE_START` のマーカーを持つファイルを指示書が読み込むリポジトリで、書き出しの出力に今と同じ「案内」の行が出る
- [ ] 8. 回答のファイルに `issues` か `ndf_policies` を書いて書き出すと、終了コード 0 で終わり、そのキーは「無視した回答」と表示されて設定に書かれない
- [ ] 9. 既存の `.ndf/project.json` に 3 の 6 キーのどれかが残っていると、`ndf project check` は理由を出して「古い」（終了コード 3）を返し、「壊れている」（終了コード 2）にしない
- [ ] 10. 9 の状態から書き出すと、人が手で直した値も含めて 6 キーとその指紋が消える。残る項目について、解析が書いた値か人が直した値かの見分けは変わらない
- [ ] 11. 10 の後に、同じ入力でもう 1 回書き出すと、`.ndf/project.json` は変わらず、`ndf project check` は「新しい」（終了コード 0）を返す
- [ ] 12. 人が書いた `materials` は、10 の書き出しの後も同じ値で残る
- [ ] 13. 設定の形の定義（スキーマ）・NDF が書くファイルの一覧（`ndf-files.json`）のキーの並び・`project-analysis.md`・`ndf-settings.md`・確定仕様 `ndf-project-analysis-record.md` が新しい形を書き、追跡している `.ndf/project.json` に 6 キーが無い。`ndf check` と `python3 scripts/check-skill-frontmatter.py` が通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`.ndf/project.json` の形、`ndf project measure` の結果の項目、回答できるキーから 6 項目が無くなる。`互換なし`（消した項目を書き続ける経路は持たない） |
| データ | 既存の `.ndf/project.json` から 6 項目を次の書き出しで取り除く。移行のコマンドは持たない |
| 既存の振る舞い | 解析が呼ぶ GitHub の API と、AI が回答する質問が減る。NDF の工程の進み方は変わらない |
| ほかの課題 | #2239 の受け入れ条件は、起票先の `issues.primary` を読むことを前提にしている。この変更の後に #2239 を実装するときは、読む仕組みと一緒に、その値の設定の形と作り方を決め直す |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 設定が古いと判定した | 開発の作業の開始（`ndf project check`） | 判定できなければ、今の設定のまま作業を進める | — |
| E2 | リポジトリを測った | E1 の後の `ndf project measure` | 「解析できなかった」と示して作業を進める。設定は変わらず、残った 6 項目は次の作業でまた E1 になる | E1 |
| E3 | 設定を書き出し、6 項目を取り除いた | `ndf project write` | 今の書き出しと同じく、途中で失敗すれば次の判定が「古い」になる | E2 |
| E4 | 差分をコミットした | 利用者の操作 | コミットしなければ、チームの設定に 6 項目が残り、別の clone でも E1〜E3 が起きる | E3 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests -q -n 4`（全体は CI の `pytest`） |
| 静的解析・型検査 | `ndf check`・`python3 scripts/check-skill-frontmatter.py`・`bash scripts/check-lint.sh` |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 読み手には利用者も含める | 起票時の本文の「目的」 | 利用者は、手順書や出力がその値を見て何かするよう求めている場合だけ読み手に数える。ファイルを開けば読める、というだけで数えると、すべての項目が残るため |
| S2 | 読み手は項目の単位で数える | 起票時の本文の「対象範囲」 | 受け入れ条件 1。残す `materials` の中のキーは、`research.md` の表がすべて読み方を定めているので分けない |
| S3 | 既存の設定に残る値は、人が手で直した値も取り除く | この要求の作成で追加（#4516 の「旧い形を読み続ける仕組みは持たない」） | 受け入れ条件 9・10。取り除く差分は git に出るので、利用者はコミットの前に確かめられる |
| S4 | `issues` は、[#2239](https://github.com/devbasex/ai-plugins/issues/2239) が将来読む予定でも消す | この要求の作成で追加（確定仕様の「後で使う仕組みを作るときは、その仕組みと一緒に測る処理を作る」） | 受け入れ条件 3・4・5。今は読み手が無いため |
| S5 | `instructions` は項目を消し、指示書の案内は残す | この要求の作成で追加 | 受け入れ条件 7。案内は利用者が読んで指示書を直すための出力で、今の約束である |

## リクエスト（原文）

> 使われない情報を取得・保存する必要はないです

（書き手: 利用者、日付: 2026-11-25。[PR #4518 のコメント](https://github.com/devbasex/ai-plugins/pull/4518#issuecomment-7162632854)）

<details>
<summary>起票時の本文</summary>

> #4516 の設計承認ゲートで、利用者が「使われない情報を取得・保存する必要はないです」と指摘した（https://github.com/devbasex/ai-plugins/pull/4518#issuecomment-7162632854 ）。#4516 は、実行ごとに変わる計測値（ファイル数・CI の実行時間・ジョブ数）と、読み手の無い `ci.workflows`・`ci.provider` を測るのも書くのもやめる。
>
> 同じ基準で見ると、`.ndf/project.json` の解析が書くほかの項目にも、コードの読み手が見つからないものがある。#4516 の設計の時点で、`plugins/ndf/src` を `grep -rn "languages"` で探すと、`languages`（`name`・`frameworks`・`version`）を読むのは解析と書き出しだけだった。`services`・`checks` などのほかの項目は、#4516 では読み手を数えていない。
>
> 種類: 機能追加（解析が測って書く設定の項目を、読み手のあるものだけにする）
>
> 目的: 解析が書く設定の項目を、読み手（コード・Skill の手順・利用者）があるものだけにする。読み手の無い項目は、測るのも書くのもやめる
>
> 含む: `.ndf/project.json` の解析が書く項目（`languages`・`services`・`issues`・`checks`・`ndf_policies`・`instructions`・`materials`）ごとに読み手を数えること。読み手の無い項目と、その取得の処理を消すこと
>
> 含まない: #4516 が扱う解析の記録・計測値・`test_duration`・`ci.workflows`・`ci.provider`
>
> 受け入れ条件: 1. 解析が書く項目ごとに、読み手（ファイルと行）か「読み手無し」を書いた表が課題の本文にある。2. 読み手の無い項目は、`ndf project measure` が測らず、`ndf project write` が書かない

（書き手: 起票者、日付: 不明）

</details>
