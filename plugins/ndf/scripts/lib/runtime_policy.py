"""ランタイムの宣言（`.ndf/runtimes.json`）の読み取りと照合（#1598）。

利用側のリポジトリが、NDF が CLI として起動してよいランタイムを宣言する。形は次のとおり。

```json
{"allowed": ["claude"], "review_seats": ["claude", "claude-2"]}
```

- `allowed`（必須）: 起動してよいランタイム。`ALL_RUNTIMES` の名前だけ、1 つ以上、重複なし
- `review_seats`（任意）: cross-review の固定の組。`SEAT_PATTERN` に合う 2 つの異なる席の名前で、
  どちらのランタイムも `allowed` にある

宣言は利用者だけが書き、NDF は読むだけにする。宣言が無ければ `read_policy` は `None` を返し、どこも制限しない。
**壊れた宣言（読めない・空・知らない名前・知らないキー）は `None` へ落とさず止める。** 綴りの誤りを
「宣言が無い」と読むと、制限が黙って外れるためである（設計の決定 2）。

読む順は `project_decl` と同じで、メインディレクトリの `.ndf/` → `root` の `.ndf/`。
シェルからは `runtime_policy.py check <runtime> --root <dir>` で呼ぶ（外か壊れていれば終了コード 3）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import repo
import statefile
from assignment import ALL_RUNTIMES, SEAT_PATTERN, AssignmentError

DECL = Path(".ndf") / "runtimes.json"
KEYS = ("allowed", "review_seats")
# `check_runtime`（副命令 `check`）の終了コード。external-ai の `EXIT_PRECONDITION` と supervise の「止まった」に揃える。
EXIT_POLICY = 3


class RuntimePolicyError(AssignmentError):
    """宣言が壊れている、または宣言の外のランタイムを起動しようとした。"""


@dataclass(frozen=True)
class RuntimePolicy:
    """読んだ宣言。`path` は理由の文に出すための宣言のファイルのパス。"""

    path: str
    allowed: tuple[str, ...]
    review_seats: Optional[tuple[str, str]] = None

    def allows(self, runtime: str) -> bool:
        return runtime in self.allowed

    def reason(self, names: Iterable[str], source: str) -> str:
        """外の名前を止める理由の文。宣言のパスと `allowed` を必ず入れる（AC16）。"""
        outside = ", ".join(names)
        return f"{outside} は宣言の外（{self.path} の allowed: {', '.join(self.allowed)}）。{source} を外すか宣言を直す"

    def require(self, names: Iterable[Optional[str]], source: str) -> None:
        """`names` のうち `allowed` に無いもの（席の名前はランタイムへ直す）があれば `RuntimePolicyError`。"""
        outside = [n for n in dict.fromkeys(n for n in names if n) if not self.allows(_runtime_of(n))]
        if outside:
            raise RuntimePolicyError(self.reason(outside, source))

    def keep_allowed(self, state: dict[str, Any], field_name: str, names: list[str], info: Callable[[str], Any]) -> list[str]:
        """再開で記録から引き継いだ名前（席の名前も可）のうち、宣言の外のものを落とす（設計の決定 5）。

        落としたら `info` へ知らせの 1 行を出し、`state["resume_changes"]` へ `policy:<field_name>` の変更を積む。
        cross-review と cross-refactoring が共通に使う。
        """
        kept = [n for n in names if self.allows(_runtime_of(n))]
        dropped = [n for n in names if n not in kept]
        if dropped:
            info(f"ℹ {', '.join(dropped)} は宣言の外のため、記録から引き継がずに外しました（{self.path}）")
            state.setdefault("resume_changes", []).append(
                {"at": statefile.now(), "field": f"policy:{field_name}", "from": names, "to": kept}
            )
        return kept

    def to_state(self) -> dict[str, Any]:
        """状態ファイルへ残す写し。"""
        return {
            "path": self.path,
            "allowed": list(self.allowed),
            "review_seats": list(self.review_seats) if self.review_seats else None,
        }


def policy_state(policy: Optional[RuntimePolicy]) -> Optional[dict[str, Any]]:
    """状態ファイルへ残す宣言の写し。宣言が無ければ `None`。"""
    return policy.to_state() if policy is not None else None


def differs_from_record(policy: Optional[RuntimePolicy], recorded_participants: Optional[dict[str, Any]]) -> bool:
    """記録（状態ファイルの `participants`）の宣言の写しと今の宣言が違うか（再開した時点の宣言に従う。前提 8）。"""
    return policy_state(policy) != (recorded_participants or {}).get("policy")


def _runtime_of(name: str) -> str:
    m = SEAT_PATTERN.match(name)
    return m.group(1) if m else name


def parse_policy(data: Any, path: str) -> RuntimePolicy:
    """宣言の中身を確かめて `RuntimePolicy` にする。破れていれば壊れた箇所を書いた `RuntimePolicyError`。"""

    def broken(what: str) -> RuntimePolicyError:
        return RuntimePolicyError(f"ランタイムの宣言が壊れています（{path}）: {what}")

    if not isinstance(data, dict):
        raise broken("最上位がオブジェクトでない")
    unknown = [k for k in data if k not in KEYS]
    if unknown:
        raise broken(f"知らないキー: {', '.join(map(str, unknown))}（{' / '.join(KEYS)} のみ）")
    if "allowed" not in data:
        raise broken("allowed が無い")
    allowed = data["allowed"]
    if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        raise broken("allowed は文字列の配列にする")
    if not allowed:
        raise broken("allowed が空")
    bad = [a for a in allowed if a not in ALL_RUNTIMES]
    if bad:
        raise broken(f"知らないランタイム: {', '.join(bad)}（{'/'.join(ALL_RUNTIMES)} のいずれか）")
    if len(set(allowed)) != len(allowed):
        raise broken("allowed に同じ名前が重なっている")
    seats = data.get("review_seats")
    pinned: Optional[tuple[str, str]] = None
    if seats is not None:
        if not isinstance(seats, list) or len(seats) != 2 or not all(isinstance(s, str) for s in seats):
            raise broken("review_seats は席の名前 2 つの配列にする")
        bad_seats = [s for s in seats if not SEAT_PATTERN.match(s)]
        if bad_seats:
            raise broken(f"review_seats の席の名前の形が違う: {', '.join(bad_seats)}")
        if seats[0] == seats[1]:
            raise broken(f"review_seats に同じ席の名前が 2 つある: {seats[0]}")
        outside = [s for s in seats if _runtime_of(s) not in allowed]
        if outside:
            raise broken(f"review_seats のランタイムが allowed に無い: {', '.join(outside)}")
        pinned = (seats[0], seats[1])
    return RuntimePolicy(path=path, allowed=tuple(allowed), review_seats=pinned)


def find(root) -> Optional[Path]:
    """宣言のファイル。メインディレクトリ → `root` の順に探す。無ければ `None`。"""
    if not root:
        return None
    main = repo.main_dir(root)
    for base in dict.fromkeys(p for p in (main, Path(root).resolve()) if p):
        f = Path(base) / DECL
        if f.is_file():
            return f
    return None


def read_policy(root) -> Optional[RuntimePolicy]:
    """`root` のリポジトリの宣言。無ければ `None`、壊れていれば `RuntimePolicyError`。"""
    f = find(root)
    if f is None:
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimePolicyError(f"ランタイムの宣言が壊れています（{f}）: JSON として読めない（{e}）") from e
    return parse_policy(data, str(f))


def check_runtime(runtime: str, root, source: str = "このランタイムの指定") -> None:
    """`runtime`（席の名前も可）を起動してよいか。外か壊れていれば `RuntimePolicyError`。宣言が無ければ何もしない。"""
    policy = read_policy(root)
    if policy is not None:
        policy.require([runtime], source)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ランタイムの宣言（.ndf/runtimes.json）を確かめる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ランタイムを起動してよいか（外か壊れていれば終了コード 3）")
    c.add_argument("runtime")
    c.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    try:
        check_runtime(args.runtime, args.root)
    except RuntimePolicyError as e:
        print(str(e), file=sys.stderr)
        return EXIT_POLICY
    return 0


if __name__ == "__main__":
    sys.exit(main())
