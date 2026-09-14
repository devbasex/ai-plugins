# #610 / #495: 主ディレクトリの HEAD を開始時に動かさない / 宣言を共有と個人に分ける

設計は [issue-495-610-design.md](issue-495-610-design.md) にある。この文書は「何を満たすか」だけを扱う。

**2 つの課題は 2 本の Pull Request で直す。** #610 は宣言の分け方に依存せずに直せる。そのため
#495 を待たずに先に出す（設計文書の決定 1）。受け入れ条件も課題ごとに分ける。

## 目的

- 並列に動くエージェントのどれが開始・再開しても、主ディレクトリの HEAD が動かない
- 主ディレクトリの追従を使いたい利用者は、宣言で有効にできる
- ポートの帯やローカル環境の持ち込み物を、差分に載せずに各自の値へ変えられる
- 宛先の検査・作業ツリーの起点・案内を出さないパスは、clone した全員で同じ値のまま残る

## 対象範囲

含む:

- `worktree-session.sh` の追従を宣言の `follow_branch` で有効にする形へ変える（#610）
- 共通層の `wt_declaration` に個人の宣言の重ね合わせを足す（#495）
- `worktree-setup.sh` の `init` / `status` / `check` の個人の宣言の扱い（#495）
- 文書（SKILL.md / declaration.md / schema / README / AGENTS.md）とテスト

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| 追従の判定そのもの（`wt_follow_target` の規則）の変更 | 有効にしたときの挙動は変えない |
| 主ディレクトリが既に detached HEAD のまま残っている利用者への案内 | 変更前の追従で detached になった主ディレクトリは、変更後に自動では戻らない（設計文書の決定 4） |
| `scripts/check-pr-base.sh` と、起点をインラインで読む 5 つの Skill の変更 | 共有の宣言だけを読む形のままで AC17 を満たす |
| `.ndf/projects.json` の個人化 | 前提 3 |
| `wt_declaration_stamp` の変更 | `worktree-guard.sh` の控えは上書きできない項目しか持たない（設計文書の決定 14） |
| クラス図・ER 図 | 型も永続データのスキーマも持たない（シェルスクリプトと JSON の宣言ファイル）。宣言の形の変更はスキーマファイルと設計文書の「入出力の契約」が持つ |
| `CHANGELOG.md` と版数 | 配布の工程が書く |

## 受け入れ条件（#610）

既定で動かさない:

- [ ] AC1: `follow_branch` を書かない宣言で、ブランチを持つ開発用の作業ツリーが 1 つある。このとき
  開始時の hook を実行しても、主ディレクトリの HEAD（ブランチ名とコミット）が変わらず、reflog の行数が増えない
- [ ] AC2: `follow_branch` を書かない宣言で、主ディレクトリが起点の古いコミットで detached HEAD である。
  このとき作業ツリーが 0 個でも 2 個でも HEAD が変わらない
- [ ] AC3: 作業ツリーを 0 → 1 → 2 → ブランチ持ち 1 個 + detached 1 個と変えながら、開始時の hook を
  4 回実行する。reflog に `checkout:` の行が 1 行も増えない（issue の並列の事象の再現）
- [ ] AC4: agy の `PreInvocation`（`invocationNum` 0）と、事象名を持たない入力（Kiro の `agentSpawn`）
  でも AC1 が成り立つ
- [ ] AC5: `follow_branch` が `false` / `"true"` / `1` / `null` のとき、AC1 と同じく HEAD が変わらない
- [ ] AC6: 追従しないとき、開始時の hook は origin へ通信しない。origin を到達できない URL にした木で、
  `FETCH_HEAD` が作られない

有効にしたときに変わらない:

- [ ] AC7: `follow_branch: true` のとき、既存の追従のテストが、宣言に `follow_branch: true` を足しただけで
  通る。対象は 1 個へ detach、0 個・複数で起点へ、未コミット変更で止まる、レビュー用の作業ツリーを
  数えない、起点の取得のテストである

退行しない:

- [ ] AC8: 主ディレクトリに追跡対象の未コミット変更があるとき、`follow_branch` の値によらず、変更の
  件数と一覧が開始時の出力に入る
- [ ] AC9: 開始時の hook は、どの経路でも終了コード 0 で終わる

文書:

- [ ] AC10: 次の 5 か所が、既定で追従しないことと `follow_branch` で有効にする方法を書く。
  `grep -rn "ブランチ追従が動きます" plugins/ndf` が 0 件になる

  | 場所 |
  | --- |
  | `worktree` の SKILL.md「主ディレクトリのブランチ」 |
  | `references/declaration.md` |
  | `schemas/worktree.schema.json` |
  | `plugins/ndf/README.md` の hook の表 |
  | `init` の完了の文言 |

## 受け入れ条件（#495: 反映される値と、変わらない運用）

個人の値が反映される:

