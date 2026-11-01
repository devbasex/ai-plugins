"""queue の差し込み（`{queue_pr:<名>}`・`{queue_state:<名>}`）を、名前の一致する計画の結果で埋める（#1488・#2079）。
`{queue_record}` は、計画の `課題` のリリース記録を置いた PR を GitHub から探して埋める（#2230。`sprint_close_lib/record_find.py`）。

`queue.py` から切り出した。`queue` は同じ名前で読み込み直して公開する。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from supervise_lib.paths import report_result, state_dir_of
from supervise_lib.plan import pr_number

FINISHED = ("完了", "関門")  # 打ち直しでも流し直さない結果（止まったは次の実行の回で頭から流す）


QUEUE_PR = re.compile(r"\{queue_pr:([A-Za-z0-9._-]+)\}")  # 名前の一致する計画の Pull Request 1 本
QUEUE_RECORD = "{queue_record}"  # 計画の `課題` のリリース記録を置いた PR（#2230）
QUEUE_STATE = re.compile(r"\{queue_state(\??):([A-Za-z0-9._-]+)\}")  # 名前の一致する計画の状態ディレクトリ（#1488）。`?` 付きは無ければ空


def plan_pr(item: dict) -> str:
    """完了か関門で終えた計画の報告の Pull Request の番号（設計のプランは承認ゲート 1 の関門で終わる。#1426 の決定 4）。無ければ空。"""
    rep = Path(item.get("report") or "")
    if item.get("result") not in FINISHED or not rep.is_file():
        return ""
    m = re.search(r"^- Pull Request: (.*)$", rep.read_text(), re.M)
    return pr_number(m.group(1).strip()) if m else ""


def named(plan: str, name: str) -> bool:
    stem = Path(plan).stem
    return stem == name or stem.endswith(f"-{name}")


def results_named(name: str, items: list[dict], plan: str) -> list[dict]:
    """名前の一致する計画の結果。`items` に無ければ、`plan` と同じディレクトリの計画の報告を読む（関門の後に単独で流すとき）。"""
    hits = [i for i in items if named(i.get("plan", ""), name)]
    if hits:
        return hits
    return [
        {
            "plan": str(f),
            "result": report_result((state_dir_of(str(f)) / "report.md").read_text()),
            "report": str(state_dir_of(str(f)) / "report.md"),
        }
        for f in sorted(Path(plan).parent.glob("*.json"))
        if str(f) != plan and named(str(f), name) and (state_dir_of(str(f)) / "report.md").is_file()
    ]


def _fill_prs(text: str, names: list[str], items: list[dict], plan: str) -> tuple[str, list[str]]:
    """{queue_pr:<名>} を完了か関門で終えた結果の PR の番号（無ければ 0）で置き換える。そうした結果が無い名前は欠けの文で返す。
    設計のプランは承認ゲート 1 の関門で終わるため、関門も受ける（plan_pr と同じ FINISHED）。"""
    missing = []
    for name in names:
        done = [i for i in results_named(name, items, plan) if i.get("result") in FINISHED]
        if not done:
            missing.append(f"{{queue_pr:{name}}}（名前の一致する計画の完了か関門で終えた報告が無い）")
            continue
        prs = [n for n in (plan_pr(i) for i in done) if n]
        text = text.replace(f"{{queue_pr:{name}}}", prs[-1] if prs else "0")
    return text, missing


def _fill_states(text: str, states: list[tuple[str, str]], items: list[dict], plan: str) -> tuple[str, list[str]]:
    """{queue_state:<名>} を状態ディレクトリの絶対パスで置き換える。`?` 付きは無ければ空文字、無印は欠けの文で返す。"""
    missing = []
    for opt, name in states:
        found = [state_dir_of(str(Path(i["plan"]).absolute())) for i in results_named(name, items, plan) if i.get("plan")]
        dirs = [d for d in found if d.is_dir()]
        if not dirs and not opt:
            missing.append(f"{{queue_state:{name}}}（名前の一致する計画の状態ディレクトリが無い）")
            continue
        text = text.replace(f"{{queue_state{opt}:{name}}}", str(dirs[-1]) if dirs else "")
    return text, missing


