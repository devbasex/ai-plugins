// GitHub の口の本物。App の JWT（RS256、秘密鍵は GITHUB_APP_PRIVATE_KEY_SECRET_ARN）で導入の token を作り、
// 利用者の認可（App のクライアント）で利用者の届く導入を引く。token と秘密鍵の値はログ・例外の文に出さない
import { importPKCS8, SignJWT } from "jose";
import { createPrivateKey } from "node:crypto";
import { pickRankingTable, type GitHubPort, type InstallationToken, type IssueSummary, type ReachableRepository } from "./port.ts";

// 決定 6 の権限。導入の token はこの範囲とリポジトリ 1 つに絞る
export const INSTALLATION_PERMISSIONS = {
  metadata: "read",
  contents: "write",
  pull_requests: "write",
  issues: "write",
  checks: "read",
  actions: "read",
} as const;

export interface GitHubAppConfig {
  appId: string;
  slug: string;
  clientId: string;
  clientSecret: () => Promise<string>;
  privateKeyPem: () => Promise<string>;
  apiUrl?: string;
  fetch?: typeof fetch;
}

type Json = Record<string, unknown>;

export class GitHubApp implements GitHubPort {
  private readonly api: string;
  private readonly http: typeof fetch;

  constructor(private readonly config: GitHubAppConfig) {
    this.api = config.apiUrl ?? "https://api.github.com";
    this.http = config.fetch ?? fetch;
  }

  installUrl(): string {
    return `https://github.com/apps/${this.config.slug}/installations/new`;
  }

  authorizeUrl(state: string): string {
    const q = new URLSearchParams({ client_id: this.config.clientId, state });
    return `https://github.com/login/oauth/authorize?${q}`;
  }

