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
| pr    | push して Draft の Pull Request を作る（スクリプト）。本文は材料（計画の値・コミット・変更の統計・run の結果・設計文書）から LLM が書く。`"body": "template"` なら材料をそのまま本文にする。本文には必ず「## 利用者向けの変化」の節を置く（段の `changes`、無ければ `summary`、それも無ければ題名から。配布の説明文の材料）。末尾の署名は本文に無いときだけ足す | 本文だけTool なし |

使い方:
    supervise.py run <plan.json> [--state-dir DIR] [--from <段の id>]
    supervise.py new impl --issue N --worktree DIR --tests PATH... --title T [--files PATH...] [--changes TEXT] [--prompt-file F] [--branch B] [--out F]
    supervise.py new check --pr N --worktree DIR [--issue N...] [--scope PATH...] [--out F]
    supervise.py new release --version V (--prs N... | --prs-from-queue) --channel dev|prod --worktree DIR
                             [--issue N...] [--prev-tag T] [--repo DIR] [--out F]
        # --prs-from-queue: queue が --then でこの計画を流す前に、先行の計画の報告の Pull Request を集めて
        # --prs に足す（--prs の固定の番号と併用できる）。prod の段の最後は後片付け（merged-steps.py cleanup）
    supervise.py new mission --name M --worktree <リポジトリの根> --issue N... [--design N...] [--tests PATH...] [--out DIR]
        # 並列の設計 → 関門 1 → ミッションのブランチ → 並列の実装（ミッションのブランチへ集める）→ 検査 1 回 → 配布
        # を波ごとの計画ファイルと mission.json へ書き出す。波の中は queue --max 3 で流す
    supervise.py queue <plan.json>... [--max 3] [--then <plan.json>...] [--done <パス>]
        # 空いた枠へ順に流す。作業ツリーは起動の前に 1 本ずつ作る。--then の計画は前の計画がすべて完了のときだけ
        # 続けて流す（実装の queue の後の配布など）。終わると結果の JSON を --done（省けば最初の計画の
        # <計画>-state/queue-done.json）へ書く。始めに流す計画の一覧を done の隣（<done>.plans.json）へ書く
    supervise.py wait <done のパス> [--timeout 秒] [--poll 秒]
        # queue の終わり（done）か、queue が流す計画の attention の行まで待つ。出力は要約の 1 行と結果の JSON。
        # 終了コード: done = 0 / attention = 20 / 上限 = 3。attention の後にもう一度打つと、その続きから待つ
    supervise.py note <引き継ぎ文書.md> --report <report.md> [--next 次の欄] [--section 見出しの語]
    supervise.py sync-check [--root DIR] [--commit]   # 生成物の同期と検査 4 本
    supervise.py example            # 計画の例を出す

new / queue / wait / note / sync-check の結果は lib/step_result.py の形の 1 行の JSON（status を見る）。

