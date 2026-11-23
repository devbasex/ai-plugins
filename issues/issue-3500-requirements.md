## この課題の範囲（2026-11-23 15:5x JST、利用者の指示で上位のタスクにした）

**NDF が作るファイル群をどこに置くか（置き場の決まり）と、一時的なディレクトリ・ファイルをどうやって ignore するかを決める。** 利用者の指摘「`.ndf/` のファイル設置ルール自体が混乱している」を受けて、ignore の仕方だけでなく、置き場の決まりそのものをこの課題で扱う。

- 置き場の決まり: NDF が作るファイルとフォルダ（`.ndf/` の下と、`.worktrees/`・`.cross_refactoring/` のように根に作るもの）ごとに、中身が何に属するか（リポジトリ全体で共有する設定・メインディレクトリに 1 つの作業状態・worktree ごと・個人の設定・使い捨て）で単位と置き場を決め、パスを解くコードと置き場の一覧（`plugins/ndf/contracts/domains/locations.json`）と手順書をそろえる
- ignore の仕方: 追跡しないものの無視を、作る側が作るときに済ませる形にそろえる（下の「期待する結果」）
- 進め方: オーガナイザーが置き場の棚卸しと決まりの案を作り、利用者の判断を取ってから設計（PR #4197）をその決まりの上で書き直す。下の本文（「何が起きたか」以降）は、ignore の仕方に絞っていた時点の版で、決まりが決まった後に組み直す

### 利用者の決定（2026-11-23 16:0x JST）

1. **置き場は中身の持ち主で 4 種に分ける。**

   | 種 | 例 | 置き場 | 無視の仕方 |
   | --- | --- | --- | --- |
   | 追跡する設定 | `project.json`・`mvv.json`・`worktree.json` | 各 worktree の `.ndf/`（ブランチとともに PR で変える）。PR で差し替えられては困るキーだけ、キー単位でメインディレクトリから読むと宣言する | 追跡する |
   | 人が読む、ブランチをまたぐ作業状態 | 引継ぎ文書・計画（`plan.json`・`run.log`）・作業メモ・棚卸しの状態・個人設定 | メインディレクトリの `.ndf/` に 1 つ | フォルダは作るときに中へ `*` の `.gitignore`。個人設定のファイルは `.ndf/.gitignore` |
   | 機械だけが読む状態 | ステートマシン・ループ・錠・worktree の割り当て・cross 系の作業場所 | git の共通ディレクトリ（`.git/ndf/`）。外部サービスが読むもの（`loop.json` など）はパスを結果 JSON で渡す | 要らない |
   | worktree ごとの使い捨て | `.cross_refactoring/`・テストの出力 | 今の worktree | 作るときに中へ `*` の `.gitignore` |

   あわせて、置き場の一覧（`locations.json`）の起点の値を worktree・メインディレクトリ・git の共通ディレクトリの 3 つにし、無い行（`.git/ndf/`・`.worktrees/.ndf/`・`.cross_refactoring/`）を足し、書き手の無い行は消す。パスを求める関数を 1 つにし、置き場の名前を渡すと一覧の起点のとおりにパスを返す。残骸は決まりを入れた後に退避してから消す
2. **引継ぎ文書は、引き継ぐ対象のステートマシン（スプリント・レーン・計画）と同じデータベースに、その記録の 1 つとして持つ**（2026-11-23 16:1x JST の利用者の決定。最初の回答は「nd-factory のように複数のマシンで動く場合も検討して決定して。できれば小さな単位で納められればその方が良い」）。
   - 理由: 複数マシンで続きを始めるにはステートマシンの共有が先に要り、引継ぎを同じ保存先に置けば状態と引継ぎが必ずそろって届く。単位はステートマシンごとに 1 つで小さく、ステートマシンが閉じれば一緒に閉じる。リポジトリに置かないので誤コミットと ignore が要らない（devbasex のリポジトリは public で、GitHub の課題や PR に置く案は採らない）
   - 人と AI はコマンドで読み書きする（例: `ndf handoff write <ID> --file -`・`ndf handoff show <ID>`）。中継の再開はステートマシンの ID から読む
   - `.ndf/handoff` は置き場の一覧から消す。今の `.ndf/handoff/*.md` は移し替えの後に退避して消す
   - 複数マシンでの共有は今の仕組みで足りる。ステートマシンの DB は環境変数 `NDF_PROGRESS_DB`（`sqlite:///` か `postgresql://`。`plugins/ndf/contracts/domains/env.json` 90 行）で指せ、nd-factory は PostgreSQL の URL を入れる。無ければチェックアウトの保存先の `progress.sqlite`
3. **設計は #3500 の 1 つ（PR #4197 を書き直す）、実装は子の課題に分ける**（一覧とパスを求める関数の 1 本化・置き場の移し替えと ignore・残骸の退避と削除）。`loop.json` と `plan-progress` の契約の食い違いは、外部サービスに効く不具合として先に別の課題に起票する

<details>
<summary>置き場の棚卸し（2026-11-23、origin/main d5da7b601 と /work/ai-plugins の実物）</summary>

### `.ndf/` とリポジトリの根に NDF が置くものの棚卸し

origin/main（`d5da7b601`）のコードと、`/work/ai-plugins` の実物を照らしました。ファイルは編集していません。

**要点:**
- 置き場の一覧（`locations.json`）は、実際に書いている場所のうち 3 つを持っていません。git の共通ディレクトリの `.git/ndf/`、cross 系の作業場所の `.worktrees/.ndf/`、相手役の出力の `.cross_refactoring/` です。
- `.ndf/loop` は、一覧・手順書・実物のどれもがコードと違う場所を指しています。
- メインディレクトリの解き方は 3 通りあり、今の worktree の根を起点にする関数も 5 つあります。

## 1. 置き場の表

略記: **WT** = 今の worktree の根（`rev-parse --show-toplevel`）、**MAIN** = メインディレクトリ、**COMMON** = git の共通ディレクトリ（`.git`）。根拠のパスはすべて `plugins/ndf/src/ndf/` から書いています。

### A. 追跡する設定（`.ndf/` の直下）