- [ ] AC11: 共有の `testenv.port_band` が `[20000, 29999]`、個人が `[40000, 40999]` とする。
  `worktree-testenv.sh env` が割り当てるポートが 40000 以上 40999 以下になる
- [ ] AC12: 個人の `testenv.port_roles` が `{"db": 5}` だけとする。重ね合わせた宣言の `db` は 5 になり、
  共有にある他の役割は共有の値のまま残る（オブジェクトは深く併合する）
- [ ] AC13: 個人の `localenv.copy_from_main` が `["node_modules"]` とする。重ね合わせた宣言の値は
  `["node_modules"]` だけになる（配列は置き換える。継ぎ足さない）
- [ ] AC14: 個人の `follow_branch: true` で、共有に書かれていなくても追従が有効になる

リポジトリの運用は個人の宣言で変わらない:

- [ ] AC15: 個人の宣言に `base_branch` / `production_branch` / `guard` / `version` / 未知の項目を書く。
  `wt_base_branch` / `wt_production_branch` / `wt_allow_paths` の出力が、共有の宣言だけのときと一致する
- [ ] AC16: 個人の `testenv.expose` は反映されない。共有が `expose.enabled: false` なら、重ね合わせた
  宣言でも `false` のままである
- [ ] AC17: 個人の宣言に `base_branch: "main"` を書いた木を作る。`scripts/check-pr-base.sh` の判定と、
  起点をインラインで読む 5 つの Skill の解決結果が、個人の宣言が無い木と一致する

## 受け入れ条件（#495: 壊れた宣言・追跡・文書）

壊れた個人の宣言:

- [ ] AC18: 個人の宣言が次の 5 つの形のどれでも、hook と `worktree-localenv.sh` / `worktree-testenv.sh`
  は共有の宣言だけで動く。終了コードは個人の宣言が無いときと同じになる。形は「JSON として壊れている」
  「空」「最上位が配列」「`version` が 1 でない」「ディレクトリ」である
- [ ] AC19: AC18 の状態で、`worktree-setup.sh status` と `check` が「個人の宣言: 読めません」の行を出す。
  `check` の終了コードは個人の宣言が無いときと同じである
- [ ] AC20: 個人の宣言で `testenv` / `localenv` がオブジェクトでない、または `follow_branch` が真偽値でない。
  このときその項目だけが反映されず、他の上書きできる項目は反映される
- [ ] AC21: AC15・AC16・AC20 で反映しなかった項目の名前が、`status` と `check` の 1 行に並ぶ

共有の宣言が無い・読めない:

- [ ] AC22: 共有の宣言が無く、個人の宣言だけがある。hook は何も出力せず、`check` は終了コード 2 で
  終わり、`status` は「個人の宣言: 使っていません」の行を出す
- [ ] AC23: 共有の宣言が読めないとき、個人の宣言の状態によらず `check` は終了コード 3 で終わる

追跡しない:

- [ ] AC24: `init` が共有の宣言を作ると、`.ndf/.gitignore` も作られる。`git check-ignore -q
  .ndf/worktree.local.json` は終了コード 0、`git check-ignore -q .ndf/worktree.json` は 1 で終わる
- [ ] AC25: 既にある `.ndf/.gitignore` を `init` は上書きしない
- [ ] AC26: 個人の宣言があり追跡から外れていないとき、`status` が登録の案内を 1 行出す
- [ ] AC27: このリポジトリに `.ndf/.gitignore` が追跡され、`git check-ignore -q .ndf/worktree.local.json`
  が終了コード 0 で終わる

退行しない:

- [ ] AC28: 個人の宣言ファイルが無いとき、`status` と `check` の標準出力と終了コードが変更前と同じである

文書:

- [ ] AC29: `references/declaration.md` に個人の宣言の節がある。置き場所・上書きできる項目・重ね合わせの
  規則（深い併合 / 配列の置換 / 反映しない項目 / 壊れたときの扱い）が書かれている。`worktree` の
  SKILL.md の手順 0 が、個人の値はコミットしないファイルへ書くことを書く

## 受け入れ条件（両方）

- [ ] AC30: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] AC31: `bash scripts/build-runtime-plugins.sh --check` と `claude plugin validate .` が終了コード 0 で終わる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 開始時の hook の実行時間が、変更前を上回らない（追従しない既定では git の checkout と origin への通信が消える） |
| 運用・保守性 | 個人の宣言を無視したとき、`status` と `check` の出力だけで無視した理由と項目名が分かる |
| セキュリティ | 追跡されない個人の宣言から、外部への公開（`testenv.expose`）を有効にできない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 追従を前提にしていた利用者 | 既定で追従しなくなる。使うには `follow_branch: true` を書く |
| 宣言の形 | 最上位の `follow_branch`（真偽値）と、個人の宣言ファイルが増える。`version` は上げない（項目の追加） |
| `worktree-setup.sh` の出力 | 個人の宣言ファイルがあるときだけ、`status` / `check` に行が増える。`check` の終了コードは変わらない |
| 継続的統合 | 変わらない（共有の宣言だけを読む） |

