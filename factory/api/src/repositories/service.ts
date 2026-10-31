// 取り込み（F1・F2）。候補の一覧と候補の証明・登録・外し・一覧。I32・I33・I36
import type { AccountContext } from "../accounts/model.ts";
import { ConflictError, ForbiddenError, InvalidRequestError, NotFoundError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import type { Registration } from "../runs/model.ts";
import type { RunsTable } from "../runs/table.ts";
import { signProof, signState, verifyProof, verifyState, type ProofKey } from "./proof.ts";

export interface RepositoryDeps {
  table: RunsTable;
  github: GitHubPort;
  proofKey: ProofKey;
  now?: () => Date;
}

// 持ち主だけの操作。席を持つだけの人には他人のアカウントと同じ 403（前提 4）
export function requireOwner(ctx: AccountContext): void {
  if (ctx.role !== "owner") throw new ForbiddenError();
}

const view = (r: Registration, active?: number) => ({
  repository_id: r.repositoryId,
  full_name: r.fullName,
  installation_id: r.installationId,
  registered_at: r.registeredAt,
  ...(active === undefined ? {} : { active_sprint_runs: active }),
});

export class RepositoryService {
  constructor(private readonly deps: RepositoryDeps) {}

  installUrl(): { url: string } {
    return { url: this.deps.github.installUrl() };
  }

  async authorizeUrl(ctx: AccountContext): Promise<{ url: string }> {
    requireOwner(ctx);
    const state = await signState(this.deps.proofKey, ctx);
    return { url: this.deps.github.authorizeUrl(state) };
  }

  // 利用者の認可の戻りから、導入の対象で利用者の届くリポジトリを候補にする（AC1）。利用者の token は使い捨てる
  async candidates(ctx: AccountContext, body: Record<string, unknown>) {
    requireOwner(ctx);
    if (!(await verifyState(this.deps.proofKey, body.state, ctx))) throw new InvalidRequestError("GitHub の確かめをやり直してください");
    if (typeof body.code !== "string" || body.code === "") throw new InvalidRequestError("GitHub の確かめをやり直してください");
    const userToken = await this.deps.github.exchangeCode(body.code);
    if (!userToken) throw new InvalidRequestError("GitHub の確かめをやり直してください");
    const repos = await this.deps.github.listReachableRepositories(userToken);
    const registered = new Set((await this.deps.table.listRegistrations(ctx.accountId)).map((r) => r.repositoryId));
    return {
      proof: await signProof(this.deps.proofKey, ctx, repos),
      candidates: repos.map((r) => ({
        repository_id: r.repositoryId,
        full_name: r.fullName,
        installation_id: r.installationId,
        registered: registered.has(r.repositoryId),
      })),
    };
  }

  // 登録。証明に無い ID と、直前の確かめで導入が届かないものは 403（I33）。同じ ID は 409（I32）
  async register(ctx: AccountContext, body: Record<string, unknown>) {
    requireOwner(ctx);
    const repos = await verifyProof(this.deps.proofKey, body.proof, ctx);
    if (!repos) throw new InvalidRequestError("GitHub の確かめをやり直してください");
    const repo = repos.find((r) => r.repositoryId === body.repository_id);
    if (!repo) throw new ForbiddenError();
    if (!(await this.deps.github.createInstallationToken(repo.installationId, repo.repositoryId))) throw new ForbiddenError();
    const registration: Registration = {
      accountId: ctx.accountId,
      repositoryId: repo.repositoryId,
      fullName: repo.fullName,
      installationId: repo.installationId,
      registeredBy: ctx.userId,
      registeredAt: (this.deps.now?.() ?? new Date()).toISOString(),
    };
    if (!(await this.deps.table.putRegistration(registration))) {
      throw new ConflictError("repository_exists", "このリポジトリは登録済みです");
    }
    return view(registration);
  }

  async list(ctx: AccountContext) {
    const regs = await this.deps.table.listRegistrations(ctx.accountId);
    const out = [];
    for (const r of regs) out.push(view(r, (await this.deps.table.getSlots(ctx.accountId, r.repositoryId)).repository));
    return { repositories: out };
  }

  async unregister(ctx: AccountContext, repositoryId: number): Promise<void> {
    requireOwner(ctx);
    const result = await this.deps.table.deleteRegistration(ctx.accountId, repositoryId);
    if (result === "not_found") throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
    if (result === "in_use") throw new ConflictError("repository_in_use", "終わっていないスプリントの実行があるため外せません");
  }
}