計画（JSON）:
    {
      "フェーズ": "検査", "課題": [818], "モード": "standard",
      "作業場所": "/abs/worktree",
      "branch": "feat/issue-818-x",         # 省略可。作業場所が無ければ起動時に作業ツリーを作る
      "起点": "origin/develop",             # branch から作るときの起点（既定 origin/develop）
      "リポジトリ": "/abs/repo",             # 作業ツリーの元（省略時は作業場所の /.worktrees/ より前）
                                            # git worktree add が .git/config の lock で落ちたら 5 回までやり直す
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
- `cmd` の `{pr}` は Pull Request の番号に、`{pr_url}` は URL に置き換わる（drive の段の `args` も同じ）。
  Pull Request は pr の段で作ったもの、または計画の `"Pull Request"`（URL なら末尾の数字を番号として読む）
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

途中の報告（`<state-dir>/progress.jsonl`。1 行 1 つの JSON。LLM は使わない）:
- `"kind": "step"`: 段の切り替わりごとに 1 行（at・step・type・exit・seconds・cost・next・summary）
- `"kind": "alive"`: 最後の行から計画の `"report_interval"`（既定 600 秒）動きが無いとき（step・elapsed・
  worker の最後の報告。run の段なら stderr の最後の行を last_output に）。長い段（work・run・drive）の
  待ちは区切って見るので、段の途中でも書く
- `"kind": "worker"`: work の段の worker が区切りごとに追記する 1 行（プロンプトに書き方と置き場を渡す）
- `"kind": "attention"`: conductor の判断が要る出来事（reason が 止まった・関門・同じ失敗の繰り返し・
  判断の段で stop が出そう）。worker の行の語と繰り返し、段の結果からスクリプトで分ける。
  `queue` はこの行を標準出力の `{"tool": "supervise-queue", "event": "attention", ...}` で知らせる

作業ディレクトリ（`<state-dir>/work/`。起動時に作る）: work と judge の段のプロンプトに渡す。worker は
作業ファイル（スクリプト・初期化の出力・プロンプト）をここに置く。計画ごとに別なので、並行する計画どうしで
同じ名前のファイルを上書きし合わない

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
PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"  # PR 本文の末尾の署名（1 度だけ）
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
- 先頭に何を変えたかを 1〜3 文。続けて「## 利用者向けの変化」「## 変更の要点」「## テスト」の節
- 「## 利用者向けの変化」は配布の CHANGELOG と更新案内へそのまま載る。利用者に何ができるようになるか・使い方が
  変わる点を 1〜5 項目の箇条書きにする。今の決まりだけを書き、以前との比較や課題番号を書かない。利用者に見える
  変化が無ければ「- 無し」
- 課題を閉じる語（Fix #番号・Closes・Resolves など）を書かない。課題は「#番号」とだけ書く
- 材料に無いことを書かない。本文だけを返し、前置きや囲みを付けない"""

JUDGE_SYSTEM = """あなたは NDF のフェーズの判断だけを行う。Tool は無い。
渡された結果と規則だけを根拠に、次の段を 1 つ選ぶ。
答えは JSON 1 つだけを返す: {"decision": "<選んだ値>", "reason": "<1 行>"}"""

REPORT_INTERVAL = 600  # 最後の行から動きが無いときに「まだ動いている」を足すまでの秒数。計画の "report_interval"
TICK = 5.0             # 子プロセスの待ちを区切って見る秒数の上限
PROGRESS_PROMPT = """## 途中の報告
区切り（課題の本文を読み終えた・テストを足した・実装を 1 つ終えた・コミットした）ごとに、次の 1 行の JSON を
{path} へ追記する（このファイルだけは作業場所の外でも追記してよい。書き換えず、末尾へ足す）:
{{"kind": "worker", "at": "<ISO 8601 の時刻>", "text": "<1 行の要約>"}}
例: printf '%s\\n' '{{"kind": "worker", "at": "'"$(date -Iseconds)"'", "text": "テストを 2 件足した"}}' >> {path}
止まった・関門に当たった・同じ失敗を繰り返しているときは、text にそのことを書く。"""
WORKDIR_PROMPT = """## 作業ディレクトリ
作業ディレクトリ: {path}
作業ファイル（スクリプト・初期化の出力・プロンプト・長い出力）はここに置く（ここだけは作業場所の外でも書いてよい）。
共有の scratchpad や /tmp の直下には置かない（並行する計画と同じ名前で上書きし合う）。"""
# worker の途中の報告を分ける語（スクリプトで見る。LLM は使わない）
PROGRESS_STOP = re.compile(r"止まった|止まる|進めない|進められない|判断が要る|できなかった|stuck", re.I)
PROGRESS_GATE = re.compile(r"関門|承認が要る|承認を待つ")
PROGRESS_FAIL = re.compile(r"失敗|落ちた|落ちる|エラー|\berror\b|\bfailed\b|traceback", re.I)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_ticking(cmd, tick=None, every: float = TICK, timeout: float | None = None, input: str | None = None,
                err_path: Path | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run と同じく待つが、every 秒ごとに tick() を呼ぶ（長い段の待ちの中で進行を書く）。

    err_path を渡すと stderr をそのファイルへ書かせる（待ちの間に最後の行を読めるように）。
    打ち切りは subprocess.TimeoutExpired を投げる。"""
    errf = open(err_path, "w", encoding="utf-8") if err_path else None
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=errf or subprocess.PIPE, text=True, **kw)
    except BaseException:
        if errf:
            errf.close()
        raise
    deadline = time.time() + timeout if timeout else None
    first = True
    while True:
        wait = every if deadline is None else max(0.01, min(every, deadline - time.time()))
        try:
            out, err = p.communicate(input if first else None, timeout=wait)
            if errf:
                errf.close()
                err = Path(err_path).read_text(encoding="utf-8", errors="replace")
            return subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            first = False
            if deadline is not None and time.time() >= deadline:
                p.kill()
                p.communicate()
                if errf:
                    errf.close()
                raise subprocess.TimeoutExpired(cmd, timeout)
            if tick:
                tick()


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
                env: dict | None = None, tick=None, every: float = TICK) -> dict:
    """claude -p を 1 回呼び、結果の本文と使用量を返す（既定は最小構成）。

    `env` は環境に足す変数（認証の切り替え）。利用上限で落ちたら `"limit": true` と、読めれば
    解除の時刻（UNIX 時刻）を `"resets_at"` に残す。`tick` は待ちの間に every 秒ごとに呼ぶ。
    """
    started = time.time()
    try:
        p = run_ticking(claude_cmd(system, tools, cwd, full, serena, resume), tick, every, input=prompt,
                        cwd=cwd, timeout=timeout, env={**os.environ, **env} if env else None)
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


CHANGES_HEADING = "## 利用者向けの変化"  # 配布の説明文（release-steps.py notes）の材料になる PR 本文の節


def user_changes(step: dict, title: str) -> str:
    """PR 本文の「利用者向けの変化」の節。段の changes（無ければ summary、それも無ければ題名）から組む。"""
    text = (step.get("changes") or step.get("summary") or title or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()] or ["無し"]
    items = [l if l.startswith(("- ", "* ")) else f"- {l}" for l in lines]
    return CHANGES_HEADING + "\n\n" + "\n".join(items)


def normalize_plan(plan: dict) -> dict:
    for old, new in PLAN_KEY_ALIASES.items():
        if old in plan:
            value = plan.pop(old)
            plan.setdefault(new, value)
    return plan


def pr_number(value) -> str:
    """計画の Pull Request（番号か URL）から番号を返す。URL なら末尾の数字を読む。読めなければ空。"""
    m = re.search(r"(\d+)/*$", str(value or "").strip())
    return m.group(1) if m else ""


WORKTREE_LOCK_RETRIES = 5        # .git/config の lock で落ちたときのやり直しの回数
WORKTREE_LOCK_WAIT = 1.0         # やり直しの間隔（秒）


def is_config_lock(stderr: str) -> bool:
    return "could not lock config file" in stderr or "File exists" in stderr


