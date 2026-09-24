#!/usr/bin/env python3
"""テスト用の偽の serena。受けた引数・SERENA_HOME・cwd を記録し、言語ごとに決めた結果を返す。

FAKE_SERENA_LOG: 記録の JSONL
FAKE_SERENA_RESULT: "bash=1,php=hang" のような言語ごとの結果（既定は 0）
"""
import json
import os
import sys
import time
from pathlib import Path

TEMPLATE = Path(__file__).with_name("serena-1.7.0-project.yml")


def log(entry):
    with open(os.environ["FAKE_SERENA_LOG"], "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def main():
    args = sys.argv[1:]
    log({"args": args, "serena_home": os.environ.get("SERENA_HOME"), "cwd": os.getcwd()})
    if args[:2] == ["project", "create"]:
        langs = [args[i + 1] for i, a in enumerate(args) if a == "--ls"]
        root = Path(args[-1])
        text = TEMPLATE.read_text().replace("language_servers:\n- python\n",
                                            "language_servers:\n" + "".join(f"- {l}\n" for l in langs))
        (root / ".serena").mkdir(parents=True, exist_ok=True)
        (root / ".serena/project.yml").write_text(text)
        return 0
    if args[:2] == ["project", "health-check"]:
        root = Path(args[2])
        serena = root / ".serena"
        gi = serena / ".gitignore"
        if not gi.exists():
            gi.write_text("/cache\n/project.local.yml\n")
        text = (serena / "project.yml").read_text()
        langs = [l[2:].strip() for l in text.split("language_servers:", 1)[1].splitlines()[1:]
                 if l.startswith("- ")]
        results = dict(kv.split("=") for kv in os.environ.get("FAKE_SERENA_RESULT", "").split(",") if kv)
        logs = serena / "logs/health-checks"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"health_check_{time.time_ns()}.log").write_text(",".join(langs))
        log({"checked": langs})
        outcome = results.get(langs[0] if langs else "", "0")
        if outcome == "hang":
            time.sleep(60)
            return 0
        return int(outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
