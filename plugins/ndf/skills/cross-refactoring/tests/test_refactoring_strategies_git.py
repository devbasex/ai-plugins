"""リファクタリングの戦略の形の項目が、取り消されずに採用まで進むこと（#1814）を**実際の git と pytest** で確かめる。

| 受け入れ条件 | 確かめること |
| --- | --- |
| AC1・AC6（分割・クラスの抽出・共通モデルの抽出） | 範囲の外の別のディレクトリに新しいファイルを作った項目が採用される |
| AC1c・AC6（移動・改名・シグネチャの変更・インライン化） | 範囲の外の既存の呼び手の読み込み・呼び出しだけを書き換えた項目が採用され、そのファイルがレビューへ引き継がれる |
| AC1c（移動の `R`） | 範囲の中のファイルを範囲の外のまだ無いパスへ移した項目が採用される |
| AC1c・AC2（修正での呼び手の追従） | 実装が書き換え忘れた呼び手の import だけを修正が直すと採用される |
| AC2（PR 1732 の形） | 構造検査で落ちた項目を、修正担当が範囲の外の新しいモジュールへ分けて直すと採用される |
| AC3 | 新しいファイルを作った項目に D1 が立つ |
| AC6（テストの共通化）・AC8 | テストの値を新しい補助のファイルへ移した項目が採用される。値が本当に消えた項目は取り消す |
| AC6b（不要コードの削除） | 範囲テストの対象の無い `remove_dead_code` の項目が D4 の全体テストで検証され採用される |
| AC6a・I8 | 2 コミットの項目を取り消すと、2 コミットとも戻る |
| AC7 | 見積を大きく超える実差分の項目を取り消さない |
"""

from __future__ import annotations

import argparse

import pytest

from crossref_helpers import build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_result, write_state

FAR = "2099-01-01T00:00:00+00:00"
FIX_STEM = "claude-fix-rf130"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _emitted(capsys, key):
    out = capsys.readouterr().out
    values = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _item(item_id, technique, path, test, *, smell="long_method", rank=1):
    """改善項目。見積の差分は 1 行にしておく（実差分がその何倍でも取り消さないこと＝AC7 を全件で確かめる）。"""
    return {
        "id": item_id,
        "rank": rank,
        "path": path,
        "symbol": "x",
        "smell": smell,
        "technique": technique,
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": [test] if test else [],
        "command": ["pytest", "-q", test] if test else None,
        "command_source": "targets" if test else "deletion",
        "scope_commands": None if test else [],
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "status": "planned",
        "commits": {"test": None, "implement": [], "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 1,
    }


def _start(flow, cmd_setup, files, items):
    """`files` を起点に置き、項目と計画の起点（`plan.base_sha`）を書き、実装の手順を始める。

    ほかのモジュールを 15 本置く。ありふれた語はコードのファイルの 20% 以上かつ 3 ファイル以上に現れる語で、
    数本だけのリポジトリでは呼び手が 2 本ある名前までありふれた語になってしまう。
    """
    work = flow["work"]
    for n in range(15):
        _write(work, f"misc/filler_{n:02d}.py", f"VALUE_{n:02d} = {n}\n")
    for rel, text in files.items():
        _write(work, rel, text)
    commit_with_trailers(work, "既存のモジュール", {})
    state = read_state(flow["path"])
    for item in items:
        if item["scope_commands"] is None:
            item.pop("scope_commands")
        if item["command"] is None:
            item.pop("command")
    state["items"] = items
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR,
        "table_source": "defaults",
    }
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")


def _commit(flow, item_id, files, *, remove=()):
    work = flow["work"]
    for rel in remove:
        git("rm", "-q", rel, cwd=work)
    for rel, text in files.items():
        _write(work, rel, text)
    return commit_with_trailers(work, f"Refactor {item_id}", item_trailers(item_id))


def _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """実装を取り込み、検証して最終ゲートまで進め、採用の件数を返す。"""
    _call(cmd_implement, "cmd_merge_implement")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done", _items(flow)
    _call(cmd_gate, "cmd_final_gate")
    return ledger.tally(read_state(flow["path"])).adopted


