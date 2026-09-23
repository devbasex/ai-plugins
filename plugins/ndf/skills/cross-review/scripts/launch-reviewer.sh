#!/usr/bin/env bash
# cross-review レビュワー起動の入口（4 ランタイム共通）。
#
# Usage: launch-reviewer.sh <seat> <STATE_PR> <ROUND>
#
#   seat         claude | codex | agy | kiro（同じランタイムの 2 つ目は `-2`〜`-9` を付ける）
#
# 引数 STATE_PR は state.json の key (= 最初に init した PR 番号)。
# レビュー対象の PR は state.json の `current_pr` を読む。
#
# **ランタイムごとにファイルを分けない。** 担当は 4 ランタイムのどれでもなりうる
# （既定の母集合は claude / codex / kiro とホストで、agy は `--include agy` で足す）。
# 担当ごとに起動スクリプトを持つと同じ内容が 4 本に散る。CLI ごとの違い（プロンプトの渡し方・
# 作業領域の宣言・実行時間の上限）は共通層の `lib/launch-cli.sh` が 1 箇所で持つ。
#
# 注意:
#   - 既存コメントは**プロンプトへインライン埋め込み**する。読み取りの往復が要らず、
#     作業領域の外を読ませずに済む。
#   - 完了判定は monitor.py が pidfile + result.json で多軸判定する。
#
# **席の名前で受ける。** 使える者が 2 者に満たないラウンドでは、同じランタイムの 2 つ目が
# 席に入る（設計の決定 10）。起動する CLI は `${SEAT%%-*}` で選び、結果ファイルの名前は
# 席の名前で組む。両者を分けないと、2 つの席の結果が同じファイルを奪い合う。
#
# 状態ファイル: $TMP_DIR/<seat>-review-pr<STATE_PR>-{result,err,stdout,pid}.json
# (パスは STATE_PR ベースで固定 — monitor.py / state.py と一致させる。)

set -euo pipefail

SEAT=${1:?seat required}
STATE_PR=${2:?STATE_PR required}
ROUND=${3:?ROUND required}
# 席の名前の形（`lib/assignment.py` の `SEAT_PATTERN` と同じ規則）。
if [[ ! $SEAT =~ ^(claude|codex|agy|kiro)(-[2-9])?$ ]]; then
  echo "受け付けられない席の名前です: $SEAT" >&2; exit 1
fi
RUNTIME=${SEAT%%-*}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

STATE=$TMP_DIR/cross-review-pr$STATE_PR-state.json
[ -s "$STATE" ] || { echo "state.json not found: $STATE" >&2; exit 1; }

load_context() {
WORKTREE=$(jq -r '.worktree_path' "$STATE")
REPO=$(jq -r '.repo' "$STATE")
EXTRA_REVIEW_INSTRUCTIONS=$(jq -r '.review_instructions // .extra_review_instructions // ""' "$STATE")
# PR (=current_pr) は gh コマンドのレビュー対象 PR 番号として使う。
# tmp パス側は STATE_PR で固定 (monitor.py / state.py との読み書き整合のため)。
PR=$(jq -r '.current_pr' "$STATE")
# head のコミットは state.json から読む（#271）。`start-round` が同期のときに解決した
# 値をラウンドへ記録しており、codex と agy が同じ値を別々に取る必要が無い。
# 前の版の state.json から再開したときだけ、従来の `gh pr view` へ落ちる。
SHA=$(jq -r '(.rounds[-1].head_sha // "")' "$STATE")
[ -n "$SHA" ] || SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
}

