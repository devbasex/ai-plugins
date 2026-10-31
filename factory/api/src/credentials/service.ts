// キーの持ち込み（F3）。値は Secrets Manager にだけ書き、応答は登録済みか・種類・更新の時刻・外部 ID だけ（I38）
import { randomUUID } from "node:crypto";
import { CreateSecretCommand, DeleteSecretCommand, PutSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import type { AccountContext } from "../accounts/model.ts";
import { InvalidRequestError, ServiceUnavailableError } from "../errors.ts";
import { requireOwner } from "../repositories/service.ts";
import type { CredentialKind } from "../runs/model.ts";
import type { RunsTable } from "../runs/table.ts";

export interface SecretsPort {
  create(name: string, value: string): Promise<string>;
  put(secretArn: string, value: string): Promise<void>;
  // 復元の期間を置かずに消す（決定 14）
  delete(secretArn: string): Promise<void>;
}

export class AwsSecrets implements SecretsPort {
  constructor(private readonly client = new SecretsManagerClient({})) {}
  async create(name: string, value: string) {
    const out = await this.client.send(new CreateSecretCommand({ Name: name, SecretString: value }));
    if (!out.ARN) throw new Error("置き場を作れません");
    return out.ARN;
  }
  async put(secretArn: string, value: string) {
    await this.client.send(new PutSecretValueCommand({ SecretId: secretArn, SecretString: value }));
  }
  async delete(secretArn: string) {
    await this.client.send(new DeleteSecretCommand({ SecretId: secretArn, ForceDeleteWithoutRecovery: true }));
  }
}

// 外部 ID はサービスがアカウントごとに作る（決定 10）
export const externalIdOf = (accountId: string) => `ndf-factory:${accountId}`;

const ROLE_ARN = /^arn:aws[a-z-]*:iam::\d{12}:role\/.+$/;
const REGION = /^[a-z]{2}(-[a-z]+)+-\d$/;

export interface CredentialView {
  registered: boolean;
  kind: CredentialKind | null;
  updated_at: string | null;
  external_id: string;
}

// 入力を置き場へ書く値にする。形が違えば 400
export function credentialSecretValue(accountId: string, body: Record<string, unknown>): { kind: CredentialKind; value: string } {
  if (body.kind === "anthropic_api_key") {
    const key = body.api_key;
    if (typeof key !== "string" || key.length < 1 || key.length > 512) throw new InvalidRequestError("API キーを入力してください");
    return { kind: "anthropic_api_key", value: JSON.stringify({ kind: "anthropic_api_key", api_key: key }) };
  }
  if (body.kind === "aws_role") {
    const { role_arn: roleArn, region } = body;
    if (typeof roleArn !== "string" || roleArn.length > 2048 || !ROLE_ARN.test(roleArn)) {
      throw new InvalidRequestError("IAM のロールの ARN の形で入力してください");
    }
    if (typeof region !== "string" || region.length > 32 || !REGION.test(region)) throw new InvalidRequestError("AWS の地域の名前で入力してください");
    return { kind: "aws_role", value: JSON.stringify({ kind: "aws_role", role_arn: roleArn, region, external_id: externalIdOf(accountId) }) };
  }
  throw new InvalidRequestError("種類は anthropic_api_key か aws_role です");
}

export class CredentialService {
  constructor(
    private readonly table: RunsTable,
    private readonly secrets: SecretsPort,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async get(ctx: AccountContext): Promise<CredentialView> {
    requireOwner(ctx);
    const row = await this.table.getCredential(ctx.accountId);
    return { registered: row !== null, kind: row?.kind ?? null, updated_at: row?.updatedAt ?? null, external_id: externalIdOf(ctx.accountId) };
  }

  async put(ctx: AccountContext, body: Record<string, unknown>): Promise<CredentialView> {
    requireOwner(ctx);
    const { kind, value } = credentialSecretValue(ctx.accountId, body);
    const existing = await this.table.getCredential(ctx.accountId);
    const updatedAt = this.now().toISOString();
    try {
      let secretArn: string;
      if (existing) {
        await this.secrets.put(existing.secretArn, value);
        secretArn = existing.secretArn;
      } else {
        // 名前にアカウントの ID を入れない
        secretArn = await this.secrets.create(`ndf-factory/model-credentials/${randomUUID()}`, value);
      }
      await this.table.putCredential({ accountId: ctx.accountId, kind, secretArn, updatedAt });
    } catch {
      throw new ServiceUnavailableError("credential_unavailable");
    }
    return { registered: true, kind, updated_at: updatedAt, external_id: externalIdOf(ctx.accountId) };
  }

  async delete(ctx: AccountContext): Promise<void> {
    requireOwner(ctx);
    const row = await this.table.getCredential(ctx.accountId);
    if (!row) return;
    try {
      await this.secrets.delete(row.secretArn);
      await this.table.deleteCredential(ctx.accountId);
    } catch {
      throw new ServiceUnavailableError("credential_unavailable");
    }
  }
}