  async exchangeCode(code: string): Promise<string | null> {
    const res = await this.http("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: this.config.clientId, client_secret: await this.config.clientSecret(), code }),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Json;
    return typeof body.access_token === "string" ? body.access_token : null;
  }

  async listReachableRepositories(userToken: string): Promise<ReachableRepository[]> {
    const out: ReachableRepository[] = [];
    for (const inst of await this.getAllPages(`/user/installations`, userToken, "installations")) {
      const installationId = inst.id as number;
      for (const r of await this.getAllPages(`/user/installations/${installationId}/repositories`, userToken, "repositories")) {
        out.push({ repositoryId: r.id as number, fullName: r.full_name as string, installationId });
      }
    }
    return out;
  }

  async createInstallationToken(installationId: number, repositoryId: number): Promise<InstallationToken | null> {
    const res = await this.http(`${this.api}/app/installations/${installationId}/access_tokens`, {
      method: "POST",
      headers: { ...this.headers(await this.appJwt()), "Content-Type": "application/json" },
      body: JSON.stringify({ repository_ids: [repositoryId], permissions: INSTALLATION_PERMISSIONS }),
    });
    if (res.status === 404 || res.status === 403 || res.status === 422) return null;
    if (!res.ok) throw new Error(`GitHub の導入の token を作れません（${res.status}）`);
    const body = (await res.json()) as Json;
    return { token: body.token as string, expiresAt: body.expires_at as string };
  }

  async repositoryName(installationId: number, repositoryId: number): Promise<string | null> {
    const token = await this.createInstallationToken(installationId, repositoryId);
    if (!token) return null;
    const repo = await this.get(`/repositories/${repositoryId}`, token.token);
    return typeof repo?.full_name === "string" ? repo.full_name : null;
  }

  async checkOpenIssues(installationId: number, fullName: string, numbers: number[]) {
    const token = await this.tokenFor(installationId, fullName);
    const invalid: number[] = [];
    for (const n of numbers) {
      const issue = token ? await this.get(`/repos/${fullName}/issues/${n}`, token) : null;
      if (!issue || issue.state !== "open" || issue.pull_request !== undefined) invalid.push(n);
    }
    return { invalid };
  }

  async listRecentOpenIssues(installationId: number, fullName: string, limit: number): Promise<IssueSummary[]> {
    const token = await this.tokenFor(installationId, fullName);
    if (!token) return [];
    const list = (await this.getList(`/repos/${fullName}/issues?state=open&sort=created&direction=desc&per_page=${limit * 2}`, token)) ?? [];
    return list
      .filter((i) => i.pull_request === undefined)
      .slice(0, limit)
      .map((i) => ({ number: i.number as number, title: i.title as string }));
  }

  async findRankingTable(installationId: number, fullName: string) {
    const token = await this.tokenFor(installationId, fullName);
    if (!token) return null;
    const list = (await this.getList(`/repos/${fullName}/milestones?state=open&per_page=100`, token)) ?? [];
    const picked = pickRankingTable(
      list.map((m) => ({ title: m.title as string, number: m.number as number, description: (m.description as string | null) ?? null })),
    );
    if (!picked) return null;
    // 題は表に無いため、そのマイルストーンの開いた課題から引く。閉じた課題と Pull Request は候補にしない
    const titles = new Map<number, string>();
    for (const i of await this.getAllPages(`/repos/${fullName}/issues?state=open&milestone=${picked.milestoneNumber}`, token, null)) {
      if (i.pull_request === undefined) titles.set(i.number as number, i.title as string);
    }
    const rows = picked.numbers.filter((n) => titles.has(n)).map((n) => ({ number: n, title: titles.get(n)! }));
    return rows.length > 0 ? { milestone: picked.milestone, rows } : null;
  }

  // per_page=100 で最後のページまで読む。key があればその配列を、無ければ本体の配列を集める
  private async getAllPages(path: string, token: string, key: string | null): Promise<Json[]> {
    const out: Json[] = [];
    const sep = path.includes("?") ? "&" : "?";
    for (let page = 1; ; page++) {
      const body = await this.get(`${path}${sep}per_page=100&page=${page}`, token);
      const items = ((key ? body?.[key] : body) as Json[] | undefined) ?? [];
      out.push(...items);
      if (items.length < 100) return out;
    }
  }

  private async tokenFor(installationId: number, fullName: string): Promise<string | null> {
    const repo = await this.get(`/repos/${fullName}`, (await this.installationTokenAll(installationId)) ?? "");
    if (typeof repo?.id !== "number") return null;
    return (await this.createInstallationToken(installationId, repo.id))?.token ?? null;
  }

  private async installationTokenAll(installationId: number): Promise<string | null> {
    const res = await this.http(`${this.api}/app/installations/${installationId}/access_tokens`, {
      method: "POST",
      headers: { ...this.headers(await this.appJwt()), "Content-Type": "application/json" },
      body: JSON.stringify({ permissions: { metadata: "read" } }),
    });
    if (!res.ok) return null;
    return ((await res.json()) as Json).token as string;
  }

  private headers(token: string): Record<string, string> {
    return { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "X-GitHub-Api-Version": "2022-11-28" };
  }

  private async get(path: string, token: string): Promise<Json | null> {
    const res = await this.http(`${this.api}${path}`, { headers: this.headers(token) });
    if (res.status === 404 || res.status === 403 || res.status === 401) return null;
    if (!res.ok) throw new Error(`GitHub の読み出しに失敗しました（${res.status}）`);
    return (await res.json()) as Json;
  }

  private async getList(path: string, token: string): Promise<Json[] | null> {
    return (await this.get(path, token)) as unknown as Json[] | null;
  }

  private async appJwt(): Promise<string> {
    const pem = await this.config.privateKeyPem();
    // GitHub の秘密鍵は PKCS#1 の形で配られるため、PKCS#8 へ直してから読む
    const pkcs8 = createPrivateKey(pem).export({ type: "pkcs8", format: "pem" }).toString();
    const key = await importPKCS8(pkcs8, "RS256");
    const now = Math.floor(Date.now() / 1000);
    return new SignJWT({}).setProtectedHeader({ alg: "RS256" }).setIssuer(this.config.appId).setIssuedAt(now - 60).setExpirationTime(now + 540).sign(key);
  }
}
