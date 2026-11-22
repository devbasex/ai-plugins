# cross-refactoring: 担当が無視の規則を越えて入れた作業ファイルを、公開の前のコミットから除く（#3500）

cross-refactoring のコードを書く工程の担当が `git add -f` で作業ファイル（`.cross_refactoring/` の下の結果ファイルと進捗のファイル）をコミットすると、オーケストレーターはそのまま公開するか、正しい直しを範囲外として止める。担当の起動 1 回ごとに、起動の後に増えたコミットから作業ファイルだけを除いて作り直し、メッセージとほかの変更はそのまま残す。差し戻し・`git filter-branch`・`git filter-repo` などの採らなかった案は「決定の記録」に、git の一次情報は「外部の調査」にある。

## 今の姿

何が起きたか・目的・受け入れ条件は要求仕様（[issue-3500-requirements.md](./issue-3500-requirements.md)、正は #3500 の本文）にある。この文書は「どう解くか」と「なぜその解き方か」だけを書く。

```mermaid
flowchart LR
  D["drive._launch_status<br/>（5 工程の起動の口）"] --> L["launch.run<br/>担当を起動し結果ファイルを回収"]
  L --> F["修正担当（claude）<br/>work/ でコミット"]
  F -.->|"git add -f"| X[".cross_refactoring/ の<br/>作業ファイルがコミットに入る"]
  D --> V["検証・最終ゲート・<br/>フレーキーの検査"]
  V --> P["公開（push）"]
  X -.-> V
```

設計に要る今の構造は次のとおりである。

- 担当の起動はどの工程も `drive._launch_status` を通る（`_code`・`_launch`・最終ゲートの `fix`・フレーキーの直しのどれも）。コードを書く 5 工程（表 `loop-phases` の coding が真）は書き込み用の作業ディレクトリ `work/` で動く
- 作業ファイルの置き場は `work/.cross_refactoring/` で、除外は共通の git ディレクトリの `info/exclude` の 1 行だけである（`workspace._exclude_results`）。除外は追跡に入るのを止めないので、`git add -f` で入り、入った後は追跡され続ける（要求仕様の「今の姿」の実測）
- 起動の後にコミットの中身を見るのは、フレーキーの直し（`flaky_decide.violations`）と最終ゲートの直し（`final_gate_decide.verify_final_fix`）だけで、どちらも作業ファイルを見分けない。テストの追加・実装・修正の 3 工程は何も見ない
- **根本原因**: 作業ファイルを差分に入れない約束を、除外の 1 行という「既定の `git add` が拾わない」仕組みだけで守っていて、担当がそれを越えたときに戻す所が無いこと

## 文脈と制約

- 担当の作るコミットは、起動の後で公開の前である。公開（push）はオーケストレーターだけが、検証を通した後に行う（`prompts/fix.md` の「守ること」）。最終ゲートの直しも、push 済みの HEAD の上に担当が新しいコミットを積む形である。起動の前の HEAD より後のコミットは、作り直しても force push が要らない
- 作業ディレクトリには、担当が残した未コミットの変更がありうる。作り直しは作業ツリーと未コミットの変更を変えてはいけない（受け入れ条件 3）
- 担当が書いた結果ファイルは、`launch.run` が回収先へ移した後である。進捗のファイルは作業ディレクトリに残る
- コミットのメッセージのトレーラー（`Item-Id`・`Impl-Runtime`・`Impl-Model`・`Flaky-Fix`）は、後の検査と記録が読む。作り直しで変えてはいけない（受け入れ条件 2）
- git の起動はアダプター（`ndf.adapters.git`）が持つ。今の `Git.run` は環境変数と標準入力を渡せない

## 解き方の全体像

