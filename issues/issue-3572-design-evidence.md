# #3572 の実物の確かめ: 検証用マーケットプレイス・merge queue・ブランチ戦略の節

設計文書 [issue-3572-design.md](issue-3572-design.md) の実物の確かめ（要求の受け入れ条件 2d・2g・2o・2r）を置く。打ったコマンドと出力をそのまま載せる。
用語は設計文書の「用語」の表の意味で使う。

## 検証用マーケットプレイス（受け入れ条件 2d）

使い捨てのディレクトリに、正式版の `main` と検証用ブランチ `verify/next` を持つ git のリポジトリを作り、HTTPS で配った
（`git http-backend`。`claude plugin marketplace add` は `file://` と `git://` を「Invalid marketplace source format」で受け付けない）。
`HOME`・`CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_PLUGIN_CACHE_DIR` を使い捨てのディレクトリへ向け、Claude Code 2.1.325 で打った（2026-11-15）。
`main` の `marketplace.json` の名前は `exp-plugins`、`verify/next` は `exp-plugins-next` である。

**確かめ 1: 同じリポジトリを違う名前と違う ref で登録すると、両方が残る**

```text
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git
✔ Successfully added marketplace: exp-plugins (declared in user settings)
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git#verify/next
Cloning repository (timeout: 120s): https://127.0.0.1:18443/repo.git (ref: verify/next)
✔ Successfully added marketplace: exp-plugins-next (declared in user settings)
$ claude plugin marketplace list
  ❯ exp-plugins       Source: Git (https://127.0.0.1:18443/repo.git)
  ❯ exp-plugins-next  Source: Git (https://127.0.0.1:18443/repo.git@verify/next)
```

登録（`known_marketplaces.json`）では、`exp-plugins` が `{"source": "git", "url": ...}` を持つ。
`exp-plugins-next` は同じ URL に `"ref": "verify/next"` を持つ。
同じプラグイン `demo` を両方から入れると、`demo@exp-plugins` と `demo@exp-plugins-next` が両方とも有効になる。
`claude plugin disable demo@exp-plugins` で片方だけを止められた。

**確かめ 2: 検証用ブランチを作り直した後に、更新で新しい中身が入る**

```text
（作り直しの前の exp-plugins-next の marketplace.json の説明）v1.1.0-pr1
$ claude plugin marketplace update exp-plugins-next
Cloning repository (timeout: 120s): https://127.0.0.1:18443/repo.git (ref: verify/next)
Replacing the existing marketplace clone…
✔ Successfully updated marketplace: exp-plugins-next
（作り直しの後）v1.2.0-pr2、head のコミット a1f94fe verify2
```

**確かめ 3: 正式版の登録が変わらない**

`exp-plugins` の `marketplace.json` の SHA-256 は、作り直しの前後とも `89aeee40…5265f7` だった。head は `2b72bc9 main1`、登録の取得元は
`{'source': 'git', 'url': 'https://127.0.0.1:18443/repo.git'}` のままだった。

**あわせて確かめたこと: 名前を正式版と同じにした ref は登録できない**

```text
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git#verify/same
✘ Failed to add marketplace: Cannot add marketplace "exp-plugins": its source doesn't match its extraKnownMarketplaces entry in user or managed settings; ...
```

利用者の登録への影響: 利用者の登録の取得元は前後で同じだった（`ai-plugins` は `https://github.com/devbasex/ai-plugins.git`）。
ただし、`CLAUDE_CODE_PLUGIN_CACHE_DIR` を向ける前の 1 回（取得に失敗した試み）で、利用者の登録の `lastUpdated` が 2026-11-15T11:53:54Z
（20:53 JST）に書き換わった。取得元と ref は変わっていない。この 1 回は受け入れ条件 2d の「利用者の登録を変えない場所」を満たしておらず、
扱いを設計文書の「人へ残す判断」の C6 の行で人が判断する。使い捨ての確かめには、3 つの変数をそろえて向ける。

## merge queue（受け入れ条件 2g）