| 置き場 | 種類 | 中身 | 書く主体 | 読む主体と今のコードの起点 | 一覧 root / tracked | git の追跡（実物） | 単位の見立て | 食い違い |
|---|---|---|---|---|---|---|---|---|
| `.ndf/` | フォルダ | 設定の置き場 | — | — | repo / true | 追跡 | ブランチごと（設定は PR で変える） | — |
| `project.json` | ファイル | 言語・テスト・CI・`branch_strategy` など | 人と `ndf project write`。手順書は「メインディレクトリの `.ndf/` に書く」（project-analysis.md:101） | `progress/command.py:354` と `:98-100` は **WT**。`branch_strategy` だけは `main_keys` で **MAIN** | repo / true | 追跡 | ブランチごと。`branch_strategy` はリポジトリ全体 | 書く側の手順書は MAIN、読む側のコードは WT |
| `worktree.json` | ファイル | remote・guard・deps | 人 | `domains/worktree/ndf_settings.py:62-68` は **MAIN** だけを読む | repo / true | 追跡 | リポジトリ全体 | 一覧は repo なのに、ファイル全体を MAIN から読む（`main_keys` にも無い） |
| `worktree.local.json` | ファイル | 個人の機械の値 | 人。無視の行は `ndf worktree init` が作る（`domains/worktree/setup.py:7`） | `ndf_settings.py:71-72` は **MAIN** | repo / false | `.ndf/.gitignore:2` で無視 | 個人の設定・MAIN に 1 つ | 一覧は repo、コードは MAIN |
| `mvv.json`・`mvv.md` | ファイル | MVV の本体と版 | 人の承認 | `command.py:55,475` は **WT** | repo / true | 追跡 | リポジトリ全体 | — |
| `backlog.json` | ファイル | 棚卸しの設定 | 人 | `domains/backlog/depth.py:31-38` は **WT** | repo / true | 追跡 | リポジトリ全体 | — |
| `release.json` | ファイル | リリースの手順 | 人 | `domains/release/cli.py:64-71` は **MAIN**（`common_dir().parent`。「PR の変更で差し替えられないため」） | repo / true | 追跡 | リポジトリ全体 | 一覧は repo、コードは MAIN（`main_keys` に無い） |
| `runtimes.json` | ファイル | 許可するランタイム | 人 | `core/converge/participants.py:96-100` は **MAIN** | repo / true | 実物なし | リポジトリ全体 | `main_keys` が `allowed` を持つ。一覧と一致 |
| `supervise`・`glossary`・`instructions`・`pace`・`review` の各 `.json` | ファイル | 各設定 | 人 | 起点は 1 つずつは確かめていない | repo / true | 追跡 | ブランチごと | 未確認 |
| `ask`・`context`・`estimate`・`issue-items`・`projects`・`code-metrics` の各 `.json` | ファイル | 各設定 | 人 | `estimate` は呼び手が渡す root（`domains/crossrefactor/allocation.py:46-48`） | repo / true | 実物なし | — | — |
| `design.json` | ファイル | `representative_repos` | 人 | 手順書 `skills/design/references/design-template.md:125` だけ。src は 0 件（`git grep -c -E 'design\.json\|representative_repos' origin/main -- plugins/ndf/src ':!*tests*'`） | **一覧に無い** | 追跡 | リポジトリ全体 | 追跡しているのに `ndf-files.json` に行が無い |
| `document.json` | ファイル | 文書の投稿先・認証の環境変数名 | 人 | 手順書 `development-workflow/references/document-destinations.md:12` などで 11 件。src は 0 件 | **一覧に無い** | 実物なし | リポジトリ全体 | `ndf-files.json` に行が無い |
| `.ndf/.gitignore` | ファイル | 無視する行 | `ndf worktree init`（無いときだけ作る。`setup.py:7`） | git | — | 追跡 | ブランチごと | 中身は 10 系の名前（`sprints/`・`sprint-*/`・`loop/`）を持つのに、`tmp/` を持たない |

### B. 追跡しない記録（`.ndf/` の下）