def ensure_worktree(plan: dict, sleep=time.sleep) -> str | None:
    """計画に branch があり作業場所が無ければ、作業ツリーを作る。誤りの文を返す（無ければ None）。

    run と queue の両方が使う。同時に作ると .git/config の lock で落ちるので、そのときは
    WORKTREE_LOCK_WAIT 秒おきに WORKTREE_LOCK_RETRIES 回までやり直す。
    """
    plan = normalize_plan(dict(plan))
    branch = plan.get("branch")
    wt = Path(plan["作業場所"])
    if not branch or wt.exists():
        return None
    repo = plan.get("リポジトリ") or (str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None)
    if not repo:
        return "作業ツリーの元のリポジトリが分からない（計画に リポジトリ を書く）"
    base = plan.get("起点", "origin/develop")
    if base.startswith("origin/"):
        subprocess.run(["git", "-C", repo, "fetch", "-q", "origin"], capture_output=True, text=True)
    p = None
    for i in range(WORKTREE_LOCK_RETRIES + 1):
        if i:
            sleep(WORKTREE_LOCK_WAIT)
        has = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                             capture_output=True, text=True).returncode == 0
        cmd = ["git", "-C", repo, "worktree", "add", "-q"] + ([str(wt), branch] if has
                                                             else ["-b", branch, str(wt), base])
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return None
        if not is_config_lock(p.stderr):
            break
        # lock で途中まで作られた作業ツリーは、次のやり直しの前に片付ける
        subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True, text=True)
        if wt.exists() and not any(wt.iterdir()):
            wt.rmdir()
    return f"作業ツリーを作れない: {p.stderr.strip()[:300]}"


