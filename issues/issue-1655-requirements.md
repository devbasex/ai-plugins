# #1655: cross-refactoring: 単独起動の手順を直す（ai-plugins 以外で打つ場所・最終ゲートの最終ステータスの決め方）

正は課題の本文（#1655）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

cross-refactoring の単独起動（`SKILL.md` の「前提」「実行」の節と `scripts/drive.py`）の手順の問題を 1 本の要求にまとめる。この本文は、元の #1655（打つ場所）に #1656（最終ステータスの決め方）を取り込んだものである。#1656 の受け入れ条件もここに含む。元の本文の観測は下の「元の本文」の節に残す。

## 依頼（原文）

> #1655。この要求には #1656 を取り込む（単独起動の最終ゲートで、スイープが未検証でも approved と記録される。最終ステータスの決め方を loop_drive.review_status の 1 つへ寄せる）。どちらも cross-refactoring の単独起動（SKILL.md・drive.py）の手順の問題として 1 本の設計で扱う。#1656 の受け入れ条件も同じ写し（issues/issue-1655-requirements.md）へ含め、#1656 の本文には #1655 の要求へ取り込んだことを書く。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

## 目的

- ai-plugins 以外のリポジトリでも、`SKILL.md` に書かれたとおりに打てば、cross-refactoring の単独起動が `assess` の判定から finalize まで通る状態にする（Value 5: どのプロジェクトでも使える形を保つ）
- 単独起動の最終ゲートの最終ステータスを決める規則を `loop_drive.review_status` の 1 か所だけが持ち、最後の HEAD が承認されていない実行が「通った実行」として配分の履歴へ入らず、Draft も解除されない状態にする（Value 6: 同じ役割の関数を Skill ごとに分けない）

## 元の本文

### #1655: 打つ場所（何を見つけたか）

`SKILL.md` は「Skill のディレクトリで」`python3 scripts/refactor.py assess` と `python3 scripts/drive.py` を打つと書くが、実装は対象のリポジトリを現在のディレクトリ（cwd）から決める。インストール先の Skill のディレクトリ（`~/.claude/plugins/cache/ai-plugins/ndf/*/skills/cross-refactoring`）は git リポジトリではないため、書かれたとおりに打つと assess は常に終了コード 2（判定できない）になり、drive → init は origin から PR を取れずに止まる。ai-plugins では Skill のディレクトリが対象のリポジトリの中にあるため、書かれたとおりでも偶然動く。supervise は絶対パスと対象の worktree を cwd にして打つため影響を受けない。

cwd を読む箇所（2026-10-03 時点の develop で確かめた）:

- `plugins/ndf/skills/cross-refactoring/SKILL.md` 「前提」の assess の 1 行、「実行」の `drive.py` と `bg-wait.sh` の打ち方
- `scripts/refactor_lib/commands/assess.py:30`（`production_code_changes(os.getcwd(), args.base)`）
- `scripts/refactor_lib/paths.py:52`（`github_repo_from_origin` が cwd の origin を読む）・`:78`（状態ファイルの候補に cwd を足す）
- `scripts/refactor_lib/commands/setup.py` の `_fetch_pr_context`（`github_repo_from_origin` と `gh repo view`）と、`git fetch` / `git worktree add` を cwd で打つ箇所
- `scripts/refactor_lib/runtime_decl.py:20`（`.ndf/runtimes.json` を cwd から読む）
- `scripts/drive.py` の `known_tmp`（`paths.default_tmp_dir` が cwd の origin から状態の置き場を決める）

### #1656: 最終ステータスの決め方（何を見つけたか）

単独起動の最終ゲートで止まったとき（終了コード 23）、`drive.py` の `pause_review` が書くプロンプトは、LLM に cross-review の状態ファイルから最終ステータスを手で決めさせる。その規則（「final が approved で sweep.verified が true・sweep.remaining_open が 0・sweep.commit が null なら approved、それ以外は final の値」）は、`plugins/ndf/scripts/lib/loop_drive.py` の `review_status()` と違う。`final=approved` かつ `sweep.verified=false` のとき、実装は `unverified` を返すが、プロンプトの規則では `approved` になる。このまま finalize へ進むと、`refactor_lib/commands/report.py` の `cmd_finalize`（`status == APPROVED` で履歴へ書く）が、最後の HEAD が承認されていない実行を配分の履歴へ入れる。

- `drive.py` は `review_status` を import しているが使っていない
- supervise は `cross-review/scripts/drive.py` が出す `metrics.review_status`（`review_status()` の結果）を `supervise_lib/worker_steps.py` で機械的に結果ファイルへ写しており、同じ値の決め方が 2 つある
- `SKILL.md` の終了コード 23 の行の「続けて Draft を解除する」には、解除してよい条件（どの最終ステータスなら解除するか）が書かれていない

## 前提