ai-plugins の main には merge queue が無く、足すには ruleset の書き換えが要る。設計の工程では ruleset を書き換えず、外部へ公開する
リポジトリも作らない（要求の「含まない」）。そのため、確かめは (1) GitHub の API の型と ai-plugins の設定を読む、(2) merge queue を使って
いる公開リポジトリの記録を読む、の 2 つで行った。キューへ入れて落とす操作は実装のときに行う（設計文書の「未確認のまま残ること」と
「人へ残す判断」の C2 の行）。どれも 2026-11-15 に打った。

**確かめ 4: キューへ入れる操作は、承認した head を引数に取れる**

```text
$ gh api graphql -f query='{ __type(name:"EnqueuePullRequestInput"){ inputFields{ name description } } }'
clientMutationId  A unique identifier for the client performing the mutation.
pullRequestId     The ID of the pull request to enqueue.            （必須）
jump              Add the pull request to the front of the queue.
expectedHeadOid   The expected head OID of the pull request.
```

`enqueuePullRequest` は `expectedHeadOid` を持つ。マージの API の `sha`（STD1）と同じ役割で、承認した head を渡せる。head が違うときの
応答の形は、キューの無いブランチでは確かめられない（キューの有無の判定が先に断る見込みで、確かめにならない）。

**確かめ 5: キューのエントリの状態と、PR の型が持つ項目**

```text
$ gh api graphql -f query='{ __type(name:"MergeQueueEntryState"){ enumValues{ name } } }'
QUEUED / AWAITING_CHECKS / MERGEABLE / UNMERGEABLE / LOCKED
$ gh api graphql -f query='{ __type(name:"MergeQueueEntry"){ fields{ name } } }'
baseCommit, enqueuedAt, enqueuer, estimatedTimeToMerge, headCommit, id, jump, mergeQueue, position, pullRequest, solo, state
$ gh api graphql -f query='{ __type(name:"PullRequest"){ fields{ name } } }'   （queue を含む項目だけ）
isInMergeQueue, isMergeQueueEnabled, mergeQueue, mergeQueueEntry
```

キューで待つ間は `mergeQueueEntry.state` を、終わった後は PR の `merged` と `isInMergeQueue` を読めば、マージされたかキューから外れたかを
区別できる。

**確かめ 6: ai-plugins の今の設定**

```text
$ gh api graphql -f query='{ repository(owner:"devbasex",name:"ai-plugins"){ visibility owner{__typename} mergeQueue(branch:"main"){ id } } }'
{'visibility': 'PUBLIC', 'owner': {'__typename': 'Organization'}, 'mergeQueue': None}
$ gh api repos/devbasex/ai-plugins/rulesets/22332172 --jq '{bypass:.bypass_actors, rules:[.rules[].type]}'
bypass: [{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"pull_request"}]
rules:  deletion / non_fast_forward / pull_request（allowed_merge_methods: ["merge"]、承認 1、dismiss_stale_reviews_on_push: false）/
        required_status_checks（17 個、strict_required_status_checks_policy: false）
$ grep -l merge_group .github/workflows/*.yml | wc -l
0
```

ai-plugins は Organization が持つ public のリポジトリで、merge queue を使える条件（STD9）に当たる。必須のチェックを出す workflow は、
キューの一時のコミットで起動する `merge_group` を 1 つも持たない。キューを足す前に、17 個のチェックを出す workflow へ `merge_group` を
足す必要がある（STD7・STD8）。

**確かめ 7: キューの一時のコミットで必須のチェックが走り、確かめたコミットがそのまま本番ブランチへ入る（公開リポジトリの記録）**

`microsoft/vscode` は main に merge queue を持つ。2026-11-15 にキューを通ってマージされた PR #348333 の記録を読んだ。

