// 委任の管理（#2384 の F2・F3）。委任の資格情報の発行・一覧・取り消しと、値の照合。
// 値は ndfd_<資格情報の ID>_<秘密> の不透明な値で、要求のたびに表を強い整合で読み、照合の結果をためない（決定 8）
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import type { Caller, Idp } from "../accounts/model.ts";
import { ForbiddenError, InvalidRequestError, ServiceUnavailableError } from "../errors.ts";
import { claimOf, parseIdempotencyKey, type IdempotencyRef } from "../idempotency.ts";
import {
  CREDENTIAL_PREFIX,
  DELEGATION_SCOPES,
  LIST_LIMIT,
  MAX_CLIENT_NAME,
  MAX_EXPIRES_SECONDS,
  MIN_EXPIRES_SECONDS,
  delegationView,
  stateOf,
  type DelegationRecord,
  type DelegationScope,
} from "./model.ts";
import type { DelegationStore } from "./store.ts";

// RFC 4648 の base32 を小文字で、埋めの = を付けずに書く。16 バイトは 26 字
const BASE32 = "abcdefghijklmnopqrstuvwxyz234567";
function base32(bytes: Buffer): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += BASE32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += BASE32[(value << (5 - bits)) & 31];
  return out;
}

const VALUE = /^ndfd_([a-z2-7]{26})_([A-Za-z0-9_-]{43})$/;
const sha256 = (secret: string) => createHash("sha256").update(secret, "utf8").digest("hex");

// 照合が通った委任の資格情報。人の直の操作と同じ Caller を組むための値と、範囲を持つ
export interface VerifiedDelegation {
  userId: string;
  idp: Idp;
  credentialId: string;
  scope: DelegationScope;
}

export interface DelegationDeps {
  store: DelegationStore;
  now?: () => Date;
  random?: (n: number) => Buffer;
}

export class DelegationService {
  private readonly now: () => Date;
  private readonly random: (n: number) => Buffer;

  constructor(private readonly deps: DelegationDeps) {
    this.now = deps.now ?? (() => new Date());
    this.random = deps.random ?? randomBytes;
  }

  private iso(): string {
    return this.now().toISOString();
  }

  // 値の照合（E2・E10）。形が違う・無い・秘密が違う・期限切れ・取り消し済みはどれも null（呼ぶ側が 401 にする）
  async verify(value: string): Promise<VerifiedDelegation | null> {
    const m = VALUE.exec(value);
    if (!m) return null;
    let record: DelegationRecord | null;
    try {
      record = await this.deps.store.get(m[1]!);
    } catch {
      throw new ServiceUnavailableError("delegation_unavailable");
    }
    if (!record) return null;
    const given = Buffer.from(sha256(m[2]!), "hex");
    const saved = Buffer.from(record.secretHash, "hex");
    if (given.length !== saved.length || !timingSafeEqual(given, saved)) return null;
    if (stateOf(record, this.iso()) !== "active") return null;
    return { userId: record.userId, idp: record.idp, credentialId: record.credentialId, scope: record.scope };
  }

  // 発行（E1）。値は結果で 1 度だけ返す。同じ冪等の鍵の 2 回目は同じ資格情報を返し、値を null にする（I14）
  async issue(caller: Caller, element: Record<string, unknown>) {
    const clientName = element.client_name;
    if (typeof clientName !== "string" || clientName.trim() === "" || clientName.length > MAX_CLIENT_NAME) {
      throw new InvalidRequestError(`クライアントの名前は 1〜${MAX_CLIENT_NAME} 字にしてください`);
    }
    const scope = element.scope;
    if (!DELEGATION_SCOPES.includes(scope as DelegationScope)) throw new InvalidRequestError("範囲は read か read_write です");
    const seconds = element.expires_in_seconds;
    if (!Number.isInteger(seconds) || (seconds as number) < MIN_EXPIRES_SECONDS || (seconds as number) > MAX_EXPIRES_SECONDS) {
      throw new InvalidRequestError(`期限は ${MIN_EXPIRES_SECONDS}〜${MAX_EXPIRES_SECONDS} 秒にしてください`, "invalid_expires_in");
    }
    const ref: IdempotencyRef = { userId: caller.userId, operationId: "createDelegation", key: parseIdempotencyKey(element.idempotency_key) };
    const now = this.now();
    const replayed = await this.replay(ref, now.toISOString());
    if (replayed) return replayed;
    const credentialId = base32(this.random(16));
    const secret = this.random(32).toString("base64url");
    const record: DelegationRecord = {
      credentialId,
      userId: caller.userId,
      idp: caller.idp,
      clientName,
      scope: scope as DelegationScope,
      secretHash: sha256(secret),
      createdAt: now.toISOString(),
      expiresAt: new Date(now.getTime() + (seconds as number) * 1000).toISOString(),
      revokedAt: null,
    };
    const created = await this.deps.store.create(record, claimOf(ref, credentialId, record.createdAt));
    if (created === "duplicate") {
      const won = await this.replay(ref, now.toISOString());
      if (!won) throw new ServiceUnavailableError("delegation_unavailable");
      return won;
    }
    return { delegation: delegationView(record, record.createdAt), credential: `${CREDENTIAL_PREFIX}${credentialId}_${secret}` };
  }

  private async replay(ref: IdempotencyRef, now: string) {
    const id = await this.deps.store.getIdempotent(ref, now);
    const record = id ? await this.deps.store.get(id) : null;
    return record ? { delegation: delegationView(record, now), credential: null } : null;
  }

  // 自分が発行したものだけを、新しい順で返す
  async list(caller: Caller) {
    const now = this.iso();
    return (await this.deps.store.listByUser(caller.userId, LIST_LIMIT)).map((r) => delegationView(r, now));
  }

  // 取り消し（E9）。期限切れと取り消し済みは何も変えずに成功を返す。他人の資格情報と無い資格情報は同じ forbidden
  async revoke(caller: Caller, credentialId: unknown) {
    const record = typeof credentialId === "string" ? await this.deps.store.get(credentialId) : null;
    if (!record || record.userId !== caller.userId) throw new ForbiddenError();
    const now = this.iso();
    if (stateOf(record, now) === "active") {
      await this.deps.store.revoke(record.credentialId, now);
      return delegationView({ ...record, revokedAt: now }, now);
    }
    return delegationView(record, now);
  }
}