- 前提 1: 「ai-plugins 以外のリポジトリ」は、Skill のディレクトリを含まない git リポジトリで、origin が GitHub を指し、対象の Pull Request がそのリポジトリにあるものを指す。GitHub 以外の origin（GitLab など）は扱わない（現状の `github_repo_from_origin` の範囲のまま）
- 前提 2: 単独起動の利用者は、対象のリポジトリ（またはその worktree）の中で会話を始めている。`SKILL.md` は、利用者がそこから打つ前提で書いてよい
- 前提 3: Draft を解除してよい最終ステータスは `approved` だけである。`unverified`・`approved` 以外の `final` の値・`unknown` では Draft のまま残し、最終ステータスを報告して止まる
- 前提 4: supervise から起動される経路（絶対パスで呼び、対象の worktree を cwd にする）の振る舞いは変えない。最終ステータスの値は今と同じ `metrics.review_status` を写す
- 前提 5: cross-review の単独起動の `SKILL.md`（「Skill のディレクトリで」と書く）にも同じ cwd の依存がある（`review_lib/github.py:53` の `_git_remote_url`、`review_lib/store.py:52`、`review_lib/participants.py:167`）。この要求は cross-refactoring の単独起動だけを扱い、cross-review の単独起動は別の課題で扱う。ただし、cross-refactoring の最終ゲートが打つ cross-review の駆動は、対象のリポジトリを cwd にして打つ（この要求の受け入れ条件に含む）

## 対象範囲

含む:
- cross-refactoring の `SKILL.md` の単独起動の手順（「前提」の assess の打ち方、「実行」の `drive.py` と `bg-wait.sh` の打ち方、終了コード 23 の行）
- 単独起動で `assess`・`drive.py`（init を含む）が対象のリポジトリを決める仕組み
- 単独起動の最終ゲートで、最終ステータスを決めて結果ファイルへ書く手順（`drive.py` の `pause_review` のプロンプトと、結果ファイルを読む側）
- 単独起動の最終ゲートの後に Draft を解除してよい条件の明記

含まない:
- cross-review の単独起動の打つ場所（前提 5。別の課題で扱う）
- supervise から起動される経路の変更（前提 4）
- `loop_drive.review_status` の規則そのものの変更（規則は今のまま、持ち手を 1 つにする）
- GitHub 以外の origin への対応（前提 1）
- 配分の履歴にすでに入った行の訂正（過去の実行の記録は直さない）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が対象のリポジトリで単独起動を始めた | 利用者が `/ndf:cross-refactoring` を呼んだ | — | — |
| E2 | assess がプロダクションコードの差分を判定した | `SKILL.md` の「前提」の 1 行を打った | 起点を解けなければ終了コード 2（判定できない。飛ばしてよいとは読まない） | E1 |
| E3 | drive の init が対象の Pull Request と作業ディレクトリを決めた | `SKILL.md` の「実行」の 1 行を打った | Pull Request のメタデータを取れなければ中断（終了コード 1・`metrics.exit` 4） | E2 が 3 以外 |
| E4 | 駆動が最終ゲートで止まった（終了コード 23） | 単独起動の最終ゲートが `cross-review` を要した | — | E3 の後、実装と検証が終わった |
| E5 | cross-review の駆動が終わった | `items[0].command` を対象のリポジトリで打った | cross-review の駆動が中断したら、結果ファイルを書かずに止まる | E4 |
| E6 | 最終ステータスが結果ファイルへ書かれた | E5 の結果の `metrics.review_status` | 値が無ければ `unknown` | E5 |
| E7 | 打ち直した駆動が finalize を打ち、`approved` のときだけ配分の履歴へ 1 行を足した | 同じコマンドの打ち直し | 結果ファイルを読めなければ中断 | E6 |
| E8 | 最終ステータスが `approved` のときだけ Draft を解除した | E7 の結果 JSON の `metrics.review_status` | `approved` 以外なら Draft のまま報告して止まる | E7 |

## 受け入れ条件

打つ場所（#1655）:

- [ ] AC1: `SKILL.md` の単独起動の手順（assess・`drive.py`・`bg-wait.sh` の 3 つ）は、Skill のディレクトリへ移らずに対象のリポジトリの中から打つ形で書かれている。スクリプトの位置は、Skill のディレクトリに依らない形（絶対パスか、インストール先から求める手順）で示されている
- [ ] AC2: ai-plugins を含まない git リポジトリ（origin が GitHub・プロダクションコードの差分あり）の中で、`SKILL.md` の「前提」の assess の 1 行をそのまま打つと、終了コードは 0 か 3 になる（2 にならない）
- [ ] AC3: AC2 と同じリポジトリの中で、`SKILL.md` の「実行」の 1 行をそのまま打つと、init がそのリポジトリの Pull Request のメタデータを取り、作業ディレクトリをそのリポジトリの worktree として作る（origin を読めない・PR を取れないことを理由に止まらない）
- [ ] AC4: cwd が git リポジトリでない（例: インストール先の Skill のディレクトリ）ときに assess・`drive.py` を打つと、黙って別のリポジトリを対象にせず、「対象のリポジトリを決められない」ことを示すメッセージで止まる（assess は終了コード 2、drive は中断）
- [ ] AC5: 単独起動の最終ゲートのプロンプトは、cross-review の駆動を打つ作業ディレクトリ（対象のリポジトリ）を示す

