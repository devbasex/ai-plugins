#!/usr/bin/env bash
# NDF plugin: 設計 Pull Request の本文の「決めたこと」の節を、設計文書の決定の見出しと突き合わせる。
#
#   pr-body-decisions.sh check <PR番号> [--repo <所有者>/<リポジトリ>]
#   pr-body-decisions.sh sync  <PR番号> [--repo <所有者>/<リポジトリ>]
#
# **本文は決定の中身を持たず、設計文書の `## 決定の記録` の下の `### ` の見出しだけを写す。**
# 見出しだけであれば文字列の一致で食い違いを判定でき、`sync` は節の外を 1 バイトも変えずに
# 書き直せる。
#
# 対象は head のブランチ名が `design/` で始まる Pull Request だけである。それ以外は変更した
# ファイルも設計文書も読まず、対象外として 0 を返す。判定をここに持つのは、呼び出し元
# （pr / fix / 承認の提示 / 継続的統合）へ同じ条件を書かないためである。
#
# 設計文書は Pull Request の head のコミットから、本文は Pull Request の `body` から読む。
# 手元の作業ツリーにも、継続的統合の checkout（マージの試行のコミット）にも左右されない。
#
# 終了コード:
#   0  一致した（`sync` は書き込んだ後の突き合わせで一致した）。対象外も 0
#   1  食い違った（`check` は差分を標準出力へ出す。`sync` は書き込んだ後も食い違った）
#   2  読めなかった（`gh` が無い・Pull Request が無い・API の失敗）、または書き込みに失敗した
#   3  呼び出しの誤り（副コマンドが無い・未知の副コマンド・番号が数値でない）。GitHub を読まない
#
# **2 を 0 へ畳まない。** この結果は承認の提示に使う。確かめられなかったことを一致と報告しない。
set -uo pipefail

usage() {
  printf 'usage: pr-body-decisions.sh check|sync <PR番号> [--repo <所有者>/<リポジトリ>]\n' >&2
  exit 3
}

SUB="${1:-}"
case "$SUB" in
  check|sync) ;;
  *) usage ;;
esac
PR="${2:-}"
case "$PR" in
  ''|*[!0-9]*) printf 'ERROR: PR 番号が数値ではありません: %s\n' "$PR" >&2; usage ;;
esac
shift 2
REPO=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) [ -n "${2:-}" ] || usage; REPO="$2"; shift 2 ;;
    *) printf 'ERROR: 知らない引数です: %s\n' "$1" >&2; usage ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  printf 'ERROR: gh が見つかりません。本文と設計文書を読めません\n' >&2
  exit 2
