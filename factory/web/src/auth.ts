import { OidcClient, WebStorageStateStore } from "oidc-client-ts";
import type { FactoryConfig } from "./config.ts";

export type IdentityProvider = "Google" | "GitHub";

export type LoginResult = { ok: true } | { ok: false; error: string };

export interface Auth {
  // Cognito の管理されたログインへ、IdP を指定して送る（IdP の選択の画面を飛ばす）
  login(idp: IdentityProvider): Promise<void>;
  // ログインの戻りの URL を token に換える。error つきの戻りでは token を保存しない
  completeLogin(url: string): Promise<LoginResult>;
  getToken(): string | null;
  clearToken(): void;
  // token を消してから Cognito のログアウトへ送る
  logout(): void;
}

export interface Navigator {
  assign(url: string): void;
}

const TOKEN_KEY = "ndf-factory.access_token";

// 認可コードと PKCE で token を得て、sessionStorage に置く（決定 10）
export function createAuth(
  config: FactoryConfig,
  storage: Storage = window.sessionStorage,
  navigator: Navigator = { assign: (url) => window.location.assign(url) },
): Auth {
  const authority = `https://${config.cognitoDomain}`;
  const client = new OidcClient({
    authority,
    client_id: config.cognitoClientId,
    redirect_uri: `${config.webUrl}/callback`,
    response_type: "code",
    scope: "openid email profile",
    metadata: {
      issuer: authority,
      authorization_endpoint: `${authority}/oauth2/authorize`,
      token_endpoint: `${authority}/oauth2/token`,
      end_session_endpoint: `${authority}/logout`,
    },
    stateStore: new WebStorageStateStore({ store: storage }),
  });

  return {
    async login(idp) {
      const request = await client.createSigninRequest({ extraQueryParams: { identity_provider: idp } });
      navigator.assign(request.url);
    },

    async completeLogin(url) {
      const error = new URL(url).searchParams.get("error");
      if (error) return { ok: false, error };
      try {
        const response = await client.processSigninResponse(url);
        if (!response.access_token) return { ok: false, error: "no_token" };
        storage.setItem(TOKEN_KEY, response.access_token);
        return { ok: true };
      } catch {
        return { ok: false, error: "login_failed" };
      }
    },

    getToken() {
      return storage.getItem(TOKEN_KEY);
    },

    clearToken() {
      storage.removeItem(TOKEN_KEY);
    },

    logout() {
      storage.removeItem(TOKEN_KEY);
      const url = new URL(`${authority}/logout`);
      url.searchParams.set("client_id", config.cognitoClientId);
      url.searchParams.set("logout_uri", `${config.webUrl}/login`);
      navigator.assign(url.toString());
    },
  };
}
