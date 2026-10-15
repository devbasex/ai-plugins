# #893 ndf の版ごとのトークン消費と所要時間 — 要求と受け入れ条件

モード: light（リポジトリの開発用スクリプトと集計値の記録を足す。配布物 `plugins/` の振る舞いは変えない）

## 依頼（原文）

> ndf の版が上がるたびに、トークン消費と所要時間がどれだけ変わったかを、後から版ごとに比べられるようにする。

この Pull Request の範囲（conductor が絞った内容）:

- 含む: #827 の使い捨ての集計（`measure.py` / `poll.py` / `report.py`）をもとに `scripts/` へ集計スクリプトを入れる。codex / kiro の記録から cross-review / cross-refactoring の外部 CLI の分を足す。今ある記録から 10.14〜10.17.x の基準を集計し、集計値だけを消えない場所へ保存する
- 含まない: `release` へ「配布ごとに集計を残す」手順を足すこと（Skill の振る舞いの変更で standard のため別 PR）

## 決めたこと

| 項目 | 決定 | 理由 |
| --- | --- | --- |
| 置き場所 | `scripts/token-usage.py`（新設）。`skill-stats` へは統合しない | 統合すると配布物の Skill を変える（light を外れる）。持ち場の語彙だけは `plugins/ndf/scripts/lib/transcript_agents.py` から読み込み、2 か所に持たない |
| 集計値の保存先 | `docs/metrics/ndf-token-usage/<集計日>.md` と同名の `.json` | `docs/` はリポジトリ知識の置き場で、記録の保存期間（90 日）に左右されない |
| 保存する値 | 版・モード・モデル・Claude Code の版・持ち場ごとの件数・トークン・換算費用・所要時間だけ | 会話の本文・ファイルのパス・リポジトリ名・会話の ID は保存しない（他社のリポジトリ名を含むため） |

## 前提

- 前提: 会話の ndf の版は、記録中の `Base directory for this skill: …/ai-plugins/ndf/<版>/skills/` の最初の出現で決める。Skill を 1 度も起動しない会話は集計に入らない
- 前提: モードは `progress-record.sh` / `projects-sync.sh` へ渡した `--mode <値>` / `mode "<値>"` の最多の値。渡していない会話は `不明`、2 種類以上なら `混在`
- 前提: 「PR 1 本」は会話（配下のサブエージェントを含む）の中で `gh pr create` が返した Pull Request の URL の数で数える。0 本の会話は PR あたりの表から外し、件数だけを示す
- 前提: 所要時間は、会話の行の時刻の差を 30 分で打ち切って足した値（利用者の返答を待つ時間を入れない）。サブエージェントは最初と最後の行の差
- 前提: 外部 CLI は作業ディレクトリ `/tmp/ndf-worktrees/<所有者>--<リポジトリ>/(pr|rf)<番号>` で cross-review（`pr`）/ cross-refactoring（`rf`）を見分け、同じ文字列を記録中に含み、時刻が会話の範囲に入る会話へ寄せる
- 前提: 換算費用は #827 と同じ重み（input 1 / cache read 0.1 / cache write 5 分 1.25・1 時間 2 / output 5）

## 読めないランタイム

| ランタイム | 状態（2026-09-23 に確認） | 扱い |
| --- | --- | --- |
| codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`。`event_msg` の `token_count` に累計の usage、`turn_context` にモデル | トークンを集計する |
| kiro | `~/.kiro/sessions/cli/<id>.json`（2026-07-30 以降）。`user_turn_metadatas` のトークン数は 380 件すべて 0 で、`metering_usage` の credit と `turn_duration` だけが値を持つ | **トークンは読めない。** credit と所要時間を集計する |
| claude（cross-review の席） | `~/.claude/projects/` のうち作業ディレクトリが `/tmp/ndf-worktrees/` の会話 | 外部 CLI の分として集計する |
| agy | `~/.gemini/antigravity-cli/conversations/*.db`（会話ごとの SQLite） | **読まない。** 中身の形式を確かめていない。既定の担当からも外れている（cross-refactoring） |

## 受け入れ条件

- [ ] AC1: `python3 scripts/token-usage.py` の 1 回で、ndf の版ごとの「PR 1 本あたり」の表と「持ち場ごと」の表が Markdown で出る。表にはトークン（入力の合計・出力）・換算費用・所要時間が入る
- [ ] AC2: 表の軸を `--by` で選べる。選べる軸は ndf の版・モード・モデル・Claude Code の版・cross-review の担当の組み合わせ
- [ ] AC3: 「PR 1 本あたり」の表に外部 CLI（codex のトークン、kiro の credit、claude の席のトークン）の列が入る
- [ ] AC4: `--format json` で同じ集計値を JSON で出す。出力に会話の本文・ファイルのパス・リポジトリ名・会話の ID が入らない
- [ ] AC5: 小さな合成の記録を入力にしたテストが、版・持ち場・モード・PR の数え方・外部 CLI の寄せ方を確かめる
- [ ] AC6: 10.14.0〜10.17.x の集計値が `docs/metrics/ndf-token-usage/` に保存され、記録が消えた後も読める

## 検証手段

- `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest scripts/tests/test_token_usage.py -q`
- 全体: `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q -n 4`
- 実物の記録で `python3 scripts/token-usage.py --min-version 10.14.0` を実行し、表が出ることを見る

## 境界

- 行わない: 配布物（`plugins/`）の変更、`cleanupPeriodDays` の変更、生の記録や本文のコミット
