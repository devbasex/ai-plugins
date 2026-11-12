"""exit_codes.py: NDF のスクリプトが使う終了コードの表（#2988）。

終了コードの定数・承認ゲートと判断待ちの範囲・見出しを持つ。値を書くのはこのファイルだけで、結果を出す層
（`step_result.py`）と依存の確認（`deps.py`）は同じ名前で公開する。標準ライブラリも NDF のモジュールも読まない
（ラッパーのバージョンディレクトリへ写しても依存を増やさず、どのモジュールからも輪を作らずに読める）。
"""

EXIT_OK = 0
EXIT_VIOLATION = 1  # チェックで違反あり・手順が失敗した
EXIT_UNREADABLE = 2  # 読めない・呼び出しの誤り（「一致」「0 件」と読ませない）
EXIT_PRECONDITION = 3  # 前提が無い（宣言・認証・対象のファイル）、または各スクリプトが定めた正常な否定の結果
# （立たない・変更なし・飛ばしてよい）。読めないときは 2 で返し、3 と混ぜない
# PR がベースブランチと衝突している（mergeStateStatus が DIRTY か、gh pr merge がマージのコミットを作れずに拒んだ）。
# merge-when-green が返し、プランの merge はベースブランチの取り込みへ戻る（#1917）
EXIT_BASE_CONFLICT = 4
# CD の結果が失敗。本番チャネルへ入ったが反映に失敗し、デプロイの工程は完了でない（`release-steps.py record`。#2623）
EXIT_CD_FAILED = 5
# 欠けが残る。手順は終わったが、`gaps` に書いた材料・記録・目標が欠けた（`stopped` だけ。#2227）
EXIT_INCOMPLETE = 6
EXIT_GATE = 10  # 10〜19: 承認ゲート（人の同意が要る）
EXIT_PAUSE = 20  # 20〜29: LLM の判断待ち
# 依存の欠け（`deps.require` が外部パッケージを用意できずに止まった。何も判定していない。sysexits.h の EX_UNAVAILABLE）。
# 共通の契約の 0〜3・10〜29 と重ねない（#1654）
EXIT_DEPS_MISSING = 69
# CI の基盤待ち（Runner が付かずに取り消されたジョブが再実行でも解けない。sysexits.h の EX_TEMPFAIL。
# 中身の失敗ではなく、後で打ち直せば通りうる。merge-when-green と release が返す。#1645）
EXIT_INFRA_WAIT = 75

GATE_CODES = range(10, 20)
PAUSE_CODES = range(20, 30)
# `stopped` の結果が返してよい終了コード（`step_result.code_matches`）
STOPPED_CODES = (
    EXIT_VIOLATION,
    EXIT_UNREADABLE,
    EXIT_PRECONDITION,
    EXIT_BASE_CONFLICT,
    EXIT_CD_FAILED,
    EXIT_INCOMPLETE,
    EXIT_DEPS_MISSING,
    EXIT_INFRA_WAIT,
)

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
