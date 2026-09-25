#!/usr/bin/env python3
"""フェーズをスクリプトで駆動する。

supervisor（サブエージェント）の代わりに、このスクリプトがフェーズの手順を順に進める。
判断の要らない段（コマンドの実行・待ち・進行の記録）はスクリプトが行い、LLM は
次の 2 つの段でだけ、毎回新しい最小構成の `claude -p` として起動する。

| 段 | 何をするか | LLM |
| --- | --- | --- |
| run   | コマンドを実行して終わるまで待ち、出力をファイルへ残す | 使わない |
| work  | 1 つの作業（修正・調査）を worker として行わせる | Tool あり（Read/Edit/Write/Bash/Grep/Glob）。`"full": true` なら設定・プラグイン・Skill をそのまま読む claude -p で Skill を回す（cross-review など） |
| drive | 駆動（cross-review / cross-refactoring の drive.py）を run として回し、`pause` のときだけ worker に判断・修正をさせて駆動へ返す | pause のときだけ（work と同じ最小構成） |
| judge | 結果ファイルと規則の抜粋だけを渡し、次の段を決めさせる | Tool なし |
| pr    | push して Draft の Pull Request を作る（スクリプト）。本文は材料（計画の値・コミット・変更の統計・run の結果・設計文書）から LLM が書く。`"body": "template"` なら材料をそのまま本文にする | 本文だけTool なし |

使い方:
    supervise.py run <plan.json> [--state-dir DIR] [--from <段の id>]
    supervise.py new impl --issue N --worktree DIR --tests PATH... --title T [--prompt-file F] [--branch B] [--out F]
    supervise.py new check --pr N --worktree DIR [--issue N...] [--scope PATH...] [--out F]
    supervise.py new release --version V --prs N... --channel dev|prod --worktree DIR [--issue N...]
                             [--prev-tag T] [--repo DIR] [--out F]
    supervise.py new mission --name M --worktree <リポジトリの根> --issue N... [--design N...] [--tests PATH...] [--out DIR]
        # 並列の設計 → 関門 1 → ミッションのブランチ → 並列の実装（ミッションのブランチへ集める）→ 検査 1 回 → 配布
        # を波ごとの計画ファイルと mission.json へ書き出す。波の中は queue --max 3 で流す
    supervise.py queue <plan.json>... [--max 3]   # 空いた枠へ順に流す
    supervise.py note <引き継ぎ文書.md> --report <report.md> [--next 次の欄] [--section 見出しの語]
    supervise.py sync-check [--root DIR] [--commit]   # 生成物の同期と検査 4 本
    supervise.py example            # 計画の例を出す

new / queue / note / sync-check の結果は lib/step_result.py の形の 1 行の JSON（status を見る）。

計画（JSON）:
    {
      "フェーズ": "検査", "課題": [818], "モード": "standard",
      "作業場所": "/abs/worktree",
      "branch": "feat/issue-818-x",         # 省略可。作業場所が無ければ起動時に作業ツリーを作る
      "起点": "origin/develop",             # branch から作るときの起点（既定 origin/develop）
      "リポジトリ": "/abs/repo",             # 作業ツリーの元（省略時は作業場所の /.worktrees/ より前）
      "記録": "/abs/projects-sync.sh",      # 省略可。stage を記録する
      "規則": "判断の規則の抜粋（文字列）",   # judge へ毎回渡す
      "上限": 30,                           # 実行する段の数の上限（ループの歯止め）
      "steps": [
        {"id": "test", "type": "run", "cmd": "pytest -q", "stage": "完了判定",
         "timeout": 1800, "on_fail": "judge-test", "next": "end"},
        {"id": "judge-test", "type": "judge", "inputs": ["test"],
         "question": "テストの失敗を直すか、止めるか", "choices": ["fix", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test"],
         "prompt": "失敗したテストを直してコミットする", "next": "test"}
      ]
    }

パートに分ける: work の段に `"parts": [{"name": ..., "files": [...]}, ...]` を書くと、パートごとに
新しい文脈の claude -p の段（`<id>-1`, `<id>-2`, ...）へ展開する。大きな実装は分けて書く。

Serena: work の段に `"serena": true` を書くと Serena の MCP だけを載せる（大きなコードを何度も読む実装向け）。

課題の本文: work の段に `"issues": [858]`（`true` なら計画の `課題`）を書くと、`gh issue view` の題と本文を
プロンプトの先頭へ入れる。

worker のランタイム: work と drive の段に `"runtime": "codex"`（`kiro` / `agy` / `claude`）を書くと、worker を
`external-ai.py run` で起動する（起動・上限つきの待ち・回収はそのコマンドが持つ）。書かなければ最小構成の claude -p。

drive の段: `cmd`（または `"drive": "cross-review" | "cross-refactoring"` と `"args"`）を打ち、最後の行の JSON を読む。
- `status` が `ok` なら成功。`metrics`（ラウンド数・指摘・未解決・適用・取り消しなど）を報告の「件数」へ載せる
- `gate` なら `items[0]` の `prompt_file` を worker に渡し、`result_file` を書かせてから同じコマンドを打ち直す。
  `command` を持つ pause（最終ゲートの cross-review）は、その駆動を同じ形で回し、`metrics.review_status` を
  `result_file` へ書く
- `stopped`・結果ファイルが書かれない・`"max_pauses"`（既定 12）を超える、のどれかなら失敗

段ごとの作業場所: run と work の段に `"cwd"` を書くと、その段だけ別の場所で動く（取り込みで PR ごとに
作業ツリーが違うとき）。work の段では worker へ渡す「作業場所」もその `cwd` になる。

配布の雛形（new release）: dev は bump → changelog → 説明文 → sync-check → release → verify-install（develop）
→ approval-facts → 提示物の説明文。approval-facts の提示物は `issues/approval-ndf-v<正式版>.md` へ写す。
prod は bump → changelog → 説明文 → トークン消費の記録 → sync-check → release → verify-install（main）。
落ちた run の段は judge が fix・同じ段のやり直し・stop を選ぶ。

利用上限: claude -p（work・drive の worker・judge・pr）が利用上限（session limit・HTTP 429・
`api_error_status: 429`・「You've hit your limit … resets …」。lib/monitor.py の USAGE LIMIT の表と同じ文言）で
落ちたら、段の失敗とは区別する（on_fail・judge へ回さない）。段の結果に `"limit": true` と読めた解除時刻を残す。
- 環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK`（`KEY=VALUE` を空白区切り。例 `CLAUDE_CODE_USE_BEDROCK=1`）が
  あれば、それを環境に足した同じ claude -p で 1 度だけ起動し直す。報告に `認証: 切り替え（<変数名>）` を書く
- それでも上限なら、解除時刻 + 1 分まで（読めなければ計画の `"limit_retry_seconds"`、既定 900 秒）待って
  同じ呼び出しを起動し直す。待ちは LLM を使わない（time.sleep）。queue の枠は待ちの間も保つ
- 待ちの合計が計画の `"limit_wait_max"`（既定 10800 秒）を超えるなら `結果: 止まった`・`理由: 利用上限`

run の段:
- `"preset"`: 定型のコマンド。`sync-check`（生成物の同期と検査 4 本）・`assess`（構造改善の要否）・
  `doc-lint`（追加した行の書き方の検査）。`cmd` を書けばそちらを使う
- `cmd` の `{pr}` は pr の段で作った Pull Request の URL に置き換わる
- `"rerun_failed": true`: 失敗したら落ちたテストだけ（`pytest --lf`）を走らせ直し、通れば成功として進む
- `"skip_to": "<段の id>"`: 終了コードが `skip_code`（既定 3。`refactor.py assess` の「飛ばしてよい」）なら
  その段へ進む
- 終了コード 10〜19（共通の契約の関門）は失敗にしない。段の結果に `gate` を残して `"gate_next"`（無ければ
  `next`）へ進み、最後まで進めば報告は `結果: 関門`。結果 JSON の `presentation_path` を報告の `提示物` に写す。
  `"presentation_to": "<パス>"` があれば提示物をそのパス（段の作業場所から）へ写し、そちらを載せる
- pytest の成果物（`reports/`）を作らない（`PYTEST_ADDOPTS` に `-p no:playwright-kit` を足す）。
  作らせるときは `"reports": true`

段の遷移:
- `next` に `end` を書くと、そこでフェーズを完了として終える
- run: 終了コード 0 なら `next`（無ければ次の段）。10〜19 は関門として `gate_next` か `next`。
  それ以外の 0 以外なら `on_fail`（無ければ止まる）
- work: 終了後に `next`（無ければ次の段）
- judge: 答えの `decision` が段の id ならその段へ、`next` なら次の段へ、`stop` なら止まる、
  `gate` なら関門として止まる。`choices` を渡すとその中から選ばせる

最後に `## フェーズの報告` を標準出力と `<state-dir>/report.md` へ書く。conductor はこの
スクリプトを背景の Bash で起動し、終わりの通知で報告を読む。
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import emit, result  # noqa: E402
from monitor import USAGE_LIMIT_FATAL  # noqa: E402  利用上限の文言の表
from pr_mode import with_mode_line  # noqa: E402

WORK_TOOLS = "Read,Edit,Write,Bash,Grep,Glob"
# work の段に載せる MCP は Serena だけ（mcp-serena の .mcp.json と同じ起動）。シンボル単位で読み・直し、
# 大きなファイルの全文を読まずに済ませる。Tool の定義で起動の固定費が約 1.1 万増える（実測: 1 関数の修正で
# $0.047 → $0.131）ため既定では載せず、段に "serena": true を書いたときだけ載せる
SERENA_MCP = {"mcpServers": {"serena": {
    "type": "stdio", "command": "uvx",
    "args": ["--from", "serena-agent==1.7.0", "serena", "start-mcp-server", "--context", "claude-code",
             "--project-from-cwd", "--add-mode", "no-memories", "--add-mode", "no-onboarding",
             "--enable-web-dashboard", "False"],
    "env": {"SERENA_HOME": ".serena"}}}}
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch"
TAIL = 6000  # LLM へ渡す出力の末尾の文字数
SELF = Path(__file__).resolve()
SKILLS = SELF.parent.parent / "skills"
DRIVES = {"cross-review": SKILLS / "cross-review" / "scripts" / "drive.py",
          "cross-refactoring": SKILLS / "cross-refactoring" / "scripts" / "drive.py"}
EXTERNAL_AI = SKILLS / "external-ai" / "scripts" / "external-ai.py"

# run の段の定型（"preset"）。作業場所（リポジトリの根）で動く
PRESETS = {
    "sync-check": f"python3 {SELF} sync-check --commit",
    "assess": "python3 plugins/ndf/skills/cross-refactoring/scripts/refactor.py assess --base origin/develop",
    "doc-lint": "python3 plugins/ndf/scripts/doc-lint.py --base origin/develop",
}
# sync-check が順に回すコマンド（生成物の同期 → 検査 4 本）
SYNC_CHECKS = [
    ("build", "bash scripts/build-runtime-plugins.sh"),
    ("frontmatter", "python3 scripts/check-skill-frontmatter.py"),
    ("line-limit", "python3 scripts/check-doc-line-limit.py"),
    ("links", "python3 scripts/check-markdown-links.py"),
    ("instructions", "python3 plugins/ndf/scripts/instructions-check.py --root ."),
]
PYTEST = ("uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest --with pytest-xdist "
          "pytest {paths} -q -n 4")
NO_REPORTS = "-p no:playwright-kit"  # playwright-kit の plugin が reports/ を書く
LIMIT_RETRY = 900      # 利用上限の解除時刻が読めないときの待ち（秒）。計画の "limit_retry_seconds"
LIMIT_WAIT_MAX = 10800  # 利用上限の待ちの最大（秒）。計画の "limit_wait_max"
# claude の古い形の上限の文言（`Claude AI usage limit reached|<解除の UNIX 時刻>`）
LIMIT_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.I)
LIMIT_RESETS = re.compile(r"resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.I)

WORK_SYSTEM = """あなたは NDF の worker である。1 つの作業だけを行う。
- 人間へ問わない。別のサブエージェントを起動しない。進行を記録しない
- 作業場所の外を触らない。push しない
- Serena の Tool（mcp__serena__*）があれば、コードはシンボル単位（get_symbols_overview / find_symbol /
  replace_symbol_body など）で読み・直す。Serena の memory と onboarding は使わない