def _fill_record(text: str, plan: str) -> tuple[str, list[str]]:
    """{queue_record} を、計画の `課題` のリリース記録を置いた PR の番号で置き換える。見つからなければ理由の文で返す。"""
    from sprint_close_lib.record_find import find_record  # GitHub を読む差し込みを持つ計画だけが読み込む

    try:
        info = json.loads(text)
    except ValueError:
        return text, [f"{QUEUE_RECORD}（計画の JSON を読めない）"]
    issues = [n for n in info.get("課題") or [] if isinstance(n, int)] if isinstance(info, dict) else []
    root = (info.get("リポジトリ") if isinstance(info, dict) else None) or str(Path(plan).parent)
    hit = find_record(root, None, issues)
    if hit.pr is None:
        return text, [f"{QUEUE_RECORD}（{hit.reason()}）"]
    return text.replace(QUEUE_RECORD, str(hit.pr)), []


def fill_queue_pr(plan: str, items: list[dict]) -> str | None:
    """計画の {queue_pr:<名>} を、前のステージの名前の一致する計画の Pull Request 1 本で置き換える。
    前のステージに無ければ同じディレクトリの計画の報告を読む（関門の後に単独で流すとき）。完了して PR が無い・飛ばされたなら 0。
    {queue_state:<名>} は、名前の一致する計画の状態ディレクトリの絶対パスで置き換える（#1488）。結果（完了した報告・
    状態ディレクトリ）が見つからなければ、0・空文字で埋めずに計画を書き換えず理由を返す（#2079）。
    {queue_state?:<名>} だけは、無いことを読む側が扱うと明示した差し込みで、無ければ空文字で埋める（単独で流す本番の approve）。
    {queue_record} は計画の `課題` のリリース記録を置いた PR で置き換え、見つからなければ同じく書き換えずに理由を返す（#2230）。"""
    try:
        text = Path(plan).read_text()
    except OSError as e:
        return f"計画を読めない: {e}"
    names, states = sorted(set(QUEUE_PR.findall(text))), sorted(set(QUEUE_STATE.findall(text)))
    record = QUEUE_RECORD in text
    if not names and not states and not record:
        return None
    text, missing_prs = _fill_prs(text, names, items, plan)
    text, missing_states = _fill_states(text, states, items, plan)
    text, missing_record = _fill_record(text, plan) if record else (text, [])
    missing = missing_prs + missing_states + missing_record
    if missing:
        return "差し込みを埋める前の計画の結果が見つからない: " + "、".join(missing)
    write_text_atomic(Path(plan), text)
    return None


