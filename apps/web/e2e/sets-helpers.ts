import { createHash, randomUUID } from "node:crypto";
import { expect, type APIResponse, type Page } from "@playwright/test";

/** The seeded test accounts of the API in fake mode (packages/research-api/tests/fixtures/seed-users.json). */
export const PEOPLE = {
  ana: { username: "ana", password: "test-password-ana-01" },
  beto: { username: "beto", password: "test-password-beto-02" },
  dani: { username: "dani", password: "test-password-dani-03" },
} as const;

export type Person = keyof typeof PEOPLE;

/** The part of a version page that the tests read. */
export interface VersionAnswer {
  version: { status: string; md5: string; etag: string };
  items: { id: string }[];
}

const ORIGIN = "http://127.0.0.1:4173";

/** Signs in through the login screen against the real API and waits for the app. */
export async function signInAs(page: Page, who: Person, next = "/sets") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Username").fill(PEOPLE[who].username);
  await page.getByLabel("Password", { exact: true }).fill(PEOPLE[who].password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[/?]/g, "\\$&")}$`));
}

export function md5(text: string | Buffer): string {
  return createHash("md5").update(text).digest("hex");
}

async function csrf(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === "re_csrf")?.value;
  if (!token) throw new Error("not signed in: no CSRF cookie");
  return token;
}

async function must(response: APIResponse, status: number): Promise<APIResponse> {
  if (response.status() !== status) {
    throw new Error(`${response.url()}: ${response.status()} ${await response.text()}`);
  }
  return response;
}

/** The API as the signed-in person of this page, for what a test must set up or check. */
export function apiOf(page: Page) {
  const write = async () => ({
    "x-csrf-token": await csrf(page),
    origin: ORIGIN,
  });
  const base = "/api/v1";
  return {
    async get(path: string): Promise<APIResponse> {
      return page.request.get(`${base}${path}`);
    },
    async json<T = any>(path: string): Promise<T> {
      return (await must(await page.request.get(`${base}${path}`), 200)).json() as Promise<T>;
    },
    async send(
      method: "POST" | "PUT" | "DELETE",
      path: string,
      options: { data?: unknown; ifMatch?: string; raw?: string } = {},
    ) {
      return page.request.fetch(`${base}${path}`, {
        method,
        headers: {
          ...(await write()),
          ...(options.ifMatch ? { "if-match": options.ifMatch } : {}),
          ...(options.raw !== undefined ? { "content-type": "application/octet-stream" } : {}),
        },
        ...(options.raw !== undefined
          ? { data: options.raw }
          : options.data !== undefined
            ? { data: options.data }
            : {}),
      });
    },
    async etag(name: string, version: string): Promise<string> {
      const page_ = await this.json<{ version: { etag: string } }>(
        `/sets/${name}/versions/${version}?limit=1`,
      );
      return page_.version.etag;
    },
    /** One decision, recorded against the draft as it is now. */
    async review(
      name: string,
      version: string,
      itemId: string,
      verdict: "accept" | "reject" | "edit",
      extra: { note?: string; edit?: object } = {},
    ) {
      const etag = await this.etag(name, version);
      return must(
        await this.send("POST", `/sets/${name}/versions/${version}/reviews`, {
          ifMatch: etag,
          data: { item_id: itemId, verdict, ...extra },
        }),
        201,
      );
    },
  };
}

/** A line-per-item qset/1 file made from the demo set, with `count` items if asked. */
export async function demoFile(page: Page, count?: number): Promise<string> {
  const text = await (
    await must(await page.request.get("/api/v1/sets/demo-papers/versions/v2/export"), 200)
  ).text();
  if (count === undefined) return text;
  const [header, first] = text.split("\n");
  const template = JSON.parse(first!) as Record<string, unknown>;
  const items = Array.from({ length: count }, (_, index) =>
    JSON.stringify({ ...template, id: `B-${String(index + 1).padStart(3, "0")}` }),
  );
  return `${header}\n${items.join("\n")}\n`;
}

/** A private copy of the demo draft, owned by the person signed in on `page`, with no decisions. */
export async function seedDraft(page: Page, options: { count?: number } = {}) {
  const name = `e2e-${randomUUID().slice(0, 8)}`;
  const api = apiOf(page);
  const body = await demoFile(page, options.count);
  const response = await must(
    await api.send("POST", `/sets/import?name=${name}`, { raw: body }),
    201,
  );
  const created = (await response.json()) as { version: { md5: string; items: number } };
  return { name, version: "v1", md5: created.version.md5, items: created.version.items, api };
}