class Supervisor:
    def __init__(self, plan: dict, state_dir: Path):
        self.plan = normalize_plan(plan)
        plan["steps"] = expand_parts(plan["steps"])
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.order = [s["id"] for s in plan["steps"]]
        self.cwd = plan["作業場所"]
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        # 計画ごとの作業ディレクトリ。状態ディレクトリの下なので並行する計画どうしで重ならない
        self.work = (self.dir / "work").resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict] = {}
        self.log: list[dict] = []
        self.llm = {"work": 0, "judge": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                    "output": 0, "cost": 0.0}
        self.last_stage = "無し"
        self.gates: list[dict] = []      # run の段が返した関門（終了コード 10〜19）
        self.switched: list[str] = []    # 利用上限で足した認証の変数の名前
        self.cur: dict = {}
        self.fail_counts: dict[str, int] = {}
        # 途中の報告（progress.jsonl）。LLM を使わずスクリプトで書き・分ける
        self.progress = self.dir / "progress.jsonl"
        self.interval = float(self.plan.get("report_interval", REPORT_INTERVAL))
        self.every = max(0.05, min(TICK, self.interval / 4))
        self.progress_seen = self.progress.stat().st_size if self.progress.is_file() else 0
        self.last_line_at = time.time()
        self.step_started = time.time()
        self.worker_last = ""
        self.worker_counts: dict[str, int] = {}
        self.attention_keys: set[str] = set()
        self.pcount = {"step": 0, "alive": 0, "worker": 0, "malformed": 0, "attention": 0, "llm": 0,
                       "llm_cost": 0.0}

    # --- 途中の報告 ---
    def progress_write(self, rec: dict) -> None:
        rec = {"kind": rec.pop("kind"), "at": now_iso(), **rec}
        with open(self.progress, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.progress_seen = self.progress.stat().st_size
        self.last_line_at = time.time()
        if rec["kind"] in self.pcount:
            self.pcount[rec["kind"]] += 1

    def attention(self, reason: str, text: str) -> None:
        """conductor の判断が要る出来事を 1 行残す（同じ理由と文は 1 度だけ）。"""
        key = f"{reason}\n{text}"
        if key in self.attention_keys:
            return
        self.attention_keys.add(key)
        self.progress_write({"kind": "attention", "step": self.cur.get("id"), "reason": reason, "text": text[:300]})

    def classify_worker(self, text: str) -> None:
        """worker の 1 行を語と繰り返しで分け、conductor の判断が要るものだけを attention にする。"""
        norm = re.sub(r"(?<![#\d])\d+", "N", text.strip())  # 件数や秒は畳み、課題番号（#906）は残す
        n = self.worker_counts[norm] = self.worker_counts.get(norm, 0) + 1
        if PROGRESS_GATE.search(text):
            self.attention("関門", text)
        elif PROGRESS_STOP.search(text):
            self.attention("止まった", text)
        elif PROGRESS_FAIL.search(text) and n >= 2:
            self.attention("同じ失敗の繰り返し", text)
        elif n >= 3:
            self.attention("止まった（同じ報告の繰り返し）", text)

    def read_worker_lines(self) -> None:
        """前に読んだ所から後の progress.jsonl を読み、worker の行を分ける。"""
        if not self.progress.is_file() or self.progress.stat().st_size <= self.progress_seen:
            return
        with open(self.progress, "rb") as f:
            f.seek(self.progress_seen)
            data = f.read()
        end = data.rfind(b"\n") + 1  # 書きかけの行は次に読む
        if not end:
            return
        self.progress_seen += end
        self.last_line_at = time.time()
        for raw in data[:end].decode("utf-8", "replace").splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                d = None
            if not isinstance(d, dict):
                self.pcount["malformed"] += 1
                continue
            if d.get("kind") != "worker":
                continue  # supervise.py が書いた行
            if not isinstance(d.get("text"), str) or not d["text"].strip():
                self.pcount["malformed"] += 1
                continue
            self.pcount["worker"] += 1
            self.worker_last = d["text"].strip()[:300]
            self.classify_worker(self.worker_last)

    def tick(self) -> None:
        """子プロセスの待ちの間に呼ぶ。worker の行を分け、動きが無ければ「まだ動いている」を足す。"""
        self.read_worker_lines()
        if time.time() - self.last_line_at >= self.interval:
            line = {"kind": "alive", "step": self.cur.get("id"), "type": self.cur.get("type"),
                    "elapsed": round(time.time() - self.step_started, 1), "worker": self.worker_last or "無し"}
            last = self.run_last_output()
            if last:
                line["last_output"] = last
            self.progress_write(line)

    def run_last_output(self) -> str | None:
        """run の段が stderr へ書いた最後の空でない行（run の段の待ちの間だけ）。"""
        path = getattr(self, "run_log", None)
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path else ""
        except OSError:
            return None
        return next((l.strip()[:300] for l in reversed(text.splitlines()) if l.strip()), None)

    def step_line(self, nxt: str | None) -> None:
        """段の切り替わりの 1 行（id・type・exit・秒・費用・次・要約）。"""
        c = self.cur
        text = c.get("text", "") or ""
        summary = c.get("decision") or next((l for l in reversed(text.splitlines()) if l.strip()), "")
        self.progress_write({"kind": "step", "step": c.get("id"), "type": c.get("type"), "exit": c.get("exit"),
                             "seconds": c.get("seconds"), "cost": (c.get("llm") or {}).get("cost", 0.0),
                             "next": nxt or "end", "summary": summary.strip()[:160]})

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
        kw = {"tick": self.tick, "every": self.every, **kw}
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
            until = time.time() + (min(wait, float(short)) if short else wait)
            while time.time() < until:  # 待ちの間も「まだ動いている」を書く
                time.sleep(max(0.0, min(self.every, until - time.time())))
                self.tick()
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
        return ensure_worktree(self.plan)

    def fill_pr(self, cmd: str) -> tuple[str, str | None]:
        """cmd / args の {pr} を Pull Request の番号に、{pr_url} を URL に置き換える。(cmd, 誤りの文) を返す。"""
        if "{pr}" not in cmd and "{pr_url}" not in cmd:
            return cmd, None
        value = str(self.plan.get("Pull Request") or "")
        number = pr_number(value)
        if not number:
            return cmd, "cmd の {pr} を置き換える Pull Request がまだ無い"
        if "{pr_url}" in cmd:
            url = value if "/pull/" in value else subprocess.run(
                ["gh", "pr", "view", number, "--json", "url", "--jq", ".url"], cwd=self.cwd,
                capture_output=True, text=True).stdout.strip()
            if not url:
                return cmd, f"cmd の {{pr_url}} を置き換える Pull Request #{number} の URL を読めない"
            cmd = cmd.replace("{pr_url}", url)
        return cmd.replace("{pr}", number), None

    def run_cmd(self, step: dict, extra_addopts: str = "") -> tuple[int, str]:
        cmd = step.get("cmd") or PRESETS.get(step.get("preset", ""), "")
        if not cmd:
            return 2, f"段 {step['id']} に cmd も知っている preset も無い"
        cmd, err = self.fill_pr(cmd)
        if err:
            return 2, err
        env = dict(os.environ)
        # 親の run の段から受け継いだ NO_REPORTS は、reports: true なら外す
        addopts = [env.get("PYTEST_ADDOPTS", "").replace(NO_REPORTS, "").strip()]
        if not step.get("reports"):
            addopts.append(NO_REPORTS)
        addopts.append(extra_addopts)
        env["PYTEST_ADDOPTS"] = " ".join(a for a in addopts if a)
        self.run_log = self.dir / "run-stderr.log"
        try:
            p = run_ticking(cmd, self.tick, self.every, shell=True, cwd=step.get("cwd", self.cwd),
                            timeout=step.get("timeout", 3600), env=env, err_path=self.run_log)
            return p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            return 124, f"打ち切り（{e.timeout} 秒）"
        finally:
            self.run_log = None

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
            p = run_ticking(cmd, self.tick, self.every, cwd=cwd, timeout=step.get("timeout", 1800) + 120)
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
                  + f"{step['prompt']}\n\n## 入力\n{self.inputs_text(step)}\n\n"
                  + WORKDIR_PROMPT.format(path=self.work) + "\n\n"
                  + PROGRESS_PROMPT.format(path=self.progress.resolve()))
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
        cmd, err = self.fill_pr(cmd)
        if err:
            self.cur.update(exit=2, text=err)
            return False, err
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
        changes = user_changes(step, title)
        body = f"""{step.get('summary', '')}

{changes}

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

{PR_FOOTER}
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
                body = res["text"].strip() + "\n"
                if not re.search(rf"^{CHANGES_HEADING}\s*$", body, re.M):
                    body = changes + "\n\n" + body
                if PR_FOOTER not in body:
                    body = body.rstrip() + f"\n\n{PR_FOOTER}\n"
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
                  + f"作業ディレクトリ: {self.work}（worker の作業ファイルの置き場所）\n"
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
            self.step_started = time.time()
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
            self.read_worker_lines()
            self.step_line(nxt)
            if self.cur.get("exit") not in (0, None) and not is_gate(self.cur.get("exit")) and nxt:
                fails = self.fail_counts[sid] = self.fail_counts.get(sid, 0) + 1
                if fails >= 2 and (self.steps.get(nxt) or {}).get("type") == "judge":
                    self.attention("判断の段で stop が出そう",
                                   f"段 {sid} が {fails} 回落ちた（exit={self.cur.get('exit')}）。次は判断の段 {nxt}")
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
        self.attention("関門", f"段 {step['id']} が関門を返した（exit={self.cur.get('exit')}）"
                       + (f"。提示物 {path}" if path else ""))

    def report(self, result: str, reason: str) -> str:
        l = self.llm
        self.read_worker_lines()
        pc = self.pcount
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
{extra}- 途中の報告: 段 {pc['step']} / まだ動いている {pc['alive']} / worker {pc['worker']}（形が違う {pc['malformed']}）/ conductor 向け {pc['attention']} / LLM へ回した {pc['llm']} 回・${pc['llm_cost']:.3f}（{self.progress}）
- 提示物: {presented}
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
             "環境や変更に無関係なら次の段（限ったテストなら pr、全体テストなら doc-lint）。"
             "2 回直しても同じ失敗なら stop。")
RULE_CHECK = ("全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、変更に起因するなら fix、"
              "変更に無関係なら ready。2 回直しても同じなら stop。")
FIX_PROMPT = "失敗した箇所を直してコミットする（push しない）。変更に起因しない失敗は直さない。"
MERGE_CMD = "python3 plugins/ndf/scripts/merged-steps.py merge-when-green {pr}"
IMPL_RULES = ("共通:\n"
              "- 文書には今の決まりだけを書く。.md の文言を照合するテストは書かない\n"
              "- コミットの件名と本文に課題を閉じる語（Closes / Fixes / Resolves）を書かない。"
              "区切りごとにコミットする（push しない）")


def plan_done(path: Path) -> bool:
    """計画が終わったか。状態ディレクトリの report.md の結果が完了なら終わり。"""
    rep = state_dir_of(str(path)) / "report.md"
    return rep.is_file() and report_result(rep.read_text()) == "完了"


def other_files(out: Path) -> list[tuple[str, list[str]]]:
    """同じディレクトリにある、まだ終わっていない他の計画の「触るファイル」を [(課題, パス)] で返す。"""
    found = []
    for f in sorted(out.parent.glob("*.json")):
        if f.resolve() == out.resolve() or plan_done(f):
            continue
        try:
            plan = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        files = plan.get("触るファイル") if isinstance(plan, dict) else None
        if isinstance(files, list) and files:
            issues = " ".join(f"#{i}" for i in plan.get("課題", []))
            found.append((issues or f.name, [str(x) for x in files]))
    return found


def impl_prompt(a, out: Path | None) -> str:
    """実装の指示文。--prompt が無ければ課題への参照を組み、共通の規則と他の計画の除外を足す。"""
    n = a.issue[0]
    if a.prompt_file:
        head = Path(a.prompt_file).read_text().rstrip()
    elif a.prompt:
        head = a.prompt.rstrip()
    else:
        head = f"課題 #{n} を実装する。本文は `gh issue view {n}` で読む（何をするか と 受け入れ条件）。"
        if getattr(a, "files", None):
            head += "\n触る範囲: " + "、".join(a.files)
    parts = [head]
    others = other_files(out) if out else []
    if others:
        parts.append("並行して別の計画が次を触る。それらは変えない: "
                     + "、".join(f"{', '.join(files)}（{issues}）" for issues, files in others))
    parts.append(IMPL_RULES)
    return "\n".join(parts) + "\n"


def plan_impl(a, out: Path | None = None) -> dict:
    if out is None and hasattr(a, "out"):
        out = Path(a.out or f"plan-{a.issue[0]}.json")
    prompt = impl_prompt(a, out)
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
             "cmd": PYTEST.format(paths=tests), "on_fail": "judge", "next": "pr"},
            {"id": "judge", "type": "judge", "inputs": ["test-limited", "test-all"],
             "question": "テストの失敗を直すか（fix）、限ったテストの失敗が変更に無関係なら PR へ（pr）、"
                         "全体テストの失敗が変更に無関係なら文書の検査へ（doc-lint）、止めるか（stop）",
             "choices": ["fix", "pr", "doc-lint", "stop"]},
            {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-limited", "test-all"],
             "prompt": FIX_PROMPT, "next": "test-limited"},
            # Draft の PR を全体テストの前に出し、CI と手元の全体テストを並べる。直した後は pr の段が push して本文を更新する
            {"id": "pr", "type": "pr", "stage": "Pull Request", "base": a.base, "title": a.title,
             "summary": a.summary or "", "changes": getattr(a, "changes", None) or "", "next": "test-all"},
            {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
             "cmd": PYTEST.format(paths="."), "on_fail": "judge", "next": "doc-lint"},
            {"id": "doc-lint", "type": "run", "preset": "doc-lint", "stage": "完了判定", "on_fail": "fix-doc",
             "next": "ready"},
            {"id": "fix-doc", "type": "work", "kind": "修正", "inputs": ["doc-lint"],
             "prompt": "ヒットした行を今の決まりだけを書く形へ直してコミットする（push しない）。", "next": "doc-lint"},
            {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "next": "merge"},
            {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "next": "end"},
        ],
    }
    if getattr(a, "files", None):
        plan["触るファイル"] = a.files
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
STEPS_PY = "python3 plugins/ndf/scripts/release-steps.py"
VERIFY_PY = "python3 plugins/ndf/scripts/release-verification-steps.py"
MERGED_PY = "python3 plugins/ndf/scripts/merged-steps.py"
QUEUE_PRS = "{queue_prs}"  # queue が --then の計画を流す前に、先行の計画の Pull Request の番号へ置き換える


def plan_release(a) -> dict:
    """配布の計画。dev: bump → changelog → 説明文 → sync-check → release → verify-install → approval-facts
    → 提示物の欄。prod: bump → changelog → 説明文 → 消費の記録 → sync-check → release → verify-install。
    説明文と提示物の欄は release-steps.py notes が PR 本文の「利用者向けの変化」から組む（LLM を使わない）。"""
    v, dev = a.version, a.channel == "dev"
    base = re.sub(r"-.*$", "", v)  # 開発版の本番承認の提示物は正式版の番号で作る
    # --prs-from-queue なら、queue が先行の計画の Pull Request の番号で QUEUE_PRS を置き換える
    prs = " ".join([*map(str, a.prs), *([QUEUE_PRS] if getattr(a, "prs_from_queue", False) else [])])
    repo = a.repo or (a.worktree.split("/.worktrees/")[0] if "/.worktrees/" in a.worktree else None)
    run_ids = ["bump", "changelog", "notes"] + ([] if dev else ["snapshot"]) + ["sync", "release", "verify"] + (
        ["facts", "explain"] if dev else [])
    # 説明文は PR 本文の「利用者向けの変化」から機械で組む（節が無い PR は題名）
    notes = (f"sh -c '{STEPS_PY} notes --version {v} --prs {prs} && git add -A && "
             f"(git diff --cached --quiet || git commit -q -m \"Release: ndf v{v}\")'")
    steps = [
        {"id": "bump", "type": "run", "stage": "配布", "cmd": f"{STEPS_PY} bump --plugin ndf --to {v}",
         "on_fail": "judge", "next": "changelog"},
        {"id": "changelog", "type": "run", "cmd": f"{STEPS_PY} changelog --version {v} --prs {prs}",
         "on_fail": "judge", "next": "notes"},
        {"id": "notes", "type": "run", "stage": "配布", "cmd": notes, "on_fail": "judge",
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
         "on_fail": "judge", "next": "facts" if dev else "cleanup"},
    ]
    if not dev:
        # 後片付け: 配布の PR（release/v{v} → main）とミッションの PR（--prs）のブランチと作業ツリー
        run_ids.append("cleanup")
        steps.append(
            {"id": "cleanup", "type": "run", "stage": "後片付け", "cwd": repo,
             "cmd": f"sh -c '{MERGED_PY} cleanup $(gh pr list --head release/v{v} --base main --state merged "
                    f"--json number --jq \".[].number\") {prs}'",
             "on_fail": "judge", "next": "end"})
    if dev:
        approval = f"issues/approval-ndf-v{base}.md"
        prev = f" --prev-tag {a.prev_tag}" if a.prev_tag else ""
        steps += [
            {"id": "facts", "type": "run", "cwd": repo,
             "cmd": f"{STEPS_PY} approval-facts --version {base} --prs {prs}{prev}",
             "presentation_to": approval, "on_fail": "judge", "gate_next": "explain", "next": "explain"},
            {"id": "explain", "type": "run", "cwd": repo,
             "cmd": f"{STEPS_PY} notes --version {v} --prs {prs} --approval {approval} "
                    f"--verified claude,codex,kiro --ref develop",
             "on_fail": "judge", "next": "end"},
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
                  {"steps": len(plan["steps"])},
                  next=(f"実装の queue へ --then {out} で渡す（実装がすべて完了した後に続けて流れる）"
                        if a.kind == "release" else None))


RULE_DESIGN = ("設計の cross-review は上限 3 ラウンドで関門 1 へ渡す（収束を待たない）。レビューが ok か、"
               "上限・振動で打ち切られたなら gate。駆動そのものが失敗したら stop。")
WORKTREE_SETUP = SELF.parent / "worktree-setup.sh"


def mission_branch(name: str) -> str:
    return f"mission/{name}"


def plan_mission_design(a, n: int, repo: str) -> dict:
    """設計のフェーズ: 設計文書を書き、設計 PR を出し、cross-review（設計の既定 3 ラウンド）の後に関門 1 で止まる。"""
    branch = f"design/issue-{n}"
    return {
        "フェーズ": "設計", "課題": [n], "モード": a.mode, "作業場所": f"{repo}/.worktrees/{branch}",
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
        "フェーズ": "実装", "課題": a.issue, "モード": a.mode, "作業場所": repo, "規則": "", "上限": 3,
        "steps": [{"id": "mission-branch", "type": "run", "stage": "作業場所の用意", "timeout": 600,
                   "cmd": cmd, "next": "end"}],
    }


def plan_mission_impl(a, n: int, repo: str) -> dict:
    """実装のフェーズ: 課題の作業ツリーをミッションのブランチから切り、課題の PR をミッションのブランチへ集める。"""
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
    """検査のフェーズ: ミッションのブランチから develop へ PR を 1 本出し、構造改善・cross-review・完了判定を 1 回通す。"""
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
        "フェーズ": "取り込み", "課題": a.issue, "モード": a.mode, "作業場所": repo, "規則": "", "上限": 3,
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


def attention_lines(prog: Path, offset: int) -> tuple[list[dict], int]:
    """progress.jsonl の offset から後の、書き終わった attention の行と、読んだ所を返す。"""
    if not prog.is_file() or prog.stat().st_size <= offset:
        return [], offset
    with open(prog, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    found = []
    for raw in data[:end].decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("kind") == "attention":
            found.append(d)
    return found, offset + end


def notify_attention(plan: str, offset: int) -> int:
    """計画の progress.jsonl の offset から後の attention の行を標準出力へ知らせ、読んだ所を返す。"""
    prog = state_dir_of(plan) / "progress.jsonl"
    found, offset = attention_lines(prog, offset)
    for d in found:
        print(json.dumps({"tool": "supervise-queue", "event": "attention", "plan": plan,
                          "progress": str(prog), **{k: d.get(k) for k in ("at", "step", "reason", "text")}},
                         ensure_ascii=False), flush=True)
    return offset


def queue_done_path(plans: list[str], done: str | None) -> Path:
    """queue の終わりに結果の JSON を書く所。省けば最初の計画の状態ディレクトリの queue-done.json。"""
    return Path(done) if done else state_dir_of(plans[0]) / "queue-done.json"


def queue_plans_path(done: Path) -> Path:
    """queue が始めに流す計画の一覧を書く所（done の隣）。wait が読む。"""
    return done.with_suffix(".plans.json")


def wait_cursor_path(done: Path) -> Path:
    """wait が attention をどこまで知らせたかを残す所（done の隣）。"""
    return done.with_suffix(".wait.json")


def progress_size(plan: str) -> int:
    prog = state_dir_of(plan) / "progress.jsonl"
    return prog.stat().st_size if prog.is_file() else 0


def queue_prs(items: list[dict]) -> list[str]:
    """完了した計画の報告の Pull Request を番号にして、重ねずに順に返す。"""
    out: list[str] = []
    for i in items:
        rep = Path(i.get("report") or "")
        if i.get("result") != "完了" or not rep.is_file():
            continue
        m = re.search(r"^- Pull Request: (.*)$", rep.read_text(), re.M)
        n = pr_number(m.group(1).strip()) if m else ""
        if n and n not in out:
            out.append(n)
    return out


def fill_queue_prs(plan: str, prs: list[str]) -> str | None:
    """計画の QUEUE_PRS を prs で置き換えて書き戻す。置き換えられなければ理由を返す。"""
    try:
        text = Path(plan).read_text()
    except OSError as e:
        return f"計画を読めない: {e}"
    if QUEUE_PRS not in text:
        return None
    if not prs:
        return "--prs-from-queue の計画だが、先行の計画の報告に Pull Request が無い"
    write_atomic(Path(plan), text.replace(QUEUE_PRS, " ".join(prs)))
    return None


def write_atomic(path: Path, text: str) -> None:
    """一時ファイルへ書いてから rename する（待つ側が書きかけを読まない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def run_batch(plans: list[str], max_: int, poll: float) -> list[dict]:
    """計画を同時に max_ 本まで走らせ、空いた枠へ順に流し、終わった順に結果を返す。"""
    pending, running, items = list(plans), {}, []
    seen: dict[str, int] = {}
    while pending or running:
        while pending and len(running) < max_:
            plan = pending.pop(0)
            # 作業ツリーは queue の側で順に作る（同時の git worktree add は .git/config の lock で落ちる）。
            # 作れなかったときの報告は run が同じ誤りで書く
            try:
                ensure_worktree(json.loads(Path(plan).read_text()))
            except (OSError, ValueError, KeyError):
                pass
            prog = state_dir_of(plan) / "progress.jsonl"
            seen[plan] = prog.stat().st_size if prog.is_file() else 0  # 前の実行の行は知らせない
            log = open(Path(plan).with_suffix(".log"), "w")
            running[plan] = (subprocess.Popen([sys.executable, str(SELF), "run", plan], stdout=log,
                                              stderr=subprocess.STDOUT), log, time.time())
        for plan in list(running):
            seen[plan] = notify_attention(plan, seen[plan])
        for plan, (proc, log, started) in list(running.items()):
            if proc.poll() is None:
                continue
            seen[plan] = notify_attention(plan, seen[plan])
            log.close()
            rep = state_dir_of(plan) / "report.md"
            res = report_result(rep.read_text()) if rep.is_file() else "報告なし"
            items.append({"plan": plan, "result": res, "exit": proc.returncode, "report": str(rep),
                          "seconds": round(time.time() - started, 1)})
            del running[plan]
        if running:
            time.sleep(poll)
    return items


