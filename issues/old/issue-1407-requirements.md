# #1407: 工程の単位「ミッション」を「スプリント」へ改名する（MVV の Mission と語が重なるため）

正は課題の本文（#1407）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ミッションMVVというのは用語が被ってしまってますね。MVVは変えられないので、プロジェクトのサブ単位である「ミッション」の名称を変えましょう。
> スプリントで行きましょう（2026-09-28 利用者の決定）

## 目的

NDF の工程の単位「ミッション」（1 つの版として出す課題と Pull Request のセット）は、MVV の Mission（使命）と同じ語で、「ミッション MVV」のように並ぶと 1 語 1 意味が崩れる。MVV は変えられないため、工程の単位の側を **スプリント** へ改名する。

達成したい状態: 工程の単位を指す語・コマンド・ブランチ・ファイル名がすべて「スプリント」（`sprint`）になり、「ミッション」「Mission」は MVV の Mission の意味だけで使われる。改名の前に始めたスプリント（旧: ミッション）も止まらずに再開できる。

## 今の状態（2026-09-28 に調べた）

- 用語集: 「ミッション」「ミッション状態ファイル」「ミッションブランチ」「ミッション課題」「ミッション MVV」（`docs/glossary/glossary.json`、定義は `plugins/ndf/skills/development-workflow/references/glossary.md`）。ほかに「レッドライン」「MVV」「MVV 判定」「根拠の項目」「実行計画」「マイルストーン」「リリース記録」「conductor」の意味の文に「ミッション」が入る
- `plugins/ndf` の 74 ファイルに 581 か所（`grep -ro ミッション plugins/ndf`）。指示書は `AGENTS.md` 3 か所・`CLAUDE.md` 1 か所。NDF の共通原則（`plugins/ndf/scripts/data/ndf-common-principles.md`・`issues/ndf-common-principles.md`）に「ミッション（1 つの版として出す仕事のまとまり）」がある。`.ndf/` には 0 件
- 公開の形:
  - コマンド: `supervise.py new mission`・`supervise.py new check --mission <状態>`
  - ブランチ: `mission/<名前>`（`worktree-setup.sh` が課題の worktree の起点に使う）
  - スクリプト: `mission-state.py`・`mission-close.py`・`lib/mission_mvv.py`・`supervise_lib/mission.py`・`supervise_lib/mission_waves.py`
  - 目録: `mission.json`（キーは `ミッション`・`ブランチ`・`ステージ`）。JSON のキー `mission`・`mission_mvv`・`mission_prs`
  - `scripts/script-structure-allow/` の許可の 2 件が `mission-state.py` のパスを持つ
- 進行中の状態: `~/.local/state/ndf/sv/` に 10 件。7 件が `mission.json` を持つ。プランの JSON（`*-state` を含む）は `mission-state.py` などのスクリプトの絶対パスをコマンドの文字列に埋めている
- 候補の衝突（grep）: スプリントは用語集・`plugins/ndf` とも 0 件。バンドル（用語集にある）・バッチ（本文 10 か所）・ウェーブ（`mission_waves.py`）は衝突

## 前提

- 前提 1: 新しい名前は次の対応で決める。語: ミッション → スプリント、ミッション状態ファイル → スプリント状態ファイル、ミッションブランチ → スプリントブランチ、ミッション課題 → スプリント課題、ミッション MVV → スプリント MVV。コードの名前: `mission` → `sprint`（`supervise.py new sprint`・`--sprint`・`sprint/<名前>`・目録 `sprint.json`・`sprint-state.py`・`sprint-close.py`・`lib/sprint_mvv.py`・`supervise_lib/sprint.py`・`supervise_lib/sprint_waves.py`・キー `sprint`・`sprint_mvv`・`sprint_prs`）
- 前提 2: スプリント MVV の項目の番号の形（根拠の項目の「ミッション Value 4」→「スプリント Value 4」）は改名に合わせて変える。`R<番号>` はそのまま
- 前提 3: 旧名の受け付け（AC4）は、この改名を載せた正式版の次の正式版まで残し、その次の版で外す。版数はリリースで決まるため、決めの記録には「改名を載せた正式版 + 1 つ」の規則と、決まった時点の版数を書く
- 前提 4: 旧名のスクリプト（`mission-state.py`・`mission-close.py`）は、新しい名前のスクリプトを呼ぶ薄い入口として残す。既存のプランの JSON がパスを埋めているためである（AC5）。旧名のモジュール（`lib/mission_mvv.py` など）はリポジトリの外から読まれないため残さない
- 前提 5: 目録は、新しい名前（`sprint.json`）が無く旧名（`mission.json`）があれば旧名を読む。旧名のファイルをその場で書き換えない（戻すときに旧版の NDF が読めなくなるため）
- 前提 6: 過去の記録（`CHANGELOG.md`・`docs/ndf-version-decisions.md`・`issues/` の既存の文書・`docs/development-history/`・`docs/presentations/`・`docs/articles/`・既存の課題と Pull Request の本文）は書いた時点の記録として書き換えない
- 前提 7: `AGENTS.md` と `CLAUDE.md` の運用の節の書き換えは C7 に当たり、NDF の共通原則の書き換えは共通原則の改訂に当たる。どちらも中身（規則）を変えず語だけを置き換える範囲に限り、設計の承認（承認ゲート 1）で利用者の承認を得てから行う
- 前提 8: 実施は進行中の m1389・m1400 を今の名前で通し切った後に始める。#1400 の設計・実装で足した語もこの課題でまとめて置き換える