```text
$ gh run list -R microsoft/vscode --event merge_group --limit 2 --json headBranch,conclusion,headSha,workflowName
{"conclusion":"success","headBranch":"gh-readonly-queue/main/pr-348333-827998555230f67dee66b1ae1521d8398cacb933",
 "headSha":"0fbab1451fed17c969feb57b0ab596adf895ba0b","workflowName":"Code OSS"}  ほか 1 件
$ gh pr view 348333 -R microsoft/vscode --json headRefOid,state,mergeCommit
{"head":"29e2cc9d20c3352eb744c55a671f4f59de388af2","merge":"0fbab1451fed17c969feb57b0ab596adf895ba0b","state":"MERGED"}
$ gh api repos/microsoft/vscode/commits/0fbab1451fed17c969feb57b0ab596adf895ba0b --jq '[.parents[].sha]'
["827998555230f67dee66b1ae1521d8398cacb933"]
$ gh api repos/microsoft/vscode/issues/348333/timeline --jq '.[]|select(.event|test("queue|merged"))|{event,actor:.actor.login,commit_id}'
{"event":"added_to_merge_queue","actor":"benibenj","commit_id":null}
{"event":"merged","actor":"benibenj","commit_id":"0fbab1451fed17c969feb57b0ab596adf895ba0b"}
{"event":"removed_from_merge_queue","actor":"github-merge-queue[bot]","commit_id":null}
$ gh api repos/microsoft/vscode/pulls/348333 --jq '{merged_by:.merged_by.login, merge_commit_sha, head:.head.sha}'
{"merged_by":"benibenj","merge_commit_sha":"0fbab1451fed17c969feb57b0ab596adf895ba0b","head":"29e2cc9d20c3352eb744c55a671f4f59de388af2"}
```

読み取れること:

| 項目 | 記録 | 設計への反映 |
| --- | --- | --- |
| 一時のコミット | ブランチ `gh-readonly-queue/main/pr-<番号>-<main の SHA>` に作られ、`merge_group` の workflow がその SHA で走った | 必須のチェックを出す workflow に `merge_group` を足す（決定 23） |
| マージしたコミット | PR のマージコミット（`merge_commit_sha`）は、キューで確かめた一時のコミットと同じ SHA。親はキューへ入れた時点の main | 組み合わせを確かめたコミットがそのまま main へ入る（I14） |
| マージした人 | `merged_by` と `merged` の出来事の主はキューへ入れた人。外したのは `github-merge-queue[bot]` | 人がキューへ入れたときの承認した人は `merged_by` で読める（経路 (b)。決定 4） |
| PR の head | PR の head（29e2cc9）は一時のコミット（0fbab14）と別の SHA | 承認した head は PR の head で照らし、マージした結果は `merge_commit_sha` で読む（I3・I14） |

vscode のマージは squash で、一時のコミットの親は 1 つである。ai-plugins の ruleset は `merge` だけを許すため、キューの一時のコミットは
main と PR の head を親に持つマージコミットになる見込みで、これは実装のときの確かめで見る。

**GitHub Docs の記述（STD7〜STD9）**: キューの一時のコミットで必須のチェックが落ちるか、本番ブランチと衝突すると、PR はキューから外れる。
タイムラインには理由が出る（「if there are failed required status checks or conflicts with the base branch, the pull request will be removed from
the queue」）。外れる理由は、チェックの失敗・チェックの待ちの時間切れ・API か画面からの取り外し・自動で解けない保護の違反の 4 つである。
キューへ入れられるのは、PR が保護の条件（承認・必須のチェック）をすべて満たした後で、書き込みの権限を持つ利用者である。

## ブランチ戦略の節のスキーマ（受け入れ条件 2m・2r。確かめ 8、2026-11-16）