- それ以外のファイルは全文を読まない。grep -n で位置を探し、Read の offset / limit で要る範囲だけを読む
  （200 行未満のファイルと、これから書き換える関数の周りは除く）。同じ範囲を読み直さない
- テストや検査の出力は、失敗した箇所と要約だけを読む（`| tail`・`-q`・`--tb=short`）
- 判断が要るときは、作業をせずに「結果: 判断が要る」と理由を書いて終える
- 最後に次の形で終える:
## 作業の報告
- 作業: <種類>
- 結果: 完了 / 判断が要る / できなかった
- 見つけたもの: <件数と場所。無ければ 無し>
- 次にすること: <1 行。無ければ 無し>"""

FULL_SYSTEM = """あなたは NDF のフェーズの 1 段を CLI（claude -p）として回している。人は見ていない。
- 応答を終えるとこのプロセスは終わり、背景の処理と完了通知は捨てられる。待ちは前景のコマンドで行い
  （run_in_background・Monitor を使わない。gh pr checks --watch や待ちのスクリプトを前景で実行する）、
  作業が終わるまで応答を終えない
- 人間へ問わない（AskUserQuestion を使わない）。Skill の確認は提示して進める
- 関門に当たる操作（設計 PR のマージ・main への配布・タグ）をしない
- 課題を閉じる語（Fix #番号・Closes など）をコミットと PR 本文に書かない
- 待ちは背景起動と完了通知で行い、応答を途中で終えない
- 最後に次の形で終える:
## 作業の報告
- 作業: <Skill 名>
- 結果: 完了 / 判断が要る / できなかった
- 見つけたもの: <件数と場所。無ければ 無し>
- 次にすること: <1 行。無ければ 無し>"""

REPORT_DONE = re.compile(r"## 作業の報告[\s\S]*?結果:\s*完了")
RESUME_PROMPT = ("続けて。作業の報告で終えるまで応答を終えない。応答を終えるとこのプロセスは終わり、背景の処理と"
                 "完了通知は捨てられるので、待ちは前景のコマンドで行う。既に書いたもの（コミット・PR・コメント）を"
                 "確かめてから続ける。")

PR_SYSTEM = """あなたは Pull Request の本文だけを書く。Tool は無い。
渡された材料（コミット・変更の統計・テストの結果・設計文書）だけを根拠に、日本語の Markdown で書く。
- 先頭に何を変えたかを 1〜3 文。続けて「## 変更の要点」「## テスト」の節
- 課題を閉じる語（Fix #番号・Closes・Resolves など）を書かない。課題は「#番号」とだけ書く
- 材料に無いことを書かない。本文だけを返し、前置きや囲みを付けない"""

JUDGE_SYSTEM = """あなたは NDF のフェーズの判断だけを行う。Tool は無い。
渡された結果と規則だけを根拠に、次の段を 1 つ選ぶ。
答えは JSON 1 つだけを返す: {"decision": "<選んだ値>", "reason": "<1 行>"}"""


def claude_cmd(system: str, tools: str | None, cwd: str, full: bool = False,
               serena: bool = False, resume: str | None = None) -> list[str]:
    base = shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE", "claude"))
    if full:
        # Skill を回す段（cross-review など）。設定・プラグイン・Skill・hook をそのまま読む
        # 新しい文脈の claude -p。本体の会話なのでキャッシュはサブスクリプションなら 1 時間。
        # 報告が無いまま終わったときに --resume で起こし直すため、会話は残す
        return base + ["-p", "--output-format", "json",
                       "--permission-mode", "acceptEdits", "--allowed-tools", FULL_TOOLS,
                       "--append-system-prompt", system] + (["--resume", resume] if resume else [])
    cmd = base + [
        "-p", "--output-format", "json", "--no-session-persistence",
        "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
        "--system-prompt", system,
    ]
    if tools:
        allowed = tools
        if serena:
            cmd += ["--mcp-config", json.dumps(SERENA_MCP)]
            allowed += ",mcp__serena"
        cmd += ["--tools", tools, "--allowed-tools", allowed, "--permission-mode", "acceptEdits",
                "--add-dir", cwd]
    else:
        cmd += ["--tools", ""]
    model = os.environ.get("NDF_SUPERVISE_MODEL")
    if model:
        cmd += ["--model", model]
    return cmd


def call_claude(system: str, prompt: str, tools: str | None, cwd: str, timeout: int,
                full: bool = False, serena: bool = False, resume: str | None = None,
                env: dict | None = None) -> dict:
    """claude -p を 1 回呼び、結果の本文と使用量を返す（既定は最小構成）。

    `env` は環境に足す変数（認証の切り替え）。利用上限で落ちたら `"limit": true` と、読めれば
    解除の時刻（UNIX 時刻）を `"resets_at"` に残す。
    """
    started = time.time()
    try:
        p = subprocess.run(claude_cmd(system, tools, cwd, full, serena, resume), input=prompt, capture_output=True,
                           text=True, cwd=cwd, timeout=timeout, env={**os.environ, **env} if env else None)
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": f"打ち切り（{timeout} 秒）", "usage": {}, "seconds": timeout}
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    if not isinstance(data, dict):
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    ok = p.returncode == 0 and not data.get("is_error")
    text = data.get("result") or p.stderr[-TAIL:]
    limit = not ok and is_usage_limit("\n".join([str(data.get("result") or ""), p.stderr, p.stdout]))
    return {
        "ok": ok,
        "text": text,
        "usage": data.get("usage") or {},
        "cost": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "session": data.get("session_id"),
        "seconds": round(time.time() - started, 1),
        "limit": limit,
        "resets_at": limit_reset_at("\n".join([str(data.get("result") or ""), p.stderr])) if limit else None,
    }


def is_gate(code: int | None) -> bool:
    """run の段の終了コード 10〜19 は共通の契約の関門（lib/step_result.py の EXIT_GATE）。"""
    return code is not None and 10 <= code <= 19


def is_usage_limit(text: str) -> bool:
    """利用上限で落ちたか。lib/monitor.py の USAGE LIMIT の表と同じ文言で照合する。"""
    return any(rx.search(text or "") for rx in USAGE_LIMIT_FATAL) or bool(LIMIT_EPOCH.search(text or ""))


def limit_reset_at(text: str, now: float | None = None) -> float | None:
    """上限の文言から解除の時刻（UNIX 時刻）を読む。読めなければ None。

    読む形: `usage limit reached|<UNIX 時刻>` と `resets 3pm (Asia/Tokyo)` / `resets at 15:30`。
    時刻だけの形は、今より後の最初のその時刻（時間帯が無ければ手元の時間帯）とする。
    """
    now = time.time() if now is None else now
    m = LIMIT_EPOCH.search(text or "")
    if m:
        return float(m.group(1))
    m = LIMIT_RESETS.search(text or "")
    if not m:
        return None
    hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        tz = ZoneInfo(zone.strip()) if zone else None
    except (KeyError, ValueError):
        tz = None
    cur = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    at = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp()


def fallback_env() -> dict:
    """NDF_SUPERVISE_CLAUDE_FALLBACK（`KEY=VALUE` を空白区切り）を読む。"""
    out = {}
    for tok in shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE_FALLBACK", "")):
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k] = v
    return out


class UsageLimit(Exception):
    """利用上限の待ちが最大を超えた。段の失敗とは区別して止まる。"""


def parse_decision(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"decision": "stop", "reason": f"判断の答えを読めない: {(text or '')[:200]}"}


def last_json(text: str) -> dict | None:
    """出力の最後の JSON の行（step_result の形）を読む。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def counts_text(counts: dict) -> str:
    return " / ".join(f"{k} {v}" for k, v in counts.items() if v is not None) or "無し"


