# 公開の側に入っている非公開の参照の一覧

devbasex/ai-plugins は公開のリポジトリである。ここに入っている非公開の参照（非公開リポジトリへの完全な参照・手元のアカウント名・個人のメール）と、非公開リポジトリを参照する公開の Issue・PR の場所を並べる。#3391 の 3391-6 と 3391-3 の一覧で、S1c #3410 が作った。

**この一覧には場所と分類だけを書き、非公開の中身（リポジトリ名・アカウント名・メール）を写さない。** 値は下の「数え方」のコマンドを手元で打って確かめる。

**1 件ずつ、消すか書き換えるか残すかは人が決める**（#3395 の「人へ残す判断」）。今のファイルから消しても git の履歴には残り、履歴から消すには履歴の書き換え（C4）が要る。公開の Issue・PR の本文やコメントを書き換えるのは、他者が読む内容を変えること（C5）である。決めた扱いは、この一覧の行ごとに書き足す。

例: `docs/specifications/ndf-design-phase.md` の 61 行は、非公開リポジトリのマイルストーンの実測を根拠に書いている（3391-3 の「確定仕様への写し」）。書き換えるなら、リポジトリ名を「利用先のリポジトリ A」のような記号に置き換え、数（8 ラウンド 10 件）だけを残す。

2026-11-14 の develop（コミット `531c70aad`）で数えた。

## 非公開リポジトリへの完全な参照

31 ファイル・44 行。参照先の非公開リポジトリは 9 本である。`docs/specifications/` の 8 本は、非公開リポジトリでの実測や出来事を確定仕様の根拠に写している（3391-3）。

