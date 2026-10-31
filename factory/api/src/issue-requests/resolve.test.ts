import { beforeEach, describe, expect, it } from "vitest";
import type { IssueRequestedEvent } from "@ndf-factory/contracts";
import { appendIssueRequestResolvedTo } from "../progress/append.ts";
import { MemoryProgressEventStore } from "../progress/memory-store.ts";
import type { Registration } from "../runs/model.ts";
import { FakeGitHub } from "../testing/runs.ts";
import { requestDigest } from "./ledger.ts";
import { MemoryIssueLedger } from "./memory-ledger.ts";
import { resolveIssueRequest, type IssueRequestDeps, type IssueRequestLogEntry } from "./resolve.ts";
import { defaultFilingStyle, type FilingStyleResolver } from "./style.ts";

// アカウント A の実行 R（org-a/repo-a、ID 1）。A は org-b/repo-b（ID 2）も登録し、どちらも導入 10 に入っている
const RUN = "sr-1";
let github: FakeGitHub;
let ledger: MemoryIssueLedger;
let store: MemoryProgressEventStore;
let registrations: Registration[];
let logs: IssueRequestLogEntry[];
let deps: IssueRequestDeps;

const reg = (accountId: string, repositoryId: number, fullName: string, installationId = 10): Registration => ({
  accountId,
  repositoryId,
  fullName,
  installationId,
  registeredBy: "u-1",
  registeredAt: "2026-10-31T00:00:00.000Z",
});

beforeEach(() => {
  github = new FakeGitHub();
  github.installations.set(10, new Map([[1, "org-a/repo-a"], [2, "org-b/repo-b"]]));
  ledger = new MemoryIssueLedger();
  store = new MemoryProgressEventStore();
  registrations = [reg("acc-a", 1, "org-a/repo-a"), reg("acc-a", 2, "org-b/repo-b")];
  logs = [];
  deps = {
    getSprintRun: async (id) => (id === RUN ? { accountId: "acc-a", repositoryId: 1 } : null),
    listRegistrations: async (accountId) => registrations.filter((r) => r.accountId === accountId),
    github,
    style: defaultFilingStyle,
    ledger,
    append: (e) => appendIssueRequestResolvedTo(store, e),
    limitPerRun: 10,
    now: () => "2026-10-31T01:00:00.000Z",
    log: (e) => logs.push(e),
  };
});

const request = (seq: number, payload: Partial<IssueRequestedEvent["payload"]> = {}, jobId = "job-1"): IssueRequestedEvent => ({
  sprint_run_id: RUN,
  idempotency_key: `${jobId}:${seq}`,
  type: "issue.requested",
  at: "2026-10-31T00:30:00.000Z",
  source: "runner",
  job_id: jobId,
  schema_version: 1,
  payload: { repo: "org-b/repo-b", title: "直してほしい", body: "由来: org-a/repo-a#1", ...payload },
});

const results = () => store.listRun(RUN).filter((e) => e.type === "issue.request_resolved").map((e) => e.payload);

describe("起票してよい先へ起票する（受け入れ条件 13・19、I46）", () => {
  it("同じアカウントが登録し導入に入った先へ、依頼の題と本文の Issue を 1 件作り、結果が参照を持つ", async () => {
    expect(await resolveIssueRequest(request(1), deps)).toMatchObject({ outcome: "filed" });
    expect(github.createdIssues).toEqual([{ installationId: 10, repositoryId: 2, title: "直してほしい", body: "由来: org-a/repo-a#1" }]);
    expect(results()).toEqual([
      { request_key: "job-1:1", outcome: "filed", issue: { repo: "org-b/repo-b", number: 1, url: "https://github.com/org-b/repo-b/issues/1" }, duplicate_of: null },
    ]);
    expect(store.listRun(RUN)[0]).toMatchObject({ idempotency_key: "job-1:1:resolved", source: "control", job_id: "job-1" });
  });

  it("起票先の名前は大文字と小文字を問わず登録と引き当てる", async () => {
    expect(await resolveIssueRequest(request(1, { repo: "Org-B/Repo-B" }), deps)).toMatchObject({ outcome: "filed" });
  });

  it("流儀の判定が GitHub Issues 以外を返すと起票せず unsupported_tracker", async () => {
    const seen: string[] = [];
    const redmine: FilingStyleResolver = { resolve: async (t) => (seen.push(t.repo), { tracker: "redmine", template: null }) };
    expect(await resolveIssueRequest(request(1), { ...deps, style: redmine })).toEqual({ outcome: "drafted", reason: "unsupported_tracker" });
    expect(seen).toEqual(["org-b/repo-b"]);
    expect(github.createdIssues).toHaveLength(0);
  });

  it("既定の流儀の判定は GitHub Issues とテンプレート無し", async () => {
    expect(await defaultFilingStyle.resolve({ repo: "org-b/repo-b", installationId: 10, repositoryId: 2 })).toEqual({
      tracker: "github_issues",
      template: null,
    });
  });
});