# ---------- 分割・クラスの抽出・共通モデルの抽出（新しいファイル） ----------

SHOP = "def order_total(prices):\n    total = 0\n    for price in prices:\n        total += price\n    return total\n"
TEST_SHOP = "from src.shop import order_total\n\n\ndef test_total():\n    assert order_total([2, 3]) == 5\n"


def test_extracting_a_common_model_into_a_new_directory_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC1・AC3・AC6（クラスの抽出・共通モデルの抽出）: 範囲の外の別ディレクトリの新しいファイルへ移しても取り消さない。"""
    _start(
        flow,
        cmd_setup,
        {"src/shop.py": SHOP, "tests/test_shop.py": TEST_SHOP},
        [_item("I-001", "extract_class", "src/shop.py", "tests/test_shop.py")],
    )
    model = "class PriceList:\n    def __init__(self, prices):\n        self.prices = list(prices)\n\n    def total(self):\n        return sum(self.prices)\n"
    _commit(
        flow,
        "I-001",
        {
            "models/price_list.py": model,
            "src/shop.py": "from models.price_list import PriceList\n\n\ndef order_total(prices):\n    return PriceList(prices).total()\n",
        },
    )

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1
    item = _items(flow)["I-001"]
    assert "D1" in item["danger"], "新しいファイルは項目の path と tests の外なので D1 が立つ"
    assert (flow["work"] / "models" / "price_list.py").exists()


BIG = "def first():\n    return 1\n"
TEST_BIG = (
    "import pathlib\n\nfrom src.big import first\n\n\n"
    "def test_big_stays_small():\n    assert len(pathlib.Path('src/big.py').read_text().splitlines()) <= 5\n\n\n"
    "def test_first():\n    assert first() == 1\n"
)


def test_splitting_a_module_that_fails_the_structure_check_is_adopted(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, cmd_gate, ledger, capsys
):
    """AC2（PR 1732 の形）: 構造検査で落ちた項目を、修正担当が範囲の外の新しいモジュールへ分けて直すと採用まで進む。

    分けた先のファイルは、同じ実行の後の修正が直しても範囲の中として扱う（AC1a）。"""
    _start(
        flow,
        cmd_setup,
        {"src/big.py": BIG, "tests/test_big.py": TEST_BIG},
        [_item("I-001", "extract_method", "src/big.py", "tests/test_big.py")],
    )
    _commit(flow, "I-001", {"src/big.py": "def first():\n    return helper_one()\n\n\ndef helper_one():\n    return 1\n"})
    _call(cmd_implement, "cmd_merge_implement")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"

    _call(cmd_setup, "cmd_start_phase", phase="fix")
    _write(flow["work"], "parts/big_parts.py", "def helper_one():\n    return 0\n")
    _write(flow["work"], "src/big.py", "from parts.big_parts import helper_one\n\n\ndef first():\n    return helper_one()\n")
    commit_with_trailers(flow["work"], "Fix I-001: 分ける", item_trailers("I-001"))
    _write(flow["work"], "parts/big_parts.py", "def helper_one():\n    return 1\n")
    commit_with_trailers(flow["work"], "Fix I-001: 分けた先を直す", item_trailers("I-001"))
    write_result(flow["path"], FIX_STEM, {"items": [{"id": "I-001", "status": "fixed"}]})
    _call(cmd_merge_fix, "cmd_merge_fix")
    assert len(_items(flow)["I-001"]["commits"]["fix"]) == 2, "新しいファイルを作った修正を取り消さない"

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    _call(cmd_gate, "cmd_final_gate")
    assert ledger.tally(read_state(flow["path"])).adopted == 1


# ---------- 移動・改名・シグネチャの変更・インライン化（範囲の外の呼び手） ----------

LEDGER = "def monthly_total(values):\n    return sum(values)\n\n\ndef label():\n    return 'ledger'\n"
TEST_LEDGER = "from src.ledger import monthly_total\n\n\ndef test_monthly():\n    assert monthly_total([1, 2]) == 3\n"
REPORT = "from src.ledger import monthly_total\n\n\ndef report():\n    return monthly_total([4, 5])\n"
TEST_REPORT = "from tools.report import report\n\n\ndef test_report():\n    assert report() == 9\n"


def _ledger_files():
    return {"src/ledger.py": LEDGER, "tests/test_ledger.py": TEST_LEDGER, "tools/report.py": REPORT, "tests/test_report.py": TEST_REPORT}


def test_moving_a_function_and_rewriting_the_outside_caller_is_adopted(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys
):
    """AC1c・AC6（移動）: 範囲の外の既存の呼び手は import の書き換えだけ通し、そのファイルをレビューへ引き継ぐ（I5）。"""
    item = _item("I-001", "move_responsibility", "src/ledger.py", "tests/test_ledger.py")
    item["test_targets"] = ["tests/test_ledger.py", "tests/test_report.py"]
    item["command"] = ["pytest", "-q", "tests/test_ledger.py", "tests/test_report.py"]
    _start(flow, cmd_setup, _ledger_files(), [item])
    _commit(
        flow,
        "I-001",
        {
            "accounting/sums.py": "def monthly_total(values):\n    return sum(values)\n",
            "src/ledger.py": "def label():\n    return 'ledger'\n",
            "tests/test_ledger.py": TEST_LEDGER.replace("src.ledger", "accounting.sums"),
            "tools/report.py": REPORT.replace("src.ledger", "accounting.sums"),
        },
    )

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1
    assert {j["path"] for j in _items(flow)["I-001"]["review_scope_judgements"]} == {"tools/report.py"}


def test_moving_a_file_to_a_new_path_outside_the_scope_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC1c（移動の `R`）: 範囲の中のファイルを範囲の外のまだ無いパスへ移しても取り消さない。"""
    files = {
        "src/fmt_money.py": "def yen(value):\n    return f'{value} yen'\n",
        "tests/test_fmt.py": "from src.fmt_money import yen\n\n\ndef test_yen():\n    assert yen(3) == '3 yen'\n",
    }
    _start(flow, cmd_setup, files, [_item("I-001", "move_responsibility", "src/fmt_money.py", "tests/test_fmt.py")])
    (flow["work"] / "formatting").mkdir()
    git("mv", "src/fmt_money.py", "formatting/fmt_money.py", cwd=flow["work"])
    _commit(flow, "I-001", {"tests/test_fmt.py": files["tests/test_fmt.py"].replace("src.fmt_money", "formatting.fmt_money")})

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1
    assert not (flow["work"] / "src" / "fmt_money.py").exists()


