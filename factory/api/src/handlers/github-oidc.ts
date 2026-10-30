import { handle } from "hono/aws-lambda";
import { Hono } from "hono";
import { createGithubOidcApp } from "../github-oidc/app.ts";
import { loadSigningKeys, type SigningKeys } from "../github-oidc/keys.ts";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}

// 署名の鍵は起動のたびに 1 回だけ読む
let keys: Promise<SigningKeys> | null = null;
let app: Hono | null = null;

function getApp(): Hono {
  app ??= createGithubOidcApp(
    {
      issuer: requireEnv("GITHUB_OIDC_ISSUER"),
      cognitoDomain: requireEnv("COGNITO_DOMAIN"),
      githubClientId: requireEnv("GITHUB_OAUTH_CLIENT_ID"),
    },
    {
      keys: () => {
        keys ??= loadSigningKeys(requireEnv("GITHUB_OIDC_SIGNING_KEY_SECRET_ARN")).catch((err: unknown) => {
          keys = null;
          throw err;
        });
        return keys;
      },
    },
  );
  return app;
}

// API Gateway の HTTP API の /oidc/github/{proxy+} から、認可なしで呼ばれる Lambda の handler
const lazy = new Hono();
lazy.all("*", (c) => getApp().fetch(c.req.raw));
export const handler = handle(lazy);