def write_text_atomic(path: Path, text: str) -> None:
    """一時ファイルへ書いてから rename する（待つ側が書きかけを読まない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ---- 版の差し込み（#2164） ----

RELEASE_VERSION, RELEASE_BASE = "{release_version}", "{release_base}"  # 版の差し込みの語（雛形の release_templates も読む）
VERSION_RECORD = "version.json"  # 版の記録
VERSION_PLAN = "version-plan.json"  # 差し込み前のプランの写し
SUPERSEDED = "superseded"  # 決め直した前の版のステップの記録の退避先
KEEP_ON_REDECIDE = {VERSION_RECORD, VERSION_PLAN, SUPERSEDED, "plan.json"}  # 決め直しで退避しないもの（plan.json は状態の実体の写し）
GATE_2_STEPS = ("approve", "mvv")  # 本番のプランの先頭で承認ゲート 2 を通すステップ


def _version_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _read_version_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _write_version_json(path: Path, obj: dict) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _filled(text: str, version: str) -> str:
    import versions

    return text.replace(RELEASE_VERSION, version).replace(RELEASE_BASE, versions.release_base(version))


def _stopped(cause: str, version: str | None = None, existing: str | None = None, next_free: str | None = None, unreadable=None):
    """埋められないときの (理由, キューの項目の version)。"""
    info = {"cause": cause, "version": version, "existing": existing, "next_free": next_free, "unreadable": list(unreadable or [])}
    why = {
        "taken": f"版 {version} はすでに出ている（{existing}。次に空いている版は {next_free}）。別の版で黙って進めない",
        "unreadable": f"版を決める値を読めない: {'・'.join(info['unreadable'])}。推定で決めない",
        "no_series": "本番が版を読む開発版のプランの版の記録が無い",
        "series_changed": f"承認ゲート 2 の後に、本番が読む開発版の記録の版が変わった（{existing} → {version}）",
    }[cause]
    return f"版の差し込みを埋められない: {why}", info


class _Fill:
    """1 本のプランの版の差し込み。状態ディレクトリの版の記録と差し込み前の写しを読み書きする。"""

    def __init__(self, plan: str, text: str, info: dict, items: list[dict]):
        import delivery

        self.path, self.text, self.info, self.items = Path(plan), text, info, items
        self.field = info["版"]
        self.sd = state_dir_of(str(self.path.absolute()))
        self.sd.mkdir(parents=True, exist_ok=True)
        self.repo = info.get("リポジトリ") or "."
        self.base = str(info.get("起点") or "").removeprefix("origin/")
        self.production = delivery.load_delivery(self.repo).production
        self.plugin = self.field.get("プラグイン")
        self.level = self.field.get("上げ幅") or "patch"
        self.explicit = self.field.get("明示") or None

    @property
    def record_path(self) -> Path:
        return self.sd / VERSION_RECORD

    def observe(self):
        import release_version

        return release_version.observe(self.repo, self.plugin, self.base, self.production)

    def branch_of(self, version: str) -> str:
        b = self.info.get("branch")
        return b if b and RELEASE_VERSION not in b else f"release/v{version}"

    def log(self, text: str, **extra) -> None:
        line = json.dumps({"kind": "version", "at": _version_now(), "text": text, **extra}, ensure_ascii=False)
        with (self.sd / "progress.jsonl").open("a") as f:
            f.write(line + "\n")

    def write_record(self, version: str, how: str, observed: dict, series_from: str | None = None) -> dict:
        import versions

        if not (self.sd / VERSION_PLAN).exists():
            write_text_atomic(self.sd / VERSION_PLAN, self.text)
        rec = {
            "version": version,
            "base": versions.release_base(version),
            "how": how,
            "level": self.level,
            "observed": observed,
            "series_from": series_from,
            "at": _version_now(),
            "history": [],
        }
        _write_version_json(self.record_path, rec)
        return rec

    def ensure_filled(self, rec: dict) -> None:
        """プランが記録の版で埋まっていなければ、差し込み前の写しを記録の版で埋めて書き直す（決め直しの途中で落ちた後も同じ）。"""
        if RELEASE_VERSION not in self.text and RELEASE_BASE not in self.text and rec["version"] in self.text:
            return
        src = self.sd / VERSION_PLAN
        text = src.read_text() if src.is_file() else self.text
        write_text_atomic(self.path, _filled(text, rec["version"]))
        self.log(f"版の差し込みを {rec['version']} で埋めた（{self.record_path}）", version=rec["version"], record=str(self.record_path))

    def redecide(self, rec: dict, new: str, reason: str, existing: str | None, observed: dict) -> dict:
        """記録の版を決め直す。ステップの記録を退避 → 記録を書き換え → 写しから埋め直す の順（落ちても流し直しで同じ所へ戻る）。"""
        import versions

        old = rec["version"]
        dst = self.sd / SUPERSEDED / f"v{old}"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(self.sd.iterdir()):
            if f.name in KEEP_ON_REDECIDE:
                continue
            target = dst / f.name
            n = 1
            while target.exists():
                target, n = dst / f"{f.name}.{n}", n + 1
            os.replace(f, target)
        left = {"作業場所": self.info.get("作業場所"), "branch": self.info.get("branch"), "superseded": str(dst)}
        hist = {"from": old, "to": new, "reason": reason, "existing": existing, "observed": observed, "left": left, "at": _version_now()}
        rec = {**rec, "version": new, "base": versions.release_base(new), "observed": observed, "at": _version_now()}
        rec["history"] = [*(rec.get("history") or []), hist]
        _write_version_json(self.record_path, rec)
        self.log(
            f"版を {old} から {new} へ決め直した（{reason}）。前の作業場所 {left['作業場所']} と branch {left['branch']} は残した",
            version=new,
            record=str(self.record_path),
        )
        return rec

    def series_record(self) -> tuple[dict | None, str | None]:
        """本番が版を読む開発版のプラン（`系列` の名前）の版の記録と、そのパス。最も新しく書かれたもの。"""
        found = []
        for i in results_named(self.field.get("系列") or "", self.items, str(self.path)):
            if not i.get("plan"):
                continue
            f = state_dir_of(str(Path(i["plan"]).absolute())) / VERSION_RECORD
            rec = _read_version_json(f)
            if rec and rec.get("base"):
                found.append((f.stat().st_mtime, rec, str(f)))
        if not found:
            return None, None
        _, rec, f = max(found, key=lambda t: t[0])
        return rec, f

    def approved(self) -> bool:
        """本番のプランの状態に承認ゲート 2 の通過（approve か mvv のステップが 0 で終えた記録）があるか。"""
        state = _read_version_json(self.sd / "state.json") or {}
        return any(e.get("id") in GATE_2_STEPS and e.get("exit") == 0 for e in state.get("log") or [] if isinstance(e, dict))


def _recheck_dev(f: _Fill, obs, rec: dict):
    """記録がある開発版の版を確かめ直す。(使う記録, 止める理由) を返す。止めるときは記録が None。"""
    import release_version

    d = release_version.taken(obs, rec["version"], rec.get("how") or "decided", f.level)
    if not d.is_taken:
        return rec, None
    own = release_version.own_released(f.repo, f.branch_of(rec["version"]), f.base)
    if own is None:
        return None, _stopped("unreadable", rec["version"], unreadable=["pr"])
    if own:
        return rec, None
    if rec.get("how") == "explicit":
        return None, _stopped("taken", d.version, d.existing, d.next_free)
    return f.redecide(rec, release_version.decide_dev(obs, f.level).version, "taken", d.existing, obs.as_dict()), None


def _fill_dev(f: _Fill, rec: dict | None):
    import release_version

    obs = f.observe()
    if obs.unreadable:
        return _stopped("unreadable", (rec or {}).get("version") or f.explicit, unreadable=obs.unreadable)
    if rec is None:
        d = release_version.taken(obs, f.explicit, "explicit", f.level) if f.explicit else release_version.decide_dev(obs, f.level)
        if d.is_taken:
            return _stopped("taken", d.version, d.existing, d.next_free)
        rec = f.write_record(d.version, d.how, obs.as_dict())
    else:
        rec, stopped = _recheck_dev(f, obs, rec)
        if stopped:
            return stopped
    f.ensure_filled(rec)
    return None, None


def _fill_prod(f: _Fill, rec: dict | None):
    import release_version

    if rec is not None and rec.get("how") != "series":
        f.ensure_filled(rec)
        return None, None
    series, series_from = (None, None) if f.explicit else f.series_record()
    if not f.explicit and series is None:
        return _stopped("no_series")
    want = f.explicit or series["base"]
    if rec is not None and rec["version"] == want:
        f.ensure_filled(rec)
        return None, None
    if rec is not None and f.approved():
        return _stopped("series_changed", want, rec["version"])
    obs = f.observe()
    if obs.unreadable:
        return _stopped("unreadable", want, unreadable=obs.unreadable)
    d = release_version.taken(obs, want, "explicit" if f.explicit else "series")
    if d.is_taken:
        return _stopped("taken", d.version, d.existing, d.next_free)
    if rec is None:
        rec = f.write_record(want, d.how, obs.as_dict(), series_from)
    else:
        rec = f.redecide(rec, want, "series_changed", rec["version"], obs.as_dict())
    f.ensure_filled(rec)
    return None, None


def fill_release_version(plan: str, items: list[dict]) -> tuple[str | None, dict | None]:
    """配布のプランの版の差し込み（`{release_version}`・`{release_base}`）を、版の記録の値で埋める（#2164）。
    (埋められない理由, キューの項目に載せる `version`) を返す。どちらも None なら流してよい。「版」の欄を持たないプランは触らない。

    開発版は、記録が無ければ origin から次に空いている版を決めて（明示の版はまだ出ていないかを確かめて）記録を書く。記録が
    あれば記録の版を使い、それがもう出ていて自分の配布のマージ済みの PR が無ければ決め直す（明示の版は止める）。本番は
    明示の版か `系列` の開発版の記録の base で出し、タグがあれば止める。承認ゲート 2 の後に開発版の記録の版が変わったら止める。"""
    try:
        text = Path(plan).read_text()
        info = json.loads(text)
    except (OSError, ValueError) as e:
        return f"計画を読めない: {e}", None
    if not isinstance(info, dict) or not isinstance(info.get("版"), dict):
        return None, None
    f = _Fill(plan, text, info, items)
    if not f.plugin or not f.base:
        return "版の差し込みを埋められない: プランの「版」の欄にプラグインが無いか、起点が無い", None
    rec = _read_version_json(f.record_path)
    return (_fill_prod if f.field.get("チャネル") == "prod" else _fill_dev)(f, rec)
