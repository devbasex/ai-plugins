"""configure: 言語を検出し、`.serena/project.yml` を書き、1 言語ずつ起動を検証する（決定 7）。"""
import difflib
import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path

from . import detect, table
from . import project_yml as py

VERIFY_TIMEOUT = 120
SERENA_CMD = "uvx --from serena-agent==1.7.0 serena"
SERENA_GITIGNORE_LINES = ["/serena_config.yml", "/logs", "/language_servers", "/cache"]
EXCLUDED_KEY = "mcp_serena_excluded"


class Terminated(Exception):
    """SIGTERM / SIGINT を受けた。finally で最後の値を書くために例外へ変える。"""


def _serena_env() -> dict:
    # 起動定義と同じ SERENA_HOME にする。既定の ~/.serena で走らせると利用者の全体の設定を書き換え得る
    return dict(os.environ, SERENA_HOME=".serena")


def _run(cmd: list, root: Path, timeout: float):
    proc = subprocess.Popen(cmd, cwd=root, env=_serena_env(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return proc.wait(timeout=timeout)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        raise


def _latest_log(root: Path, since: float):
    logs = root / ".serena/logs/health-checks"
    if not logs.is_dir():
        return None
    fresh = [p for p in logs.iterdir() if p.is_file() and p.stat().st_mtime >= since]
    return str(max(fresh, key=lambda p: p.stat().st_mtime)) if fresh else None


def health_check(root: Path, serena_cmd: list, timeout: float) -> dict:
    """1 回の health-check。{"ok": bool, "reason": str, "log": str|None}。"""
    import time
    started = time.time() - 1
    try:
        code = _run([*serena_cmd, "project", "health-check", str(root)], root, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout", "log": _latest_log(root, started)}
    except OSError:
        return {"ok": False, "reason": "serena_unavailable", "log": None}
    if code == 0:
        return {"ok": True, "reason": "", "log": None}
    return {"ok": False, "reason": f"health_check_exit_{code}", "log": _latest_log(root, started)}


def _missing_lines(path: Path, wanted: list) -> list:
    have = set(path.read_text().splitlines()) if path.exists() else set()
    return [line for line in wanted if line not in have]


def _missing_serena_gitignore(root: Path) -> list:
    return _missing_lines(root / ".serena/.gitignore", SERENA_GITIGNORE_LINES)


def _append_lines(path: Path, lines: list) -> None:
    text = path.read_text() if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "".join(f"{line}\n" for line in lines))


def _with_ignores(text: str, root: Path) -> str:
    """`.serena/**` を常に、`.worktrees/**` は `.worktrees/` があるときだけ ignored_paths へ足す。

    `.serena/` を外さないと、Serena が言語サーバのキャッシュ（`.serena/language_servers/` の
    `.d.ts` など）を解析対象に選び、検証が失敗する（`.serena/.gitignore` の無い新しいリポジトリ）。
    """
    ignored = py.read_list(text, "ignored_paths") or []
    wanted = [".serena/**"] + ([".worktrees/**"] if (root / ".worktrees").is_dir() else [])
    added = [p for p in wanted if p not in ignored]
    return py.write_list(text, "ignored_paths", ignored + added) if added else text


def _final_text(text: str, root: Path, verified: list, excluded: list) -> str:
    text = _with_ignores(py.write_list(text, "language_servers", verified), root)
    return py.write_list(text, EXCLUDED_KEY, excluded)


def _signals_to_exception():
    def handler(signum, frame):
        raise Terminated(signum)
    previous = {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGINT)}
    return previous


def _added_ignored_paths(before_text: str, after_text: str) -> list:
    before = py.read_list(before_text, "ignored_paths") or []
    return [p for p in py.read_list(after_text, "ignored_paths") or [] if p not in before]


def _not_selected_entries(not_selected: list) -> list:
    return [f"{lang} not_selected" for lang in not_selected]


def _plan_dry_run(result: dict, original, root: Path, candidates: list, not_selected: list):
    planned_excluded = _not_selected_entries(not_selected)
    planned = _final_text(original or "", root, candidates, planned_excluded)
    result["diff"] = "".join(difflib.unified_diff(
        (original or "").splitlines(True), planned.splitlines(True), "project.yml", "project.yml"))
    result["written"].update(
        language_servers=candidates, excluded=planned_excluded, created=original is None,
        ignored_paths_added=_added_ignored_paths(original or "", planned),
        serena_gitignore_added=_missing_serena_gitignore(root))
    result["dry_run"] = True
    return result, 0 if candidates else 1


def _write_gitignores(root: Path, result: dict, gitignore: bool, serena_gitignore: bool) -> None:
    if gitignore:
        path = root / ".gitignore"
        missing = _missing_lines(path, [".serena/project.yml"])
        if missing:
            _append_lines(path, missing)
            result["written"]["gitignore"] = True
    # 渡さないときは「足せば足す行」、渡したときは足した行を載せる
    missing = _missing_serena_gitignore(root)
    if serena_gitignore and missing:
        _append_lines(root / ".serena/.gitignore", missing)
    result["written"]["serena_gitignore_added"] = missing


def _precheck_yml(yml: Path, local: Path):
    """(元の project.yml の本文か None, 終了コード 3 の理由か None) を返す。"""
    try:
        if local.exists() and py.read_list(local.read_text(), "language_servers") is not None:
            return None, "project.local.yml が language_servers を持つため書きません"
        original = yml.read_text() if yml.exists() else None
        if original is not None:
            py.read_list(original, "language_servers")
            py.read_list(original, "ignored_paths")
            py.read_list(original, EXCLUDED_KEY)
    except py.UnsupportedShape as exc:
        return None, f"project.yml の形を読めません: {exc}"
    return original, None


def _create_project(cmd: list, root: Path, candidates: list, timeout: float):
    """serena project create で project.yml を作り、その本文を返す。作れなければ None。"""
    yml = root / ".serena/project.yml"
    ls_args = [a for lang in candidates for a in ("--ls", lang)]
    try:
        _run([*cmd, "project", "create", *ls_args, "--name", root.name, str(root)], root, timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return yml.read_text() if yml.exists() else None


def _select(detected: list, only):
    """(検証する言語, 検出したが選ばなかった言語) を返す。"""
    found = [d["language"] for d in detected]
    candidates = list(only) if only else found
    return candidates, [lang for lang in found if lang not in candidates]


def _prepare(result: dict, root: Path, candidates: list, not_selected: list, dry_run, serena_cmd):
    """検証の前の段階。(途中で終える (結果, 終了コード) か None, 元の本文, コマンド, 待ち時間) を返す。"""
    yml = root / ".serena/project.yml"
    local = root / ".serena/project.local.yml"
    # 終了コード 3 の検査は、書き換えの try / finally に入る前に済ませる
    original, error = _precheck_yml(yml, local)
    if error:
        return ({**result, "error": error}, 3), None, None, None

    cmd = shlex.split(serena_cmd)
    if dry_run:
        return _plan_dry_run(result, original, root, candidates, not_selected), None, None, None
    if not shutil.which(cmd[0]):
        return ({**result, "error": f"{cmd[0]} が見つかりません"}, 2), None, None, None

    timeout = float(os.environ.get("SERENA_LSP_VERIFY_TIMEOUT", VERIFY_TIMEOUT))
    if original is None:
        original = _create_project(cmd, root, candidates, timeout)
        if original is None:
            return ({**result, "error": "serena project create が project.yml を作りませんでした"}, 2), \
                None, None, None
        result["written"]["created"] = True
    return None, original, cmd, timeout


def _verify_each(root: Path, original: str, candidates: list, not_selected: list, cmd: list, timeout: float):
    """1 言語ずつ検証し、中断されても finally で確定の値を書く。(verified, failed, excluded, final) を返す。"""
    yml = root / ".serena/project.yml"
    verified, failed = [], []
    base = _with_ignores(original, root)
    previous = _signals_to_exception()
    try:
        for lang in candidates:
            yml.write_text(py.write_list(base, "language_servers", [lang]))
            outcome = health_check(root, cmd, timeout)
            if outcome["ok"]:
                verified.append(lang)
            else:
                failed.append({"language": lang, "reason": outcome["reason"], "log": outcome["log"]})
    finally:
        excluded = [f"{f['language']} {f['reason']}" for f in failed] + \
            _not_selected_entries(not_selected)
        final = _final_text(original, root, verified, excluded)
        yml.write_text(final)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return verified, failed, excluded, final


def configure(root: Path, dry_run=False, gitignore=False, serena_gitignore=False, only=None,
              serena_cmd=SERENA_CMD):
    """(結果の辞書, 終了コード) を返す。"""
    root = Path(root)
    data = table.load()
    result = {"root": str(root), "detected": [], "skipped": [], "verified": [], "failed": [],
              "written": {"created": False, "language_servers": [], "excluded": [],
                          "ignored_paths_added": [], "gitignore": False, "serena_gitignore_added": []}}
    try:
        detected, skipped = detect.detect(root, data)
    except detect.GitUnavailable as exc:
        return {**result, "error": f"git を使えません: {exc}"}, 2
    result["detected"], result["skipped"] = detected, skipped
    candidates, not_selected = _select(detected, only)

    stop, original, cmd, timeout = _prepare(result, root, candidates, not_selected, dry_run, serena_cmd)
    if stop:
        return stop

    verified, failed, excluded, final = _verify_each(root, original, candidates, not_selected, cmd, timeout)
    result["verified"], result["failed"] = verified, failed
    result["written"].update(
        language_servers=verified, excluded=excluded,
        ignored_paths_added=_added_ignored_paths(original, final))

    _write_gitignores(root, result, gitignore, serena_gitignore)
    return result, 0 if verified else 1