NOT_RUN = "流さなかった"


def cmd_queue(plans: list[str], max_: int, poll: float = 1.0, then: list[str] | None = None,
              done: str | None = None) -> dict:
    """計画を同時に max_ 本まで走らせ、空いた枠へ順に流す。

    走っている計画の progress.jsonl に conductor 向けの行（"kind": "attention"）が足されたら、
    標準出力へ 1 行の JSON（"event": "attention"）で知らせる。最後の行は従来どおり結果の JSON。
    then の計画は、前の計画がすべて 完了 のときだけ同じ枠（max_）で続けて流す。1 本でも 完了 でなければ
    流さず、items に 流さなかった と理由を残す。then の計画の QUEUE_PRS（new release --prs-from-queue）は、
    流す前に前の計画の報告の Pull Request の番号で置き換える。
    始めに流す計画の一覧を done の隣へ書き（wait が読む）、終わったら（後続を含めて）結果の JSON を done へ書く。"""
    then = then or []
    done_path = queue_done_path(plans, done)
    done_path.unlink(missing_ok=True)  # 前の queue の終わりを待つ側が読まないように、始めに消す
    wait_cursor_path(done_path).unlink(missing_ok=True)
    all_plans = [*plans, *then]
    write_atomic(queue_plans_path(done_path), json.dumps(
        {"started": now_iso(), "plans": all_plans, "offsets": {p: progress_size(p) for p in all_plans}},
        ensure_ascii=False) + "\n")
    items = run_batch(plans, max_, poll)
    if then:
        not_done = [i for i in items if i["result"] != "完了"]
        if not_done:
            reason = "前の計画が完了していない: " + "、".join(f"{i['plan']}（{i['result']}）" for i in not_done)
            items += [{"plan": p, "result": NOT_RUN, "reason": reason} for p in then]
        else:
            prs, runnable, skipped_then = queue_prs(items), [], []
            for p in then:
                err = fill_queue_prs(p, prs)
                if err:
                    skipped_then.append({"plan": p, "result": NOT_RUN, "reason": err})
                else:
                    runnable.append(p)
            items += skipped_then + (run_batch(runnable, max_, poll) if runnable else [])
    ran = [i for i in items if i["result"] != NOT_RUN]
    skipped = len(items) - len(ran)
    stopped = [i for i in ran if i["result"] not in ("完了", "関門")]
    gates = [i for i in ran if i["result"] == "関門"]
    status = "stopped" if stopped else "gate" if gates else "ok"
    summary = f"{len(ran)} 本: 完了 {len(ran) - len(stopped) - len(gates)} / 関門 {len(gates)} / 止まった {len(stopped)}"
    if skipped:
        summary += f"。後続 {skipped} 本は流さなかった"
    nxt = "関門の計画の report.md を読んで提示する" if status == "gate" else None
    res = result("supervise-queue", status, summary, items,
                 {"plans": len(ran), "stopped": len(stopped), "gate": len(gates), "not_run": skipped,
                  "max": max_, "done": str(done_path)}, next=nxt)
    write_atomic(done_path, json.dumps(res, ensure_ascii=False) + "\n")
    return res