## 対象範囲

含む:
- 用語集（`docs/glossary/glossary.json`・`plugins/ndf/skills/development-workflow/references/glossary.md`・`docs/glossary.md`）
- `plugins/ndf` の Skill・エージェント定義・README・`references/`・スクリプトとテスト・フックの本文
- `docs/`（前提 6 の過去の記録を除く）・`README.md`・`scripts/script-structure-allow/` の該当の許可
- `AGENTS.md`・`CLAUDE.md`・NDF の共通原則の語（前提 7 の承認を得た範囲）
- 旧名の受け付けと案内（`new mission`・`--mission`・`mission/` のブランチ・`mission.json`・旧名のスクリプト）
- 決めの記録への、旧名の受け付けをやめる版の規則（前提 3）

含まない:
- MVV の Mission（使命）の意味の「ミッション」「Mission」
- GitHub のマイルストーンの名前と規則（`AGENTS.md` の「版と配布の方針」）
- 前提 6 の過去の記録
- 既存の課題（#1272・#1227・#1273・#1271・#1298 など、タイトルに「ミッション」がある課題）のタイトルと本文
- 既存のリモートのブランチ（`mission/*`）の改名と削除
- 工程の中身（プランのステージ・承認ゲート・pace の振る舞い）の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 用語集の語を置き換え、旧名を廃止した語に載せた | 実装の最初の変更 | 用語チェックが落ちた → 本文の置き換えの漏れを直す（AC1・AC2） | m1389・m1400 の完了（前提 8） |
| E2 | 本文・コード・ファイル名をスプリントへ置き換えた | E1 | 既存のテストが落ちた → 置き換えの漏れを直す（AC6） | E1 |
| E3 | 利用者がスプリントを始めた（`supervise.py new sprint`） | 利用者か conductor | 旧名 `new mission` で呼ばれた → 同じ振る舞いで動き、新しい名前を案内した（AC4） | E2 |
| E4 | スプリントブランチを作った（`sprint/<名前>`） | E3 | `sprint/<名前>` が無く、`mission/<名前>` がある → `mission/<名前>` を使い続けた。両方あれば `sprint/<名前>` を使った（AC4・AC5） | E3 |
| E5 | 改名の前に始めた実行を再開した | 利用者か conductor が既存のプランを `queue` に渡した | `sprint.json` が無い → `mission.json` を読んだ。旧名のスクリプトのパスを呼んだ → 新しいスクリプトへ渡った（AC5） | E2 |
| E6 | 旧名の受け付けを外した | 前提 3 の版のリリース | 旧名で呼ばれた → 新しい名前を示して終了コード 0 以外で止まった（AC7） | E3〜E5 の版を 1 つの正式版として出した後 |

## 用語

| 用語 | 意味 |
| --- | --- |
| スプリント | 1 つの版として出す課題と Pull Request のセット。期間ではなく、1 つの版として出す中身で切る。工程はスプリント単位で 1 回ずつ通し、モードもスプリントで 1 つにする |
| スプリント状態ファイル | スプリントのプラン・done・承認ゲートの記録・MVV・版を持つファイル（パスは呼ぶ側が決め、手順書の例は `sprint-state.json`。目録 `sprint.json` とは別のファイル） |
| スプリントブランチ | 課題の Pull Request を集め、ベースブランチへの Pull Request をスプリントで 1 本にするブランチ（`sprint/<名前>`） |
| スプリント課題 | スプリントに含まれる Pull Request の本文が、閉じる語で指す課題 |
| スプリント MVV | スプリント単位の MVV。プロジェクト MVV の範囲での具体化 |

## 受け入れ条件