describe("起票しない先は下書きへ回す（受け入れ条件 15、I50）", () => {
  it("登録が無い・ほかのアカウントだけが登録している先は、どちらも not_registered", async () => {
    registrations = [reg("acc-a", 1, "org-a/repo-a"), reg("acc-other", 2, "org-b/repo-b")];
    expect(await resolveIssueRequest(request(1), deps)).toEqual({ outcome: "drafted", reason: "not_registered" });
    expect(await resolveIssueRequest(request(2, { repo: "org-c/repo-c" }), deps)).toEqual({ outcome: "drafted", reason: "not_registered" });
    expect(github.createdIssues).toHaveLength(0);
    expect(results().map((r) => r.reason)).toEqual(["not_registered", "not_registered"]);
  });

  it("登録はあるが導入が起票先を含まないと not_installed", async () => {
    github.installations.get(10)!.delete(2);
    expect(await resolveIssueRequest(request(1), deps)).toEqual({ outcome: "drafted", reason: "not_installed" });
    expect(github.createdIssues).toHaveLength(0);
    expect(ledger.counts.get(RUN) ?? 0).toBe(0);
  });
});

describe("公開範囲を広げない（決定 11）", () => {
  it.each([
    ["public", "private", "drafted"],
    ["public", "internal", "drafted"],
    ["internal", "private", "drafted"],
    ["internal", "internal", "filed"],
    ["internal", "public", "filed"],
    ["private", "internal", "filed"],
    ["private", "private", "filed"],
    ["public", "public", "filed"],
    ["private", "public", "filed"],
  ] as const)("起票先が %s・実行のリポジトリが %s なら %s", async (target, source, outcome) => {
    github.visibilities.set(2, target);
    github.visibilities.set(1, source);
    const result = await resolveIssueRequest(request(1), deps);
    expect(result.outcome).toBe(outcome);
    if (outcome === "drafted") expect(result).toMatchObject({ reason: "visibility" });
  });
});

describe("同じ依頼から Issue を 2 件作らない（受け入れ条件 16、I48・決定 10）", () => {
  it("同じ中身の依頼を別の seq・別のジョブで流すと Issue は 1 件で、2 件目は同じ参照と duplicate_of を持つ", async () => {
    await resolveIssueRequest(request(1), deps);
    await resolveIssueRequest(request(1, {}, "job-2"), deps);
    expect(github.createdIssues).toHaveLength(1);
    const [first, second] = results();
    expect(second).toEqual({ ...first, request_key: "job-2:1", duplicate_of: "job-1:1" });
  });

  it("同じ依頼を 2 回流しても結果は 1 件", async () => {
    await resolveIssueRequest(request(1), deps);
    await resolveIssueRequest(request(1), deps);
    expect(github.createdIssues).toHaveLength(1);
    expect(results()).toHaveLength(1);
  });

  it("同じ中身の別の依頼が起票の途中なら投げてやり直させる", async () => {
    await ledger.claim(RUN, requestDigest("org-b/repo-b", "直してほしい", "由来: org-a/repo-a#1"), "job-1:1");
    await expect(resolveIssueRequest(request(2), deps)).rejects.toThrow();
    expect(results()).toHaveLength(0);
  });

  it("自分の行が filing のまま流し直すと続きから進み、作成の要求の印があれば作り直さず filing_failed", async () => {
    const digest = requestDigest("org-b/repo-b", "直してほしい", "由来: org-a/repo-a#1");
    await ledger.claim(RUN, digest, "job-1:1");
    expect(await resolveIssueRequest(request(1), deps)).toMatchObject({ outcome: "filed" });

    const digest2 = requestDigest("org-b/repo-b", "別の題", "由来: org-a/repo-a#1");
    await ledger.claim(RUN, digest2, "job-1:2");
    await ledger.reserve(RUN, digest2, 10, "2026-10-31T00:59:00.000Z");
    expect(await resolveIssueRequest(request(2, { title: "別の題" }), deps)).toEqual({ outcome: "drafted", reason: "filing_failed" });
    expect(github.createdIssues).toHaveLength(1);
  });

  it("作成の要求が失敗したら作り直さず filing_failed で、数を戻す", async () => {
    github.failCreate.add(2);
    expect(await resolveIssueRequest(request(1), deps)).toEqual({ outcome: "drafted", reason: "filing_failed" });
    expect(ledger.counts.get(RUN)).toBe(0);
    github.failCreate.clear();
    expect(await resolveIssueRequest(request(1), deps)).toEqual({ outcome: "drafted", reason: "filing_failed" });
    expect(github.createdIssues).toHaveLength(0);
  });

  it("読み出しが失敗したら投げ、行は filing のまま残ってやり直せる", async () => {
    const failing = { ...deps, listRegistrations: async () => Promise.reject(new Error("読めない")) };
    await expect(resolveIssueRequest(request(1), failing)).rejects.toThrow();
    expect(await resolveIssueRequest(request(1), deps)).toMatchObject({ outcome: "filed" });
  });
});