| 置き場 | 種類 | 中身 | 書く主体 | 読む主体と今のコードの起点 | 一覧 root / tracked | git の追跡（実物） | 単位の見立て | 食い違い |
|---|---|---|---|---|---|---|---|---|
| `.ndf/handoff/<名>.md` | ファイル | 引継ぎ文書（実物 90 件超） | conductor が手順書どおり書く。手順書は「保存先は MAIN の `.ndf/handoff/` だけ」（`skills/development-workflow/references/handoff.md:21-23`）。コードは書かず、パスの正規表現だけを持つ（`domains/relay/context_records.py:35`） | `ndf records handoff check` は `--cwd` の **WT**（`domains/records/cli.py:46-48`）。手順書が `--cwd <MAIN>` を渡させて補っている（handoff.md:13-14,73-74） | repo / false | `.ndf/.gitignore:6` で無視 | MAIN に 1 つの作業状態（会話をまたぐ） | 一覧 repo と手順書 MAIN が食い違う。handoff.md:25 と project-analysis.md:113 は「`.gitignore` に書かない」、実物は書いている |
| `.ndf/parallel/<計画>/plan.json` | ファイル | 計画の入力 | conductor が手で書く。手順書は「`ndf lanes init` を打ったリポジトリの MAIN」（`parallel-cross-repo.md:70-71`） | `ndf lanes init/start <パス>`。パスは呼び手が渡す | repo / false | `.ndf/.gitignore:8` で無視 | MAIN に 1 つ（全レーンを束ねる） | 一覧 repo と手順書 MAIN が食い違う |
| `.ndf/parallel/<計画>/run.log` | ファイル | 切り離して動かした `lanes run` の出力 | `progress/plan/run.py:406-419`（`record_dir` が `command.ndf_settings_path` を使う） | 人 | 同上 | 同上 | MAIN に 1 つ | **コードは WT**。worktree から打つと worktree の下に別の置き場ができる |
| `.ndf/loop/<ループ>/loop.json` | フォルダ | 開発ループの記録 | 一覧の `ndf-files` `loop-record`（reader: service）と手順書（handoff.md:34、workflow-modes.md:353）はここを指す。**コードはここへ書かない**。`domains/loop/store.py:26,90-93` は `durable.store_root` 経由で `COMMON/ndf/state/loops/<名>` に書く（`core/durable.py:403-412`） | — | main / false | `.ndf/.gitignore:7`。実物の `lcore`・`lproc` は 11-08 付けで、10 系の残骸と推測 | MAIN（か COMMON）に 1 つ | 一覧・手順書・実物の 3 つがコードと違う場所を指す |
| `.ndf/backlog/state/<repo>/`（候補・順位・見積・journal など） | フォルダ | 棚卸しの記録 | `domains/backlog/state.py:49-57`。起点は **MAIN**（`git worktree list` の先頭、`:32-41`）。`NDF_UPKEEP_STATE_DIR` で差し替えられる | 同 | main / false（env あり） | `.ndf/.gitignore:5` | MAIN に 1 つ | 一致 |
| `.ndf/backlog/{causes.jsonl, depths.jsonl, owner.json, overhaul/, review-*.json}` | 混在 | 10 系の棚卸しの記録 | src に書き手なし（`git grep -E 'causes\.jsonl\|owner\.json'` は 0 件） | — | 一覧に無い | 無視 | 残骸 | `depths.jsonl` は一覧では `state/<repo>/depths.jsonl` だが、実物は直下にある |
| `.ndf/backlog/research/` | フォルダ | 外部調査の記録 | 手順書 `design/references/external-survey.md:55` だけ | — | 一覧に無い | 実物なし | — | 一覧に無い |
| `.ndf/sprints/` | フォルダ | 一覧の `sprint`（M1） | **書き手なし**（src で `ndf-sprints`・`.ndf/sprint` は 0 件） | — | repo / false | 実物なし | — | 一覧だけにある |
| `.ndf/sprint-*/`（34 個） | フォルダ | 10 系の supervise のプランと状態 | 11 系に書き手なし（同じ検索で 0 件） | 確定仕様（`ndf-work-during-approval-wait-and-business-hours.md:113`）が今も読む前提で書かれている | 一覧に無い | `.ndf/.gitignore:10` | 残骸 | 一覧に無いのに、無視の行と実物が残る |
| `.ndf/tmp/` | フォルダ | 下の 3 種が混ざる | 下の 3 行 | — | repo / false（`scratch`・`plan-progress`） | **ルートの汎用の `tmp/`（`.gitignore:34`）だけが無視している** | 種類で分かれる | 下を参照 |
| └ `.ndf/tmp/junit.xml` | ファイル | テストの JUnit の出力 | `.ndf/project.json:30-32` のテストのコマンド。打った場所の相対で **WT** | supervise | 同上 | 同上 | worktree ごと・使い捨て | 一致 |
| └ `.ndf/tmp/<レーン>/<設計-N>/`（承認資料・PR 本文・cross-review の出力など） | フォルダ | conductor と supervisor の作業メモ | 慣習。skills・agents・src のいずれも `.ndf/tmp` が 0 件。実物は MAIN にある（`issues/issue-4089-design.md:300` が MAIN を指す） | 人 | 同上 | 同上 | MAIN に 1 つ・使い捨て | 手順書に決まりが無い |
| └ `plan-progress`（`<sprint>/<n>-<plan>-state/progress.jsonl`） | ファイル | 外部サービスが読むプランの進み（`entrypoints.json:23`、`NDF_FACTORY_RUN`） | 11 系のステートマシンは `COMMON/ndf/state/progress-work/<hash>/…` へ書く（`progress/run.py:77,510,861`、`core/plan/records.py:22`）。**`.ndf/tmp` へ書く経路は見つからなかった**（推測。全経路は追っていない） | service | ndf-tmp / false | — | — | 公開の契約の場所と、実際に書く場所が違う疑い |

### C. 根やその他に作るもの

| 置き場 | 種類 | 中身 | 書く主体とコードの起点 | 一覧 | git の追跡 | 単位の見立て | 食い違い |
|---|---|---|---|---|---|---|---|
| `.worktrees/<ブランチ>/` | フォルダ | 開発の worktree | `domains/worktree/repo.py:23-30` は **MAIN** | repo / false | `.gitignore:63`。`create.py:65` は無視されていないと止まる | MAIN に 1 つ | 一覧 repo、コード MAIN |
| `.worktrees/.ndf/<rf番号>/…` | フォルダ | cross 系の作業場所（locked の worktree が実物で 17 本） | `core/converge/location.py:21-26,50-52` は **MAIN**（`common_dir().parent`）。`NDF_WORKTREE_BASE` で差し替えられる | **一覧に無い** | `.worktrees/` の配下として無視 | MAIN に 1 つ | 一覧に無い。`dev_worktrees` は除外している（repo.py:58-66） |
| `.cross_refactoring/` | フォルダ | 相手役の提出物と進捗 | `domains/crossrefactor/launch_decide.py:18`。担当の作業ディレクトリ（worktree）に置き、`workspace.py:16` が `info/exclude` に足す | **一覧に無い** | `.gitignore:49` とあわせて二重に無視 | worktree ごと・使い捨て | 一覧に無い |
| `.cross_review/` | フォルダ | 10 系の cross-review の作業場所 | src は 0 件 | 一覧に無い | `.gitignore:47` | 残骸（実物は 10-29 付け） | 無視の行だけが残る |
| `issues/approval-*.md` | ファイル | 承認資料のコピー | 承認ゲート（`ndf-files.json` の `approval`） | issues / false | `.gitignore:79` | MAIN に 1 つ・使い捨て | — |
| `COMMON/ndf/state/`（`progress.sqlite`・`dbos/`・`progress-work/`・`loops/`） | フォルダ | ステートマシンと耐久の記録 | `core/durable.py:403-412`、`progress/command.py:68-69`、`core/plan/cli.py:42-43` | **一覧に無い** | `.git` の中 | リポジトリ全体で 1 つ | 一覧に無い |
| `COMMON/ndf/worktree-registry.json`・`worktree-trash/`・`acceptance-<N>.lock` | 混在 | worktree の割り当て・退避・受け入れの錠 | `domains/worktree/allocations.py:25,49`、`trash.py:28-33`、`domains/loop/acceptance.py:72` | **一覧に無い** | `.git` の中 | リポジトリ全体で 1 つ | 一覧に無い |
| `COMMON/ndf-main.lock`・`ndf-branch-strategy.lock` | ファイル | 錠 | `mainlock.py:20,27`、`strategy_write.py:35,211` | **一覧に無い** | `.git` の中 | 同上 | `ndf/` の外に直に置いている |
| `COMMON/ndf/{estimate/, sprint-limpl/, step-history.jsonl（2.2MB）, test-run/, state.v10-20261118/}` | 混在 | 10 系の記録と退避先 | src に書き手なし（`whole\.jsonl`・`step-history` は 0 件） | — | — | 残骸 | — |

