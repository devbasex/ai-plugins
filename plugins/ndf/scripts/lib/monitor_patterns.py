"""監視の照合の表（#1142 の C3 で `monitor.py` から分けた）。

ログ（err.log・stdout.log）の行を、利用上限・致命・警告・無害・CLI の上限・codex の終わりの印の表と照らす。
標準ライブラリだけを import する。`supervise.py` は利用上限の表（`USAGE_LIMIT_FATAL`）をここから読む。
"""
from __future__ import annotations

import re


# **利用上限** の文言 (kill 対象。理由は `usage_limit`)。起動し直しても解けないため、
# 他の致命と区別して結末に理由を添える（#729 の決定 4）。err.log は全担当で見る。
# 照合は致命の表より **先** に行う。上限で落ちた後に別の致命が続く形が普通で、上限のほうが原因。
USAGE_LIMIT_FATAL = [
    # kiro の実物（#619）
    re.compile(r"Monthly request limit reached"),
    # claude の `--output-format json` の結果行（#647）。`:` の前後の空白は問わない
    re.compile(r'"api_error_status"\s*:\s*429'),
    # quota / rate limit （`m.start()` をキーワード位置に合わせるため `^.*` を付けない。
    # `_match_is_quoted()` が backtick / 「」 引用を判定するために match 開始位置を使うため）
    re.compile(r"\b(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE),
    # HTTP 429 の状態行
    re.compile(r"^HTTP/\d\S* 429 ", re.MULTILINE),
    # codex と claude の実物（#811）。**行頭で始まる形だけを読む。** 担当は作業中の
    # コマンドの出力（差分・ファイルの中身）も err.log へ書くため、文書やテストを
    # 読み上げた行に一致させない。codex は誤りの行に `ERROR: ` を付ける。
    # claude は `You've hit your ` の後に期間や種類（週・セッション・支出）を差し込む。
    re.compile(r"^(?:ERROR:\s*)?You['’]ve hit your (?:[\w'’ ]+ )?(?:limit|budget)\b",
               re.MULTILINE),
    # codex の再試行の上限。**最後の状態が 429 のときだけ利用上限と読む**（503 などの
    # 一時的な誤りは起動し直せば解けうる）。
    re.compile(r"^(?:ERROR:\s*)?exceeded retry limit, last status: 429\b", re.MULTILINE),
]

# err.log の行頭に近い形で出る **明確な致命** パターン (kill 対象。理由は `early_error`)。
# auth / sandbox / HTTP 401-403 など、プロセスが続行しても result を生成できないと
# 判明しているケースだけを入れる。利用上限は `USAGE_LIMIT_FATAL` の側。
EARLY_ERROR_FATAL = [
    # HTTP エラーステータス行 (`HTTP/1.1 401 Unauthorized` 等)
    re.compile(r"^HTTP/\d\S* (?:401|403) ", re.MULTILINE),
    # 認証 / 権限系（行頭限定）
    re.compile(r"^(?:Authentication failed|Permission denied)", re.MULTILINE),
    # API key 系
    re.compile(r"\bAPI key (?:not found|missing|invalid)\b", re.IGNORECASE),
    # codex 固有: sandbox エラー
    re.compile(r"\bsandbox error\b", re.IGNORECASE),
]

# **CLI 自身の上限** で結果を書かずに終わったことを示す文言（理由は `cli_timeout`）。
# **終了した後、結果ファイルが無いときだけ** 照合する。生きている間に見ると途中の警告を
# 致命と読み、結果ファイルがあれば上限に当たっても書き終えているので使える（#729 の決定 5）。
CLI_TIMEOUT_AFTER_EXIT = [
    # agy の `--print-timeout` の打ち切り（#598 / #537 の実物）
    re.compile(r"print timeout after \S+ with turn in progress"),
]

# **警告の見た目で出る致命** パターン。`EARLY_ERROR_FATAL` と違い、行頭の
# `warning:` を benign とする規則を適用しない（適用すると自分自身が消える）。
# 引用符・バックティック・markdown 引用による誤検知の除外だけを効かせる。
EARLY_ERROR_FATAL_WARNING_SHAPED = [
    # kiro 固有: ツール承認漏れ。**プロセスは終了コード 0 で正常終了する**ため、
    # 終了コードでは検知できない。`--trust-all-tools` を渡していれば本来出ないが、
    # フラグが効かない環境を検知するために残す。
    re.compile(r"is rejected because it matches one or more rules on the denied list"),
    # kiro 固有: `--trust-tools` にツール名の綴り違いを渡すと、警告だけ出して
    # 「何も信頼しない状態」で正常終了する。何も起きていない成功と区別できない。
    re.compile(r"WARNING: --trust-tools arg for custom tool"),
    # claude 固有: root 実行で bypassPermissions が拒否される。
    re.compile(r"--dangerously-skip-permissions cannot be used with root"),
]

# 行頭の生 `Error:` / `Traceback` 系は **kill しない警告のみ** に降格。
# - codex がレビュー対象 diff の test コード片を echo して `Traceback` が混入するケース
# など、続行可能な誤検知が頻発するため。プロセスは sentinel / result.json / timeout
# で別途判定する。
EARLY_ERROR_WARN = [
    re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE),
]