設計文書の「ブランチ戦略の節」の 2 つの設定の例が、節のスキーマの案に通ることと、組み合わせを壊した設定が落ちることを確かめた。
スキーマの案は実装（#3587）で `plugins/ndf-next/contracts/schemas/declarations/branch-strategy.schema.json` に置く形の下書きである。
`$defs.reviews` は今の `worktree.schema.json` の `branch_flow.reviews`（`jq '.properties.branch_flow.properties.reviews'` で取り出したもの）をそのまま使った。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "declarations/branch-strategy.schema.json",
  "title": "ブランチ戦略の設定（.ndf/branch-strategy.json）",
  "type": "object",
  "additionalProperties": false,
  "required": ["version"],
  "properties": {
    "$schema": {"type": "string"},
    "version": {"type": "integer", "enum": [1]},
    "branch_strategy": {"$ref": "#/$defs/section"}
  },
  "$defs": {
    "section": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "base_branch": {"type": "string", "minLength": 1},
        "production_branch": {"type": "string", "minLength": 1},
        "release_strategy": {"enum": ["merge_base_branch", "release_branch", "trunk"], "default": "merge_base_branch"},
        "start": {"enum": ["base", "production"], "default": "base"},
        "integrate": {"enum": ["check", "release"], "default": "check"},
        "doc_prs": {"enum": ["base", "sprint"], "default": "base"},
        "reviews": {"$ref": "#/$defs/reviews"},
        "production_merge": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "method": {"enum": ["merge-queue", "strict"]},
            "admin": {"type": "boolean"}
          }
        },
        "verify": {
          "type": "object",
          "required": ["kind", "ref"],
          "properties": {
            "kind": {"enum": ["marketplace", "deploy"]},
            "ref": {"type": "string", "minLength": 1},
            "marketplace": {"type": "string", "minLength": 1},
            "command": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "timeout_seconds": {"type": "integer", "minimum": 1}
          },
          "oneOf": [
            {"properties": {"kind": {"const": "marketplace"}}, "required": ["marketplace"],
             "propertyNames": {"enum": ["kind", "ref", "marketplace"]}},
            {"properties": {"kind": {"const": "deploy"}}, "required": ["command", "timeout_seconds"],
             "propertyNames": {"enum": ["kind", "ref", "command", "timeout_seconds"]}}
          ]
        },
        "release": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "version_store": {
              "type": "object",
              "required": ["kind"],
              "oneOf": [
                {"properties": {"kind": {"const": "plugin"}}, "propertyNames": {"enum": ["kind"]}},
                {"properties": {"kind": {"const": "json"}, "path": {"type": "string", "minLength": 1},
                                "key": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}},
                 "required": ["path", "key", "name"], "propertyNames": {"enum": ["kind", "path", "key", "name"]}}
              ]
            }
          }
        }
      },
      "allOf": [
        {
          "if": {"properties": {"release_strategy": {"const": "release_branch"}}, "required": ["release_strategy"]},
          "then": {"properties": {"start": {"const": "production"}, "doc_prs": {"const": "sprint"}, "integrate": {"const": "check"}},
                   "required": ["start", "doc_prs"]}
        },
        {
          "if": {"properties": {"release_strategy": {"const": "trunk"}}, "required": ["release_strategy"]},
          "then": {"properties": {"start": {"const": "production"}, "doc_prs": {"const": "sprint"}, "integrate": {"const": "release"},
                                  "production_merge": {"required": ["method"]}, "release": {"required": ["version_store"]},
                                  "reviews": {"not": {"required": ["release"]}}},
                   "required": ["start", "doc_prs", "integrate", "production_merge", "release"]},
          "else": {
            "if": {"properties": {"release_strategy": {"const": "release_branch"}}, "required": ["release_strategy"]},
            "then": true,
            "else": {"properties": {"start": {"const": "base"}, "doc_prs": {"const": "base"}, "integrate": {"const": "check"},
                                    "reviews": {"not": {"required": ["release"]}}}}
          }
        }
      ]
    }
  }
}
```

設定の例（設計文書の「ブランチ戦略の節」と同じ全文）:

```json
{
  "version": 1,
  "branch_strategy": {
    "base_branch": "main",
    "production_branch": "main",
    "release_strategy": "trunk",
    "start": "production",
    "integrate": "release",
    "doc_prs": "sprint",
    "reviews": {
      "impl": {"checks": ["cross-review"], "diff": "pr"},
      "sprint": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},
      "design": {"checks": ["cross-review"], "diff": "pr"},
      "spec": {"checks": [], "diff": "none"}
    },
    "production_merge": {"method": "merge-queue", "admin": true},
    "verify": {"kind": "marketplace", "ref": "verify/next", "marketplace": "ai-plugins-next"},
    "release": {"version_store": {"kind": "plugin"}}
  }
}
```

```json
{
  "version": 1,
  "branch_strategy": {
    "base_branch": "develop",
    "production_branch": "main",
    "release_strategy": "merge_base_branch",
    "start": "base",
    "integrate": "check",
    "doc_prs": "base",
    "verify": {
      "kind": "deploy",
      "ref": "verify/staging",
      "command": ["gh", "workflow", "run", "deploy-staging.yml", "-f", "ref={ref}"],
      "timeout_seconds": 900
    },
    "release": {"version_store": {"kind": "json", "path": "package.json", "key": "version", "name": "web"}}
  }
}
```

検査のスクリプトは `/tmp/bs3572/check.py`（jsonschema 4.26.0 の `Draft202012Validator`。ndf-next の依存と同じバージョン）で、上の 3 つのファイルを読む。

```text
$ cd /tmp/bs3572 && /work/ai-plugins/.worktrees/design/issue-3572/plugins/ndf-next/.venv/bin/python -I check.py
ai-plugins: 通る
web-develop: 通る
壊した例 trunk でマージの方式が無い: 落ちる
壊した例 trunk でバージョンの置き場が無い: 落ちる
壊した例 trunk で start が base: 落ちる
壊した例 trunk で reviews に release: 落ちる
壊した例 merge_base_branch で start が production: 落ちる
壊した例 表に無い行: 落ちる
壊した例 表に無いマージの方式: 落ちる
壊した例 deploy でコマンドが無い: 落ちる
壊した例 表に無い検証環境の種類: 落ちる
壊した例 json の置き場で path が無い: 落ちる
壊した例 release_branch で doc_prs が無い: 落ちる
release_branch の例: 通る
節の無いファイル: 通る
```

ai-plugins の今の設定ファイルに、節へ移す前の値（検証環境の行・バージョンの置き場・マージの方式）があるかを数えた結果（a677e2d51 の時点）:

```text
$ grep -c '"verify"\|version_store\|"method"' .ndf/project.json .ndf/release.json .ndf/worktree.json
.ndf/project.json:0
.ndf/release.json:0
.ndf/worktree.json:0
```

この 3 つは #3572 の設計で足した値で、ai-plugins の設定ファイルにはまだ無い。移す値は `.ndf/worktree.json` の `base_branch`・`production_branch`・`branch_flow` と `.ndf/project.json` の `production_merge.admin` である。

## #3613 の設計との一致（受け入れ条件 2o。確かめ 9、2026-11-16）

#3613 の設計（`issues/issue-3613-design.md` の「ブランチ戦略の節へ移す行」）の移す行の新しい位置が、この設計の「ブランチ戦略の節」の「節のキー」の表に
すべてあり、置き場のファイルと節の名前が両方の文書にあり、移行の側の差分の記号（`<F>`・`<S>`）が残っていないことを、次のスクリプトで確かめた。

```python
import re, sys
d3613 = open("issues/issue-3613-design.md").read()
d3572 = open("issues/issue-3572-design.md").read()
sec = d3572[d3572.index("### 節のキー"):d3572.index("行が許す値:")]
news = re.findall(r"\| `\.ndf/branch-strategy\.json` \| `(/branch_strategy/[^`]+)` \|", d3613)
print("3613 の移す行の新しい位置:", len(news))
for p in news:
    key = p.removeprefix("/branch_strategy/").replace("/", ".")
    print(f"  {p}\t3572 の節のキーの表: {'ある' if re.search(r'\| `' + re.escape(key) + r'(\.|`)', sec) else '無い'}")