def test_renaming_a_function_with_an_outside_caller_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC1c・AC6（改名）: 範囲の外の呼び手の名前の書き換えを通す。"""
    item = _item("I-001", "rename", "src/ledger.py", "tests/test_ledger.py", smell="inconsistent_naming")
    item["test_targets"] = ["tests/test_ledger.py", "tests/test_report.py"]
    item["command"] = ["pytest", "-q", "tests/test_ledger.py", "tests/test_report.py"]
    _start(flow, cmd_setup, _ledger_files(), [item])
    _commit(
        flow,
        "I-001",
        {
            "src/ledger.py": LEDGER.replace("monthly_total", "sum_of_month"),
            "tests/test_ledger.py": TEST_LEDGER.replace("monthly_total", "sum_of_month"),
            "tools/report.py": REPORT.replace("monthly_total", "sum_of_month"),
        },
    )

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1


SCALE = "def scale(value, factor=3):\n    return value * factor\n"
TEST_SCALE = "from src.scaler import scale\n\n\ndef test_scale():\n    assert scale(2) == 6\n"


def test_changing_a_signature_with_an_outside_caller_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC1c・AC6（シグネチャの変更）: 範囲の外の呼び手の引数を減らす書き換えを通す。"""
    files = {
        "src/scaler.py": SCALE,
        "tests/test_scaler.py": TEST_SCALE,
        "tools/scale_use.py": "from src.scaler import scale\n\n\ndef run():\n    return scale(5, 3)\n",
    }
    _start(
        flow, cmd_setup, files, [_item("I-001", "change_signature", "src/scaler.py", "tests/test_scaler.py", smell="long_parameter_list")]
    )
    _commit(
        flow,
        "I-001",
        {
            "src/scaler.py": "def scale(value):\n    return value * 3\n",
            "tools/scale_use.py": "from src.scaler import scale\n\n\ndef run():\n    return scale(5)\n",
        },
    )

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1