# 文脈に含まれていたら benign（doc 引用 / コードレビューコメント等）と見なし誤検知扱い。
# FATAL / WARN 双方のスキャンに適用される。
EARLY_ERROR_BENIGN = [
    # diff のコンテキスト行 (` `, `+`, `-` で始まり、その後 markdown 表記が来る)
    re.compile(r"^[ +-].*[\|`]", re.MULTILINE),
    # markdown のリスト / 引用
    re.compile(r"^\s*[-*>]\s", re.MULTILINE),
    # markdown の表セル行 (`| ... | ...` 形式)。SKILL.md / docs/*.md が
    # 検知パターンを表で列挙しており、それを codex が echo すると誤検知する。
    re.compile(r"^\|", re.MULTILINE),
    # grep / ripgrep 形式のソース引用行 (`path/to/file.ext:42:    <code>`)。
    # codex がレビュー対象のテストコード片を grep 形式で echo すると、
    # その文字列リテラル内の FATAL キーワードを誤検知する (PR #23 round 2 で発生)。
    re.compile(r"^\S+\.[A-Za-z0-9]+:\d+:", re.MULTILINE),
    # warning は致命ではない
    re.compile(r"^warning: ", re.IGNORECASE | re.MULTILINE),
]

# `EARLY_ERROR_FATAL_WARNING_SHAPED` に適用する benign 規則。
# 「行頭が warning:」だけを外し、ドキュメント引用の除外は維持する。
EARLY_ERROR_BENIGN_KEEP_WARNINGS = [
    p for p in EARLY_ERROR_BENIGN
    if p.pattern != r"^warning: "
]


def _match_is_quoted(line: str, match_start: int, match_end: int) -> bool:
    """マッチ位置がドキュメント引用 / コード文字列リテラルに囲まれているか判定。

    - backtick: マッチ開始までの `` ` `` カウントが奇数 かつ マッチ終了以降に `` ` `` がある
    - 日本語クォート: マッチ開始までに直近の `「` が `」` よりも後 かつ マッチ終了以降に `」` がある
    - ダブル/シングルクォート文字列リテラル: マッチ開始までの `"` (or `'`) カウントが
      奇数 かつ マッチ終了以降に同じクォートがある (= リテラルの内側)

    Why: SKILL.md / docs/*.md 内で FATAL キーワードを `「quota exceeded」` のように
    引用列挙しており、codex がそれを echo する。さらに tests/*.py の
    `"quota exceeded: please upgrade"` のような **テスト用文字列リテラル** を
    codex がレビュー中に echo するケース (PR #23 round 2 で実際に発生) もある。
    いずれも引用形であり本物のエラーではないため benign 扱いする。
    """
    before = line[:match_start]
    after = line[match_end:]
    if before.count("`") % 2 == 1 and "`" in after:
        return True
    if before.rfind("「") > before.rfind("」") and "」" in after:
        return True
    # コード文字列リテラル (ダブル / シングルクォート)。
    # エスケープされたクォート (`\"` / `\'`) はリテラルを開閉しないため
    # パリティ計算から除外する。これを数えると、文字列内にエスケープ
    # クォートを含む行で「引用内/外」の判定がずれ、本物のエラー行を
    # 誤って benign 扱い (= FATAL 見逃し) する恐れがある。
    for q in ('"', "'"):
        if _unescaped_count(before, q) % 2 == 1 and q in after:
            return True
    return False


def _unescaped_count(text: str, quote: str) -> int:
    """`quote` のうちバックスラッシュでエスケープされていない出現数を数える。

    直前の連続バックスラッシュ数が奇数なら、そのクォートはエスケープ
    されている (リテラルを開閉しない) ものとして除外する。
    """
    count = 0
    for i, ch in enumerate(text):
        if ch != quote:
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and text[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            count += 1
    return count

CODEX_SENTINEL = re.compile(r"^tokens used$", re.MULTILINE)

# ANSI エスケープ（CSI / OSC / 単独の 2 文字シーケンス）。
# kiro-cli は `NO_COLOR=1` / `TERM=dumb` / 非 TTY のいずれでも色コードを出し続けるため、
# **パターン照合の前に必ず除去する**。除去しないと行頭アンカー (`^Error:`) が
# 色コードに阻まれて一致せず、致命エラーを取りこぼす。
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


# claude の `--output-format json` 出力に現れる致命パターン。
# 標準出力側に出るため err.log ではなく stdout.log を見る。
CLAUDE_STDOUT_FATAL = [
    # 承認失敗。空配列 `[]` は正常なので「非空」だけを致命とする。
    re.compile(r'"permission_denials"\s*:\s*\[\s*\{'),
    re.compile(r'"is_error"\s*:\s*true'),
]

# claude の stdout.log に出る利用上限（理由は `usage_limit`）。err.log と stdout.log の
# どちらに出るか未確認のため両方を見る（#729 の決定 6）。JSON 向けの照合で除外を掛けない。
CLAUDE_STDOUT_USAGE_LIMIT = [
    re.compile(r'"api_error_status"\s*:\s*429'),
]