def expand_parts(steps: list[dict]) -> list[dict]:
    """work の段の `parts` を、パートごとに別の claude -p の段へ展開する。

    1 つの文脈で全部を書くと、文脈が育つほど往復ごとの読み直しが増える（大きさ × 回数）。
    パートごとに新しい文脈で起動し、前のパートの成果はコミットから読ませる。
    `parts`: [{"name": "release-steps", "files": ["plugins/.../release-steps.py", ...]}, ...]
    """
    out = []
    for s in steps:
        parts = s.get("parts")
        if s.get("type") != "work" or not parts:
            out.append(s)
            continue
        for i, part in enumerate(parts, 1):
            p = {k: v for k, v in s.items() if k not in ("parts", "id", "next")}
            p["id"] = f"{s['id']}-{i}"
            if i < len(parts):
                p["next"] = f"{s['id']}-{i + 1}"
            elif s.get("next"):
                p["next"] = s["next"]
            p["prompt"] = (s["prompt"] + f"\n\n## このパート（{i}/{len(parts)}: {part['name']}）\n"
                           f"触るのは次のファイルだけ: {', '.join(part['files'])}。"
                           "他のパートは別の作業が受け持つ。前のパートの成果は git log と該当ファイルの要る範囲で確かめる。"
                           "このパートの変更をコミットして終える。")
            out.append(p)
        # 元の id を指す遷移は最初のパートへ
        for t in steps:
            for key in ("next", "on_fail"):
                if t.get(key) == s["id"]:
                    t[key] = f"{s['id']}-1"
    return out


# 計画の旧いキー（持ち場）を今のキー（フェーズ）へ読み替える。旧い計画の JSON も読めるようにする
PLAN_KEY_ALIASES = {"持ち場": "フェーズ", "次の持ち場": "次のフェーズ"}


def normalize_plan(plan: dict) -> dict:
    for old, new in PLAN_KEY_ALIASES.items():
        if old in plan:
            value = plan.pop(old)
            plan.setdefault(new, value)
    return plan