describe("起票の数の上限（受け入れ条件 17、I49）", () => {
  it("上限 2 で中身の違う依頼を 3 件流すと、3 件目は limit_exceeded", async () => {
    deps.limitPerRun = 2;
    for (const seq of [1, 2, 3]) await resolveIssueRequest(request(seq, { title: `題 ${seq}` }), deps);
    expect(github.createdIssues).toHaveLength(2);
    expect(results().map((r) => r.reason ?? r.outcome)).toEqual(["filed", "filed", "limit_exceeded"]);
  });

  it("2 件目で GitHub が断ると数が戻り、3 件目は起票される", async () => {
    deps.limitPerRun = 2;
    await resolveIssueRequest(request(1, { title: "題 1" }), deps);
    github.failCreate.add(2);
    await resolveIssueRequest(request(2, { title: "題 2" }), deps);
    github.failCreate.clear();
    await resolveIssueRequest(request(3, { title: "題 3" }), deps);
    expect(results().map((r) => r.reason ?? r.outcome)).toEqual(["filed", "filing_failed", "filed"]);
  });

  it("下書きは数に入らない", async () => {
    deps.limitPerRun = 1;
    await resolveIssueRequest(request(1, { repo: "org-c/repo-c" }), deps);
    expect(await resolveIssueRequest(request(2), deps)).toMatchObject({ outcome: "filed" });
  });
});

describe("秘密らしい値（受け入れ条件 18）", () => {
  it.each([
    ["本文の AWS のアクセスキー", { body: "鍵は AKIAABCDEFGHIJKLMNOP です" }],
    ["本文の PEM の秘密鍵", { body: "-----BEGIN RSA PRIVATE KEY-----\nMIIE" }],
    ["本文の GitHub の token", { body: `token: ghp_${"a".repeat(36)}` }],
    ["題の GitHub の token", { title: `ghs_${"b".repeat(36)} が漏れた` }],
    ["本文の Anthropic の API キー", { body: "sk-ant-api03-abcdefghijklmnop" }],
    ["本文の JWT", { body: "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJqb2ItMSJ9.c2lnbmF0dXJlLXZhbHVl" }],
  ])("%s を含む依頼は起票せず secret_suspected で、ログに本文が無い", async (_name, payload) => {
    expect(await resolveIssueRequest(request(1, payload), deps)).toEqual({ outcome: "drafted", reason: "secret_suspected" });
    expect(github.createdIssues).toHaveLength(0);
    const text = JSON.stringify(logs);
    for (const value of Object.values(payload)) expect(text).not.toContain(value);
  });

  it("ログは決まった列だけで、題と本文を含まない", async () => {
    await resolveIssueRequest(request(1), deps);
    expect(logs).toEqual([{ type: "issue.request_resolved", request_key: "job-1:1", outcome: "filed", reason: null, duplicate: false }]);
  });
});