| ファイル | 行 | 分類 |
| --- | --- | --- |
| `README.md` | 102 | 参照 |
| `docs/ndf-parallel-model.md` | 182 | 参照 |
| `docs/specifications/cross-refactoring-failed-item-rules.md` | 37 | 確定仕様への写し（3391-3） |
| `docs/specifications/cross-refactoring-scope-template-probe.md` | 63・272 | 確定仕様への写し（3391-3） |
| `docs/specifications/cross-review-no-test-declaration-sweep.md` | 62・209・239 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-design-external-survey.md` | 36 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-design-phase.md` | 61 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-development-loop-limits.md` | 64 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-sprint-close-final-stage.md` | 23 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` | 52 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` | 12 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-supervise-work-step-web-tools-and-report-stop.md` | 35 | 確定仕様への写し（3391-3） |
| `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` | 322 | 確定仕様への写し（3391-3） |
| `docs/specifications/quality-gates-design-match.md` | 67 | 確定仕様への写し（3391-3） |
| `issues/issue-1142-design-fixes-design-detail.md` | 299・850 | 参照 |
| `issues/issue-1142-design-labels.md` | 141・214 | 参照 |
| `issues/issue-1142-design-next-design-detail.md` | 41 | 参照 |
| `issues/issue-1142-design.md` | 69 | 参照 |
| `issues/issue-1142-requirements.md` | 130・136・297 | 参照 |
| `issues/issue-2567-requirements.md` | 17 | 参照 |
| `issues/issue-2616-requirements.md` | 7 | 参照 |
| `issues/issue-3395-design-decisions.md` | 142 | 参照 |
| `issues/issue-3395-design.md` | 239・256・271・272・276 | 参照 |
| `issues/old/handoff-v10.6.0.md` | 139・140 | 参照 |
| `issues/old/issue-1436-design.md` | 7 | 参照 |
| `issues/old/issue-1436-requirements.md` | 9 | 参照 |
| `issues/old/issue-1437-requirements.md` | 9 | 参照 |
| `plugins/ndf-next/contracts/domains/entrypoint-cases.json` | 27 | 参照 |
| `plugins/ndf-next/contracts/domains/entrypoints.json` | 25・27 | 参照 |
| `plugins/ndf-next/src/ndf/parity/features.json` | 240 | 参照 |
| `plugins/ndf-next/tests/domains/crossrefactor/test_gate_and_probe.py` | 17 | 参照 |

## 確定仕様とテストの見本への写し（3391-3）

上の表で分類が「確定仕様への写し」の 8 本に加え、次の見本は非公開リポジトリの中身（プロダクトの種類・利用者・市場の調べ・使うライブラリ）を、リポジトリの名前を鍵にして写している。今のプラグインの中にあるため、書き換えるときは即時修正の印（`plugins/ndf-next/src/ndf/checks/README.md` の「I2 と即時修正」）が要る。

| ファイル | 分類 |
| --- | --- |
| `plugins/ndf/skills/backlog-refinement/tests/fixtures/materials-four-projects.json` | テストの見本への写し |

## 個人のメール

3 ファイル・3 行。

| ファイル | 行 | 分類 |
| --- | --- | --- |
| `.claude-plugin/marketplace.json` | 6 | 個人のメール |
| `issues/old/issue-1576-design.md` | 61 | 個人のメール |
| `issues/old/issue-1576-requirements.md` | 65 | 個人のメール |

## 手元のアカウント名

利用者が手元で使い分ける Claude のアカウントの名前。11 ファイル・46 行。

| ファイル | 行 | 分類 |
| --- | --- | --- |
| `docs/archive/version-decisions/v10.17.9-v10.17.51.md` | 381・403 | 手元のアカウント名 |
| `issues/old/issue-1453-design.md` | 12・13・291・292・489・500・510 | 手元のアカウント名 |
| `issues/old/issue-1453-requirements.md` | 99・104 | 手元のアカウント名 |
| `issues/old/issue-1523-design.md` | 8・11・15・16・22 | 手元のアカウント名 |
| `issues/old/issue-1523-requirements.md` | 41・45・48・65・177 | 手元のアカウント名 |
| `issues/old/issue-1576-design.md` | 28・30・33・34・39・40・55・56・66・67・68・69 | 手元のアカウント名 |
| `issues/old/issue-1576-requirements.md` | 32・60・77・141 | 手元のアカウント名 |
| `issues/old/issue-660-note-article-design.md` | 19 | 手元のアカウント名 |
| `issues/old/issue-660-note-article-skill-workflow.md` | 31 | 手元のアカウント名 |
| `issues/old/relay-capacity-estimate-2026-09-28.md` | 9・18・19・20・21・38 | 手元のアカウント名 |
| `plugins/ndf/scripts/tests/test_claude_accounts.py` | 447 | 手元のアカウント名 |

## GitHub のアカウント名

作者・所有者の表記（`plugin.json` の `author`・`LICENSE`・`CODEOWNERS` など）と、公開リポジトリの所有者としての参照。36 ファイル・72 行。公開のアカウントで、作者の表記として意図して書いたものが多い。

| ファイル | 行 | 分類 |
| --- | --- | --- |
| `.claude-plugin/marketplace.json` | 5 | GitHub のアカウント名 |
| `.github/CODEOWNERS` | 7・9・10・11・13・14・16・17・18・19 | GitHub のアカウント名 |
| `CODE_OF_CONDUCT.md` | 48 | GitHub のアカウント名 |
| `GOVERNANCE.md` | 23 | GitHub のアカウント名 |
| `LICENSE` | 3 | GitHub のアカウント名 |
| `README.md` | 110 | GitHub のアカウント名 |
| `docs/plugin-development-guide.md` | 13・14 | GitHub のアカウント名 |
| `docs/versioning-and-distribution.md` | 410 | GitHub のアカウント名 |
| `issues/old/external-reviews/pr-57-codex-business-review.md` | 5・13 | GitHub のアカウント名 |
| `issues/old/external-reviews/pr-57-gemini-technical-review.md` | 5・13・14 | GitHub のアカウント名 |
| `issues/old/promotion-ideas-for-ai-agents.md` | 346 | GitHub のアカウント名 |
| `plugins/mcp/mcp-aws-docs/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-aws-docs/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-bigquery/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-bigquery/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-chrome-devtools/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-chrome-devtools/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-dbhub/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-dbhub/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-dbhub/README.md` | 175 | GitHub のアカウント名 |
| `plugins/mcp/mcp-devin/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-devin/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-markitdown/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-markitdown/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-notion/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-notion/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-redash/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-redash/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-serena/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/mcp/mcp-serena/.codex-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/ndf-next/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/ndf/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/ndf/skills/cross-review/tests/test_state_rest_metadata.py` | 27・101 | GitHub のアカウント名 |
| `plugins/playwright-kit/.claude-plugin/plugin.json` | 6・7 | GitHub のアカウント名 |
| `plugins/playwright-kit/plugin.json` | 7・8 | GitHub のアカウント名 |
| `tests/runtime-smoke/fixtures/hooks-positive-control/.claude-plugin/marketplace.json` | 5 | GitHub のアカウント名 |

## 非公開リポジトリを参照する公開の Issue・PR

本文・題・コメントのどれかに非公開リポジトリへの完全な参照を持つもの。120 件（Issue 116 件・PR 4 件）。

| 種類 | 状態 | 件数 | 番号 |
| --- | --- | ---: | --- |
| Issue | CLOSED | 95 | #21・#463・#464・#536・#553・#584・#585・#598・#638・#641・#642・#647・#687・#705・#706・#707・#735・#792・#811・#817・#822・#824・#825・#1434・#1435・#1436・#1437・#1447・#1459・#1496・#1593・#1594・#1630・#1631・#1675・#1676・#1680・#1698・#1742・#1777・#1793・#1875・#2054・#2082・#2084・#2095・#2168・#2170・#2172・#2173・#2174・#2189・#2216・#2228・#2258・#2275・#2276・#2383・#2405・#2407・#2439・#2445・#2446・#2447・#2448・#2449・#2486・#2505・#2519・#2529・#2553・#2567・#2568・#2569・#2597・#2598・#2600・#2616・#2624・#2646・#2717・#2718・#2726・#2733・#2773・#2815・#2838・#2933・#2935・#2983・#3022・#3023・#3038・#3039・#3287 |
| Issue | OPEN | 21 | #1142・#1568・#1574・#1592・#1699・#1715・#1809・#2270・#2316・#2603・#2606・#2608・#2653・#2719・#2721・#2724・#2749・#2934・#2984・#3304・#3341 |
| PR | MERGED | 4 | #2133・#2659・#2723・#2839 |

## 数え方

リポジトリの根で打つ。名前の一覧は手元の一時ディレクトリにだけ置き、リポジトリへ書かない。

```bash
tmp=$(mktemp -d)
# 1. 参照している owner/repo のうち、非公開のものを求める
git grep -hIoE '(github\.com/)?\b[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+' \
  | sed -E 's#^github\.com/##; s/[.]+$//' | sort -u \
  | grep -E "^($(gh api user/orgs -q '[.[].login]|join("|")')|$(gh api user -q .login))/" \
  | while read -r r; do
      [ "$(gh repo view "$r" --json visibility -q .visibility 2>/dev/null)" = PRIVATE ] && echo "$r"
    done > "$tmp/private-repos.txt"