- [ ] AC1: 用語集で、「ミッション」「ミッション状態ファイル」「ミッションブランチ」「ミッション課題」「ミッション MVV」の 5 語が「用語」の表の 5 語に置き換わり、旧名がそれぞれの `deprecated` に載る。「スプリント」の意味に「期間ではなく 1 つの版として出す中身で切る」が入る。ほかの語の意味の文にも工程の単位の意味の「ミッション」が残らない
- [ ] AC2: 対象範囲の本文（前提 6 の過去の記録を除く）で `grep -rn ミッション` に当たる行が、MVV の Mission（使命）の意味の行だけになる。`python3 plugins/ndf/scripts/glossary.py check --diff origin/develop --rules all` が 0 で終わる
- [ ] AC3: `supervise.py new sprint --name M ...` が今の `new mission` と同じプランを書き出し、目録を `sprint.json`・スプリントブランチを `sprint/<名前>` として作る。`supervise.py new check --sprint <状態>` が今の `--mission` と同じに動く。スクリプト・モジュール・JSON のキーが前提 1 の名前になる
- [ ] AC4: 前提 3 の版まで、旧名 `supervise.py new mission`・`--mission`・旧名のスクリプト（`mission-state.py`・`mission-close.py`）が新しい名前と同じ結果を返し、標準エラーに新しい名前を 1 行で案内する。`sprint/<名前>` のブランチが無く、既存の `mission/<名前>` のブランチがあるスプリントだけが `mission/<名前>` を使い続ける（両方あれば `sprint/<名前>` を使う）
- [ ] AC5: 改名の前に `~/.local/state/ndf/sv/` に書かれたプランと `mission.json`（キー `ミッション`・`ブランチ`・`ステージ`）を持つ実行を、改名の後の NDF の `supervise.py queue` に渡すと、止まった所から再開する。`mission.json` はその場で書き換えられない
- [ ] AC6: 既存のテスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る。AC3〜AC5 の旧名と新名の両方の呼び方にテストがある
- [ ] AC7: 決めの記録（設計の決定の記録と、リリースの時の `docs/ndf-version-decisions.md`）に、旧名の受け付けをやめる版の規則（前提 3）が書かれる。受け付けをやめた版で旧名を呼ぶと、新しい名前を示して終了コード 0 以外で止まる（外す作業は別の課題で行う）
- [ ] AC8: 退行しないこと: GitHub のマイルストーンの名前の規則・`AGENTS.md` の「版と配布の方針」の規則・MVV の Mission の意味の文・プランのステージと承認ゲートの振る舞いが変わらない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 旧名の受け付けは 1 か所（新しい名前へ渡す入口）にまとめ、前提 3 の版で消せる形にする。同じ役割の関数を旧名と新名で 2 つ持たない |
| 移行性 | 改名の前に始めた実行が、利用者の操作なしで再開できる（AC5）。旧版の NDF へ戻しても、改名の前の状態ファイルが読める（前提 5） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`supervise.py new mission` → `new sprint`・`--mission` → `--sprint`・`mission/` → `sprint/`・スクリプトの名前。旧名は前提 3 の版まで受け付けて案内する |
| データ | 状態ファイルの名前とキーが変わる。旧名のファイルは読むだけで移さない（前提 5） |
| 既存の振る舞い | 語と名前だけが変わり、工程の中身は変わらない（AC8） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・`claude plugin validate .` |
| 用語 | `python3 plugins/ndf/scripts/glossary.py check --diff origin/develop --rules all`・`grep -rn ミッション plugins/ndf docs AGENTS.md CLAUDE.md README.md`（残りが MVV の意味だけか） |
| 手動確認 | AC5: 改名の前の実行の写し（`~/.local/state/ndf/sv/` の 1 件を一時ディレクトリへ写したもの）を改名の後の `supervise.py queue` に渡し、止まった所から進むことを conductor が見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`（Skill は `plugins/ndf/skills/`、スクリプトは `plugins/ndf/scripts/`。既定で働く変更のため安定版の経路） |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`・`AGENTS.md`（`.md` の文言を固定するテストを書かない）・用語集（`docs/glossary/glossary.json`） |
| テスト戦略 | AC3〜AC5・AC7 は `plugins/ndf/scripts/tests/` の単体テスト。AC1・AC2 は用語チェックと grep で見る（文言のテストは書かない） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、用語チェック、旧名から新しい名前への案内 |
| 確認してから行う | `AGENTS.md`・`CLAUDE.md` の運用の節と NDF の共通原則の語の置き換え（前提 7、C7）、リモートの `mission/*` のブランチの扱い |
| 行わない | GitHub のマイルストーンの名前と規則の変更、過去の記録の書き換え（前提 6）、工程の中身の変更、旧名の受け付けを前提 3 の版より前に外すこと |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 旧名の受け付けを外す作業を起票する時期（前提 3 の版が決まった時点か、この課題の振り返りか） | `design` | 設計 PR |
| 旧名の入口の形（`mission-state.py` を新しいスクリプトを呼ぶ数行の入口にするか、シンボリックリンクにするか） | `design` | 設計 PR |
| `AGENTS.md`・`CLAUDE.md`・NDF の共通原則の語の置き換えを認めるか（前提 7） | 利用者 | 承認ゲート 1 |

モード: `standard`（公開インタフェースの変更）