def test_inlining_a_function_with_an_outside_caller_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC1c・AC6（インライン化）: 範囲の外の呼び手で、消した関数の import と呼び出しを置き換える書き換えを通す。"""
    inl = "def double(x):\n    return twice(x)\n\n\ndef twice(x):\n    return x * 2\n"
    files = {
        "src/inl.py": inl,
        "tests/test_inl.py": "from src.inl import double\n\n\ndef test_double():\n    assert double(4) == 8\n",
        "tools/inl_use.py": "from src.inl import twice\n\n\ndef run():\n    return twice(4)\n",
    }
    _start(flow, cmd_setup, files, [_item("I-001", "inline", "src/inl.py", "tests/test_inl.py")])
    _commit(flow, "I-001", {"src/inl.py": "def double(x):\n    return x * 2\n", "tools/inl_use.py": "\n\ndef run():\n    return 4 * 2\n"})

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1


def test_a_fix_that_only_rewrites_a_forgotten_outside_caller_is_adopted(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, cmd_gate, ledger, capsys
):
    """AC1c・AC2: 実装が移した名前の呼び手を書き換え忘れ、修正がその import だけを直すと採られる。

    修正のコミットだけから変えた名前を集めると、移した名前が入らず取り消される（決定 2）。"""
    item = _item("I-001", "move_responsibility", "src/ledger.py", "tests/test_ledger.py")
    item["test_targets"] = ["tests/test_ledger.py", "tests/test_report.py"]
    item["command"] = ["pytest", "-q", "tests/test_ledger.py", "tests/test_report.py"]
    _start(flow, cmd_setup, _ledger_files(), [item])
    _commit(
        flow,
        "I-001",
        {
            "accounting/sums.py": "def monthly_total(values):\n    return sum(values)\n",
            "src/ledger.py": "def label():\n    return 'ledger'\n",
            "tests/test_ledger.py": TEST_LEDGER.replace("src.ledger", "accounting.sums"),
        },
    )
    _call(cmd_implement, "cmd_merge_implement")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"

    _call(cmd_setup, "cmd_start_phase", phase="fix")
    _write(flow["work"], "tools/report.py", REPORT.replace("src.ledger", "accounting.sums"))
    commit_with_trailers(flow["work"], "Fix I-001", item_trailers("I-001"))
    write_result(flow["path"], FIX_STEM, {"items": [{"id": "I-001", "status": "fixed"}]})
    _call(cmd_merge_fix, "cmd_merge_fix")
    item = _items(flow)["I-001"]
    assert len(item["commits"]["fix"]) == 1, "呼び手の import だけを直した修正を取り消さない"
    assert {j["path"] for j in item["review_scope_judgements"]} == {"tools/report.py"}

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    _call(cmd_gate, "cmd_final_gate")
    assert ledger.tally(read_state(flow["path"])).adopted == 1


# ---------- テストの共通化（期待値の和集合） ----------

VALS = "def base_rate():\n    return 7\n"
TEST_VALS = "from src.vals import base_rate\n\n\ndef test_rate():\n    assert base_rate() == 7\n"


def test_moving_test_values_into_a_new_helper_file_is_adopted(flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys):
    """AC6（テストの共通化）・AC8: 値を新しい補助のファイルへ移しただけなら、期待値の変更として取り消さない。"""
    _start(
        flow,
        cmd_setup,
        {"src/vals.py": VALS, "tests/test_vals.py": TEST_VALS},
        [_item("I-001", "extract_test_helper", "tests/test_vals.py", "tests/test_vals.py")],
    )
    _commit(
        flow,
        "I-001",
        {
            "tests/vals_expect.py": "EXPECTED_RATE = 7\n",
            "tests/test_vals.py": "from src.vals import base_rate\nfrom vals_expect import EXPECTED_RATE\n\n\ndef test_rate():\n    assert base_rate() == EXPECTED_RATE\n",
        },
    )

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1


def test_a_test_value_that_really_disappeared_still_rejects_the_item(flow, cmd_setup, cmd_implement):
    """AC8・AC10: 値が項目の変えたテストのファイルのどこにも無くなった変更は、今までどおり取り消す。"""
    _start(
        flow,
        cmd_setup,
        {"src/vals.py": VALS, "tests/test_vals.py": TEST_VALS},
        [_item("I-001", "extract_test_helper", "tests/test_vals.py", "tests/test_vals.py")],
    )
    _commit(
        flow,
        "I-001",
        {
            "src/vals.py": "def base_rate():\n    return 8\n",
            "tests/vals_expect.py": "EXPECTED_RATE = 8\n",
            "tests/test_vals.py": "from src.vals import base_rate\nfrom vals_expect import EXPECTED_RATE\n\n\ndef test_rate():\n    assert base_rate() == EXPECTED_RATE\n",
        },
    )

    with pytest.raises(SystemExit):
        _call(cmd_implement, "cmd_merge_implement")
    assert "期待する振る舞い" in _items(flow)["I-001"]["failure_reason"]


# ---------- 不要コードの削除 ----------


def test_removing_dead_code_without_a_scope_test_is_verified_by_the_whole_test(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, ledger, capsys
):
    """AC6b: 範囲テストの対象が無い `remove_dead_code` は、D4 の全体テストで検証して採用まで進む。"""
    dead = "def live():\n    return 1\n\n\ndef unused():\n    return 2\n"
    files = {"src/dead.py": dead, "tests/test_dead.py": "from src.dead import live\n\n\ndef test_live():\n    assert live() == 1\n"}
    _start(flow, cmd_setup, files, [_item("I-001", "remove_dead_code", "src/dead.py", None, smell="dead_code")])
    _commit(flow, "I-001", {"src/dead.py": "def live():\n    return 1\n"})

    assert _adopted(flow, cmd_implement, cmd_converge, cmd_gate, ledger, capsys) == 1
    assert "D4" in _items(flow)["I-001"]["danger"]


def test_a_dead_code_removal_that_breaks_the_whole_test_goes_to_fix(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC6b: 全体テストが落ちれば今と同じく修正へ回る。"""
    dead = "def live():\n    return 1\n\n\ndef unused():\n    return 2\n"
    files = {
        "src/dead.py": dead,
        "tests/test_dead.py": "from src.dead import live, unused\n\n\ndef test_live():\n    assert live() + unused() == 3\n",
    }
    _start(flow, cmd_setup, files, [_item("I-001", "remove_dead_code", "src/dead.py", None, smell="dead_code")])
    _commit(flow, "I-001", {"src/dead.py": "def live():\n    return 1\n"})
    _call(cmd_implement, "cmd_merge_implement")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "fix"


# ---------- 複数コミットの項目と取り消し ----------


def test_dropping_a_two_commit_item_reverts_both_commits(flow, cmd_setup, cmd_implement):
    """AC6a・I8: 2 コミット目が範囲の外の既存のファイルを勝手に変えた項目は、2 コミットとも戻る。"""
    work = flow["work"]
    _start(
        flow,
        cmd_setup,
        {"src/shop.py": SHOP, "tests/test_shop.py": TEST_SHOP},
        [_item("I-001", "rename", "src/shop.py", "tests/test_shop.py")],
    )
    _commit(
        flow,
        "I-001",
        {
            "src/shop.py": SHOP.replace("total = 0", "running = 0")
            .replace("total +=", "running +=")
            .replace("return total", "return running")
        },
    )
    _commit(flow, "I-001", {".gitignore": (work / ".gitignore").read_text() + "build/\n"})

    with pytest.raises(SystemExit):
        _call(cmd_implement, "cmd_merge_implement")

    assert _items(flow)["I-001"]["status"] == "reverted"
    assert (work / "src" / "shop.py").read_text() == SHOP
    assert "build/" not in (work / ".gitignore").read_text()