```mermaid
flowchart LR
  D["drive._launch_status"] -->|"起動の前の HEAD を控える"| L["launch.run"]
  L --> R["Git.drop_paths(起動の前の HEAD, .cross_refactoring)<br/>当たるコミットだけを作り直す"]
  R -->|"作り直した"| S["state.json の dropped_results と<br/>標準エラーの 1 行"]
  R --> V["検証・最終ゲート・フレーキーの検査<br/>（作業ファイルを見ない）"]
  V --> P["公開（push）"]
```

- 担当の起動 1 回ごとに、`_launch_status` が起動の前の HEAD を控え、起動の後に `Git.drop_paths` を呼ぶ。読み取りの工程の担当は `work/` でコミットを作らないので、当たるコミットが無く何もしない
- `Git.drop_paths` は、起動の前の HEAD から今の HEAD までに `.cross_refactoring/` の下のパスを含むコミットがあるときだけ、そのコミットとそれより後のコミットを、作業ファイルを除いた木で作り直す。メッセージ・作者・日付はそのまま、作業ファイルだけを変えたコミットは落とす。作業ツリーと未コミットの変更には触れない
- 作り直したときは、工程・起動の名前・元のコミットを状態ファイルに残し、標準エラーへ 1 行出す
- 後に続くフレーキーの検査・最終ゲートの範囲の検査・検証は、作業ファイルの無いコミットだけを見る

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `Git.drop_paths`（`ndf.adapters.git`、新設） | 起点から HEAD までのコミットから、指定した最上位のディレクトリの下のパスを除いて作り直し、HEAD と索引を合わせる。当たるコミットが無ければ何もしない。作り直した元のコミットを返す |
| `drive._launch_status`（変える） | 起動の前の HEAD を控え、起動の後に `Git.drop_paths` へ `launch_decide.RESULT_DIR` を渡す。作り直したら状態ファイルと標準エラーへ残す |
| `docs/01-state-and-propose.md`（cross-refactoring の Skill の文書、変える） | 作業場所の箇条と状態ファイルの表に、除いた記録を足す |

```mermaid
flowchart TB
  subgraph crossrefactor["ndf.domains.crossrefactor"]
    DR["drive._launch_status"]
    LD["launch_decide.RESULT_DIR"]
  end
  subgraph adapters["ndf.adapters"]
    G["git.Git.drop_paths"]
  end
  DR --> LD
  DR --> G
  G --> GIT[("work/ の git<br/>（共通の git ディレクトリ）")]
```

## 処理の流れ

```mermaid
sequenceDiagram
  participant D as drive._launch_status
  participant L as launch.run
  participant G as Git.drop_paths
  participant S as state.json
  D->>D: before = HEAD
  D->>L: 担当を起動し、結果ファイルを回収
  L-->>D: 結果・監視の状態
  D->>G: drop_paths(before, ".cross_refactoring")
  alt before..HEAD に作業ファイルを含むコミットが無い
    G-->>D: []（何もしない）
  else 含むコミットがある
    G->>G: 古い順に、作業ファイルを除いた木でコミットを作り直す<br/>（空になったコミットは落とし、親を付け替える）
    G->>G: HEAD を作り直した先へ移し、索引から作業ファイルを外す
    G-->>D: 作り直した元のコミット
    D->>S: dropped_results へ 1 行足す
    D->>D: 標準エラーへ 1 行
  end
  D->>D: 所要の記録・安全網の判定（今のまま）
```

作り直しが失敗したとき（git の誤り）は `GitError` が上がり、今の起動の失敗と同じく実行が止まる。HEAD を移すのは作り直しがすべて済んだ後の 1 回だけなので、途中で止まっても HEAD は元のままである。

## 決定の記録

### 決定 1: 5 工程を 1 か所で守るため、担当の起動の口（`_launch_status`）で起動の後に除く