class Supervisor:
    def __init__(self, plan: dict, state_dir: Path):
        self.plan = normalize_plan(plan)
        plan["steps"] = expand_parts(plan["steps"])
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.order = [s["id"] for s in plan["steps"]]
        self.cwd = plan["作業場所"]
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict] = {}
        self.log: list[dict] = []
        self.llm = {"work": 0, "judge": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                    "output": 0, "cost": 0.0}
        self.last_stage = "無し"
        self.gates: list[dict] = []      # run の段が返した関門（終了コード 10〜19）
        self.switched: list[str] = []    # 利用上限で足した認証の変数の名前

    # --- 共通 ---
    def out_path(self, n: int, sid: str) -> Path:
        return self.dir / f"{n:02d}-{sid}.out"

    def record_stage(self, stage: str | None) -> None:
        if not stage or stage == self.last_stage:
            return
        self.last_stage = stage
        rec = self.plan.get("記録")
        if not rec:
            return
        for issue in self.plan.get("課題", []):
            subprocess.run(["bash", rec, str(issue), "stage", stage], cwd=self.cwd,
                           capture_output=True, text=True)

    def inputs_text(self, step: dict) -> str:
        parts = []
        for sid in step.get("inputs", []):
            r = self.results.get(sid)
            if r:
                parts.append(f"### 段 {sid}（exit={r.get('exit')}）\n{r['text'][-TAIL:]}")
        return "\n\n".join(parts) or "（入力なし）"

    def add_usage(self, kind: str, res: dict) -> None:
        u = res.get("usage", {})
        self.llm[kind] += 1
        self.llm["input"] += u.get("input_tokens", 0)
        self.llm["cache_read"] += u.get("cache_read_input_tokens", 0)
        self.llm["cache_write"] += u.get("cache_creation_input_tokens", 0)
        self.llm["output"] += u.get("output_tokens", 0)
        self.llm["cost"] += res.get("cost") or 0.0
        # 段ごとの内訳（往復の回数・トークン・費用）。同じ段で複数回呼べば足し合わせる
        c = self.cur.setdefault("llm", {"calls": 0, "turns": 0, "input": 0, "cache_read": 0,
                                        "cache_write": 0, "output": 0, "cost": 0.0})
        c["calls"] += 1
        c["turns"] += res.get("turns") or 0
        c["input"] += u.get("input_tokens", 0)
        c["cache_read"] += u.get("cache_read_input_tokens", 0)
        c["cache_write"] += u.get("cache_creation_input_tokens", 0)
        c["output"] += u.get("output_tokens", 0)
        c["cost"] = round(c["cost"] + (res.get("cost") or 0.0), 4)

    def claude(self, system: str, prompt: str, tools: str | None, cwd: str, timeout: int, **kw) -> dict:
        """claude -p を呼ぶ唯一の口（work / drive の worker / judge / pr）。利用上限をここで扱う。

        上限に当たったら、NDF_SUPERVISE_CLAUDE_FALLBACK があればその変数を足して 1 度だけ起動し直す。
        それでも上限なら、解除の時刻 + 1 分（読めなければ "limit_retry_seconds"）まで待って同じ呼び出しを
        起動し直す。待ちの合計が "limit_wait_max" を超えるなら UsageLimit を投げる。
        待ちの実際の秒数は NDF_SUPERVISE_LIMIT_SLEEP で短くできる（試験用）。
        """
        retry = self.plan.get("limit_retry_seconds", LIMIT_RETRY)
        wait_max = self.plan.get("limit_wait_max", LIMIT_WAIT_MAX)
        fallback = fallback_env()
        tried_fallback, waited = False, 0.0
        while True:
            res = call_claude(system, prompt, tools, cwd, timeout, **kw)
            if res.get("limit"):
                self.note_limit(res)
                if fallback and not tried_fallback:
                    tried_fallback = True
                    for k in fallback:
                        if k not in self.switched:
                            self.switched.append(k)
                    self.cur["auth"] = "切り替え（" + ", ".join(fallback) + "）"
                    res = call_claude(system, prompt, tools, cwd, timeout, env=fallback, **kw)
                    if res.get("limit"):
                        self.note_limit(res)
            if not res.get("limit"):
                return res
            wait = max(0.0, res["resets_at"] + 60 - time.time()) if res.get("resets_at") else float(retry)
            if waited + wait > wait_max:
                raise UsageLimit(f"利用上限の待ちが最大 {wait_max} 秒を超える（待った {round(waited)} 秒、"
                                 f"次の待ち {round(wait)} 秒）: {(res.get('text') or '')[:200]}")
            short = os.environ.get("NDF_SUPERVISE_LIMIT_SLEEP")
            time.sleep(min(wait, float(short)) if short else wait)
            waited += wait
            self.cur["limit_waited"] = round(self.cur.get("limit_waited", 0) + wait, 1)

    def note_limit(self, res: dict) -> None:
        self.cur["limit"] = True
        self.cur["limit_hits"] = self.cur.get("limit_hits", 0) + 1
        if res.get("resets_at"):
            self.cur["limit_resets"] = datetime.fromtimestamp(res["resets_at"]).astimezone().isoformat(
                timespec="minutes")

    def next_of(self, sid: str, step: dict) -> str | None:
        """成功したときの次の段。`next` が無ければ並びの次へ進むが、失敗したときにだけ通る段
        （どこかの `on_fail` が指す段と、そこから `next` で戻る段）は飛ばす。"""
        if step.get("next"):
            return None if step["next"] == "end" else step["next"]
        fail_only = {s["on_fail"] for s in self.steps.values() if s.get("on_fail")}
        fail_only |= {s["id"] for s in self.steps.values()
                      if s["type"] == "work" and s.get("next") in self.steps and s.get("inputs")
                      and any(self.steps.get(i, {}).get("on_fail") for i in s["inputs"])}
        i = self.order.index(sid) + 1
        while i < len(self.order) and self.order[i] in fail_only:
            i += 1
        return self.order[i] if i < len(self.order) else None

    # --- 段 ---
    @staticmethod
    def is_skip(step: dict, code: int | None) -> bool:
        return bool(step.get("skip_to")) and code == step.get("skip_code", 3)

    def ensure_worktree(self) -> str | None:
        """計画に branch があり作業場所が無ければ、作業ツリーを作る。誤りの文を返す（無ければ None）。"""
        branch = self.plan.get("branch")
        wt = Path(self.cwd)
        if not branch or wt.exists():
            return None
        repo = self.plan.get("リポジトリ") or (str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt)
                                             else None)
        if not repo:
            return "作業ツリーの元のリポジトリが分からない（計画に リポジトリ を書く）"
        base = self.plan.get("起点", "origin/develop")
        if base.startswith("origin/"):
            subprocess.run(["git", "-C", repo, "fetch", "-q", "origin"], capture_output=True, text=True)
        has = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                             capture_output=True, text=True).returncode == 0
        cmd = ["git", "-C", repo, "worktree", "add", "-q"] + ([str(wt), branch] if has
                                                             else ["-b", branch, str(wt), base])
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            return f"作業ツリーを作れない: {p.stderr.strip()[:300]}"
        return None

    def run_cmd(self, step: dict, extra_addopts: str = "") -> tuple[int, str]:
        cmd = step.get("cmd") or PRESETS.get(step.get("preset", ""), "")
        if not cmd:
            return 2, f"段 {step['id']} に cmd も知っている preset も無い"
        if "{pr}" in cmd:
            if not self.plan.get("Pull Request"):
                return 2, "cmd の {pr} を置き換える Pull Request がまだ無い"
            cmd = cmd.replace("{pr}", self.plan["Pull Request"])
        env = dict(os.environ)
        # 親の run の段から受け継いだ NO_REPORTS は、reports: true なら外す
        addopts = [env.get("PYTEST_ADDOPTS", "").replace(NO_REPORTS, "").strip()]
        if not step.get("reports"):
            addopts.append(NO_REPORTS)
        addopts.append(extra_addopts)
        env["PYTEST_ADDOPTS"] = " ".join(a for a in addopts if a)
        try:
            p = subprocess.run(cmd, shell=True, cwd=step.get("cwd", self.cwd), capture_output=True,
                               text=True, timeout=step.get("timeout", 3600), env=env)
            return p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            return 124, f"打ち切り（{e.timeout} 秒）"

    def do_run(self, step: dict) -> tuple[bool, str]:
        started = time.time()
        code, text = self.run_cmd(step)
        if (code not in (0, 124) and not is_gate(code) and step.get("rerun_failed")
                and not self.is_skip(step, code)):
            # 落ちたテストだけを走らせ直す。通れば揺れとして成功にする
            code2, text2 = self.run_cmd(step, "--lf")
            self.cur["rerun"] = {"exit": code2}
            text = f"{text}\n\n## 落ちたテストだけの再実行（exit={code2}）\n{text2}"
            if code2 == 0:
                code, text = 0, text + "\n再実行で通った（揺れとして進む）"
        self.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return code == 0, text

    def issue_text(self, step: dict) -> str:
        nums = step.get("issues")
        if nums is True:
            nums = self.plan.get("課題", [])
        parts = []
        for n in nums or []:
            p = subprocess.run(["gh", "issue", "view", str(n), "--json", "title,body"], cwd=self.cwd,
                               capture_output=True, text=True)
            try:
                d = json.loads(p.stdout)
                parts.append(f"## 課題 #{n}: {d.get('title', '')}\n\n{d.get('body', '')}")
            except json.JSONDecodeError:
                parts.append(f"## 課題 #{n}\n\n（本文を取れない。gh issue view {n} で読む）")
        return "\n\n".join(parts)

    def call_worker(self, step: dict, prompt: str, cwd: str, name: str) -> dict:
        """worker を 1 回起動する。`runtime` があれば external-ai.py run、無ければ最小構成の claude -p。"""
        rt = step.get("runtime")
        if not rt or rt == "claude-p":
            return self.claude(WORK_SYSTEM, prompt, WORK_TOOLS, cwd, step.get("timeout", 1800),
                               serena=bool(step.get("serena")))
        pf, of = self.dir / f"{name}-prompt.md", self.dir / f"{name}-output.md"
        pf.write_text(WORK_SYSTEM + "\n\n" + prompt)
        started = time.time()
        cmd = [sys.executable, str(EXTERNAL_AI), "run", rt, "--prompt-file", str(pf), "--output-file", str(of),
               "--phase", step.get("phase", "implement"), "--workdir", cwd,
               "--timeout", str(step.get("timeout", 1800))]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=step.get("timeout", 1800) + 120)
            out = last_json(p.stdout) or {}
        except subprocess.TimeoutExpired:
            out = {"status": "stopped", "summary": "打ち切り"}
        text = of.read_text() if of.is_file() else out.get("summary", "")
        return {"ok": out.get("status") == "ok", "text": text, "usage": {}, "cost": None, "turns": None,
                "seconds": round(time.time() - started, 1), "runtime": rt}

    def do_work(self, step: dict) -> tuple[bool, str]:
        issues = self.issue_text(step)
        cwd = step.get("cwd", self.cwd)
        prompt = (f"作業: {step.get('kind', '修正')}\n作業場所: {cwd}\n\n"
                  + (f"{issues}\n\n## 指示\n" if issues else "")
                  + f"{step['prompt']}\n\n## 入力\n{self.inputs_text(step)}")
        if step.get("full"):
            # Skill の本文が手順を持つ。プロンプトは Skill の呼び出しをそのまま渡す
            prompt = step["prompt"]
        full = bool(step.get("full"))
        if full:
            res = self.claude(FULL_SYSTEM, prompt, WORK_TOOLS, cwd, step.get("timeout", 1800), full=True)
        else:
            res = self.call_worker(step, prompt, cwd, step["id"])
        self.add_usage("work", res)
        # 報告が無いまま応答を終えた Skill の段は、同じ会話を起こし直す（supervisor へ SendMessage で
        # 続けさせていたのと同じ。3 回まで）
        for _ in range(3):
            if not full or REPORT_DONE.search(res["text"] or "") or not res.get("session"):
                break
            res = self.claude(FULL_SYSTEM, RESUME_PROMPT, WORK_TOOLS, cwd, step.get("timeout", 1800),
                              full=True, resume=res["session"])
            self.add_usage("work", res)
        if full and not REPORT_DONE.search(res["text"] or ""):
            res["ok"] = False
        self.cur.update(exit=0 if res["ok"] else 1, text=res["text"], seconds=res["seconds"])
        return res["ok"], res["text"]

    def drive_cmd(self, step: dict) -> str:
        if step.get("cmd"):
            return step["cmd"]
        script = DRIVES.get(step.get("drive", ""))
        return f"python3 {script} {step.get('args', '')}".strip() if script else ""

    def drive_loop(self, step: dict, cmd: str, depth: int = 0) -> tuple[bool, dict | None, str]:
        """駆動を打ち、pause のたびに worker へ渡して打ち直す。(成功, 最後の結果, 出力) を返す。"""
        cwd = step.get("cwd", self.cwd)
        texts = []
        for _ in range(step.get("max_pauses", 12) + 1):
            code, text = self.run_cmd({**step, "cmd": cmd})
            texts.append(text)
            out = last_json(text)
            if out is None:
                return False, None, "\n".join(texts) + f"\n駆動の結果 JSON を読めない（exit={code}）"
            if out.get("status") == "ok":
                return True, out, "\n".join(texts)
            item = (out.get("items") or [{}])[0]
            if out.get("status") != "gate" or not item.get("result_file"):
                return False, out, "\n".join(texts)
            kind = item.get("pause", out.get("next", "pause"))
            self.cur.setdefault("pauses", []).append(kind)
            res_file = Path(item["result_file"])
            if item.get("command") and depth == 0:
                ok, sub, sub_text = self.drive_loop(step, item["command"], depth + 1)
                texts.append(sub_text)
                if not ok:
                    return False, sub, "\n".join(texts) + f"\n{kind} の駆動が止まった"
                res_file.write_text(json.dumps({"review_status": (sub.get("metrics") or {}).get("review_status")
                                                or "unknown"}))
                continue
            pf = item.get("prompt_file")
            if not pf or not Path(pf).is_file():
                return False, out, "\n".join(texts) + f"\n{kind} の prompt_file が無い"
            prompt = (f"作業: {kind}\n作業場所: {cwd}\n\n{Path(pf).read_text()}\n\n"
                      f"終えたら結果ファイル {res_file} を書く。")
            res = self.call_worker(step, prompt, cwd, f"{step['id']}-{kind}-{len(self.cur['pauses'])}")
            self.add_usage("work", res)
            texts.append(f"## {kind} の worker\n{res['text'][-TAIL:]}")
            if not res_file.is_file():
                return False, out, "\n".join(texts) + f"\n{kind} の worker が結果ファイルを書かなかった"
        return False, None, "\n".join(texts) + f"\npause が上限 {step.get('max_pauses', 12)} を超えた"

    def do_drive(self, step: dict) -> tuple[bool, str]:
        started = time.time()
        cmd = self.drive_cmd(step)
        if not cmd:
            self.cur.update(exit=2, text=f"段 {step['id']} に cmd も知っている drive も無い")
            return False, self.cur["text"]
        if "{pr}" in cmd:
            if not self.plan.get("Pull Request"):
                self.cur.update(exit=2, text="cmd の {pr} を置き換える Pull Request がまだ無い")
                return False, self.cur["text"]
            cmd = cmd.replace("{pr}", self.plan["Pull Request"])
        ok, out, text = self.drive_loop(step, cmd)
        if out:
            self.cur["counts"] = out.get("metrics") or {}
        self.cur.update(exit=0 if ok else 1, text=text, seconds=round(time.time() - started, 1))
        return ok, text

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, text=True).stdout.rstrip()

    def do_pr(self, step: dict) -> tuple[bool, str]:
        """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す。LLM を使わない。"""
        base = step.get("base", "develop")
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        push = subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=self.cwd,
                              capture_output=True, text=True)
        if push.returncode != 0:
            self.cur.update(exit=push.returncode, text=push.stderr)
            return False, push.stderr
        rng = f"origin/{base}..HEAD"
        commits = self.git("log", "--reverse", "--format=- %s", rng) or "- （無し）"
        stat = self.git("diff", "--stat", rng).splitlines()
        tests = []
        for sid, r in self.results.items():
            if r.get("type") == "run":
                last = next((l for l in reversed(r.get("text", "").splitlines()) if l.strip()), "")
                tests.append(f"| {sid} | {r.get('exit')} | {last[:120]} |")
        issues = " ".join(f"#{i}" for i in self.plan.get("課題", []))
        docs = "\n".join(f"- `{d}`" for d in step.get("docs", [])) or "- 無し"
        title = step.get("title") or (self.git("log", "--reverse", "--format=%s", rng).splitlines() or [branch])[0]
        body = f"""{step.get('summary', '')}

## 課題と設計

- 課題: {issues}
{docs}

## コミット

{commits}

## 変更の統計

```text
{chr(10).join(stat[-15:])}
```

## テスト（supervise.py の run の段）

| 段 | exit | 最後の行 |
| --- | ---: | --- |
{chr(10).join(tests) or '| 無し | | |'}

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        if step.get("body", "llm") == "llm":
            design = ""
            for d in step.get("docs", []):
                f = Path(self.cwd) / d
                if f.is_file():
                    design += f"\n### {d}\n" + f.read_text()[:TAIL]
            res = self.claude(PR_SYSTEM, f"課題: {issues}\n要約の手がかり: {step.get('summary', '')}\n\n"
                              f"## 材料\n{body}\n## 設計文書（抜粋）{design or ' 無し'}",
                              None, self.cwd, step.get("timeout", 600))
            self.add_usage("judge", res)
            if res["ok"] and res["text"].strip():
                body = res["text"].strip() + "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
        body = with_mode_line(body, self.plan.get("モード"), self.passed_stages(step))
        found = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url",
                                "--jq", ".[0].url"], cwd=self.cwd, capture_output=True, text=True).stdout.rstrip()
        if found:
            p = subprocess.run(["gh", "pr", "edit", found, "--body", body], cwd=self.cwd,
                               capture_output=True, text=True)
            url = found
        else:
            p = subprocess.run(["gh", "pr", "create", "--draft", "--base", base, "--title", title,
                                "--body", body], cwd=self.cwd, capture_output=True, text=True)
            url = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if url:
            self.plan["Pull Request"] = url
        self.cur.update(exit=p.returncode, text=(url + "\n" + p.stderr).strip())
        return p.returncode == 0, url

    def passed_stages(self, step: dict | None = None) -> list[str]:
        """通した工程（段の stage）を通った順に重ねずに返す。step を渡せばその段の工程も含める。"""
        stages: list[str] = []
        ids = [e.get("id") for e in self.log] + ([step["id"]] if step else [])
        for sid in ids:
            stage = (self.steps.get(sid) or {}).get("stage")
            if stage and stage not in stages:
                stages.append(stage)
        return stages

    def do_judge(self, step: dict) -> dict:
        choices = step.get("choices")
        prompt = (f"フェーズ: {self.plan.get('フェーズ')} / 課題: {self.plan.get('課題')}\n"
                  f"問い: {step['question']}\n"
                  + (f"選べる値: {', '.join(choices)}（関門なら gate、止めるなら stop）\n" if choices else "")
                  + f"\n## 規則\n{self.plan.get('規則', '（無し）')}\n\n## 結果\n{self.inputs_text(step)}")
        res = self.claude(JUDGE_SYSTEM, prompt, None, self.cwd, step.get("timeout", 600))
        self.add_usage("judge", res)
        d = parse_decision(res["text"]) if res["ok"] else {"decision": "stop", "reason": res["text"][:200]}
        self.cur.update(exit=0, text=json.dumps(d, ensure_ascii=False), seconds=res["seconds"])
        return d

    # --- 駆動 ---
    def run(self, start: str | None = None) -> str:
        sid = start or self.order[0]
        result, reason = "完了", "無し"
        limit = self.plan.get("上限", 30)
        n = 0
        err = self.ensure_worktree()
        if err:
            return self.report("止まった", err)
        while sid:
            n += 1
            if n > limit:
                result, reason = "止まった", f"段の数が上限 {limit} を超えた"
                break
            step = self.steps.get(sid)
            if step is None:
                result, reason = "止まった", f"知らない段: {sid}"
                break
            self.record_stage(step.get("stage"))
            self.cur = {"id": sid, "type": step["type"]}
            try:
                if step["type"] == "judge":
                    d = self.do_judge(step)
                    dec = d.get("decision", "stop")
                    self.cur["decision"] = dec
                    if dec == "next":
                        nxt = self.next_of(sid, step)
                    elif dec == "stop":
                        result, reason, nxt = "止まった", d.get("reason", "判断が止めた"), None
                    elif dec == "gate":
                        result, reason, nxt = "関門", d.get("reason", ""), None
                    elif dec in self.steps:
                        nxt = dec
                    else:
                        result, reason, nxt = "止まった", f"判断が知らない値を返した: {dec}", None
                else:
                    do = {"run": self.do_run, "work": self.do_work, "pr": self.do_pr,
                          "drive": self.do_drive}[step["type"]]
                    ok, _ = do(step)
                    if step["type"] == "run" and self.is_skip(step, self.cur.get("exit")):
                        nxt = None if step["skip_to"] == "end" else step["skip_to"]
                        self.cur["skipped"] = True
                    elif step["type"] == "run" and is_gate(self.cur.get("exit")):
                        self.take_gate(step)
                        gnext = step.get("gate_next")
                        nxt = (None if gnext == "end" else gnext) if gnext else self.next_of(sid, step)
                    elif ok:
                        nxt = self.next_of(sid, step)
                    elif step.get("on_fail"):
                        nxt = step["on_fail"]
                    else:
                        result, reason, nxt = "止まった", f"段 {sid} が失敗した（exit={self.cur['exit']}）", None
            except UsageLimit as e:
                # 利用上限は段の失敗と区別する（on_fail・judge へ回さない）
                self.cur.setdefault("exit", 1)
                self.cur["text"] = str(e)
                result, reason, nxt = "止まった", "利用上限", None
            self.results[sid] = dict(self.cur)
            self.out_path(n, sid).write_text(self.cur.get("text", ""))
            self.log.append({k: v for k, v in self.cur.items() if k != "text"})
            (self.dir / "state.json").write_text(json.dumps(
                {"log": self.log, "llm": self.llm}, ensure_ascii=False, indent=1))
            sid = nxt
        if result == "完了" and self.gates:
            result = "関門"
            if reason == "無し":
                reason = "; ".join(f"段 {g['id']} が関門を返した（exit={g['exit']}）" for g in self.gates)
        return self.report(result, reason)

    def take_gate(self, step: dict) -> None:
        """run の段の関門を残す。提示物（結果 JSON の presentation_path）は `presentation_to` があれば写す。"""
        out = last_json(self.cur.get("text", "")) or {}
        path = out.get("presentation_path")
        dest = step.get("presentation_to")
        if path and dest and Path(path).is_file():
            target = Path(step.get("cwd", self.cwd)) / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            path = str(target)
        self.cur["gate"] = True
        if path:
            self.cur["presentation"] = path
        self.gates.append({"id": step["id"], "exit": self.cur.get("exit"), "presentation": path})

    def report(self, result: str, reason: str) -> str:
        l = self.llm
        steps = " → ".join(f"{e['id']}" + (f"[{e['decision']}]" if "decision" in e else
                                           f"(exit={e.get('exit')})") for e in self.log)
        rows = "\n".join(
            f"| {e['id']} | {e['llm']['turns']} | {e.get('seconds', '')} | {e['llm']['cache_read']} | "
            f"{e['llm']['cache_write']} | {e['llm']['output']} | ${e['llm']['cost']:.3f} |"
            for e in self.log if e.get("llm")) or "| 無し | | | | | | |"
        counts = {}
        for e in self.log:
            if "counts" in e:
                counts[e["id"]] = e["counts"]
        counts_line = "; ".join(f"{k}: {counts_text(v)}" for k, v in counts.items()) or "無し"
        if self.gates:
            gate_line = "; ".join(f"段 {g['id']}（exit={g['exit']}）" for g in self.gates)
        else:
            gate_line = "本番の系へ届く操作" if result == "関門" else "無し"
        presented = ", ".join(g["presentation"] for g in self.gates if g.get("presentation")) or "無し"
        extra = ""
        if self.switched:
            extra += f"- 認証: 切り替え（{', '.join(self.switched)}）\n"
        limited = [e for e in self.log if e.get("limit")]
        if limited:
            extra += "- 利用上限: " + "; ".join(
                f"{e['id']} {e.get('limit_hits', 1)} 回（待ち {e.get('limit_waited', 0)} 秒"
                + (f"・解除 {e['limit_resets']}" if e.get("limit_resets") else "") + "）" for e in limited) + "\n"
        text = f"""## フェーズの報告