print("置き場のファイル（3572 / 3613）:", d3572.count("`.ndf/branch-strategy.json`"), "/", d3613.count("`.ndf/branch-strategy.json`"))
print("節の名前（3572 / 3613）:", d3572.count("`branch_strategy`"), "/", d3613.count("`branch_strategy`"))
print("記号 <F>・<S> の残り（3613）:", len(re.findall(r"<F>|<S>", d3613)))
```

```text
$ python3 -I /tmp/bs3572/keys.py
3613 の移す行の新しい位置: 10
  /branch_strategy/release_strategy	3572 の節のキーの表: ある
  /branch_strategy/start	3572 の節のキーの表: ある
  /branch_strategy/integrate	3572 の節のキーの表: ある
  /branch_strategy/doc_prs	3572 の節のキーの表: ある
  /branch_strategy/reviews	3572 の節のキーの表: ある
  /branch_strategy/base_branch	3572 の節のキーの表: ある
  /branch_strategy/production_branch	3572 の節のキーの表: ある
  /branch_strategy/production_merge	3572 の節のキーの表: ある
  /branch_strategy/verify	3572 の節のキーの表: ある
  /branch_strategy/release/version_store	3572 の節のキーの表: ある
置き場のファイル（3572 / 3613）: 15 / 11
節の名前（3572 / 3613）: 7 / 1
記号 <F>・<S> の残り（3613）: 0
```