- **質問**: どこで作業ファイルを見つけて外すか
- **選んだ案**: すべての担当の起動が通る `drive._launch_status` で、起動の前の HEAD を控え、起動の後に除く
- 採らなかった案: 手順書（`prompts/*.md`）に「`git add -f` を使わない」と書く（担当が守らなければ同じことが起き、守ったかを確かめる所が無い。要求仕様の起票者の推定 S2）
- 採らなかった案: 工程ごとの検査（`flaky_decide.violations`・`verify_final_fix`）に作業ファイルの扱いを足す（テストの追加・実装・修正の 3 工程には検査が無く、5 工程に別々の規則を置くことになる）
- 採らなかった案: 公開（push）の直前に 1 回だけ除く（最終ゲートの直しとフレーキーの直しの検査は公開の前に走り、作業ファイルで正しい直しを止める）
- **代償**: 担当の起動のたびに `git rev-list` を 1 回打つ（当たらなければそれだけで終わる）
- 根拠: Value 7「不具合が根本原因から直される」・Value 11「重複と冗長が無い」（MVV 版 11）

### 決定 2: 担当の正しい変更を失わないため、作業ファイルだけを除いて続ける

- **質問**: 作業ファイルの入ったコミットを見つけたら、どう扱うか
- **選んだ案**: そのコミットから作業ファイルだけを除いて作り直し、項目の進みはそのまま続ける。作業ファイルだけを変えたコミットは落とす
- 採らなかった案: 起動の前まで取り消して担当へ変更を要求する（フレーキーの直しの形。要求仕様の起票者の推定 S3。正しい変更のために担当をもう 1 回起動し、時間と費用が倍になる）
- 採らなかった案: 作業ファイルを外すコミットを 1 つ足す（差分の合計からは消えるが、Pull Request のコミットの並びに作業ファイルが残る）
- 採らなかった案: 作業ファイルだけを変えたコミットを空のまま残す（Pull Request に中身の無いコミットが並ぶ）
- **代償**: 当たったコミットとそれより後のコミットの SHA が変わる。どれも公開の前で、起動の前の HEAD より後のものだけである
- 根拠: Value 2「安く使える」・Value 6「使うほど散らからない」（MVV 版 11）

### 決定 3: 作業ツリーに触れずにコミットだけを作り直すため、git の plumbing で作り直す

- **質問**: 公開の前のコミットから、あるディレクトリの下のパスをどの手段で除くか
- **選んだ案**: 一時の索引で各コミットの木を読み（`read-tree`）、作業ファイルを外して（`rm --cached`）木を書き（`write-tree`）、元のメッセージ・作者・日付で `commit-tree` する。最後に `update-ref` で HEAD を移し、本来の索引から作業ファイルを外す
- 採らなかった案: `git filter-branch --index-filter`（git の文書が使わないよう勧める。未コミットの変更があると「Cannot rewrite branches: You have unstaged changes.」で断る。作り直した後に作業ツリーの作業ファイルを消し、`refs/original/HEAD` を残す。2026-11-22 に実測）
- 採らなかった案: `git filter-repo`（リポジトリ全体の履歴を書き換える道具で、既定で新しい clone を求める。依存が 1 つ増え、この環境に入っていない。LIB1）
- 採らなかった案: `git rebase --exec` で各コミットを直す（作業ファイルを入れた後のコミットが進捗のファイルを書き換えていると、削除と変更の衝突で止まりうる。未コミットの変更には `--autostash` が要る。実測はしていない）
- **代償**: 作り直しの手順（約 20 行）を自前で持つ。署名付きのコミットの署名は外れる（担当は署名しない）
- 作り方: 自前（git の plumbing の組み合わせ）
- 比べた候補: LIB1（git filter-repo）・LIB2（git filter-branch）・LIB3（git rebase --exec）
- 根拠: Value 12「調べて分析した、実績のある最新のやり方で作られる」・Value 13「確かめたことだけが済んだとされる」（MVV 版 11）

### 決定 4: 作り直しを git の操作の置き場に置くため、アダプターの `Git` に 1 つのメソッドとして足す