- フェーズ: {self.plan.get('フェーズ')}
- 課題: {' '.join('#' + str(i) for i in self.plan.get('課題', []))}
- 結果: {result}
- 関門: {gate_line}
- 次のフェーズ: {self.plan.get('次のフェーズ', '無し') if result == '完了' else '無し'}
- Pull Request: {self.plan.get('Pull Request', '無し')}
- 最後に記録した工程: {self.last_stage}
- 使った worker: 修正 {l['work']}（claude -p）/ 判断 {l['judge']}（claude -p）
{extra}- 提示物: {presented}
- 理由: {reason}
- 通った段: {steps}
- 件数: {counts_line}
- LLM の使用量: 入力 {l['input']} / cache read {l['cache_read']} / cache write {l['cache_write']} / 出力 {l['output']} / ${l['cost']:.3f}
- 記録: {self.dir}

| 段 | 往復 | 秒 | cache read | cache write | 出力 | 費用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{rows}
"""
        (self.dir / "report.md").write_text(text)
        return text


EXAMPLE = {
    "フェーズ": "検査", "課題": [0], "モード": "light", "作業場所": "/abs/worktree",
    "規則": "テストが落ちたら、失敗が変更に起因するなら fix、環境や揺れなら stop。",
    "上限": 10,
    "steps": [
        {"id": "test", "type": "run", "cmd": "pytest -q", "stage": "完了判定", "on_fail": "judge-test",
         "next": "pr"},
        {"id": "judge-test", "type": "judge", "inputs": ["test"],
         "question": "テストの失敗を直すか止めるか", "choices": ["fix", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test"],
         "prompt": "失敗したテストを直してコミットする（push しない）", "next": "test"},
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": "develop",
         "title": "変更の要約（#0）", "summary": "何を変えたかの 1〜2 文", "docs": ["issues/issue-0-design.md"],
         "next": "end"},
    ],
}


def sync_check(root: str, commit: bool) -> dict:
    """生成物を同期し、検査 4 本を回す。同期で変わったファイルは commit なら 1 つのコミットにする。"""
    items, failed = [], []
    for name, cmd in SYNC_CHECKS:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
        out = (p.stdout + p.stderr).strip()
        items.append({"name": name, "result": "ok" if p.returncode == 0 else "failed", "exit": p.returncode,
                      "tail": out[-1500:] if p.returncode else ""})
        if p.returncode:
            failed.append(name)
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                             text=True).stdout.splitlines()
    if commit and changed and "build" not in failed:
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
        c = subprocess.run(["git", "commit", "-q", "-m", "Update: 生成物を同期する"], cwd=root,
                           capture_output=True, text=True)
        items.append({"name": "commit", "result": "ok" if c.returncode == 0 else "failed",
                      "exit": c.returncode, "files": len(changed)})
        if c.returncode:
            failed.append("commit")
    summary = f"失敗: {', '.join(failed)}" if failed else "同期と検査 4 本が通った"
    return result("supervise-sync-check", "stopped" if failed else "ok", summary, items,
                  {"failed": len(failed), "changed": len(changed)})


RULE_IMPL = ("限ったテストや全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、変更に起因するなら fix、"
             "環境や変更に無関係なら次の段（限ったテストなら test-all、全体テストなら doc-lint）。"
             "2 回直しても同じ失敗なら stop。")
RULE_CHECK = ("全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、変更に起因するなら fix、"
              "変更に無関係なら ready。2 回直しても同じなら stop。")
FIX_PROMPT = "失敗した箇所を直してコミットする（push しない）。変更に起因しない失敗は直さない。"
MERGE_CMD = "python3 plugins/ndf/scripts/merged-steps.py merge-when-green {pr}"


def plan_impl(a) -> dict:
    n = a.issue[0]
    prompt = Path(a.prompt_file).read_text() if a.prompt_file else (a.prompt or f"課題 #{n} を実装する。")
    tests = " ".join(a.tests)
    plan = {
        "フェーズ": "実装", "課題": a.issue, "モード": a.mode, "作業場所": a.worktree,
        "規則": RULE_IMPL, "上限": 20,
        "steps": [
            {"id": "impl", "type": "work", "kind": "実装", "serena": True, "stage": "実装", "issues": True,
             "timeout": 3600, "prompt": prompt, "next": "sync"},
            {"id": "sync", "type": "run", "preset": "sync-check", "stage": "実装", "on_fail": "fix-sync",
             "next": "test-limited"},
            {"id": "fix-sync", "type": "work", "kind": "修正", "inputs": ["sync"], "prompt": FIX_PROMPT,
             "next": "sync"},
            {"id": "test-limited", "type": "run", "stage": "完了判定", "timeout": 900, "rerun_failed": True,
             "cmd": PYTEST.format(paths=tests), "on_fail": "judge", "next": "test-all"},
            {"id": "judge", "type": "judge", "inputs": ["test-limited", "test-all"],
             "question": "テストの失敗を直すか（fix）、限ったテストの失敗が変更に無関係なら全体テストへ（test-all）、"
                         "全体テストの失敗が変更に無関係なら文書の検査へ（doc-lint）、止めるか（stop）",
             "choices": ["fix", "test-all", "doc-lint", "stop"]},
            {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-limited", "test-all"],
             "prompt": FIX_PROMPT, "next": "test-limited"},
            {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
             "cmd": PYTEST.format(paths="."), "on_fail": "judge", "next": "doc-lint"},
            {"id": "doc-lint", "type": "run", "preset": "doc-lint", "stage": "完了判定", "on_fail": "fix-doc",
             "next": "pr"},
            {"id": "fix-doc", "type": "work", "kind": "修正", "inputs": ["doc-lint"],
             "prompt": "ヒットした行を今の決まりだけを書く形へ直してコミットする（push しない）。", "next": "doc-lint"},
            {"id": "pr", "type": "pr", "stage": "Pull Request", "base": a.base, "title": a.title,
             "summary": a.summary or "", "next": "merge"},
            {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "next": "end"},
        ],
    }
    if a.branch:
        plan["branch"] = a.branch
    return plan


def plan_check(a) -> dict:
    pr = a.pr
    if a.scope:
        # 範囲が決まっていれば駆動で回す（最終ゲートは全体のテスト）
        refactor = {"id": "refactor", "type": "drive", "drive": "cross-refactoring", "kind": "構造改善",
                    "stage": "構造改善", "timeout": 3600,
                    "args": f"{pr} --workflow-step --scope {' '.join(map(shlex.quote, a.scope))} "
                            f"--baseline-test {shlex.quote(PYTEST.format(paths='.'))}",
                    "next": "review"}
    else:
        # 範囲を決める判断が要るので Skill ごと回す
        refactor = {"id": "refactor", "type": "work", "full": True, "kind": "構造改善", "stage": "構造改善",
                    "timeout": 3600, "prompt": f"/ndf:cross-refactoring {pr}", "next": "review"}
    return {
        "フェーズ": "検査", "課題": a.issue or [], "モード": a.mode, "作業場所": a.worktree,
        "規則": RULE_CHECK, "上限": 12, "Pull Request": str(pr),
        "steps": [
            {"id": "assess", "type": "run", "preset": "assess", "stage": "構造改善", "skip_to": "review",
             "on_fail": "refactor", "next": "refactor"},
            refactor,
            {"id": "review", "type": "drive", "drive": "cross-review", "kind": "実装レビュー",
             "stage": "実装レビュー", "timeout": 3600, "args": f"{pr} --max-rounds 4", "next": "test-all"},
            {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
             "cmd": "git pull -q --rebase && " + PYTEST.format(paths="."), "on_fail": "judge", "next": "ready"},
            {"id": "judge", "type": "judge", "inputs": ["test-all"],
             "question": "全体テストの失敗を直す（fix）か、変更に無関係として進める（ready）か、止める（stop）か",
             "choices": ["fix", "ready", "stop"]},
            {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-all"],
             "prompt": "失敗したテストを直してコミットし、git push する。", "next": "test-all"},
            {"id": "ready", "type": "run", "cmd": f"git push -q; gh pr ready {pr}", "next": "merge"},
            {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "next": "end"},
        ],
    }


RULE_RELEASE_DEV = ("run の段が落ちたら、出力を読んで直せるもの（版数の書き漏れ・文書の形）は fix。外部の待ち（CI・ネットワーク）"
                    "の揺れなら同じ段をもう一度（retry）。認証や権限の不足・タグの重複は stop。")
RULE_RELEASE_PROD = ("利用者は関門 2 を承認した。run の段が落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
                     "外部の待ち（CI・ネットワーク）の揺れなら同じ段をもう一度。タグの重複・権限の不足は stop。")
NO_HISTORY = "以前との比較（「以前は」「〜によらず」「〜ではなくなった」）や課題番号の由来の括弧は書かない。今の決まりだけを書く。"
STEPS_PY = "python3 plugins/ndf/scripts/release-steps.py"
VERIFY_PY = "python3 plugins/ndf/scripts/release-verification-steps.py"


def plan_release(a) -> dict:
    """配布の計画。dev: bump → changelog → 説明文 → sync-check → release → verify-install → approval-facts
    → 提示物の説明文。prod: bump → changelog → 説明文 → 消費の記録 → sync-check → release → verify-install。"""
    v, dev = a.version, a.channel == "dev"
    base = re.sub(r"-.*$", "", v)  # 開発版の本番承認の提示物は正式版の番号で作る
    prs = " ".join(map(str, a.prs))
    repo = a.repo or (a.worktree.split("/.worktrees/")[0] if "/.worktrees/" in a.worktree else None)
    run_ids = ["bump", "changelog"] + ([] if dev else ["snapshot"]) + ["sync", "release", "verify"] + (
        ["facts"] if dev else [])
    commit = f"git add -A && git commit -m 'Release: ndf v{v}' でコミットする（件名に課題を閉じる語を書かない）。"
    if dev:
        notes = (f"CHANGELOG.md の {v} の節と plugins/ndf/README.md の更新案内（この版の見出しの下）に、PR の題名の一覧が"
                 "並んでいる。これを利用者向けの説明へ書き直す: 何ができるようになったか・使い方が変わる点。"
                 f"{NO_HISTORY}書いたら {commit}")
    else:
        notes = (f"この作業ツリーは release/v{v}（本番の版上げ）。plugins/ndf/README.md の更新案内の節と CHANGELOG.md の "
                 f"[ndf {v}] の節を確かめ、開発版の見出し（v{v}-dev.N）や「開発版です」の断りが残っていれば v{v} の"
                 "正式版の形に直す。changelog の段が PR の題名の一覧へ差し替えていたら、直前の開発版の節にあった"
                 "利用者向けの説明を戻す。git log -p -1 -- plugins/ndf/README.md で前の文を見てよい。"
                 f"{NO_HISTORY}書いたら {commit}")
    steps = [
        {"id": "bump", "type": "run", "stage": "配布", "cmd": f"{STEPS_PY} bump --plugin ndf --to {v}",
         "on_fail": "judge", "next": "changelog"},
        {"id": "changelog", "type": "run", "cmd": f"{STEPS_PY} changelog --version {v} --prs {prs}",
         "on_fail": "judge", "next": "notes"},
        {"id": "notes", "type": "work", "kind": "説明文", "inputs": ["changelog"], "prompt": notes,
         "next": "sync" if dev else "snapshot"},
    ]
    if not dev:
        steps.append({"id": "snapshot", "type": "run", "stage": "配布", "timeout": 900,
                      "cmd": f"sh -c '{STEPS_PY} run --root . --stage production --version {v} && git add -A && "
                             f"(git diff --cached --quiet || git commit -q -m \"Release: ndf v{v} のトークン消費の記録\")'",
                      "on_fail": "judge", "next": "sync"})
    ref = "develop" if dev else "main"
    steps += [
        {"id": "sync", "type": "run", "preset": "sync-check", "on_fail": "judge", "next": "release"},
        {"id": "release", "type": "run", "stage": "配布", "timeout": 2400 if dev else 3000,
         "cmd": f"{STEPS_PY} release --version {v} --channel {a.channel}", "on_fail": "judge", "next": "verify"},
        {"id": "verify", "type": "run", "stage": "配布" if dev else "リリース後テスト", "timeout": 1500, "cwd": repo,
         "cmd": f"sh -c 'git pull -q --ff-only origin develop && {VERIFY_PY} verify-install --ref {ref} "
                f"--expect {v} --runtimes claude,codex,kiro'",
         "on_fail": "judge", "next": "facts" if dev else "end"},
    ]
    if dev:
        approval = f"issues/approval-ndf-v{base}.md"
        prev = f" --prev-tag {a.prev_tag}" if a.prev_tag else ""
        steps += [
            {"id": "facts", "type": "run", "cwd": repo,
             "cmd": f"{STEPS_PY} approval-facts --version {base} --prs {prs}{prev}",
             "presentation_to": approval, "on_fail": "judge", "gate_next": "explain", "next": "explain"},
            {"id": "explain", "type": "work", "kind": "説明文", "cwd": repo, "inputs": ["verify", "facts"],
             "prompt": (f"{approval} は本番承認（関門 2）の提示物で、機械で作れる部分（対象・PR と CI・同意の項目・"
                        "戻し方）が入っている。次を書く。(1) 表の「配る中身」の欄: plugins/ndf/README.md の"
                        f"「v{v} へ更新するとき」の節と CHANGELOG.md の {base} の節から、利用者に何ができるようになるかを"
                        "3〜6 項目に。(2) 表の「検証への配布で確かめたこと」の欄: 入力の verify の結果（Claude Code・"
                        f"Codex・Kiro の 3 経路で develop から ndf {v} を導入し、版と中身が一致したか）を 2〜3 行に。"
                        "(3) 「同意を求めること」の前に節「## 未検証・残る危険」を足す: 通していない検査フェーズ・"
                        "この配布で初めて実機で使った手順・本番配布の後でしか確かめられないこと。PR の本文と入力から"
                        f"読めることだけを書く。{NO_HISTORY}コミットしない。"),
             "next": "end"},
        ]
    steps += [
        {"id": "judge", "type": "judge", "inputs": run_ids,
         "question": "落ちた段を直す（fix）か、同じ段をもう一度（retry）か、止める（stop）か。retry なら decision に"
                     "落ちた段の id を返す",
         "choices": ["fix", *run_ids, "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": run_ids,
         "prompt": "落ちた段の出力を読み、原因を直してコミットする（push しない）。直したら次は落ちた段からやり直す。",
         "next": "sync"},
    ]
    plan = {
        "フェーズ": f"配布（{'開発版' if dev else '本番'}）", "課題": a.issue, "モード": a.mode, "作業場所": a.worktree,
        "branch": a.branch or f"release/v{v}", "規則": RULE_RELEASE_DEV if dev else RULE_RELEASE_PROD, "上限": 20,
        "steps": steps,
    }
    if repo:
        plan["リポジトリ"] = repo
        plan["記録"] = f"{repo}/plugins/ndf/scripts/projects-sync.sh"
    return plan


def cmd_new(a) -> dict:
    plan = {"impl": plan_impl, "check": plan_check, "release": plan_release}[a.kind](a)
    key = {"impl": lambda: a.issue[0], "check": lambda: f"{a.pr}-check",
           "release": lambda: f"release-{a.version}"}[a.kind]()
    out = Path(a.out or f"plan-{key}.json")
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return result("supervise-new", "ok", f"計画を書いた: {out}",
                  [{"path": str(out), "kind": a.kind, "steps": [s["id"] for s in plan["steps"]]}],
                  {"steps": len(plan["steps"])})


RULE_DESIGN = ("設計の cross-review は上限 3 ラウンドで関門 1 へ渡す（収束を待たない）。レビューが ok か、"
               "上限・振動で打ち切られたなら gate。駆動そのものが失敗したら stop。")
WORKTREE_SETUP = SELF.parent / "worktree-setup.sh"


def mission_branch(name: str) -> str:
    return f"mission/{name}"


def plan_mission_design(a, n: int, repo: str) -> dict:
    """設計の持ち場: 設計文書を書き、設計 PR を出し、cross-review（設計の既定 3 ラウンド）の後に関門 1 で止まる。"""
    branch = f"design/issue-{n}"
    return {
        "持ち場": "設計", "課題": [n], "モード": a.mode, "作業場所": f"{repo}/.worktrees/{branch}",
        "branch": branch, "起点": f"origin/{a.base}", "リポジトリ": repo, "規則": RULE_DESIGN, "上限": 12,
        "steps": [
            {"id": "design", "type": "work", "full": True, "kind": "設計", "stage": "設計", "issues": True,
             "timeout": 3600, "next": "pr",
             "prompt": f"/ndf:design #{n}。設計文書は 1,000 行以下にする（超える主題は設計を 2 本に分けると報告する）。"
                       "コミットする（push しない）。"},
            {"id": "pr", "type": "pr", "stage": "ドキュメントレビュー", "base": a.base, "title": f"設計: #{n}",
             "summary": f"#{n} の設計（ミッション {a.name}）", "next": "review"},
            # --max-rounds を渡さない。設計の分類の既定（3 ラウンド・前のラウンドからの変更だけ）で回る
            {"id": "review", "type": "drive", "drive": "cross-review", "kind": "ドキュメントレビュー",
             "stage": "ドキュメントレビュー", "timeout": 3600, "args": "{pr}", "on_fail": "gate", "next": "gate"},
            {"id": "gate", "type": "judge", "inputs": ["review"],
             "question": "関門 1（設計 Pull Request のマージ）へ渡す（gate）か、止める（stop）か",
             "choices": ["gate", "stop"]},
        ],
    }


def plan_mission_branch(a, repo: str) -> dict:
    """ミッションのブランチを起点（develop）から切り、origin へ送る。"""
    mb = mission_branch(a.name)
    wt = f"{repo}/.worktrees/{mb}"
    cmd = (f"bash {shlex.quote(str(WORKTREE_SETUP))} create {shlex.quote(mb)} && "
           f"git -C {shlex.quote(wt)} push -q -u origin {shlex.quote(mb)}")
    return {
        "持ち場": "実装", "課題": a.issue, "モード": a.mode, "作業場所": repo, "規則": "", "上限": 3,
        "steps": [{"id": "mission-branch", "type": "run", "stage": "作業場所の用意", "timeout": 600,
                   "cmd": cmd, "next": "end"}],
    }


def plan_mission_impl(a, n: int, repo: str) -> dict:
    """実装の持ち場: 課題の作業ツリーをミッションのブランチから切り、課題の PR をミッションのブランチへ集める。"""
    mb = mission_branch(a.name)
    branch = f"feat/issue-{n}-{a.name}"
    ns = argparse.Namespace(
        issue=[n], prompt=None, prompt_file=None, tests=a.tests or ["."], mode=a.mode,
        worktree=f"{repo}/.worktrees/{branch}", base=mb, title=f"#{n} を実装する（ミッション {a.name}）",
        summary=f"#{n}（ミッション {a.name} のブランチへ集める）", branch=branch)
    plan = plan_impl(ns)
    plan.update({"起点": f"origin/{mb}", "リポジトリ": repo})
    return plan


def plan_mission_check(a, repo: str) -> dict:
    """検査の持ち場: ミッションのブランチから develop へ PR を 1 本出し、構造改善・cross-review・完了判定を 1 回通す。"""
    mb = mission_branch(a.name)
    ns = argparse.Namespace(pr="{pr}", scope=a.scope, issue=a.issue, mode=a.mode, worktree=f"{repo}/.worktrees/{mb}")
    plan = plan_check(ns)
    plan.pop("Pull Request", None)
    closes = "\n".join(f"Closes #{i}" for i in a.issue)
    plan.update({"branch": mb, "起点": f"origin/{mb}", "リポジトリ": repo})
    plan["steps"] = [
        {"id": "collect", "type": "run", "stage": "実装", "timeout": 600,
         "cmd": f"git pull -q --ff-only origin {shlex.quote(mb)}", "next": "pr"},
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": a.base, "title": f"ミッション {a.name}",
         "body": "template", "summary": f"ミッション {a.name} の課題を develop へ取り込む。\n\n{closes}",
         "next": "assess"},
    ] + plan["steps"]
    return plan


def plan_mission_release(a, repo: str) -> dict:
    return {
        "持ち場": "取り込み", "課題": a.issue, "モード": a.mode, "作業場所": repo, "規則": "", "上限": 3,
        "steps": [{"id": "release", "type": "work", "full": True, "kind": "配布", "stage": "配布",
                   "timeout": 3600, "prompt": "/ndf:release", "next": "end"}],
    }


def mission_plans(a) -> list[dict]:
    """ミッションの波を順に返す。波の中の計画は queue --max 3 で同時に流してよい。"""
    repo = str(Path(a.worktree).resolve())
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": plan_mission_design(a, n, repo) for n in a.design}})
        waves.append({"name": "関門 1", "gate": "設計 Pull Request をまとめて承認してマージする"})
    waves += [
        {"name": "ミッションのブランチ", "plans": {"mission-branch": plan_mission_branch(a, repo)}},
        {"name": "実装", "plans": {f"impl-{n}": plan_mission_impl(a, n, repo) for n in a.issue}},
        {"name": "検査", "plans": {"check": plan_mission_check(a, repo)}},
        {"name": "配布", "plans": {"release": plan_mission_release(a, repo)}},
    ]
    return waves


def cmd_new_mission(a) -> dict:
    """ミッションの計画を波ごとのファイルへ書き出す。波は番号の順に queue で流す。"""
    out = Path(a.out or f"mission-{a.name}")
    out.mkdir(parents=True, exist_ok=True)
    items, index = [], []
    for i, wave in enumerate(mission_plans(a), 1):
        entry = {"wave": i, "name": wave["name"]}
        if "gate" in wave:
            entry["gate"] = wave["gate"]
        else:
            paths = []
            for key, plan in wave["plans"].items():
                p = out / f"{i}-{key}.json"
                p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
                paths.append(str(p))
            entry["plans"] = paths
            entry["command"] = (f"python3 {shlex.quote(str(SELF))} queue "
                                + " ".join(map(shlex.quote, paths)) + " --max 3")
        index.append(entry)
        items.append(entry)
    manifest = out / "mission.json"
    manifest.write_text(json.dumps({"ミッション": a.name, "ブランチ": mission_branch(a.name), "波": index},
                                   ensure_ascii=False, indent=2) + "\n")
    plans = sum(len(e.get("plans", [])) for e in index)
    return result("supervise-new", "ok", f"ミッション {a.name} の計画を {plans} 本・{len(index)} 波で書いた: {manifest}",
                  items, {"waves": len(index), "plans": plans, "manifest": str(manifest)},
                  next="波の番号の順に command を打つ。関門の波では承認を取ってから次へ進む")


def report_result(text: str) -> str:
    m = re.search(r"^- 結果: (\S+)", text, re.M)
    return m.group(1) if m else "不明"


def state_dir_of(plan: str) -> Path:
    return Path(plan).parent / (Path(plan).stem + "-state")


def cmd_queue(plans: list[str], max_: int, poll: float = 1.0) -> dict:
    """計画を同時に max_ 本まで走らせ、空いた枠へ順に流す。"""
    pending, running, items = list(plans), {}, []
    while pending or running:
        while pending and len(running) < max_:
            plan = pending.pop(0)
            log = open(Path(plan).with_suffix(".log"), "w")
            running[plan] = (subprocess.Popen([sys.executable, str(SELF), "run", plan], stdout=log,
                                              stderr=subprocess.STDOUT), log, time.time())
        for plan, (proc, log, started) in list(running.items()):
            if proc.poll() is None:
                continue
            log.close()
            rep = state_dir_of(plan) / "report.md"
            res = report_result(rep.read_text()) if rep.is_file() else "報告なし"
            items.append({"plan": plan, "result": res, "exit": proc.returncode, "report": str(rep),
                          "seconds": round(time.time() - started, 1)})
            del running[plan]
        if running:
            time.sleep(poll)
    stopped = [i for i in items if i["result"] not in ("完了", "関門")]
    gates = [i for i in items if i["result"] == "関門"]
    status = "stopped" if stopped else "gate" if gates else "ok"
    summary = f"{len(items)} 本: 完了 {len(items) - len(stopped) - len(gates)} / 関門 {len(gates)} / 止まった {len(stopped)}"
    return result("supervise-queue", status, summary, items,
                  {"plans": len(items), "stopped": len(stopped), "gate": len(gates), "max": max_})


def note_row(report: str, next_text: str) -> str:
    def field(name: str) -> str:
        m = re.search(rf"^- {name}: (.*)$", report, re.M)
        return m.group(1).strip() if m else ""
    cost = re.search(r"/ \$([0-9.]+)\s*$", field("LLM の使用量"))
    pr = field("Pull Request")
    state = f"{field('フェーズ') or field('持ち場')}: {field('結果')}"  # 旧い報告（持ち場）も読む
    extra = [x for x in ((pr if pr and pr != "無し" else ""), (f"${cost.group(1)}" if cost else "")) if x]
    if extra:
        state += "（" + "、".join(extra) + "）"
    if field("結果") != "完了" and field("理由") not in ("", "無し"):
        state += f"。理由: {field('理由')}"
    return f"| {field('課題') or '—'} | {state} | {next_text or '—'} |"


def cmd_note(doc: str, report_path: str, next_text: str, section: str) -> dict:
    """引き継ぎ文書の、見出しに section を含む節の最初の表の末尾へ 1 行を足す。"""
    lines = Path(doc).read_text().splitlines()
    head = next((i for i, l in enumerate(lines) if l.startswith("#") and section in l), None)
    if head is None:
        return result("supervise-note", "stopped", f"見出しに「{section}」を含む節が無い", [], {})
    last = None
    for i in range(head + 1, len(lines)):
        if lines[i].startswith("#"):
            break
        if lines[i].startswith("|"):
            last = i
        elif last is not None:
            break
    if last is None:
        return result("supervise-note", "stopped", f"節「{lines[head].lstrip('# ')}」に表が無い", [], {})
    row = note_row(Path(report_path).read_text(), next_text)
    lines.insert(last + 1, row)
    Path(doc).write_text("\n".join(lines) + "\n")
    return result("supervise-note", "ok", "表へ 1 行を足した", [{"path": doc, "line": last + 2, "row": row}],
                  {"rows": 1})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("plan")
    r.add_argument("--state-dir")
    r.add_argument("--from", dest="start", help="この段から始める（途中から再開するとき）")
    sub.add_parser("example")
    n = sub.add_parser("new", help="雛形から計画を作る")
    n.add_argument("kind", choices=["impl", "check", "release", "mission"])
    n.add_argument("--issue", type=int, nargs="+", default=[])
    n.add_argument("--pr", type=int)
    n.add_argument("--worktree", required=True)
    n.add_argument("--tests", nargs="+", default=[], help="impl: 限ったテストの範囲")
    n.add_argument("--scope", nargs="+", default=[], help="check: 構造改善の範囲")
    n.add_argument("--title", help="impl: PR の題名")
    n.add_argument("--summary")
    n.add_argument("--prompt", help="impl: 実装の指示文")
    n.add_argument("--prompt-file", help="impl: 実装の指示文のファイル")
    n.add_argument("--branch", help="作業場所が無ければ作る作業ツリーのブランチ")
    n.add_argument("--base", default="develop")
    n.add_argument("--mode", default="standard")
    n.add_argument("--version", help="release: 配る版（例 10.17.11-dev.1）")
    n.add_argument("--prs", type=int, nargs="+", default=[], help="release: 含む PR")
    n.add_argument("--channel", choices=["dev", "prod"], help="release: 開発版（dev）か本番（prod）か")
    n.add_argument("--prev-tag", help="release dev: approval-facts の前のタグ（省略時は自動）")
    n.add_argument("--repo", help="release: 元のリポジトリ（省略時は作業場所の /.worktrees/ より前）")
    n.add_argument("--out")
    n.add_argument("--name", help="mission: ミッションの名前（ブランチは mission/<名前>）")
    n.add_argument("--design", type=int, nargs="+", default=[], help="mission: 設計 PR を出す課題")
    q = sub.add_parser("queue", help="計画を同時に --max 本まで順に流す")
    q.add_argument("plans", nargs="+")
    q.add_argument("--max", type=int, default=3)
    q.add_argument("--poll", type=float, default=5.0)
    t = sub.add_parser("note", help="報告から引き継ぎ文書の表へ 1 行を足す")
    t.add_argument("doc")
    t.add_argument("--report", required=True)
    t.add_argument("--next", default="")
    t.add_argument("--section", default="今の会話の進み")
    c = sub.add_parser("sync-check", help="生成物の同期と検査 4 本")
    c.add_argument("--root", default=".")
    c.add_argument("--commit", action="store_true", help="同期で変わったファイルをコミットする")
    a = ap.parse_args()
    if a.cmd == "example":
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "new" and a.kind == "mission":
        if not (a.name and a.issue):
            ap.error("new mission には --name・--issue が要る")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.name):
            ap.error("--name は英数字・. _ - だけで書く（ブランチ名 mission/<名前> に使う）")
        emit(cmd_new_mission(a))
    if a.cmd == "new":
        if a.kind == "impl" and not (a.issue and a.tests and a.title):
            ap.error("new impl には --issue・--tests・--title が要る")
        if a.kind == "check" and not a.pr:
            ap.error("new check には --pr が要る")
        if a.kind == "release" and not (a.version and a.prs and a.channel):
            ap.error("new release には --version・--prs・--channel が要る")
        if a.kind == "release" and not (a.repo or "/.worktrees/" in a.worktree):
            ap.error("new release には --repo が要る（作業場所が /.worktrees/ の下に無い）")
        emit(cmd_new(a))
    if a.cmd == "queue":
        emit(cmd_queue(a.plans, max(1, a.max), a.poll))
    if a.cmd == "note":
        emit(cmd_note(a.doc, a.report, a.next, a.section))
    if a.cmd == "sync-check":
        emit(sync_check(a.root, a.commit))
    plan = json.loads(Path(a.plan).read_text())
    state = Path(a.state_dir) if a.state_dir else state_dir_of(a.plan)
    text = Supervisor(plan, state).run(a.start)
    print(text)
    return 0 if "結果: 完了" in text or "結果: 関門" in text else 3


if __name__ == "__main__":
    sys.exit(main())
