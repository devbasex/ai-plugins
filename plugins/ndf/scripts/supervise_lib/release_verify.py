"""リリースのプランの確かめと判断のステップ（release_templates から分けた）: リリース後テスト（/ndf:release-verification）、
リリース記録の書き込みが落ちたときの判断、落ちたステップの判断と修正（judge・fix）。まとめのプラン（sprint・sprint_lane_close）と
リリースのプラン（release_templates・delivery_templates）が使う。"""

from __future__ import annotations

from supervise_lib.paths import VERIFY_PY


def judge_record_step() -> dict:
    """record が落ちたときの判断。選べるのは record のやり直しか停止だけ（fix はデプロイへ戻るため置かない）。"""
    return {
        "id": "judge-record",
        "type": "judge",
        "inputs": ["record"],
        "question": "リリース記録の書き込み（record）が落ちた。リリースは済んでいる。GitHub の揺れなら record をもう一度、"
        "権限の不足・本番のリリースの PR が無いなら止める（stop）",
        "choices": ["record", "stop"],
    }


# 起動指示に足す、利用者の設定ディレクトリで導入しないことの要点。正本は release-verification の Skill の節（#2401 の決定 7）
NO_USER_INSTALL = (
    "導入の確かめは verify-install だけで行い、利用者の設定ディレクトリ（CLAUDE_CONFIG_DIR か ~/.claude、CODEX_HOME か ~/.codex）で"
    "導入の操作（claude plugin install / update / marketplace add / update、codex plugin add / marketplace add）を打たない。"
    "隔離のつもりで環境変数を付けて手で打つことも含む。利用者の設定ディレクトリへの導入が要る条件は、導入せずに記録の表のその行を"
    "「保留（理由: 利用者の設定ディレクトリへの導入が要る）」とし、作業の報告を「判断が要る」で返す"
    "（/ndf:release-verification の「利用者の設定ディレクトリで導入しない」）。"
)


def release_verify_steps(sprint: str, refs: str, record_pr: str, after: str = "retro") -> list[dict]:
    """控えを取る（config-save）→ リリース後テスト → 控えと比べる（config-check）→ `after`。リリース後テストが失敗したら
    config-check-on-fail で比べ、変わっていなくても止まる。3 つの run は on_fail を持たず 0 以外で止まる（#2401 の決定 1・2）。"""
    run = {"type": "run", "timeout": 120}  # 控えは {state_dir}/user-config-before.json（名前はスクリプトが決める）
    check = {**run, "cmd": f"{VERIFY_PY} user-config-check --dir {{state_dir}}"}
    return [
        {"id": "config-save", **run, "cmd": f"{VERIFY_PY} user-config-save --dir {{state_dir}}", "next": "release-verify"},
        release_verify_step(sprint, refs, record_pr),
        {"id": "config-check", **check, "next": after},
        {"id": "config-check-on-fail", **check, "on_exit": {"0": "stop"}},
    ]


def release_verify_step(sprint: str, refs: str, record_pr: str) -> dict:
    """リリース後テスト（/ndf:release-verification）。記録をリリース記録の PR へ置き、まとめの close がそれを読む（#1683）。"""
    return {
        "id": "release-verify",
        "type": "work",
        "full": True,
        "kind": "リリース後テスト",
        "stage": "リリース後テスト",
        "timeout": 3600,
        "prompt": f"/ndf:release-verification スプリント {sprint}（{refs}）。リリース記録は Pull Request #{record_pr} にある。"
        "受け入れ条件ごとに確かめ、スプリントの Pull Request の「手動確認」でリリース後テストへ回した項目と、"
        "マージ前に未検証として残した項目も行として載せて、リリース後テストの記録をその Pull Request へコメントで書く。"
        "課題は閉じない（次の close が閉じる）。"
        "進捗記録（progress-record.sh・projects-sync.sh）は打たない（このステップが ok で終わった後にプランが打つ）。"
        "番号が 0 なら本番へ出ていないため、何もせずに終える。" + NO_USER_INSTALL,
        "on_fail": "config-check-on-fail",
        "next": "config-check",
    }


def judge_fix_steps(run_ids: list[str], after_notes: str) -> list[dict]:
    """落ちたステップの判断（judge）と修正（fix）のステップ。"""
    return [
        {
            "id": "judge",
            "type": "judge",
            "inputs": run_ids,
            "question": "落ちたステップを直す（fix）か、同じステップをもう一度（retry）か、止める（stop）か。retry なら decision に"
            "落ちたステップの id を返す",
            "choices": ["fix", *run_ids, "stop"],
        },
        {
            "id": "fix",
            "type": "work",
            "kind": "修正",
            "inputs": run_ids,
            "prompt": "落ちたステップの出力を読み、原因を直してコミットする（push しない）。直したら次は落ちたステップからやり直す。",
            "back_to_failed": True,  # 落ちたのが after_notes より前（bump など）ならそこへ戻る（#1315）
            "next": after_notes,
        },
    ]