## 2. 食い違いを重い順に

1. **`.ndf/loop`: 誰も正しい場所を指していない。** 書いているのは `COMMON/ndf/state/loops/` だけ（`loop/store.py:90-93`）です。一方、一覧の `ndf-loop`、`ndf-files` の `loop-record`（reader: service）、手順書 2 か所は `.ndf/loop/<名>/loop.json` を指し、そこには 10 系の残骸があります。
   - 外部サービスと人は、契約どおりの場所を読むと古い記録をつかみます。
2. **`plan-progress`: 公開の契約と書き先が違う疑い（推測）。** 外部のランナーが読む進みのファイルを、契約は `.ndf/tmp/<sprint>/…` としています。11 系が `progress.jsonl` を書くのは `COMMON/ndf/state/progress-work/…` です。
   - そのとおりなら、`NDF_FACTORY_RUN` の呼び手は進みを読めません。
3. **`.ndf/parallel` の `run.log` が WT に落ちる**（`plan/run.py:408`）。
   - 計画を worktree から切り離して起動すると、ログは worktree の下に書かれます。
   - 無視されたファイルは `git worktree remove` を止めないため、片付けで worktree ごとログが消えます。MAIN の計画を見る別のセッションからは見えません。
   - 一覧も repo なので、conductor が一覧に従うと `plan.json` も worktree に置かれます。
4. **`.ndf/handoff`: 一覧・手順書・コードの起点が 3 つともそろっていない。**
   - 一覧は repo、手順書は MAIN、検査のコマンドは WT です（`records/cli.py:46-48`）。`--cwd` を渡し忘れると、`missing_path` を誤って出します。
   - `.gitignore` に書くかどうかで、手順書 2 か所と実物、PR #4197 の方針が食い違います。
5. **`.ndf/tmp` を無視しているのは、ルートの汎用の `tmp/` 行だけ。**
   - その行が無いリポジトリでは、`.ndf/tmp` が未追跡のまま `git add -A` で入ります。`.ndf/.gitignore` に `tmp/` はありません。
   - 置き場の単位（WT か MAIN か）は、junit（WT）と作業メモ（MAIN）が混ざっていて、手順書に決まりがありません。
6. **一覧に無い置き場が 3 系統ある**: `COMMON/ndf/`、`.worktrees/.ndf/`、`.cross_refactoring/`。
   - 持ち主と消す時点が表で管理されません。その結果として `step-history.jsonl` 2.2MB や `state.v10-*` が残っています。
7. **設定を読む起点が、一覧の行で区別されていない。**
   - `worktree.json`・`worktree.local.json`・`release.json` はファイル全体を MAIN から読みます。`project.json`・`mvv.json`・`backlog.json` は WT から読みます。`main_keys` で表しているのは `branch_strategy` と `allowed` だけです。
   - 起きること: worktree のブランチで `worktree.json` を直しても、マージして MAIN を最新にするまで効きません。
8. **メインディレクトリの解き方が 3 通りある。**
   - `common_dir().parent`: `converge/location.py:29-31`、`release/cli.py:69`、`project/check.py:138`、`plan/queue_run.py:44-49`
   - `git worktree list` の先頭: `backlog/state.py:32-41`、`location.py:34-47`、`git.py:292`
   - `locate`（サブモジュールなら WT）: `worktree/repo.py:45-51`
   - `--separate-git-dir` のリポジトリとサブモジュールで結果が割れます（`location.py:35-36` 自身が注記しています）。
   - 加えて、WT を起点にする関数が 5 つあります: `command.py:98`、`depth.py:31`、`records/cli.py:46`、`route/__init__.py:412`、`context_records.py:51`。
9. **書き手の無い置き場と残骸。** `.ndf/sprints`（一覧だけ）、`.ndf/sprint-*` が 34 個、`.ndf/backlog` の直下のファイル群、`.cross_review/`、`.ndf/.gitignore` に残る 10 系の行。
   - 新しい読み手が、どれを今の置き場だと思うべきか判断できません。
10. **一覧に無い設定: `design.json`（追跡している）、`document.json`、`.ndf/backlog/research/`。** spec-decl の検査はこれらを見ません。

## 3. 置き場の決まりの案（決めるのは利用者）

**案 1: 単位を「中身の持ち主」の 4 種で決める。**
- 追跡する設定はブランチごとで WT。ただし、PR で差し替えられると困るキーは MAIN から読み、`main_keys` で行ごとに宣言する。
- ブランチをまたぐ作業状態は 1 か所に置く。
- worktree ごとの使い捨ては WT。
- 個人の設定は MAIN。
- 得: 今の実物の大半はこの分け方に合っている。失: `worktree.json` と `release.json` を全体で MAIN と扱うか、キー単位にするかを決め直す必要がある。

**案 2: ブランチをまたぐ状態を、人が読むかどうかで MAIN と COMMON に分ける。**
- 人が読み書きするもの（handoff、`plan.json`、作業メモ）は `MAIN/.ndf/`。
- 機械だけが読むもの（ステートマシン、ループ、錠、退避、cross の作業場所）は `COMMON/ndf/`。
- 得: COMMON は `git clean` や `.gitignore` の影響を受けず、どの worktree からも同じ場所になる。失: COMMON は人と外部サービスから見えない。`loop.json` のように service が読むものは、MAIN に置くか、パスを結果 JSON で渡す必要がある。