prepare_prompt_context() {
# 前ラウンドの結果を残さない。担当が止まって今ラウンドの result.json が
# 書かれなかったとき、state.py read-result が**前ラウンドの結果を読んで**
# 同じ判定を繰り返す事故を防ぐ。
# 一時の名前のファイルも消す。前の起動が書きかけで止まった残りを、改名の対象に
# しないため。
rm -f "$TMP_DIR/$SEAT-review-pr$STATE_PR-result.json" \
      "$TMP_DIR/$SEAT-review-pr$STATE_PR-result.json.tmp" \
      "$TMP_DIR/$SEAT-review-pr$STATE_PR-round$ROUND-payload.json" \
      "$TMP_DIR/$SEAT-review-pr$STATE_PR-round$ROUND-payload.json.tmp" \
      "$TMP_DIR/$SEAT-review-pr$STATE_PR-round$ROUND-api-payload.json"

STEM=$TMP_DIR/$SEAT-review-pr$STATE_PR
PROMPT=$STEM-prompt.md
# 既存コメントは **プロンプトにインライン埋め込み** する。
# tmp dir は `<worktree>/.cross_review/` を使うが、埋め込みなら読み取りの往復が
# 要らないので確実である。
EXISTING_FILE=$TMP_DIR/cross-review-pr$STATE_PR-existing-comments.txt
if [ -s "$EXISTING_FILE" ]; then
  EXISTING_INLINE=$(cat "$EXISTING_FILE")
else
  EXISTING_INLINE="(なし)"
fi
# 前のラウンドからの変更の節（#542 の決定 4）。`state.py start-round` が、同じ PR の
# 前のラウンドと head が違うときだけ書く。無ければ何も入れない。
CHANGES_FILE=$TMP_DIR/cross-review-pr$STATE_PR-round$ROUND-changes.md
CHANGES_BLOCK=
if [ -s "$CHANGES_FILE" ]; then
  CHANGES_BLOCK=$'\n'$(cat "$CHANGES_FILE")
fi
EXTRA_REVIEW_BLOCK=
if [ -n "$EXTRA_REVIEW_INSTRUCTIONS" ]; then
  EXTRA_REVIEW_BLOCK=$(cat <<EXTRA_EOF

## 追加レビュー観点
以下の観点を通常レビューに追加して重点的に確認してください。
ただし、根拠がある修正アクションだけを指摘し、重複指摘や好みの nit は避けてください。

\`\`\`
$EXTRA_REVIEW_INSTRUCTIONS
\`\`\`
EXTRA_EOF
)
fi
}

render_review_prompt() {
cat > "$PROMPT" <<EOF
# cross-review のレビュー ($SEAT / round $ROUND)

PR #$PR を **$SEAT の観点でレビューし、指摘を 2 つのファイルへ書いて** ください。
**PR への投稿は行わない。** 投稿はレビューを回す側がこの 2 つのファイルから行う。

## 必須コンテキスト
- repo: $REPO
- PR: #$PR
- commit_id (headRefOid): $SHA
- worktree: $WORKTREE （**ファイル読み取りは必ず此処の絶対パスを使う**）

## 既存コメントスナップショット（重複指摘禁止）
workspace 外を読まなくて済むよう、以下にインライン展開する:

\`\`\`
$EXISTING_INLINE
\`\`\`
$CHANGES_BLOCK
$EXTRA_REVIEW_BLOCK

## 出し切り
- **見つけた指摘はこのラウンドですべて出す。次のラウンドへ回さない。** 重要度が minor のものも書く
  （出すのは修正アクションのある指摘だけで、下の「含めてはいけないもの」は変わらない）

## 指摘に **含めてはいけないもの**（Resolve 負荷を増やすため）
- ❌ **「良い点」/「Strengths」/「評価できる点」** — 総評にも書かない
- ❌ **対応アクションが無い指摘** — 観察・感想・現状説明だけは禁止
- ❌ **nit / スタイル指摘** — 好みの問題は指摘にしない (無視する)
- ❌ **コード引用 (\`\`\` ... \`\`\`) だけで指摘内容が無い指摘**
- ❌ **判定 \`COMMENT\` での雑感** — 直すべき点が無ければ \`APPROVE\` にする

### 指摘の書式
- \`[重要度 / カテゴリ]\` プレフィックス必須 (例: \`[major / 正確性]\`)
- 重要度は \`critical\` / \`major\` / \`minor\` のみ使う (nit は指摘にしない)
- 本文は **1 指摘 = 1 修正アクション** で完結させる。1〜2 文で具体的な修正提案を書く
- 指す行が分かる指摘は \`path\` と \`line\` を埋める。**差分の外の行でもよい**
  （差分の外を指す指摘は、投稿する側が総評へ移す）
- 設計レベル・PR 横断の指摘で行を指せないものは、\`path\` / \`line\` を省く

### 総評（\`summary\`）の書き方
- 設計レベル・PR 横断の **修正提案のみ** 書く
- 書くことが無ければ 1 行サマリだけで良い (褒め言葉や評価文は不要)

### 進捗マーカー（監視用）
- 無言ハングと区別できるよう、作業フェーズが進むたびに
  **$STEM-progress.log** へ短い 1 行を追記すること
- 内部の推論や長い説明は書かず、以下のようなフェーズ名 + 対象だけを書く:
  - \`start: review PR #$PR round $ROUND\`
  - \`scan: diff and existing comments\`
  - \`analyze: candidate findings\`
  - \`write: payload and result\`
  - \`done: result.json written\`

## 書くファイル（2 つ）

**どちらも一時の名前で書き終えてから、正式の名前へ改名する。改名の順序は控えが先、
結果ファイルが後である。** 結果ファイルが正式の名前で現れたことが、2 つとも書き終えた
印になる。途中で止まったときは正式の名前のファイルを残さない。

1. 指摘の控えを **$STEM-round$ROUND-payload.json.tmp** に書く:
   \`\`\`json
   {
     "summary": "総評（1〜数行）",
     "comments": [
       {"path": "src/foo.py", "line": 42, "body": "[major / 正確性] ...",
        "severity": "major", "evidence": "...", "falsification": "...",
        "suggested_check": "..."}
     ]
   }
   \`\`\`
   - **\`comments[]\` に載せるのは、あなたが出した指摘の全件である**
   - \`evidence\` は根拠（対象のコードと到達経路）、\`falsification\` は反証条件
     （これが成り立てば棄却できる）、\`suggested_check\` は実行できる検証手順
   - **根拠と反証条件は、別の担当がその指摘を確かめるためのものである。** 確かめられない
     書き方（「一般によくない」など）は根拠にならない
2. 判定を **$STEM-result.json.tmp** に **必ず以下のキーだけで** 書く:
   \`\`\`json
   {
     "event": "REQUEST_CHANGES",
     "by_severity": {"critical": 0, "major": 1, "minor": 0, "nit": 0}
   }
   \`\`\`
   - \`event\` は本来の判定で、\`APPROVE\` / \`REQUEST_CHANGES\` / \`COMMENT\` のいずれか。
     \`intent\` などの別名は使わない
3. 改名する（**この順で**）:
   \`\`\`bash
   mv "$STEM-round$ROUND-payload.json.tmp" "$STEM-round$ROUND-payload.json"
   mv "$STEM-result.json.tmp" "$STEM-result.json"
   \`\`\`

## 守るべきこと
- **発見を終えるまで、参照してよい既存コメントは起動時に渡されたスナップショットに
  限る。** 同じラウンドの他の担当の結果ファイル・進捗ログは参照しない
  - **担当は並列に起動する。** 他の担当の指摘を読むと、独立に見つけた指摘と区別できなく
    なる。同じ指摘が 2 者から出たことに意味があるのは、互いを見ていない場合だけである
- **リポジトリ編集禁止。PR・GitHub・git への書き込みもしない**
- **テストを実行しない。** 実行して確かめる手順は \`suggested_check\` に書く
  （進行側の \`verify-findings\` が実行する）
- **背景で処理を起動しない。** 起動した処理の終わりを待たずに結果のファイルを書かないまま
  終わると、結果が無い担当として扱われる
- worktree 外のパスは、上の 2 つのファイルと進捗マーカー以外に触らない
EOF
}

launch_reviewer() {
# 結果ファイルの置き場所が作業ツリーの外にあるときだけ、作業領域へ足す。
# `<worktree>/.cross_review/` を使う既定の配置では足さない。
WORKTREE_ABS=$(cd "$WORKTREE" && pwd -P)
TMP_ABS=$(cd "$TMP_DIR" && pwd -P)
EXTRA_DIR=
case "$TMP_ABS/" in
  "$WORKTREE_ABS"/*) ;;
  *) EXTRA_DIR=$TMP_ABS ;;
esac

# 実行時間の上限は工程名で渡す。共通層が上限の表から「監視の上限 + 120 秒」を導き、
# 打ち切りの判断を監視の側へ一本化する（#598 / #537）。
"$SCRIPT_DIR/../../../scripts/lib/launch-cli.sh" "$RUNTIME" "$WORKTREE_ABS" "$PROMPT" "$STEM" "" \
  "$EXTRA_DIR" review
}

load_context
prepare_prompt_context
render_review_prompt
launch_reviewer