WAIT_DONE, WAIT_ATTENTION, WAIT_TIMEOUT = 0, 20, 3  # 20 は共通の契約の「LLM の判断待ち」、3 は前提が無い


def cmd_wait(done: str, timeout: float, poll: float = 5.0, clock=time.time, sleep=time.sleep) -> tuple[str, dict, int]:
    """queue の終わり（done）か、queue が流す計画の attention の行まで待つ。(要約, 結果, 終了コード) を返す。

    計画の一覧と読み始める所は queue が done の隣に書いた <done>.plans.json から読む。知らせた attention の
    続きは <done>.wait.json に残し、次の wait はそこから読む（同じ行を 2 度知らせない）。"""
    done_path = Path(done)
    plans_path, cursor_path = queue_plans_path(done_path), wait_cursor_path(done_path)
    start = clock()
    while True:
        if done_path.is_file() and done_path.stat().st_size > 0 and not (
                plans_path.is_file() and plans_path.stat().st_mtime > done_path.stat().st_mtime):
            try:
                res = json.loads(done_path.read_text())
            except (OSError, json.JSONDecodeError):
                res = None
            if isinstance(res, dict):
                summary = f"queue が終わった（{res.get('status')}）: {res.get('summary')}"
                return summary, result("supervise-wait", "ok", summary, [res],
                                       {"event": "done", "queue_status": res.get("status"), "done": str(done_path)},
                                       next=res.get("next")), WAIT_DONE
        try:
            listing = json.loads(plans_path.read_text())
        except (OSError, json.JSONDecodeError):
            listing = None
        if isinstance(listing, dict):
            offsets = dict(listing.get("offsets") or {})
            try:
                cur = json.loads(cursor_path.read_text())
                if cur.get("started") == listing.get("started"):
                    offsets.update(cur.get("offsets") or {})
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            found = []
            for plan in listing.get("plans") or []:
                prog = state_dir_of(plan) / "progress.jsonl"
                lines, offsets[plan] = attention_lines(prog, int(offsets.get(plan, 0)))
                found += [{"plan": plan, "progress": str(prog),
                           **{k: d.get(k) for k in ("at", "step", "reason", "text")}} for d in lines]
            if found:
                write_atomic(cursor_path, json.dumps({"started": listing.get("started"), "offsets": offsets},
                                                     ensure_ascii=False) + "\n")
                first = found[0]
                summary = (f"attention {len(found)} 件: {first['plan']} の段 {first.get('step')}"
                           f"（{first.get('reason')}）: {first.get('text')}")
                return summary, result("supervise-wait", "gate", summary, found,
                                       {"event": "attention", "attention": len(found), "done": str(done_path)},
                                       next="attention を読んで対処し、もう一度 wait を打つ（続きから待つ）"), WAIT_ATTENTION
        if clock() - start >= timeout:
            summary = f"{timeout:g} 秒待ったが queue が終わらず attention も無い"
            return summary, result("supervise-wait", "stopped", summary, [],
                                   {"event": "timeout", "timeout": timeout, "done": str(done_path)},
                                   next="queue の <計画>.log と progress.jsonl を見て、続けるならもう一度 wait を打つ"), WAIT_TIMEOUT
        sleep(poll)


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
    n = sub.add_parser("new", help="雛形から計画を作る。release の計画は、実装の queue へ --then で渡すと"
                                   "実装がすべて完了した後に続けて流れる")
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
    n.add_argument("--files", nargs="+", default=[], metavar="PATH",
                   help="impl: 触るファイル。同じ出力先の、まだ終わっていない他の計画の指示文へ除外として載る")
    n.add_argument("--changes", help="impl: PR 本文の「利用者向けの変化」の材料（配布の説明文になる）")
    n.add_argument("--branch", help="作業場所が無ければ作る作業ツリーのブランチ")
    n.add_argument("--base", default="develop")
    n.add_argument("--mode", default="standard")
    n.add_argument("--version", help="release: 配る版（例 10.17.11-dev.1）")
    n.add_argument("--prs", type=int, nargs="+", default=[], help="release: 含む PR")
    n.add_argument("--prs-from-queue", action="store_true",
                   help="release: queue が --then で流す前に、先行の計画の報告の Pull Request を --prs に足す")
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
    q.add_argument("--then", nargs="+", default=[], metavar="PLAN",
                   help="前の計画がすべて完了したときだけ続けて流す計画（例: 配布の計画）")
    q.add_argument("--done", help="終わったときに結果の JSON を書く所（省けば最初の計画の状態ディレクトリの "
                                  "queue-done.json）。待つ側は wait <このパス> で待つ")
    w = sub.add_parser("wait", help="queue の終わりか attention の行まで待つ（done = 0 / attention = 20 / 上限 = 3）")
    w.add_argument("done", help="queue の --done のパス（省いた queue なら <最初の計画>-state/queue-done.json）")
    w.add_argument("--timeout", type=float, default=10800.0, help="待つ上限（秒）")
    w.add_argument("--poll", type=float, default=5.0)
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
        if a.kind == "release" and not (a.version and (a.prs or a.prs_from_queue) and a.channel):
            ap.error("new release には --version・--prs（か --prs-from-queue）・--channel が要る")
        if a.kind == "release" and not (a.repo or "/.worktrees/" in a.worktree):
            ap.error("new release には --repo が要る（作業場所が /.worktrees/ の下に無い）")
        emit(cmd_new(a))
    if a.cmd == "queue":
        emit(cmd_queue(a.plans, max(1, a.max), a.poll, a.then, a.done))
    if a.cmd == "wait":
        summary, res, code = cmd_wait(a.done, a.timeout, a.poll)
        print(summary, flush=True)
        emit(res, code)
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
