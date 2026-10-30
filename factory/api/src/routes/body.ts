import type { Context } from "hono";
import { InvalidRequestError } from "../errors.ts";

export async function readJson(c: Context): Promise<Record<string, unknown>> {
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    throw new InvalidRequestError();
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) throw new InvalidRequestError();
  return body as Record<string, unknown>;
}