- **質問**: 作り直しの手順をどこに置くか
- **選んだ案**: `ndf.adapters.git.Git.drop_paths(base, top)` として足す。除くディレクトリは引数で受け、cross-refactoring の置き場の名前（`launch_decide.RESULT_DIR`）は `drive` が渡す。環境変数（一時の索引・作者）はこのメソッドの中で git の起動に渡す
- 採らなかった案: `crossrefactor.workspace` に置く（git を直接起動するコードがドメインに入る。今の `workspace` はアダプターの `Git.run` しか使わない）
- 採らなかった案: cross-review と共有する所（`ndf.core.converge`）に置く（cross-review は状態の保存先をレビューする worktree の外に置き、中なら止めるため、同じ経路が無い。使い手が 1 つの共有は置かない）
- **代償**: アダプターの `Git.run` が持たない環境変数の受け渡しを、このメソッドだけが持つ
- 根拠: Value 9「責務の分かれた設計で作られる」（MVV 版 11）

### 決定 5: 起きた回数を後から数えられるよう、状態ファイルと標準エラーへ残す

- **質問**: 除いたことをどこへ残すか
- **選んだ案**: 状態ファイルの新しいキー `dropped_results` に、1 回ごとに `{"phase": <工程>, "stem": <起動の名前>, "commits": [<元のコミット>...]}` を足し、標準エラーへ「<工程> の担当がコミットした作業ファイル（.cross_refactoring/）を <N> 件のコミットから除いた」を 1 行出す
- 採らなかった案: 完了報告（`report.md`）に節を足す（読み手は Pull Request を見る人で、担当の操作の癖は直す人が数える材料である。報告の形を変えるほどの量ではない）
- 採らなかった案: 残さない（担当が除外を越える頻度を数えられず、手順書を直すかを決める材料が無くなる）
- **代償**: 状態ファイルのキーが 1 つ増える
- 根拠: Value 14「判断の理由が残り、語がそろっている」（MVV 版 11）

## 影響と危険

- **働く範囲**: この PR は設計文書だけで、振る舞いは変わらない。実装の後は、利用先のリポジトリでも `ndf refactor run` を打つすべての cross-refactoring に効く
- **プロジェクトごとに違うもの**: 無い（置き場の名前と作り直しは NDF のコードが持つ）
- **当たるモード**: cross-refactoring を通すすべてのモード
- **同じファイルを触る別の変更**: レーン lcrfast が cross-review の #4013・#4014・#4015・#4030 を実装中である。開いている PR #4186 が触るのは `ndf.domains.crossreview`・`ndf.adapters.claude` などで、この設計の触るファイル（`ndf.adapters.git`・`ndf.domains.crossrefactor.drive`）と重ならない。cross-review は作業ファイルを worktree の外に置くため、共有するロジックも無い（決定 4）。順番の制約は無い
- **壊れうる箇所**:
  - 担当の起動の後で HEAD の SHA が変わる。起動の後に控えた SHA を使う所（`phase_decide.on_no_result` の `HEAD`、フレーキーの直しの `git log base..HEAD`、最終ゲートの `changed_by_commit`）は、どれも作り直しの後に読むので影響しない。作り直しは `launch.run` の直後で、`_launch_status` が返る前に済ませる
  - 安全網の上限で止まる起動（`DriveStopped`）でも、止める前に除く。打ち直しは項目の起点からやり直すため、残ったコミットは使われないが、作業ファイルを残さないため
  - 項目の起点（`item["start"]`）・工程の起点（`phases[].base_sha`）は起動の前の HEAD 以前を指すので、作り直しの範囲に入らない
- **手順書の `Item-Id` の検査がコードに無いこと**は別の欠けで、範囲外として #4195 に起票した（要求仕様の「対象範囲」）

### 代表のリポジトリでの振る舞い

| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |
| --- | --- | --- | --- |
| Web サービス（nd-factory） | 実装の PR に cross-refactoring を流す | 担当が作業ファイルを入れても、公開される PR のコミットに作業ファイルが無い | 無し |
| モバイルアプリ | 同上 | 同上 | 無し |
| 業務文書だけ | cross-refactoring を流さない（コードの範囲が無い） | 変わらない | 無し |

### ai-plugins だけの事情

- 無し（置き場の名前と作り直しは NDF のコードが持ち、このリポジトリの宣言の値を使わない）

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1（5 工程で作業ファイルが残らない） | 担当の偽物が `git add -f` で `.cross_refactoring/` の下のファイルを含むコミットを作る。起動の後の `before..HEAD` のどのコミットにも作業ファイルが無い。工程は実装と最終ゲートの直しの 2 つで確かめる（5 工程は同じ口を通る） | `_launch_status` で除く呼び出しを外す・コーディングの工程だけに絞り忘れて別の口を通す |
| 2（メッセージ・作者・ほかの変更・並び） | 作り直した後のコミットのメッセージ（トレーラーを含む）・作者・作業ファイル以外の木が元と同じで、並びも同じ。作業ファイルだけを変えたコミットは無くなる | 作者や日付の環境変数を渡し忘れる・メッセージを再整形する・空のコミットを残す |
| 3（未コミットの変更と作業ファイルが残る） | 作業ツリーに未コミットの変更と進捗のファイルがある状態で除く。除いた後も変更とファイルが残り、`git status` で作業ファイルが無視の対象に戻る | 作業ツリーを checkout し直す（`filter-branch` の形）・索引から外し忘れて削除が索引に残る |
| 4（記録と標準エラー） | 除いた起動で `dropped_results` に工程・起動の名前・元のコミットが 1 行増え、標準エラーに 1 行出る | 記録を書き忘れる・当たらない起動でも行を足す |
| 5（最終ゲートとフレーキーで止めない） | 最終ゲートの直しで担当が作業ファイルと範囲の中の直しを入れても、範囲外で止まらない。フレーキーの直しで作業ファイルを入れても `non_test` の違反にならない | 除くのを工程の検査の後へ置く |
| 6（入れなかった起動は作り直さない） | 作業ファイルを入れない起動の後で HEAD の SHA が起動の直後と同じ | 当たりの有無を見ずに毎回作り直す |
| 7（起動の前のコミットは作り直さない） | 起動の前の HEAD より前に作業ファイルを含むコミットがあっても、そのコミットは作り直さない | 範囲を `before..HEAD` でなくブランチ全体にする |

## どこを変えるか

（詳細設計はこのセクションに畳む。新設する関数 1・シグネチャを変える関数 0 のため）

