"""材料の宣言（`materials`）の案（#2315 の決定 14・15）。git の木から候補と根拠を集め、`question` で返す。

決めるのは人である: 調べる依存（`seeds.libraries`）は直接の依存から候補を出し、人が残したものだけを調べる。
利用の記録（`needs.usage`）は解析の SDK の依存を根拠に挙げるだけで、読むコマンドと範囲は人が書く（決定 9）。
"""

from __future__ import annotations

from .measure_repo import Tree, question

MAX_LIBRARIES = 15
# 利用の記録の候補の根拠になる依存（アクセス解析・エラーの監視・製品の分析）
ANALYTICS = ("analytics", "gtag", "sentry", "datadog", "mixpanel", "amplitude", "posthog", "segment", "newrelic", "bugsnag", "rollbar")
ECOSYSTEM = {"php": "packagist", "javascript": "npm", "python": "pypi"}


def measure_materials(tree: Tree, deps: dict) -> dict:
    """材料の宣言の候補。README の冒頭（プロダクトの種類と利用者）・直接の依存・解析の SDK を根拠にする。"""
    libraries, analytics = [], []
    for lang, names in deps.items():
        for name in sorted(names):
            if name in ("php", "python", "node"):
                continue
            if any(w in name.lower() for w in ANALYTICS):
                analytics.append({"text": f"{ECOSYSTEM.get(lang, lang)}: {name}"})
            if len(libraries) < MAX_LIBRARIES:
                libraries.append({"name": name, "ecosystem": ECOSYSTEM.get(lang, lang), "alternatives": []})
    evidence = []
    for f in tree.shallow("README*.md")[:1]:
        head = [ln for ln in (tree.read(f) or "").splitlines() if ln.strip()][:8]
        evidence += [{"file": f, "text": ln[:200]} for ln in head]
    candidates = {
        "product": None,
        "needs": {"usage": [], "market": []},
        "seeds": {"libraries": libraries, "environment": []},
        "deferred_milestones": [],
    }
    return question(candidates, evidence + analytics)