**案 3: 一覧の root の値を見直す。**
- `repo` を `worktree` へ改名し、`common`（git の共通ディレクトリ）を足す。
- `COMMON/ndf/*`、`.worktrees/.ndf`、`.cross_refactoring` の行を足す。
- 書き手の無い行（`ndf-sprints`、`plan-progress` の今のパス）は直すか消す。

**案 4: パスを解く関数を 1 つにする。**
- `locations` の id と cwd を受けて root の種類どおりに解く関数を 1 つだけ置く。メインディレクトリの定義もその中の 1 つにする。
- 「`show-toplevel` に一覧の path を足す」形を、ほかの場所では検査で落とす。
- 得: 3 の `run.log` の型の不具合が構造上起きなくなる。失: 呼び出し元の 20 か所近くを差し替える。

**案 5: 無視の仕方を、作る側に寄せる（PR #4197 の方向）。**
- 追跡しないフォルダは、作るときにその中へ `*` だけの `.gitignore` を置く。`.ndf/.gitignore` には個人設定の行だけを残す。
- 得: 他のリポジトリでも、ルートの `.gitignore` に頼らずに無視される。失: handoff.md:25 の「`git status` に出る方が探しやすい」という方針と衝突する。どちらを取るかの判断が要る。

**残骸の整理**（`.ndf/sprint-*`、`.ndf/loop`、`.ndf/backlog` の直下、`.cross_review/`、`COMMON/ndf` の 10 系の記録）は、上の決まりを決めた後に、退避してから消すのがよいと考えます。

## 作業の報告

- **作業:** `.ndf/` と、NDF がリポジトリの根に作る置き場を棚卸しし、表・食い違いの重さの順・決まりの案にまとめた（読むだけで、何も変更していない）
- **結果:** 完了
- **見つけたもの:**
  - 一覧に無い置き場が 3 系統ある（`.git/ndf/`、`.worktrees/.ndf/`、`.cross_refactoring/`）
  - `.ndf/loop` は一覧・手順書・実物がコードと違う場所を指す
  - `plan-progress` は公開の契約と書き先が違う疑いがある（推測）
  - `.ndf/parallel` の `run.log` が WT に落ちる
  - handoff の起点と `.gitignore` の方針が 3 つの間で食い違う
  - `.ndf/tmp` を無視しているのはルートの汎用の行だけ
  - メインディレクトリの解き方が 3 通りある
  - 書き手の無い残骸と、一覧に無い設定（`design.json`・`document.json`）がある
- **置く場所:** この応答だけ。結果ファイルの指定は無かったため、`ndf contracts emit` は打っていない
- **次にすること:** 3 の案から利用者が方針を選び、#3500 の設計（https://github.com/devbasex/ai-plugins/pull/4197 ）に反映する。あわせて `plan-progress` の書き先の推測を、全経路を追って確かめる

</details>

## 何が起きたか

**きっかけ（事例 1）**: 2026-11-14 に PR #3491 で cross-refactoring を流したとき、検証の失敗を直すコミット（項目 I-011、修正担当は codex）に、cross-refactoring 自身の作業ファイル 2 件（`.cross_refactoring/codex-fix-rf3491-progress.log` と `-result.json`）が入った。2 件はコミットの時点で `.gitignore` の無視の対象だった（`git show c9436c319:.gitignore` の 49 行目）。cross-refactoring の最終ゲートは通り、続く cross-review の round 1 で kiro が見つけて、別のコミット（3dd9fe9b6）で外した。

```text
c9436c319 Fix: 検証の失敗を直す — plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py#prod_version
 .cross_refactoring/codex-fix-rf3491-progress.log  | 5 +++++
 .cross_refactoring/codex-fix-rf3491-result.json   | 1 +
```

2026-11-22 に 11.x の今のコード（main 5c2c1e62d、git 2.53.0）で確かめ直すと、同じことは今も起こりうる。除外の下でも `git add -f` で入り、一度入ると以後のコミットにも入り続ける。

**主題**: 調べると、NDF がリポジトリの中に作る追跡しないフォルダとファイルの無視の仕方が、置き場ごとにばらばらだった（「今の姿」）。2026-11-23 の設計承認ゲートで、利用者がこの課題を、NDF が作るリポジトリ管理外のファイルの ignore の仕方をそろえる上位の課題にした。

## 種類

改善（無視の仕方の約束を 1 つにそろえる。cross-refactoring の作業ファイルの件は不具合で、その事例として扱う）

## 今の姿

NDF がリポジトリの中に作る、git で追跡しない置き場は次の 3 つから引ける。1 つ目は置き場の一覧の設定ファイル `plugins/ndf/contracts/domains/locations.json` で、NDF が使う保存先ごとに、パスと、そのパスをどこから数えるか（今の worktree の根か、メインディレクトリの根か）と、git で追跡するかを持つ。2 つ目は工程が書くファイルの一覧 `plugins/ndf/contracts/domains/ndf-files.json` で、NDF の工程が書くファイルごとに置き場と追跡するかを持つ。3 つ目は cross-refactoring の作業ファイルの置き場で、一覧には載っていない。無視の仕方は置き場ごとに違う。

| 置き場 | 今の無視の仕方 |
| --- | --- |
| `.cross_refactoring`（cross-refactoring の作業ファイル） | cross-refactoring が共通の git ディレクトリの `info/exclude` に足す。追跡済みのファイルには効かない |
| `.worktrees` | worktree の Skill の手順 2-2 のシェルが根の `.gitignore` に足し、`ndf worktree create` が無ければ止まる |
| `.ndf/tmp`・`.ndf/handoff`・`.ndf/sprints`・`.ndf/parallel`・`.ndf/backlog/state` | NDF は何もしない。利用者の根の `.gitignore` 次第（ai-plugins では手で書いた `.ndf/.gitignore` の行と根の `tmp/` が当たる） |
| `.ndf/loop` | 置き場の一覧にはあるが、今のコードは作業ツリーでなく git の共通ディレクトリの中（`<共通ディレクトリ>/ndf/state`）に書く。無視は要らない |
| `.ndf/worktree.local.json`（個人設定） | `ndf worktree init` が `.ndf/.gitignore` を無いときだけ作って 1 行書く |

