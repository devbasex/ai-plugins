"""`issue-upkeep` の参照 `grouping.md`（クラスタ／ルートコーズの上位の分解）の契約。"""
from __future__ import annotations

import re

from _helpers import GROUPING, MILESTONES, VOCABULARY, flat, section, table


def test_cluster_separates_itself_from_duplication() -> None:
    """重複との境目は、原因を直した後に個別の作業が残るかである。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "原因を直しても各課題に個別の作業が残るならルートコーズ、残らないなら重複である" in text


def test_root_cause_has_one_condition() -> None:
    """条件は 1 つで、件数は条件ではない。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 条件 | 確かめ方 | 欠けたときの行き先 |")
    assert len(rows) == 1
    assert "修正レイヤーが現象レイヤーと違う" in rows[0][0]
    assert "既存の判定のまま" in rows[0][2]
    text = flat(body)
    assert "件数は条件ではない" in text
    assert "件数が決めるのは" in text


def test_stage_3_has_three_outcomes() -> None:
    """段 3 の対応は、件数と個別の作業の有無で 3 つに分かれ、3 行目が重複との境目である。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 同じ修正レイヤーを指す件数 | 直しても個別の作業が残るか | 対応 |")
    assert len(rows) == 3
    assert "本文へ" in rows[0][2]
    assert "親 issue" in rows[1][2]
    assert "重複" in rows[2][2]
    assert "3 行目が重複との境目である" in flat(body)


def test_cluster_keeps_the_child_issues_open() -> None:
    """子 issue を閉じない。結び付けはサブイシュー関係を既定にし、例外だけ本文の 1 行にする。"""
    body = GROUPING.read_text(encoding="utf-8")
    text = flat(body)
    assert "子 issue を閉じない" in text
    assert "固有の再現手順" in text and "個別の適用が残る" in text
    assert "サブイシュー関係" in text
    assert "sub_issue_id" in body and "データベースの ID" in text
    assert "子 1 件が持てる親は 1 件だけ" in text
    assert "サブイシューの API を持たないリポジトリ" in text


def test_cluster_shows_the_body_of_the_parent_issue() -> None:
    """親 issue に書く 4 つを挙げ、子 issue の中身は写さない。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    for item in ("修正レイヤー", "採る手", "現象レイヤーと観測", "完了条件"):
        assert item in text, item
    assert "見出しの形は決めない" in text
    assert "子 issue の中身を写さない" in text
    assert "片方だけが古くなる" in text


def test_an_issue_may_join_two_clusters() -> None:
    """1 件が複数のクラスタへ属してよく、やり直しの見分けは親 issue の側から引く。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "1 件が複数のクラスタへ属してよい" in text
    assert "親 issue どうしが同じ箇所を直すなら、それは 1 つのクラスタである" in text
    assert "親 issue の側から引く" in text


def test_cluster_does_not_gate_on_size() -> None:
    """層がまたがっても判定は変わらず、採る手は判定の条件ではない。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "修正レイヤーが 2 つ以上にまたがっても、判定は変わらない" in text
    assert "マイルストーンの割り当てと実装計画" in text
    assert "手は判定の条件ではない" in text
    assert "当てはまるものを全部書く" in text


def test_parent_takes_the_highest_priority_in_the_cluster() -> None:
    """親 issue の重要度はクラスタの最高値を採り、重要度の規則を先に効かせてから最も早いものを採る。"""
    assert "クラスタの中で最も高いもの" in flat(GROUPING.read_text(encoding="utf-8"))
    assert "milestones.md" in GROUPING.read_text(encoding="utf-8")
    part = flat(section(MILESTONES.read_text(encoding="utf-8"), "## 親 issue を割り当てる"))
    assert "重要度が「高い」" in part
    assert part.index("重要度が「高い」") < part.index("最も早いもの")
    assert "子 issue のマイルストーンは動かさない" in part


def test_grouping_separates_the_two_layers() -> None:
    """現象レイヤーと修正レイヤーを分けて書き、さかのぼる段数を決めない。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 現象レイヤーの例 | 修正レイヤーの例 |")
    phenomena = [row[0] for row in rows]
    for example in ("各コントローラ", "各サブクラス", "移譲している側", "各スクリプト"):
        assert example in phenomena, example
    text = flat(body)
    assert "さかのぼる段数を決めない" in text
    assert "そこを直せば、現象レイヤーの各所が同じ形で直るか" in text
    assert "修正レイヤーが現象レイヤーと同じこともある" in text
    assert "名指しできない" in text


def test_grouping_lists_five_moves() -> None:
    """採る手は 5 つに限り、呼び名は `refactoring` の語彙から採る。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 手 | いつ選ぶか | 対応する手法（`refactoring` の呼び名） |")
    assert [row[0] for row in rows] == ["移動", "統合", "新設", "向きの修正", "分離"]
    names = re.findall(r"`([a-z_]+)`", " ".join(row[2] for row in rows))
    assert len(names) >= 6
    vocabulary = VOCABULARY.read_text(encoding="utf-8")
    for name in names:
        assert f"| `{name}` |" in vocabulary, name
    assert "references/vocabulary.md" in body


def test_cluster_never_defers_to_a_human() -> None:
    """ルートコーズの分岐は段 2A の控えだけで決まるため、「要判断」へ倒さない。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "要判断" not in body
    text = flat(body)
    for outcome in ("ルートコーズ", "既存の判定のまま", "本文へ", "親 issue を 1 件つくり", "重複として正本へ寄せる"):
        assert outcome in text, outcome


def test_the_skill_carries_only_what_it_must() -> None:
    """その場で決めれば済む書式の雛形を置かない。実測でしか分からないことは書く。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "```markdown" not in body
    assert "```text" not in body
    assert "sub_issue_id" in body


def test_grouping_rereads_the_stated_fix() -> None:
    """課題が書いている直し方をそのまま採らず、手段の語で終わる本文は何が直るのかを書き出す。"""
    body = GROUPING.read_text(encoding="utf-8")
    part = section(body, "## 課題が書いている直し方をそのまま採らない")
    assert table(part, "| 本文が書いていること | 棚卸が確かめること |")
    text = flat(part)
    assert "起票は現象を見て書かれる" in text
    for word in ("まとめる", "整理する", "統一する", "揃える"):
        assert f"「{word}」" in text, word
    assert "何が直るのか" in text
    assert "まだ原因に届いていない" in text
