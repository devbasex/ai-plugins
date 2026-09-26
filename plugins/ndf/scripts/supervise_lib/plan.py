"""プランの形と読み替え（#1142 の C1）。`lib/` だけを import する。

計画（JSON）:
    {
      "フェーズ": "検査", "課題": [818], "モード": "standard",
      "作業場所": "/abs/worktree",
      "branch": "feat/issue-818-x",         # 省略可。作業場所が無ければ起動時に作業ツリーを作る
      "起点": "origin/main",                # branch から作るときの起点（既定 origin/<base_branch>）
      "base_branch": "main",                # 起点のブランチ。pr のステップの宛先と preset の {base}（既定は .ndf/worktree.json）
      "no_reports": "-p no:x",              # run のステップの PYTEST_ADDOPTS に足す（既定は .ndf/supervise.json の test.no_reports）
      "リポジトリ": "/abs/repo",             # 作業ツリーの元（省略時は作業場所の /.worktrees/ より前）
                                            # git worktree add が .git/config の lock で落ちたら 5 回までやり直す
      "記録": "/abs/projects-sync.sh",      # 省略可。stage を記録する
      "規則": "判断の規則の抜粋（文字列）",   # judge へ毎回渡す
      "上限": 30,                           # 実行するステップの数の上限（ループの歯止め）
      "steps": [
        {"id": "test", "type": "run", "cmd": "pytest -q", "stage": "完了判定",
         "timeout": 1800, "on_fail": "judge-test", "next": "end"},
        {"id": "judge-test", "type": "judge", "inputs": ["test"],
         "question": "テストの失敗を直すか、止めるか", "choices": ["fix", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test"],
         "prompt": "失敗したテストを直してコミットする", "next": "test"}
      ]
    }

パートに分ける: work のステップに `"parts": [{"name": ..., "files": [...]}, ...]` を書くと、パートごとに
新しい文脈の claude -p のステップ（`<id>-1`, `<id>-2`, ...）へ展開する。大きな実装は分けて書く。

Serena: work のステップに `"serena": true` を書くと Serena の MCP だけを載せる（大きなコードを何度も読む実装向け）。

課題の本文: work のステップに `"issues": [858]`（`true` なら計画の `課題`）を書くと、`gh issue view` の題と本文を
プロンプトの先頭へ入れる。

worker のランタイム: work と drive のステップに `"runtime": "codex"`（`kiro` / `agy` / `claude`）を書くと、worker を
`external-ai.py run` で起動する（起動・上限つきの待ち・回収はそのコマンドが持つ）。書かなければ最小構成の claude -p。

drive のステップ: `cmd`（または `"drive": "cross-review" | "cross-refactoring"` と `"args"`）を打ち、最後の行の JSON を読む。
- `status` が `ok` なら成功。`metrics`（ラウンド数・指摘・未解決・適用・取り消しなど）を報告の「件数」へ載せる
- `gate` なら `items[0]` の `prompt_file` を worker に渡し、`result_file` を書かせてから同じコマンドを打ち直す。
  `command` を持つ pause（最終ゲートの cross-review）は、その駆動を同じ形で回し、`metrics.review_status` を
  `result_file` へ書く
- `stopped`・結果ファイルが書かれない・`"max_pauses"`（既定 12）を超える、のどれかなら失敗

ステップごとの作業場所: run と work のステップに `"cwd"` を書くと、そのステップだけ別の場所で動く（取り込みで PR ごとに
作業ツリーが違うとき）。work のステップでは worker へ渡す「作業場所」もその `cwd` になる。

宣言（リポジトリの根の `.ndf/`。new は作業場所 → 元のリポジトリ → 今のディレクトリの順に探す。引数が先に効く）:
- `worktree.json` の `base_branch`（起点のブランチ。PR の宛先・差分の起点）と `production_branch`（本番のブランチ）
- `supervise.json`（無ければ、要る雛形は「宣言が無い」と止まる）:
      {"version": 1,
       "test": {"command": "<テストのコマンド。{paths} を範囲に置き換える>", "all": "<全体の範囲（既定 .）>",
                "no_reports": "<run のステップの PYTEST_ADDOPTS に足す。省略可>"},
       "sync_checks": [{"name": "<名前>", "command": "<同期かチェックのコマンド>"}, ...],   # 省略可。無ければ sync のステップを置かない
       "release": {"form": "package-plugin", "plugin": "<名前>", "runtimes": ["claude", ...]}}
  テストの範囲の選び方（--tests）と配布してよいかの判断は、宣言にせず conductor と judge のステップに残す

配布の雛形（new release）: 形（`release.form`）ごとにある。無い形は /ndf:release で配る（new mission は配布の計画を
書かずに検査までを書き、結果の next と items に /ndf:release で行うことを載せる。new close と new release は形を要る）。
- `package-plugin`（Claude Code のプラグイン）: dev は bump → changelog → 説明文 → sync-check → release →
  verify-install（起点のブランチ）→ approval-facts → 提示物の説明文。approval-facts の提示物は
  `issues/approval-<plugin>-v<正式版>.md` へ写す。prod は bump → changelog → 説明文 → トークン消費の記録 →
  sync-check → release → verify-install（本番のブランチ）→ 後片付け。sync-check は同期とチェックの宣言があるときだけ
落ちた run のステップは judge が fix・同じステップのやり直し・stop を選ぶ。計画のスクリプトは、このスクリプトの置き場からの
絶対パスで呼ぶ（利用者のリポジトリにプラグインの中身が無くても動く）。

利用上限: claude -p（work・drive の worker・judge・pr）が利用上限（session limit・HTTP 429・
`api_error_status: 429`・「You've hit your limit … resets …」。lib/monitor.py の USAGE LIMIT の表と同じ文言）で
落ちたら、ステップの失敗とは区別する（on_fail・judge へ回さない）。ステップの結果に `"limit": true` と読めた解除時刻を残す。
- 環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK`（`KEY=VALUE` を空白区切り。例 `CLAUDE_CODE_USE_BEDROCK=1`）が
  あれば、それを環境に足した同じ claude -p で 1 度だけ起動し直す。報告に `認証: 切り替え（<変数名>）` を書く
- それでも上限なら、解除時刻 + 1 分まで（読めなければ計画の `"limit_retry_seconds"`、既定 900 秒）待って
  同じ呼び出しを起動し直す。待ちは LLM を使わない（time.sleep）。queue の枠は待ちの間も保つ
- 待ちの合計が計画の `"limit_wait_max"`（既定 10800 秒）を超えるなら `結果: 止まった`・`理由: 利用上限`

run のステップ:
- `"preset"`: 定型のコマンド。`sync-check`（宣言した同期とチェック）・`assess`（構造改善の要否）・
  `doc-lint`（追加した行の書き方のチェック）。`cmd` を書けばそちらを使う
- `cmd` の `{pr}` は Pull Request の番号に、`{pr_url}` は URL に置き換わる（drive のステップの `args` も同じ）。
  Pull Request は pr のステップで作ったもの、または計画の `"Pull Request"`（URL なら末尾の数字を番号として読む）
- `"rerun_failed": true`: 失敗したら落ちたテストだけ（`pytest --lf`）を走らせ直し、通れば成功として進む
- `"skip_to": "<ステップの id>"`: 終了コードが `skip_code`（既定 3。`refactor.py assess` の「飛ばしてよい」）なら
  そのステップへ進む
- 終了コード 10〜19（共通の契約の関門）は失敗にしない。ステップの結果に `gate` を残して `"gate_next"`（無ければ
  `next`）へ進み、最後まで進めば報告は `結果: 関門`。結果 JSON の `presentation_path` を報告の `提示物` に写す。
  `"presentation_to": "<パス>"` があれば提示物をそのパス（ステップの作業場所から）へ写し、そちらを載せる
- テストの成果物を作らない（計画の `no_reports` を `PYTEST_ADDOPTS` に足す）。作らせるときは `"reports": true`
- `cmd` の `{base}` は起点のブランチ（計画か .ndf/worktree.json の `base_branch`）に置き換わる。
  `{state_dir}` は計画の状態ディレクトリに置き換わる
- `"gate_as_ok": true`: 終了コード 10〜19 を関門として数えず、提示物だけを写して `next` へ進む

実行の条件（計画の `"実行の条件": {"cmd": "...", "skip_code": 3}`）: run と queue が作業ツリーを作る前に
元のリポジトリで打つ。0 なら流す。`skip_code` なら作業ツリーを作らず、報告を `結果: 完了`・
`理由: 実行の条件に当たらない（<summary>）` で書いて終える。ほかは `結果: 止まった`。`--from` で再開するときは打たない。

queue の置き換え: `{queue_prs}` は前のすべてのステージの Pull Request（空白区切り）、`{queue_pr:<計画名>}` は
名前（ファイル名の stem か、その末尾の `-<計画名>`）が一致する計画の Pull Request 1 本（前のステージに無ければ
同じディレクトリの計画の報告。無い・飛ばされたなら `0`）

ステップの遷移:
- `next` に `end` を書くと、そこでフェーズを完了として終える
- run: 終了コード 0 なら `next`（無ければ次のステップ）。10〜19 は関門として `gate_next` か `next`。
  それ以外の 0 以外なら `on_fail`（無ければ止まる）
- work: 終了後に `next`（無ければ次のステップ）
- judge: 答えの `decision` がステップの id ならそのステップへ、`next` なら次のステップへ、`stop` なら止まる、
  `gate` なら関門として止まる。`choices` を渡すとその中から選ばせる

途中の報告（`<state-dir>/progress.jsonl`。1 行 1 つの JSON。LLM は使わない）:
- `"kind": "step"`: ステップの切り替わりごとに 1 行（at・step・type・exit・seconds・cost・next・summary）
- `"kind": "alive"`: 最後の行から計画の `"report_interval"`（既定 600 秒）動きが無いとき（step・elapsed・
  worker の最後の報告。run のステップなら stderr の最後の行を last_output に）。長いステップ（work・run・drive）の
  待ちは区切って見るので、ステップの途中でも書く
- `"kind": "worker"`: work のステップの worker が区切りごとに追記する 1 行（プロンプトに書き方と置き場を渡す）
- `"kind": "slow"`: ステップの経過が想定を超え、一次の調査を流すたびに 1 行（下の「遅れの見張り」）
- `"kind": "gh-limit"`: judge が打ち直すステップの前の出力が GitHub の上限（`gh_parts.is_rate_limited`）のとき、
  打ち直す前に待った 1 回ごとに 1 行（step・waited・reset）。待つのは `gh api rate_limit` の graphql の reset まで。
  読めなければ同じステップの待ちごとに 60 秒から倍々
- `"kind": "attention"`: conductor の判断が要る出来事（reason が 止まった・関門・同じ失敗の繰り返し・
  judge のステップで stop が出そう・遅れ）。worker の行の語と繰り返し、ステップの結果からスクリプトで分ける。
  `queue` はこの行を標準出力の `{"tool": "supervise-queue", "event": "attention", ...}` で知らせる

作業ディレクトリ（`<state-dir>/work/`。起動時に作る）: work と judge のステップのプロンプトに渡す。worker は
作業ファイル（スクリプト・初期化の出力・プロンプト）をここに置く。計画ごとに別なので、並行する計画どうしで
同じ名前のファイルを上書きし合わない

遅れの見張り（run・work・drive のステップ。LLM は決まった手で解けないときだけ）:
- 想定: ステップの `"expected": <秒>` があればそれ。無ければ同じステップ（フェーズ, ステップの id）の直近 `window` 件の所要の
  中央値 × `factor`（下限 `floor`）、履歴が `min_samples` 件に満たなければ `default`。所要の履歴は成功と関門の
  ステップだけを `<git の共通ディレクトリ>/ndf/step-history.jsonl`（git でなければ `<state-dir>/`）へ積む
- 経過（利用上限の待ちを除く）が想定を超えると、ステップの `"probe"` で一次の調査を流す。`"output"`（run・drive の
  既定。stderr が伸びたか）/ `"worker"`（work の既定。worker の行かコミットが足されたか）/
  `{"cmd": "<コマンド>"}`（シェルを通さずに打ち、最後の行の JSON の `metrics.action` を読む。`{pr}` `{base}`
  `{branch}` `{state_dir}` を置き換える）/ `false`（調べずに判定へ）
- `action` が `wait` か `remedied` なら、`max_waits` 回まで想定の秒だけ待ち直す。`retry` / `fix` / `stop` はそのまま
  打つ。`judge`（読めない調査も）と待ち直しの上限では、Tool なしの claude -p が retry / fix / stop / wait を選ぶ
  （ステップごとに `max_llm` 回まで。超えたら見張りを止めてステップの timeout まで待つ。答えが読めなければ wait）
- retry は同じステップを打ち直す（`max_retry` を超えると stop）。fix は `on_fail` へ、stop は `結果: 止まった`・
  `理由: 遅れ: <理由>`。打ち切ったステップは子のプロセスグループごと止め、終了コードは 125
- 設定は `--slow K=V` → 計画の `"slow"` → `.ndf/supervise.json` の `"slow"` → 既定の順に先に効く。鍵は
  `enabled`（true）・`window`（10）・`min_samples`（3）・`factor`（3.0）・`floor`（300）・`default`（900）・
  `max_waits`（3）・`max_llm`（2）・`max_retry`（1）・`probe_timeout`（120）・`judge_timeout`（300）・`history`。
  知らない鍵・形の違う値はステップを始める前に `結果: 止まった`・`理由: slow の設定が読めない（<鍵>）`
"""
from __future__ import annotations

import re

# queue が --then のプランを流す前に、先行のプランの Pull Request の番号へ置き換える印
QUEUE_PRS = "{queue_prs}"


def expand_parts(steps: list[dict]) -> list[dict]:
    """work のステップの `parts` を、パートごとに別の claude -p のステップへ展開する。

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


def pr_number(value) -> str:
    """計画の Pull Request（番号か URL）から番号を返す。URL なら末尾の数字を読む。読めなければ空。"""
    m = re.search(r"(\d+)/*$", str(value or "").strip())
    return m.group(1) if m else ""


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
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": "main",
         "title": "変更の要約（#0）", "summary": "何を変えたかの 1〜2 文", "docs": ["issues/issue-0-design.md"],
         "next": "end"},
    ],
}