除外は追跡済みのファイルに効かない（gitignore の文書「Files already tracked by Git are not affected」）。除外の下でも `git add -f` で入る。2026-11-22 の実測は次のとおりである。

| 修正担当の操作 | `.cross_refactoring/` の下のファイル |
| --- | --- |
| `git add -A` | 入らない |
| `git add -f .cross_refactoring/<ファイル>` | **入る** |
| 一度入った後の `git add -A`・`git commit -a` | **入り続ける** |

## 何を足すか・変えるか

| | 今 | 変えた後 |
| --- | --- | --- |
| NDF が作るフォルダの無視 | 置き場ごとに `info/exclude`・根の `.gitignore`・何もしない、が混ざる | 作る側が作るときに、中へ `*` だけの `.gitignore` を置く |
| `.ndf/` の中の追跡しないファイルの無視 | `.ndf/.gitignore` を無いときだけ作る | 作る側が `.ndf/.gitignore` へ行を書く（既にあれば足さない） |
| 根の `.gitignore` | `.worktrees/` の登録を求める | NDF は書かず、求めない |
| cross-refactoring の作業ファイル（事例 1） | 一度入ると追跡され続ける | 担当の起動の前に作り直し、追跡されていれば索引から外す |

## 今の利用者への影響と互換

- `ndf worktree create` は、根の `.gitignore` に `.worktrees/` が無くても止まらなくなる
- 利用者のリポジトリに出る差分は、`.ndf/.gitignore` に行が無いときのその 1 行だけになる。フォルダの中の `.gitignore` は自身も無視されるので差分に出ない
- 利用者が根の `.gitignore` に書いた既存の行は消さない

## なぜ困るか

無視の仕方がばらばらだと、置き場ごとに漏れ方が違い、利用者のリポジトリの設定次第で NDF の記録や作業ファイルが Pull Request の差分に入る。事例 1 では作業ファイルが Pull Request に入り、修正のラウンドを 1 回使った。最終ゲートの直しで起きると正しい直しが範囲外として止まり、フレーキーなテストの直しで起きると正しい直しが取り消される。

## 目的

- NDF が作る追跡しないフォルダとファイルの無視を、次の 2 つの仕組みにそろえる。根の `.gitignore` と `ndf worktree init`・`check` の登録に頼らない
  1. フォルダは、作る側が作るときに中へ `*` だけの `.gitignore` を置く
  2. `.ndf/` の中のファイルは、作る側が作るときに `.ndf/.gitignore` へ行を書く（既にあれば足さない）
- 事例 1: cross-refactoring の作業ファイルが追跡されたまま次のコミットへ持ち越されることを、担当の起動の前の片付けで止める

## 対象範囲

含む:
- 置き場の一覧の、追跡しないフォルダ（`.worktrees`・`.ndf/tmp`・`.ndf/handoff`・`.ndf/sprints`・`.ndf/parallel`・`.ndf/backlog/state`）と `.cross_refactoring` を、NDF のコードが置き場の単位（worktree ごとに 1 つか、メインディレクトリに 1 つ）のパスに作り、作るときに中へ `*` だけの `.gitignore` を置くこと
- 置き場ごとの単位（worktree ごとに 1 つか、メインディレクトリに 1 つか）を、中身が何に属するかで決めること。計画の記録（`.ndf/parallel`）とスプリントの記録（`.ndf/sprints`）は複数の worktree にまたがるのでメインディレクトリに 1 つ、テストの出力などの一時ファイル（`.ndf/tmp`）と cross-refactoring の作業ファイルは worktree ごとにする
- 今のコードが決めた単位と違う所に作る置き場を直すこと（計画の記録は、今は打った worktree の根に作られ、worktree を片付けると消える）
- 置き場の一覧の設定ファイルのパスの起点を、決めた単位に合わせること（`.worktrees`・`.ndf/handoff`・`.ndf/parallel`・`.ndf/sprints` はメインディレクトリの根から数える。`.ndf/loop` は実際は git の内部のディレクトリに書く）
- `.ndf/` の中の追跡しないファイル（`.ndf/worktree.local.json`）の行を、NDF が `.ndf/.gitignore` に書くこと
- cross-refactoring が `info/exclude` へ書くのをやめ、`ndf worktree create` が根の `.gitignore` の登録を求めるのをやめること
- 事例 1: 担当の起動の前に、担当が動くディレクトリの `.cross_refactoring/` を作り直すこと（前の作業ファイルを消し、追跡されていれば索引からも外す）

含まない:
- 担当が `git add -f` で入れた作業ファイルを、その起動のコミットから除くこと（コミットの作り直しはしない。最後の起動で入れたものは、次の起動が無ければ残る）
- 根の `.gitignore` を NDF が書くこと。`ndf worktree init`・`check` が登録の欠けを検査すること
- 利用者が根の `.gitignore` に書いた既存の行を消すこと
- 手順書の `Item-Id` の検査がコードに無いこと（#4195 に起票した）
- NDF が作らない、無視の対象のファイル（生成物・`.env` など）
- cross-review の作業ファイル。cross-review は状態の保存先をレビューする worktree の外に置き、中なら `precondition` で止める（`plugins/ndf/skills/cross-review/SKILL.md` の `--state-dir`）ため、同じ経路が無い

## 前提

- 前提 1: 前の起動の進捗のファイルは、次の起動の前に消してよい（結果ファイルは起動の直後に回収先へ移してあり、進捗のファイルは無進捗の監視が起動の間だけ測る）

## 受け入れ条件