# 2. ファイルの場所（ファイル・行）
git grep -nIFf "$tmp/private-repos.txt" | cut -d: -f1,2
# 3. 公開の Issue・PR（検索の候補を、本文・題・コメントの完全な一致で絞る）
while read -r r; do
  gh search issues --repo devbasex/ai-plugins "$r" --limit 300 --json number -q '.[].number'
  gh search prs --repo devbasex/ai-plugins "$r" --limit 300 --json number -q '.[].number'
done < "$tmp/private-repos.txt" | sort -un \
  | while read -r n; do
      gh issue view "$n" --json title,body,comments -q '[.title,.body,(.comments[].body)]|join("\n")' 2>/dev/null \
        | grep -qFf "$tmp/private-repos.txt" && echo "#$n"
    done
```

アカウント名とメールは、手元のアカウント名の一覧（1 行に 1 つ）とメールのドメインの一覧を `$tmp` に置いて `git grep -nIiFf` で探す。非公開リポジトリへの完全な参照と同じ行は、上の表に入れて数えない。

## 今後の写しを止める場所

この一覧は今入っている分の棚卸しである。これから公開の側へ書く本文の検査は AD1+（3391-1・3391-2）が持ち、確定仕様とテストの見本を記号で書く決まりは 3391-3 の扱いを人が決めた後に置く。
