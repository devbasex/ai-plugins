"""現状固定: 4 形が定める下書き先・承認境界・提出順序を保存する。"""
from pathlib import Path
import re

import pytest

REFERENCES = Path(__file__).resolve().parents[1] / "references"
FORMS = ("document", "slide", "page", "spreadsheet")

# 抽出前の本文。承認後の再生成禁止、公開範囲の再取得、条件付き索引登録を含む。
EXPECTED_DISTRIBUTION = """## 検証への配布

**対になる下書き先へ生成する。** 本番の提出先へは、承認した生成物だけが届く。
下書き先は `.ndf/document.json` の `draft` が指す（`development-workflow` の
`references/document-destinations.md`）。

## 本番への配布

**承認を得てから、下書き先の生成物を本番の提出先へ移す。** 作り直さない。
**作り直すと、承認したものと届くものが別になる。**

順序は次のとおり。

1. 下書き先で生成する
2. 生成物を取り込み、正本と内容を照合する（`document-systems` の `import.md`、用途 1）
3. 描画して版面を見る（`layout-review`）
4. **制作物承認を得る**
5. 本番の提出先へ移す
6. 公開範囲を設定し、**設定した後に読み直して確かめる**
7. 索引へ登録する（宣言に `index` があるとき）"""


def distribution_text(form: Path) -> str:
    body = form.read_text(encoding="utf-8")
    sections = []
    for heading in ("検証への配布", "本番への配布"):
        section = body.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
        assert "必ず" in section and "読み、その手順に従う" in section
        link = re.search(r"\]\(([^)]+)\)", section)
        assert link, f"{form.name}: {heading} の参照が無い"
        target, anchor = link.group(1).split("#", 1)
        assert anchor == heading
        referenced = (form.parent / target).read_text(encoding="utf-8")
        content = referenced.split(f"## {anchor}\n", 1)[1].split("\n## ", 1)[0]
        sections.append(f"## {heading}\n{content}")
    return "\n".join(sections)


def assert_distribution_contract(body: str) -> None:
    assert body.split() == EXPECTED_DISTRIBUTION.split()


@pytest.mark.parametrize("form", FORMS)
def test_documentation_distribution_contract(form: str) -> None:
    assert_distribution_contract(distribution_text(REFERENCES / f"form-{form}.md"))


def test_contract_rejects_missing_rules_and_reordered_steps() -> None:
    lines = EXPECTED_DISTRIBUTION.splitlines()
    # 本文の各行を欠落させた場合と、隣り合う各段階を入れ替えた場合を確かめる。
    for index, line in enumerate(lines):
        if line.strip():
            with pytest.raises(AssertionError):
                assert_distribution_contract("\n".join(lines[:index] + lines[index + 1:]))
    steps = [i for i, line in enumerate(lines) if re.match(r"\d+\. ", line)]
    assert len(steps) == 7
    for left, right in zip(steps, steps[1:]):
        changed = lines.copy()
        changed[left], changed[right] = changed[right], changed[left]
        with pytest.raises(AssertionError):
            assert_distribution_contract("\n".join(changed))