最終ステータスの決め方（#1656）:

- [ ] AC6: 単独起動の最終ゲートで結果ファイルに入る最終ステータスは、cross-review の状態ファイルに対する `loop_drive.review_status()` の値と一致する。少なくとも次の 4 つの状態で一致を確かめる: (a) final=approved・sweep.verified=true・remaining_open=0・commit=null → `approved`、(b) final=approved・sweep.verified=false → `unverified`、(c) final=approved・sweep.commit が null でない → `unverified`、(d) final=max_rounds → `max_rounds`
- [ ] AC7: 最終ステータスを決める規則（approved とみなす条件）は `loop_drive.review_status` の 1 か所だけにある。`drive.py` の最終ゲートのプロンプトと `SKILL.md` は、その規則を文として写さない
- [ ] AC8: (b) の状態で単独起動を finalize まで進めると、配分の履歴に行が足されず、結果 JSON の `metrics.review_status` は `unverified` になる
- [ ] AC9: `SKILL.md` の終了コード 23 の行は、Draft を解除するのは最終ステータスが `approved` のときだけで、それ以外は Draft のまま最終ステータスを報告して止まると書く
- [ ] AC10: `drive.py` に使われない import（`review_status`）が残らない（使うか、消す）

退行しないこと:

- [ ] AC11: supervise から起動される経路（`supervise_lib/worker_steps.py` が `metrics.review_status` を結果ファイルへ写す経路）の結果ファイルの形 `{"review_status": "<値>"}` と、終了コードの表（0・23・1）は変わらない
- [ ] AC12: ai-plugins の中で今の打ち方（Skill のディレクトリから打つ）をした場合も、対象のリポジトリは ai-plugins のまま決まる（今の振る舞いを壊さない）か、AC4 のメッセージで止まる。黙って別のリポジトリを対象にしない
- [ ] AC13: 既存のテスト（`plugins/ndf/skills/cross-refactoring/scripts/tests`・`plugins/ndf/scripts/tests`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 最終ステータスの規則の持ち手が 1 つになる（AC7）。打つ場所の規則は cross-review と共有できる形にする（cross-review 側の直しで同じ役割の関数を Skill ごとに分けない） |
| システム環境 | Claude Code / Codex / Kiro / agy のどのランタイムでも、`SKILL.md` の手順がインストール先のパスに依らずに打てる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `SKILL.md` の打つ場所が変わる。`drive.py` / `refactor.py` に引数を足すかは設計が決める（未決 1）。足す場合も、省いたときの振る舞いは cwd から決める今の形を保つ |
| データ | 結果ファイル・状態ファイル・配分の履歴の形は変わらない。履歴へ入る条件が実装どおり（`unverified` を入れない）になる |
| 既存の振る舞い | 単独起動の最終ゲートの最終ステータスが `unverified` になる場面が出る（今はプロンプトの規則で `approved` になっていた） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/cross-refactoring plugins/ndf/scripts/tests -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`・`claude plugin validate .` |
| 手動確認 | 配布後、ai-plugins 以外の GitHub リポジトリ（Draft の Pull Request あり）で `SKILL.md` の assess と drive の行をそのまま打ち、assess が 0 か 3、drive が init を越えることを確かめる（リリース後テスト） |

AC2・AC3・AC4 は、一時ディレクトリに作った git リポジトリ（origin に GitHub の URL を置く）を cwd にしたテストで確かめる。GitHub への問い合わせは既存のテストと同じく差し替える。AC6・AC8 は cross-review の状態ファイルの 4 つの形を与えたテストで確かめる。

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`（Skill の実体は `plugins/ndf/skills/`、共通ライブラリは `plugins/ndf/scripts/lib/`）。同じ役割の関数は共通ライブラリに 1 つだけ置く（Value 6） |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`・`python3 scripts/check-skill-frontmatter.py` |
| テスト戦略 | スクリプトの振る舞いを単体テストで確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`SKILL.md` に書くコマンドを書く前に実際に打って確かめる |
| 確認してから行う | `drive.py` / `refactor.py` の引数を足すこと（公開インタフェースの変更。設計の承認で確認する） |
| 行わない | cross-review の単独起動の手順の変更（前提 5）、supervise の経路の変更、配分の履歴の過去の行の訂正 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 対象のリポジトリの決め方: `SKILL.md` の打つ場所を対象のリポジトリへ書き換えてスクリプトを絶対パスで呼ぶか、スクリプトに対象のリポジトリを引数で受けさせるか（両方か） | 設計（`design`） | 設計の承認まで |
| 2. 最終ステータスを書く担い手: プロンプトが LLM に cross-review の `metrics.review_status` を写させるか、`drive.py` が cross-review の状態ファイルを読んで `review_status()` で決める（LLM に書かせない）か | 設計（`design`） | 設計の承認まで |
| 3. cross-review の単独起動の同じ問題（前提 5）を起票するか、既存の課題があるか | 設計の工程で範囲外として起票する（`out-of-scope`） | 設計 PR を出すまで |