fi
if [ -z "$REPO" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=lib/projects-common.sh
  . "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null && REPO=$(pj_repo_slug) || REPO=
  if [ -z "$REPO" ]; then
    printf 'ERROR: リポジトリを決められません。--repo <所有者>/<リポジトリ> を渡してください\n' >&2
    exit 2
  fi
fi

python3 - "$SUB" "$PR" "$REPO" <<'PY'
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

sub, pr, repo = sys.argv[1:4]
HEADING = "## 決めたこと"
MARKER = "<!-- 設計文書の「決定の記録」の見出しから pr-body-decisions.sh sync が作る。手で書き換えない -->"
DESIGN_BRANCH_PREFIX = "design/"
TEST_PLAN_HEADING = "## Test plan"


class Unreadable(Exception):
    pass


def gh(*args, input_file=None):
    cmd = ["gh", "api", *args]
    if input_file:
        cmd += ["--input", input_file]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unreadable(f"gh api {args[-1]}: {exc}")
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unreadable(f"gh api {' '.join(args)}: {detail[-1] if detail else done.returncode}")
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unreadable(f"gh api {args[-1]}: 応答が UTF-8 ではありません: {exc}")


def read_pr():
    try:
        data = json.loads(gh(f"repos/{repo}/pulls/{pr}"))
        return data["head"]["ref"], data["head"]["sha"], data.get("body") or ""
    except (ValueError, KeyError, TypeError) as exc:
        raise Unreadable(f"Pull Request #{pr} の応答を読めません: {exc}")


def lines_outside_fences(text):
    """(行の開始位置, 行の中身, 囲みの外か) を返す。囲みは ``` と ~~~ の 3 文字以上。"""
    fence = None
    pos = 0
    # 行は \n だけで分ける。splitlines は \u2028 などでも分けるため、節の位置がずれる。
    for raw in re.findall(r"[^\n]*\n|[^\n]+\Z", text):
        line = raw.rstrip("\r\n")
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is None and m:
            fence = m.group(1)
            yield pos, line, False
        elif fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line.strip().lstrip(fence[0]):
                fence = None
            yield pos, line, False
        else:
            yield pos, line, True
        pos += len(raw)


def is_top_heading(line):
    return line.startswith("# ") or line.startswith("## ")


def decision_headings(text):
    found, inside = [], False
    for _, line, outside in lines_outside_fences(text):
        if not outside:
            continue
        if re.fullmatch(r"## 決定の記録[ \t]*", line):
            inside = True
        elif is_top_heading(line):
            inside = False
        elif inside and line.startswith("### "):
            found.append(line[4:].rstrip())
    return found


def markdown_names(raw):
    # --paginate はページごとの配列を連結して出す。1 つずつ読み進める。
    decoder, pos, files = json.JSONDecoder(), 0, []
    try:
        while True:
            while pos < len(raw) and raw[pos].isspace():
                pos += 1
            if pos >= len(raw):
                break
            page, pos = decoder.raw_decode(raw, pos)
            files += page
        return sorted(f["filename"] for f in files
                      if f.get("status") != "removed" and f["filename"].endswith(".md"))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Unreadable(f"変更したファイルの一覧を読めません: {exc}")


def changed_markdown(head_sha):
    names = markdown_names(gh("--paginate", f"repos/{repo}/pulls/{pr}/files?per_page=100"))
    docs = []
    for name in names:
        quoted = urllib.parse.quote(name, safe="/")
        text = gh("-H", "Accept: application/vnd.github.raw",
                  f"repos/{repo}/contents/{quoted}?ref={head_sha}")
        headings = decision_headings(text)
        if headings:
            docs.append((name, headings))
    return docs


def expected_section(docs):
    if not docs:
        return None
    lines = [HEADING, "", MARKER, ""]
    for name, headings in docs:
        lines += [f"`{name}`", ""] + [f"- {h}" for h in headings] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def find_section(body, heading):
    """行全体が heading の行の開始位置と、節の終わり（次の # / ## の行の開始位置）を返す。"""
    start = None
    for pos, line, outside in lines_outside_fences(body):
        if not outside:
            continue
        if start is None:
            if re.fullmatch(re.escape(heading) + r"[ \t]*", line):
                start = pos
        elif is_top_heading(line):
            return start, pos
    return (start, len(body)) if start is not None else None


def normalize(text):
    if text is None:
        return None
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def count_decisions(docs):
    return sum(len(h) for _, h in docs)


def summary(docs):
    return f"設計文書 {len(docs)} 本 / 決定 {count_decisions(docs)} 件"


def compare(body, docs):
    span = find_section(body, HEADING)
    actual = body[span[0]:span[1]] if span else None
    expected = expected_section(docs)
    return normalize(actual) == normalize(expected), span, actual, expected


def report(actual, expected, docs):
    if expected is None:
        print(f"食い違い: 変更したファイルに「## 決定の記録」を持つ設計文書が無いのに、本文に「{HEADING}」の節がある")
        return
    if actual is None:
        print(f"食い違い: 本文に「{HEADING}」の節が無い（{summary(docs)}）")
    else:
        print(f"食い違い: 本文の「{HEADING}」の節が設計文書の決定の見出しと一致しない")
    diff = difflib.unified_diff(
        (normalize(actual) or "").split("\n") if actual is not None else [],
        normalize(expected).split("\n"),
        "本文の節", "設計文書から作った節", lineterm="")
    for line in diff:
        print(line)


def rewrite(body, span, expected):
    nl = "\r\n" if "\r\n" in body else "\n"
    text = expected.replace("\n", nl) if expected else ""
    if span:
        start, end = span
        if not text:
            return body[:start] + body[end:]
        return body[:start] + text + (nl if end < len(body) else "") + body[end:]
    plan = find_section(body, TEST_PLAN_HEADING)
    if plan:
        return body[:plan[0]] + text + nl + body[plan[0]:]
    if not body:
        return text
    return body + ("" if body.endswith(("\n", "\r")) else nl) + nl + text


def write_body(new_body):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"body": new_body}, f, ensure_ascii=False)
        payload = f.name
    try:
        gh("-X", "PATCH", f"repos/{repo}/pulls/{pr}", input_file=payload)
    finally:
        os.unlink(payload)


def sync_section(body, span, expected, docs, head_sha):
    write_body(rewrite(body, span, expected))
    _, new_sha, body = read_pr()
    # 書き込みの間に head が進んだら、進んだ先の設計文書と突き合わせる。古い決定を一致と報告しない。
    if new_sha != head_sha:
        docs = changed_markdown(new_sha)
    same, _, actual, expected = compare(body, docs)
    if same:
        print(f"書き直した: {summary(docs)}")
        return 0
    report(actual, expected, docs)
    return 1


def build_comparison(head_sha, body):
    docs = changed_markdown(head_sha)
    same, span, actual, expected = compare(body, docs)
    return head_sha, body, docs, same, span, actual, expected


def handle_comparison(sub, comparison):
    head_sha, body, docs, same, span, actual, expected = comparison
    if same:
        print(f"一致: {summary(docs)}")
        return 0
    if sub == "check":
        report(actual, expected, docs)
        return 1
    return sync_section(body, span, expected, docs, head_sha)


def main():
    try:
        head_ref, head_sha, body = read_pr()
        if not head_ref.startswith(DESIGN_BRANCH_PREFIX):
            print(f"対象外: head が design/ で始まらない（{head_ref}）")
            return 0
        comparison = build_comparison(head_sha, body)
        return handle_comparison(sub, comparison)
    except Unreadable as exc:
        print(f"ERROR: 読めなかった（一致とは扱わない）: {exc}", file=sys.stderr)
        return 2


sys.exit(main())
PY
