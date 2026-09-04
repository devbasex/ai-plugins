#!/usr/bin/env bash
# NDF plugin: issue の本文へ工程の進行を記録する。
#
#   progress-record.sh <issue番号> <工程名> [--mode M] [--worktree P] [--plan P]
#                      [--repo <所有者>/<リポジトリ>] [--note TEXT]
#
# **盤面の宣言が無いリポジトリでも進行が残る。** 記録先は issue の本文の `## 進行` の節で、
# 節の外は書き換えない（更新のたびに本文を取得し、その節だけを差し替える）。人が本文へ
# 書いた内容を消さないためである。
#
# `gh` が無い、issue を取得できない、工程名が一覧に無いときは何もせず終了コード 0 で終わる。
# **進行管理が理由で開発の工程を止めない。**
#
# 呼び出し側の誤り（引数不足・知らない工程名）だけは 2 を返す。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects-common.sh
. "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null || exit 0

SECTION_HEADING="## 進行"

usage() {
  printf 'usage: progress-record.sh <issue番号> <工程名> [--mode M] [--worktree P] [--plan P] [--repo R] [--note TEXT]\n' >&2
}

ISSUE="${1:-}"
STAGE="${2:-}"
shift 2 2>/dev/null || true

MODE= WORKTREE= PLAN= REPO= NOTE=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    --plan) PLAN="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --note) NOTE="${2:-}"; shift 2 ;;
    *) printf 'ERROR: 知らない引数です: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$ISSUE" ] || [ -z "$STAGE" ]; then
  printf 'ERROR: 引数が足りません\n' >&2
  usage
  exit 2
fi
case "$ISSUE" in
  ''|*[!0-9]*) printf 'ERROR: issue 番号が数値ではありません: %s\n' "$ISSUE" >&2; exit 2 ;;
esac
if ! pj_is_stage "$STAGE"; then
  printf 'ERROR: 工程表に無い工程名です: %s\n' "$STAGE" >&2
  exit 2
fi
if [ -n "$MODE" ] && ! pj_is_mode "$MODE"; then
  printf 'ERROR: 知らないモードです: %s\n' "$MODE" >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || exit 0

BODY_FILE=$(mktemp) || exit 0
NEW_FILE=$(mktemp) || { rm -f "$BODY_FILE"; exit 0; }
trap 'rm -f "$BODY_FILE" "$NEW_FILE"' EXIT

if [ -n "$REPO" ]; then
  gh issue view "$ISSUE" --repo "$REPO" --json body -q .body > "$BODY_FILE" 2>/dev/null || exit 0
else
  gh issue view "$ISSUE" --json body -q .body > "$BODY_FILE" 2>/dev/null || exit 0
fi

# 節の中身を組み立てる。**印を付けるのは、この呼び出しが記録する工程までである。**
# 一覧の残りは空欄のまま残し、飛ばした工程がチェックの穴として見えるようにする。
STAMP=$(date '+%Y-%m-%d %H:%M')
export PJ_STAGES SECTION_HEADING STAGE STAMP MODE WORKTREE PLAN NOTE
python3 - "$BODY_FILE" > "$NEW_FILE" <<'PY' || exit 0
import os
import re
import sys

body = open(sys.argv[1], encoding="utf-8").read()
heading = os.environ["SECTION_HEADING"]
stages = os.environ["PJ_STAGES"].split("\n")
stage = os.environ["STAGE"]
stamp = os.environ["STAMP"]

# すでにある節から、済んだ工程とその記録を読み取る。**書き直すのは節だけである。**
done: dict[str, str] = {}
mode = os.environ.get("MODE", "")
worktree = os.environ.get("WORKTREE", "")
plan = os.environ.get("PLAN", "")
# **見出しは行頭の単独の行として探す。** 部分一致で探すと、本文中の「## 進行状況」や
# 引用の中の同じ語に当たり、そこから次の見出しまでを節として差し替えてしまう。
found = re.search(r"^" + re.escape(heading) + r"[ \t]*$", body, re.M)
start = found.start() if found else -1
if start != -1:
    rest = body[start + len(heading):]
    m = re.search(r"^## ", rest, re.M)
    section = rest if m is None else rest[:m.start()]
    end = len(body) if m is None else start + len(heading) + m.start()
    for line in section.split("\n"):
        hit = re.match(r"- \[x\] (.+?)(?: — (.*))?$", line.strip())
        if hit:
            done[hit.group(1)] = hit.group(2) or ""
    meta = re.search(r"^モード: (\S+)(?: / 作業ツリー: `(.+?)`)?(?: / 計画: `(.+?)`)?$",
                     section, re.M)
    if meta:
        mode = mode or (meta.group(1) if meta.group(1) != "—" else "")
        worktree = worktree or (meta.group(2) or "")
        plan = plan or (meta.group(3) or "")
else:
    end = None

# **記録するのは「工程に入った時点」である。** 同じ工程を再び呼んでも時刻を書き換えない。
# 書き換えると、途中で止まった実行を再開したときに最初に入った時刻が失われる。
# 付随情報（`--note`）を新しく渡したときだけ、その分を足す。
note = os.environ.get("NOTE", "")
if stage in done and done[stage]:
    if note and note not in done[stage]:
        done[stage] = f"{done[stage]} / {note}"
else:
    done[stage] = " / ".join([stamp] + ([note] if note else []))

meta_parts = [f"モード: {mode or '—'}"]
if worktree:
    meta_parts.append(f"作業ツリー: `{worktree}`")
if plan:
    meta_parts.append(f"計画: `{plan}`")

lines = [heading, "", " / ".join(meta_parts), ""]
for name in stages:
    if name in done:
        lines.append(f"- [x] {name} — {done[name]}" if done[name] else f"- [x] {name}")
    else:
        lines.append(f"- [ ] {name}")
section_text = "\n".join(lines) + "\n"

if start == -1:
    updated = body.rstrip("\n") + "\n\n" + section_text
else:
    tail = "" if end is None else body[end:]
    updated = body[:start] + section_text + ("\n" + tail.lstrip("\n") if tail.strip() else "")
sys.stdout.write(updated)
PY

[ -s "$NEW_FILE" ] || exit 0
if cmp -s "$BODY_FILE" "$NEW_FILE"; then
  # 変える内容が無いときは書き込まない。同じ操作を 2 度行っても結果が変わらない。
  exit 0
fi
if [ -n "$REPO" ]; then
  gh issue edit "$ISSUE" --repo "$REPO" --body-file "$NEW_FILE" >/dev/null 2>&1 || exit 0
else
  gh issue edit "$ISSUE" --body-file "$NEW_FILE" >/dev/null 2>&1 || exit 0
fi
printf '#%s 進行 = %s\n' "$ISSUE" "$STAGE"
exit 0
