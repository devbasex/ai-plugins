"""追跡しているファイルの拡張子を数え、しきい値で言語を選ぶ。"""
import subprocess

from . import table


class GitUnavailable(Exception):
    """git が無い、または根が git のリポジトリでない。"""


def list_files(root) -> list:
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnavailable(str(exc)) from exc
    if proc.returncode != 0:
        raise GitUnavailable(proc.stderr.decode(errors="replace").strip())
    return [p for p in proc.stdout.decode(errors="surrogateescape").split("\0") if p]


def count(paths, ext_map: dict) -> dict:
    counts = {}
    for path in paths:
        lang = ext_map.get(table.suffix(path))
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def choose(counts: dict, threshold: dict):
    """(採る言語, 採らない言語) を返す。割合の分母は対応表の拡張子を持つファイルの数。"""
    total = sum(counts.values())
    detected, skipped = [], []
    for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        share = n / total if total else 0.0
        entry = {"language": lang, "files": n, "share": round(share, 3)}
        if n >= threshold["min_files"] and share >= threshold["min_share"]:
            detected.append(entry)
        else:
            skipped.append({**entry, "reason": "below_threshold"})
    return detected, skipped


def detect(root, data: dict):
    counts = count(list_files(root), table.extension_map(data))
    return choose(counts, data["threshold"])
