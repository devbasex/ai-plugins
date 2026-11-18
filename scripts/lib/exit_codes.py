"""exit_codes.py: NDF のスクリプトが使う終了コードの見出し（#2988）。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。契約の構造チェック（`contracts_lib/core_checks.py`
の検査 `exit-meaning`）が、契約の `exit` の値の見出しを引くのに使う。値の表だけを持ち、標準ライブラリも読まない。
"""

EXIT_OK = 0
EXIT_VIOLATION = 1  # チェックで違反あり・手順が失敗した
EXIT_UNREADABLE = 2  # 読めない・呼び出しの誤り（「一致」「0 件」と読ませない）
EXIT_PRECONDITION = 3  # 前提が無い（宣言・認証・対象のファイル）、または各スクリプトが定めた正常な否定の結果
EXIT_BASE_CONFLICT = 4  # PR がベースブランチと衝突している
EXIT_CD_FAILED = 5  # CD の結果が失敗
EXIT_INCOMPLETE = 6  # 欠けが残る（`stopped` だけ）
GATE_CODES = range(10, 20)  # 承認ゲート（人の同意が要る）
PAUSE_CODES = range(20, 30)  # LLM の判断待ち
EXIT_DEPS_MISSING = 69  # 依存の欠け（sysexits.h の EX_UNAVAILABLE）
EXIT_INFRA_WAIT = 75  # CI の基盤待ち（sysexits.h の EX_TEMPFAIL）

# 終了コードの見出し。契約の一覧の `exit` の値はこの見出しで始める（#2337 の決定 4・検査 `exit-meaning`）
EXIT_LABELS = {
    EXIT_OK: "成功",
    EXIT_VIOLATION: "失敗",
    EXIT_UNREADABLE: "読めない・呼び出しの誤り",
    EXIT_PRECONDITION: "前提が無い",
    EXIT_BASE_CONFLICT: "ベースブランチと衝突",
    EXIT_CD_FAILED: "CD の結果が失敗",
    EXIT_INCOMPLETE: "欠けが残る",
    **{c: "承認ゲート" for c in GATE_CODES},
    **{c: "判断待ち" for c in PAUSE_CODES},
    EXIT_DEPS_MISSING: "依存の欠け",
    EXIT_INFRA_WAIT: "基盤待ち",
}


def label_of(code: int) -> str:
    """終了コードの見出し（表に無ければ空）。"""
    return EXIT_LABELS.get(code, "")