- [ ] 1. 置き場の一覧の追跡しないフォルダと `.cross_refactoring` のどれも、NDF のコードがその置き場の単位のパス（worktree 単位なら今の worktree の根、メインディレクトリ単位ならメインディレクトリ）に作り、作るときに中へ `*` だけの `.gitignore` が置かれる。メインディレクトリと linked worktree の両方の `git status` に、置き場の中のファイルが出ない。既にある `.gitignore` は書き換えない
- [ ] 2. `.ndf/` の中の追跡しないファイル（メインディレクトリに 1 つの `.ndf/worktree.local.json`）は、NDF がその置き場を用意するときに、メインディレクトリの `.ndf/.gitignore` に行がある（無ければ足し、既にあれば足さず、既存の行は消さない）
- [ ] 3. 根の `.gitignore` と `info/exclude` に登録が無いリポジトリでも、上の置き場の中のファイル（中の `.gitignore` 自身を含む）は `git add -A` で入らない。NDF は根の `.gitignore` を書かず、登録の欠けで止まらない（`ndf worktree create` を含む）
- [ ] 4. `.worktrees/` の中の `.gitignore` は、作った worktree の中のファイルの無視に影響しない（worktree の中で足したファイルは `git add -A` で入る）
- [ ] 5. 事例 1: どの工程でも、担当を起動する直前に、担当が動くディレクトリの `.cross_refactoring/` には前の起動の作業ファイルが無く、`*` だけの `.gitignore` がある。前に追跡されていたファイルは索引から外れていて（`git ls-files .cross_refactoring` が空）、担当の次のコミットで追跡から外れる
- [ ] 6. 事例 1 の片付けは `.cross_refactoring/` だけに効き、作業ディレクトリのほかのファイル・未コミットの変更・コミットは変わらない
- [ ] 7. 計画の記録（`.ndf/parallel/<計画>/`）は、どの worktree から打ってもメインディレクトリの `.ndf/parallel/<計画>/` に書かれ、読まれる。記録を書いた worktree を片付けても記録は残る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（互換あり）。`ndf worktree create` は `.worktrees/` の登録が無くても止まらない |
| データ | 変わらない（状態ファイルの形は同じ） |
| 既存の振る舞い | NDF が作るフォルダの中に `.gitignore` が 1 つずつ増える（無視されるので差分に出ない）。利用者のリポジトリに出る差分は `.ndf/.gitignore` の行（無いときだけ）。事例 1 では、担当の起動の前に前の起動の進捗のファイルが消え、作業ファイルが追跡されていたときは次のコミットに削除が入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests -q -n 4`（CI のジョブでも同じ範囲を打つ） |
| 静的解析・型検査 | `PATH=/work/ai-plugins/.venv/bin:$PATH ndf check`（CI の検査と同じ） |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 修正担当が `git add -f` など無視の規則を越える追加をした | コミットの時点の `.gitignore` に `.cross_refactoring/` があり、通常の `git add` では入らないこと。担当が打ったコマンドの記録は見ていない | 前提として採る。2026-11-22 の実測で、除外の下で入る経路は `git add -f`（と、一度入った後の追跡）だけと確かめた（「今の姿」の表） |
| S2 | 直す場所は、修正担当への指示（`prompts/fix.md`）より、オーケストレーターのコミットの検査が適する | 推した出所: `prompts/fix.md` の「コミットの規約」が、規約から外れたコミットを打ち消す仕組みを持つこと | 採らない。2026-11-23 の設計承認ゲートで、利用者がコミットの検査と作り直しをやめ、起動の前の片付けだけにすると決めた。推した出所の「打ち消す仕組み」も 11.x のコードに無い（「今の姿」） |
| S3 | 入ったときは、トレーラーの欠けと同じようにオーケストレーターがそのコミットを差し戻す | 期待する結果の 2 行目 | 採らない。同じ設計承認ゲートの決定による（起動の前の片付けだけにする） |
| S4 | 11 系でも同じことが起きる | 2026-11-19 の棚卸しの「11 系での確かめ」 | 確かめた（「何が起きたか」の 11.x での再現）。課題は閉じない |
| S5 | 課題の主題を、NDF が作る追跡しないフォルダとファイル全体の無視の仕方へ広げる | 2026-11-23 の設計承認ゲートの回答と、オーガナイザーが変えた題 | 採る（利用者の指示。「目的」「受け入れ条件」） |

## リクエスト（原文）

<details>
<summary>起票の時点の本文（2026-11-19 の棚卸しで直した版）</summary>

> 2026-11-19 の棚卸しで、古くなった数値・パス・関連課題の状態を 11 系の今に合わせて直した。
>
> ## 事象
>
> cross-refactoring の検証の失敗を直すコミット（修正担当は codex）に、`.gitignore` の対象である作業ログ 2 件が入った。
>
> ```text
> c9436c319 Fix: 検証の失敗を直す — plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py#prod_version
>  .cross_refactoring/codex-fix-rf3491-progress.log  | 5 +++++
>  .cross_refactoring/codex-fix-rf3491-result.json   | 1 +
>  plugins/ndf-next/src/ndf/domains/loop/sprint_decide.py         | 7 +++++++
>  plugins/ndf-next/tests/domains/loop/test_loop_sprint_decide.py | 5 +++++
> ```
>
> コミットの時点の `.gitignore` の 49 行目は `.cross_refactoring/` で、2 件は無視の対象だった（`git show c9436c319:.gitignore`）。
> cross-refactoring の最終ゲートは passed で通り、続く cross-review の round 1 で kiro が指摘して、別のコミット（3dd9fe9b6）で外した。
>
> ## 期待する結果
>
> cross-refactoring の修正担当が作るコミットに、cross-refactoring 自身の作業ファイル（`.cross_refactoring/` の下）が入らない。
> 入ったときは、トレーラーの欠けと同じようにオーケストレーターがそのコミットを差し戻す。
>
> ## 再現手順
>
> 再現は 1 回（PR #3491 の cross-refactoring、項目 I-011、2026-11-14）。同じ形の再発は見ていない。
>
> 1. スプリントの検査のプランで cross-refactoring を流す
> 2. 適用した項目が範囲テストを落とし、修正（`prompts/fix.md`）を codex が担当する
> 3. 修正のコミットに `.cross_refactoring/codex-fix-rf<PR>-progress.log` と `-result.json` が入る
>
> ## 環境
>
> - NDF 10.27.64（観測の時点）。11 系の同じ指示は `plugins/ndf/skills/cross-refactoring/prompts/fix.md`
> - 修正担当: codex（Impl-Model: default）
> - 対象: devbasex/ai-plugins の PR #3491
>
> ## 影響
>
> 作業ログが Pull Request の差分に入り、レビューで見つからなければ開発の本流へ残る（Value 6「使うほど散らからない」）。
> このときはレビューが拾い、修正の 1 ラウンドを使った。
>
> ## 起票者の推定
>
> - 修正担当が `git add -f` など無視の規則を越える追加をした（推した出所: コミットの時点の `.gitignore` に `.cross_refactoring/` があり、通常の `git add` では入らないこと。担当が打ったコマンドの記録は見ていない）
> - 直す場所は、修正担当への指示（`prompts/fix.md`）より、オーケストレーターのコミットの検査（Item-Id・トレーラーを見ている所）が適する（推した出所: `prompts/fix.md` の「コミットの規約」が、規約から外れたコミットを打ち消す仕組みを持つこと）
>
> ## 由来
>
> PR #3491 の cross-review round 1 の kiro の指摘（`.cross_refactoring/codex-fix-rf3491-result.json`）。スプリント lr-fix-a-m1（#3300）の振り返りで、指摘は直したが原因の課題が残っていないことを拾った。#3300 の範囲は `plugins/ndf-next/src/ndf/domains/loop/` とそのテストで、cross-refactoring のコミットの検査は範囲外である。根拠: Value 6・Value 7（MVV 版 7）
>
> ## 11 系での確かめ（2026-11-19、main 2e43af7bc、ndf 11.0.8）
>
> 観測の時点の `plugins/ndf-next/` は、入れ替え（#3304）で `plugins/ndf/` になった。
>
> - 作業ログの置き場は今も `.cross_refactoring/` で、差分から外すのは git の除外へ 1 行足すことだけである（`plugins/ndf/src/ndf/domains/crossrefactor/workspace.py` の 58〜60 行、`launch_decide.py` の `IGNORE_ENTRY`）
> - 修正のコミットの中身から、無視の対象のパスを見つけて差し戻す検査は見当たらない。コミットの検査はトレーラーの欠け（`flaky_decide.py` の 66〜68 行）だけを見る
> - 担当が `git add -f` で除外を越えれば、11 系でも同じことが起きる見込み（未確認。再現は観測の 1 回だけ）

</details>

（書き手: 起票者（AI。スプリント lr-fix-a-m1（#3300）の振り返りで起票し、2026-11-19 の棚卸しで直した）、日付: 2026-11-14 起票・2026-11-19 更新。書き手が利用者でない文は、利用者の指示の原文として扱わない）

> フェーズ: 設計。課題: #3500。文書を書く前に、今の cross-refactoring のコードで、作業ログがどの経路でコミットへ入るかを実物で確かめる。課題の本文が古ければ（11 系の作り直しで経路が変わっていれば）、今の経路で起きるかを確かめ、起きなければ要求仕様にその結果を書いて、閉じる案を承認資料に載せる

（書き手: AI の起動指示（conductor、計画 p12 のレーン lcrrate）、日付: 2026-11-22）

> 設計承認ゲートの回答: 「直してから」。「#3500 は難しく考えすぎ」。作業（担当の起動）の前に `.cross_refactoring/` があれば削除し（追跡済みなら追跡からも外す）、`.gitignore` に入れる。それだけでよい。コミットの作り直しと失敗したときの備えはすべてやめる。`git add -f` の経路が残ることは決定の記録に 1 行で書く

（書き手: 利用者（conductor が設計承認ゲートの回答として伝えた。引用の文は conductor の要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（2 回目）: info/exclude の形は「マージしない」。方針は「作業の前に .cross_refactoring/ があれば削除し（追跡済みなら追跡からも外す）、.gitignore に入れる」のまま。`.gitignore` への登録は `/ndf:development-workflow` の初期化（`ndf worktree check`・worktree の setup.py が `.ndf/.gitignore` を作る所・create.py の `.worktrees/` の確かめ）へ、`.cross_refactoring/` と `.ndf` の一時フォルダ（`.ndf/tmp` など要るもの）の登録を寄せる

（書き手: 利用者（conductor とオーガナイザーが設計承認ゲートの回答として伝えた。引用の文は conductor の要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（3 回目）: 「直してから」。「.cross_refactoring/ を作成する際、必ず同時に .cross_refactoring/.gitignore を作るのではだめなのですか？」

（書き手: 利用者（conductor が設計承認ゲートの回答として伝えた）、日付: 2026-11-23）

> 補足（3 回目の回答の続き）: `.ndf/` の中のファイルは `.ndf/.gitignore` に書けばよい。NDF がそのファイルを作るときに `.ndf/.gitignore` へ行を書く形にし、根の `.gitignore` と `ndf worktree init` の登録に頼らない。登録の仕組みは、フォルダは作る側が中へ `*` だけの `.gitignore` を置く、`.ndf/` の中のファイルは作る側が `.ndf/.gitignore` へ行を書く、の 2 つだけにする

（書き手: 利用者（オーガナイザーと conductor が伝えた。引用の文は要約を含む）、日付: 2026-11-23）

> 設計承認ゲートの回答（4 回目）: 「直してから」。「無視するフォルダが worktree 単位なのかルートディレクトリ単位なのかが不明。例えば .cross_refactoring は worktree 単位、.ndf/handoff はルートディレクトリで 1 つのはず」

（書き手: 利用者（conductor が伝えた）、日付: 2026-11-23）

> 設計承認ゲートの回答（5 回目）: 「資料を直してから」。「表の root が repo。の意味が分からない」。追加の問い: 「.ndf/parallel とか、worktree ごとで大丈夫なの？」（単位は今のコードがどこに作るかではなく、中身が何に属するかで決める）

（書き手: 利用者（conductor とオーガナイザーが伝えた。括弧の中はオーガナイザーの補足）、日付: 2026-11-23）
