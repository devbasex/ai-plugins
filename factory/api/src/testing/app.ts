import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { createApiApp, type LogEntry } from "../handlers/api.ts";
import { createTokenKit, type TokenKit } from "./tokens.ts";

// テストで本文を読みやすくするため、json() の型を any にした応答
export type TestResponse = Omit<Response, "json"> & { json(): Promise<any> };

export interface TestApi {
  app: ReturnType<typeof createApiApp>;
  store: MemoryAccountStore;
  service: AccountService;
  tokens: TokenKit;
  logs: LogEntry[];
  call(method: string, path: string, opts?: { user?: string; idp?: "Google" | "GitHub"; body?: unknown; authorization?: string }): Promise<TestResponse>;
}

export async function createTestApi(): Promise<TestApi> {
  const store = new MemoryAccountStore();
  let n = 0;
  const service = new AccountService({ store, newId: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` });
  const tokens = await createTokenKit();
  const logs: LogEntry[] = [];
  const app = createApiApp(() => ({ verifier: tokens.verifier, accounts: service }), (e) => logs.push(e));
  return {
    app,
    store,
    service,
    tokens,
    logs,
    async call(method, path, opts = {}) {
      const headers: Record<string, string> = {};
      if (opts.authorization !== undefined) headers.Authorization = opts.authorization;
      else if (opts.user) headers.Authorization = await tokens.bearer(opts.user, opts.idp);
      if (opts.body !== undefined) headers["Content-Type"] = "application/json";
      return (await app.request(path, {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      })) as TestResponse;
    },
  };
}