## 前提

| # | 前提 |
| --- | --- |
| 1 | 並列実行の中で開始時の hook を発火させたプロセスは特定しない。直し方は発火元に依存しない（4 ランタイムとも同じ `worktree-session.sh` を呼ぶ） |
| 2 | 担当 C の #573（`init` が読めない宣言を検知する）が先に `develop` へ入る。#495 の `init` の変更はその上に載せる |
| 3 | `.ndf/projects.json` には個人の環境に依存する項目が無い。個人の宣言は `worktree.json` だけに置く |
| 4 | このリポジトリの `.ndf/worktree.json` には `follow_branch` を書かない。つまりこのリポジトリでは追従しない |

前提 1 の根拠: Claude Code の結線は `startup` だけに当たり、サブエージェントの記録 443 件に
SessionStart の発火は 0 件だった。主ディレクトリで起動した子の CLI プロセス（`claude -p` / codex /
agy / kiro）か、新しいセッションが発火させたと読む。

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 定義の検査 | `claude plugin validate .` と `python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 主ディレクトリの `git reflog -n 5` を、並列の担当を起動した前後で見比べる（AC3 の実機） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 判定は `lib/worktree-common.sh` に置き、入口のスクリプトは入力と出力の整形だけを持つ（`worktree-session.sh` の冒頭の注記） |
| コーディング規約 | 外部コマンドの挙動は書く前に実行して確かめる（`AGENTS.md` の DO） |
| テスト戦略 | 一時リポジトリで hook とスクリプトを実プロセスとして実行する既存の形（`skills/worktree/tests/`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、配布物の同期の検査 |
| 確認してから行う | 追従を既定で止めること自体の是非（#146 の依頼で入った機能のため） |
| 行わない | 追従の判定規則の変更、`check-pr-base.sh` の入力の変更 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 主ディレクトリ | clone したディレクトリ（`git rev-parse --git-common-dir` の親） |
| 追従 | 開始時の hook が主ディレクトリの HEAD を `git checkout --detach` で動かすこと |
| 開始時の hook | `worktree-session.sh`。Claude Code / Codex の `SessionStart`、Kiro の `agentSpawn`、agy の `PreInvocation`（通し番号 0） |
| 共有の宣言 | `.ndf/worktree.json`。追跡する |
| 個人の宣言 | `.ndf/worktree.local.json`。追跡しない |
| 重ね合わせた宣言 | `wt_declaration` が返す、共有の宣言に個人の宣言を反映した JSON |
| 上書きできる項目 | 個人の宣言から反映する最上位の項目。`localenv` / `testenv` / `follow_branch` の 3 つ |

## 依頼（原文）

### #610

> 複数のエージェントが同じリポジトリで並行して動くと、NDF の `worktree-session.sh`（SessionStart hook）が
> 主ディレクトリ（`/work/ai-plugins`）の HEAD を**作業の途中で**別のコミットへ切り替える。
>
> `worktree-session.sh` は、主ディレクトリで開始時に発火すると `wt_follow_target` の判定に従う。
> ブランチを持つ開発用の作業ツリーが 1 つならそのコミットへ `git checkout --detach` し、0 個か複数なら
> 起点ブランチへ合わせる。**稼働中の作業ツリーの数はセッションごとに変わり、そのたびに主ディレクトリの
> HEAD が動く。**
>
> 追従は「1 人の開発者が 1 つの作業ツリーで作業する」前提の設計で、並列のエージェント実行ではその前提が
> 成り立たない。宣言で追従を止める手段（例: `.ndf/worktree.json` の設定）が無い

### #495

> **`.ndf/` の宣言ファイルには、全員で共有すべき項目と、個人の環境に依存する項目が混在して
> いる。** いまは 2 つとも git の追跡対象で、後者を各自の値にできない。
>
> | 案 | 中身 | 影響 |
> | --- | --- | --- |
> | A. 全部を `.gitignore` へ入れる | いまの 2 ファイルを追跡対象から外す | **宛先の検査が働かなくなる。** |
> | B. 個人の項目だけ別ファイルへ分ける | `.ndf/worktree.json`（共有・追跡する）と `.ndf/worktree.local.json`（個人・追跡しない）に分け、後者が前者を上書きする | 読み取りの共通層に重ね合わせが要る |
> | C. 現状のまま | 混在を許し、個人の値が要るときは各自が戻す | ローカル環境の分離を使うリポジトリで手戻りが残る |
>
> **B が素直に見える。** ただし**重ね合わせの規則を決める必要がある**（配列は置き換えるのか継ぎ足すのか、
> `guard.allow_paths` を個人が広げてよいのか）。

（両 issue の本文から抜粋。全文は `gh issue view 610` / `gh issue view 495`）
