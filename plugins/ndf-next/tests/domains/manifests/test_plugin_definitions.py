"""実物の 2 つの定義（`plugin.json`）の名前・版数・description の版・hook の登録の契約（3341-7）。"""

from __future__ import annotations

import json

from ndf import identity
from ndf.domains.hooks import registration

from ._manifests import ROOT


MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
# 根の検査（scripts/lib/version_pattern.py）が description に求める版の形
VERSION_MARK = "(v{})"


def test_both_definitions_carry_the_same_name_and_version_and_the_version_in_the_description():
    # 3341-7: Codex の定義の版数は Claude Code の定義と同じ。根の検査（validate-runtime-plugins.sh）は push でだけ
    # 走るので、description の版の形もここで確かめる
    data = [json.loads((ROOT / rel).read_text(encoding="utf-8")) for rel in MANIFESTS]
    assert data[0]["name"] == data[1]["name"] == identity.load().name
    assert data[0]["version"] == data[1]["version"]
    for d in data:
        assert VERSION_MARK.format(d["version"]) in d["description"]


def test_both_definitions_point_at_their_hook_registration():
    for rel, runtime in zip(MANIFESTS, ("claude", "codex")):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert (ROOT / data["hooks"]).resolve() == registration.path_of(runtime).resolve()