| ファイル | 変える中身 | 決定 |
| --- | --- | --- |
| `plugins/ndf/src/ndf/adapters/git.py` | `Git.drop_paths(base: str, top: str) -> list[str]` を足す。入力: 起点のコミット `base` と最上位のディレクトリの名前 `top`。返り: 作り直した元のコミット（古い順。当たらなければ空）。`git rev-list base..HEAD -- <top>` が空なら何もしない。当たれば `base..HEAD` の全コミットを古い順に、一時の索引（`GIT_INDEX_FILE`）で木を読み `top` を外して書き、元の作者・日付（`GIT_AUTHOR_*`）とメッセージで `commit-tree` する。親は作り直した先へ付け替え、木が親と同じになったコミットは落とす。済んだら `update-ref HEAD` を 1 回打ち、本来の索引から `top` を外す（`rm -r --cached --ignore-unmatch`）。git の失敗は `GitError`。HEAD を移す前に失敗したら HEAD は変わらない | 3・4 |
| `plugins/ndf/src/ndf/domains/crossrefactor/drive.py` | `_launch_status`: `launch.run` の前に `work` の HEAD を控え、`launch.run` の直後（安全網の判定より前）に `run.work.drop_paths(before, launch_decide.RESULT_DIR)` を呼ぶ。返りが空でなければ `state["dropped_results"]` へ `{"phase", "stem", "commits"}` を足して保存し、標準エラーへ 1 行出す。シグネチャは変えない | 1・2・5 |
| `plugins/ndf/skills/cross-refactoring/docs/01-state-and-propose.md` | 「作業場所」の箇条に「担当が除外を越えて作業ファイルをコミットしたら、起動の後にそのコミットから除く」を足し、「状態ファイル」の表に `dropped_results` の行を足す | 5 |
| `plugins/ndf/tests/adapters/test_git.py` | `drop_paths` の受け入れ条件 2・3・6・7 の行 | — |
| `plugins/ndf/tests/domains/crossrefactor/test_drop_result_files.py`（新設） | 担当の偽物の起動での受け入れ条件 1・4・5 の行 | — |

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #3500 | 実装する | — | `plugins/ndf/src/ndf/adapters/git.py`、`plugins/ndf/src/ndf/domains/crossrefactor/drive.py`、`plugins/ndf/skills/cross-refactoring/docs/01-state-and-propose.md`、`plugins/ndf/tests/adapters/test_git.py`、`plugins/ndf/tests/domains/crossrefactor/test_drop_result_files.py` |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 除外の効く範囲と、履歴を作り直す手段の正しい使い方は git の文書が決める |
| 主要な製品 | 調べる | AI のコーディングの道具が自分の作業ファイルをどう扱うか |
| ライブラリとサービス | 調べる | 履歴から特定のパスを除く既存の道具 |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | gitignore | 「Files already tracked by Git are not affected」。除外は追跡済みのファイルに効かない | 一度入った作業ファイルは以後のコミットにも入り続けるので、除外の 1 行では守れない（根本原因） | https://git-scm.com/docs/gitignore （2026-11-22） |
| STD2 | git add | `-f, --force`: 「Allow adding otherwise ignored files」 | 除外を越える経路は `-f` で、担当が打てば止める手段が git の側に無い | https://git-scm.com/docs/git-add （2026-11-22） |
| STD3 | git filter-branch | 「has a plethora of pitfalls」として使わないよう勧め、`git filter-repo` を挙げる。`GIT_AUTHOR_*` は元のコミットから取る | 採らない（決定 3）。作者・日付を元のコミットから渡す形は倣う | https://git-scm.com/docs/git-filter-branch （2026-11-22） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | aider | 自分の作業ファイル（`.aider*`）を `.gitignore` へ足す（`--gitignore`、既定で有効）。除外だけで守り、越えたときに戻す仕組みは文書に無い | 今の NDF と同じ形で、同じ穴を持つ。除外に加えて、越えたときに戻す所を置く | https://aider.chat/docs/config/options.html （2026-11-22） |

### ライブラリとサービス

| # | 候補 | バージョン | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | git filter-repo | 2.47.0 | MIT | 2024-12-04 | 無し | 満たさない（リポジトリ全体の書き換えが前提で、依存が 1 つ増え、この環境に無い。`git filter-repo --version` が「not a git command」） | https://pypi.org/pypi/git-filter-repo/json （2026-11-22） |
| LIB2 | git filter-branch（git 同梱） | git 2.53.0 | GPL-2.0（git） | git に同梱 | 無し（文書が使わないよう勧める） | 満たさない（未コミットの変更があると断る。作業ツリーの作業ファイルを消す。`refs/original/HEAD` を残す。2026-11-22 の実測） | https://git-scm.com/docs/git-filter-branch （2026-11-22） |
| LIB3 | git rebase --exec（git 同梱） | git 2.53.0 | GPL-2.0（git） | git に同梱 | 無し | 満たさない（作業ファイルを後のコミットが書き換えていると衝突で止まる） | https://git-scm.com/docs/git-rebase （2026-11-22） |

調べた量: 候補 5 件・取得の試み 8 回。
