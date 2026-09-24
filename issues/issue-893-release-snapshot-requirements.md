# #893 版を配布するたびに集計結果を残す — 要求と受け入れ条件

モード: standard（`release` の Skill の振る舞いを変える）。#893 の作業項目 4 だけを扱う。作業項目 1〜3・5・6 は PR #899 #965 で済んでいる。

設計は [issue-893-release-snapshot-design.md](issue-893-release-snapshot-design.md) にある。

## 具体例: 10.17.9 を正式版として出すとき

利用者が `/ndf:release` で 10.17.9 を出す。版と `CHANGELOG.md` を上げた配布の Pull Request に、次の 2 つが一緒に載る。

- `docs/metrics/ndf-token-usage/2026-09-30.md` / `.json`（集計日が 2026-09-30 の場合）
- 表の先頭の行は、前の記録（`2026-09-24.json`）の最後の版の 10.17.7。そこから 10.17.8・10.17.9-dev.1 までの行が続き、各行に前の行との差が付く

エージェントがしたのは、宣言された段を 1 行のコマンドで走らせることと、差が大きかった版の読み取りを数行書くことだけである。集計・表・比べるときの注意の機械的な行は、スクリプトが書く。

## 依頼（原文）

> 版を配布するたびに集計結果を残す手順を `release` に足す（**別 PR**。Skill の振る舞いの変更で standard のため。置き場所は集計スクリプトの PR が決めた `docs/metrics/ndf-token-usage/<集計日>.md` / `.json` に合わせる）

conductor からの補足（2026-09-24）:

- 集計スクリプトは `scripts/token-usage.py`（リポジトリの開発用で、配布物ではない）。`--until` で集計を作り直せる。記録は 90 日で消えるため、版ごとに残す意味がある
- `release` は他のリポジトリでも使われる。このリポジトリ固有の手順をどう置くかを決定の記録で決める
- どこまでスクリプトにできるかを決定の記録に残す（利用者の方針: 判断の要らない手順は `scripts/` へ、LLM には判断だけを残す）
- 前の版との差を比べる形は `docs/metrics/ndf-token-usage/2026-09-24.md`（10.17.0〜10.17.7）が実例

## 解釈

| 依頼の語 | 具体化 |
| --- | --- |
| 版を配布するたびに | **正式版（本番への配布）のたびに 1 回。** 開発版の配布では残さない。開発版の行は、次の正式版の記録に入る（設計の決定 3） |
| 集計結果を残す | 集計日の `.md` と `.json` を、配布の Pull Request に含めてコミットする |
| `release` に足す | `release` の本文には「リポジトリが宣言した配布の段を走らせる」ことだけを書く。`scripts/token-usage.py` の名前は本文に書かない（設計の決定 1） |
| 前の版との差 | 前の記録の最後の版から今までの版を行に並べ、各行に 1 つ上の行との比を付ける（2026-09-24.md の「10.17.x の版ごとの差」の形） |

## 前提

- 前提: 集計が読むのは、配布する人の手元の記録（`~/.claude/projects` / `~/.codex/sessions` / `~/.kiro/sessions/cli`）だけである。これまでの 2 つの記録と同じで、他の開発者の分は入らない
- 前提: 正式版は 90 日より短い間隔で出る。空いた場合、その間の開発版の記録は消える（未確認の U2）
- 前提: 集計日は打ち切りの時刻の UTC の日付で決める

## 受け入れ条件

- [ ] AC1: `release` の本文とその参照に、`scripts/token-usage.py` など ai-plugins に固有の語が現れない。`python3 scripts/check-skill-repo-assumptions.py` が 0 で終わる
- [ ] AC2: リポジトリが `.ndf/release.json` で配布の段を宣言すると、`release` の手順 3 で 1 つのコマンドがその段を実行する。宣言が無いリポジトリでは何も出力せず 0 で終わる
- [ ] AC3: 段の宣言には、走らせる段階（本番 / 検証 / 両方）と、書いてよい場所を書ける。段が書いてよい場所の外を変えたら、実行は 0 以外で終わる
- [ ] AC4: 宣言が読めない（JSON として壊れている・`version` が未対応・必須の項目が無い）とき、実行は 0 以外で終わり、どこが読めないかを出す
- [ ] AC5: このリポジトリの `.ndf/release.json` は、本番への配布で `scripts/token-usage-snapshot.py` を走らせる段を宣言する
- [ ] AC6: `token-usage-snapshot.py --released <版>` の 1 回で、集計日の `.md` と `.json` ができる。置き場所は `docs/metrics/ndf-token-usage/` である。同じ日の記録が既にあれば、上書きせずに別の名前で作る
- [ ] AC7: できた `.md` の表は、前の記録の最後の版から今までの版を行に持つ。入るのは次の 4 つである。「PR 1 本あたり」の表と前の行との比、版と層ごとの呼び出しとキャッシュの表、作り直すためのコマンド（打ち切りの時刻つき）、比べるときの注意の機械的な行
- [ ] AC8: 前の行との比が ±30% を超えた版を、スクリプトが一覧で知らせる。エージェントはその版の読み取りだけを `.md` へ書き足す
- [ ] AC9: 同じ打ち切りの時刻で走らせ直すと、記録が残っている間は同じ `.json` ができる
- [ ] AC10: 出力に会話の本文・ファイルのパス・リポジトリ名・会話の ID が入らない（PR #899 の AC4 と同じ）

## 対象範囲

含む:

- `release` の本文（手順 3 と完了の判定）に、宣言された配布の段を走らせる規定を足す
- 宣言 `.ndf/release.json` の形と、それを読んで実行する配布物のスクリプト
- このリポジトリの宣言と、集計を記録へ書き出すスクリプト `scripts/token-usage-snapshot.py`
- エージェントが判断する部分の手引き（`docs/metrics/ndf-token-usage/README.md`）

含まない:

- `scripts/token-usage.py` の集計の中身の変更（読み込み口を関数として呼べるようにするだけ）
- 開発版の配布での記録
- 他の開発者の手元の記録を集めること
- クラス図: 変更が型を定義しない（関数とスクリプトだけ）
- データ構造（ER 図・テーブル定義）: 永続データは Markdown と JSON のファイルで、表を持たない。JSON の形は入出力の契約の節に書く

## 検証手段

- `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest scripts/tests/test_token_usage_snapshot.py plugins/ndf/scripts/tests/test_release_steps.py -q`
- 全体: `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q -n 4`
- `python3 scripts/check-skill-repo-assumptions.py` / `python3 scripts/check-skill-frontmatter.py` / `claude plugin validate .`
- 実物: `python3 plugins/ndf/scripts/release-steps.py run --root . --stage production --version 10.17.9 --dry-run` が段を 1 つ挙げる

## 境界

- 常に行う: 既存テストの実行、配布物の同期（`bash scripts/build-runtime-plugins.sh`）
- 確認してから行う: `release` の手順の順序を変えること
- 行わない: `cleanupPeriodDays` の変更、生の記録のコミット、`release` の本文に ai-plugins の語を書くこと
