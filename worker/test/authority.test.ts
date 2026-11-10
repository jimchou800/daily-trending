import { env, SELF, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Env, PublicationAuthority } from "../src/index";
declare module "cloudflare:test" {
  // The testing package requires this named interface augmentation to type bindings.
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface ProvidedEnv extends Env {}
}
const headers = {
  Authorization: "Bearer local-test-secret",
  "X-Installation-ID": "test-installation",
  "Content-Type": "application/json",
};
async function call(route: string, body?: unknown, method = "POST") {
  return SELF.fetch("https://example.com/_automation/" + route, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
async function install() {
  expect(
    (await call("install", { bootstrap_token: "local-bootstrap" })).status,
  ).toBe(200);
}
beforeEach(async () => {
  vi.restoreAllMocks();
  const stub = env.PUBLICATION_AUTHORITY.get(
    env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
  );
  await runInDurableObject<PublicationAuthority, void>(
    stub,
    async (_instance, state) => {
      await state.storage.deleteAll();
    },
  );
});
describe("authority bootstrap boundary", () => {
  it("fails closed when configured storage has never been explicitly initialized", async () => {
    expect((await SELF.fetch("https://example.com/")).status).toBe(503);
  });
  it("does not expose automation or artifacts publicly", async () => {
    expect(
      (await SELF.fetch("https://example.com/_automation/state")).status,
    ).toBe(401);
  });
});
describe("durable admission", () => {
  beforeEach(async () => {
    await install();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T00:15:00Z"));
  });
  it("begins idempotently and caps concurrent runs at three", async () => {
    const first = await call("begin", { period: "2026-11-10", run_id: "a" });
    expect(first.status).toBe(200);
    expect(
      await (await call("begin", { period: "2026-11-10", run_id: "a" })).json(),
    ).toEqual(await first.json());
    const results = await Promise.all(
      ["b", "c", "d"].map((run_id) =>
        call("begin", { period: "2026-11-10", run_id }),
      ),
    );
    expect(results.map((r) => r.status).sort()).toEqual([200, 200, 409]);
  });
  it("settles unknown provider usage at the full reserved bound without refund or monthly blockade", async () => {
    const { fence } = (await (
      await call("begin", { period: "2026-11-10", run_id: "a" })
    ).json()) as { fence: number };
    const args = {
      period: "2026-11-10",
      fence,
      amount_micros: 480,
      input_tokens: 1,
      output_tokens: 1,
    };
    await call("reserve", { ...args, key: "unknown" });
    expect(
      (
        await call("usage", {
          period: args.period,
          key: "unknown",
          usage_status: "unavailable",
          actual_micros: 48,
        })
      ).status,
    ).toBe(400);
    expect(
      (
        await call("usage", {
          period: args.period,
          key: "unknown",
          usage_status: "unavailable",
          actual_micros: 480,
        })
      ).status,
    ).toBe(200);
    expect((await call("reserve", { ...args, key: "retry" })).status).toBe(200);
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({
      month: { reserved_micros: 960, accounting_blocked: false },
      reservations: {
        unknown: { actual_micros: 480, usage_status: "unavailable" },
      },
    });
  });
  it("rejects old dates and cutoff starts", async () => {
    expect(
      (await call("begin", { period: "2026-11-09", run_id: "a" })).status,
    ).toBe(409);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T01:00:00Z"));
    expect(
      (await call("begin", { period: "2026-11-10", run_id: "a" })).status,
    ).toBe(409);
  });
  it("reserves before dispatch, conserves monthly cap, rejects stale fencing", async () => {
    const begin = (await (
      await call("begin", { period: "2026-11-10", run_id: "a" })
    ).json()) as { fence: number };
    const args = {
      period: "2026-11-10",
      fence: begin.fence,
      key: "call1",
      amount_micros: 20_000_000,
      input_tokens: 100,
      output_tokens: 100,
    };
    expect(await (await call("reserve", args)).json()).toMatchObject({
      dispatch: true,
    });
    expect(await (await call("reserve", args)).json()).toMatchObject({
      dispatch: false,
    });
    expect((await call("reserve", { ...args, key: "call2" })).status).toBe(409);
    await call("begin", { period: "2026-11-10", run_id: "b" });
    expect(
      (await call("reserve", { ...args, key: "call3", amount_micros: 480 }))
        .status,
    ).toBe(409);
  });
  it("pins first snapshot and returns private state only with authentication", async () => {
    const { fence } = (await (
      await call("begin", { period: "2026-11-10", run_id: "a" })
    ).json()) as { fence: number };
    const artifact = {
      period: "2026-11-10",
      fence,
      key: "snapshot",
      value: {
        complete: true,
        entries: [],
        collected_at: "2026-11-10T00:10:00Z",
        source: "https://github.com/trending?since=daily",
      },
    };
    expect((await call("artifact", artifact, "PUT")).status).toBe(200);
    expect((await call("artifact", artifact, "PUT")).status).toBe(200);
    expect(
      (
        await call(
          "artifact",
          { ...artifact, value: { ...artifact.value, entries: [{}] } },
          "PUT",
        )
      ).status,
    ).toBe(409);
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ artifacts: { snapshot: artifact.value } });
    expect(
      (await SELF.fetch("https://example.com/artifacts/snapshot")).status,
    ).toBe(404);
  });
});

const repository = "example/alpha";
const snapshot = {
  complete: true,
  source: "https://github.com/trending?since=daily",
  collected_at: "2026-11-10T00:10:00Z",
  entries: [
    {
      rank: 1,
      repository,
      repository_url: "https://github.com/" + repository,
      stars_today: 10,
      stars_total: 100,
    },
  ],
};
const entry = {
  repository,
  repository_url: "https://github.com/" + repository,
  rank: 1,
  ai_relevance_evidence: "README describes AI core functionality",
  project_type: "工具",
  introduction: "介绍",
  problem_solved: "问题",
  audience: "开发者",
  characteristics: "特点",
  heat: { stars_today: 10, stars_total: 100 },
  sources: ["https://github.com/example/alpha/blob/main/README.md"],
  reason_has_evidence: false,
  reason: null,
};
const report = {
  contract_version: "1.0",
  report_date: "2026-11-10",
  timezone: "Asia/Shanghai",
  collected_at: snapshot.collected_at,
  published_at: null,
  source: snapshot.source,
  scope: "GitHub Trending Daily（全部编程语言）",
  status: "sample_preview",
  entry_count: 1,
  entries: [entry],
};
async function prepare(selection?: Record<string, unknown>) {
  await install();
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T00:15:00Z"));
  const { fence } = (await (
    await call("begin", { period: "2026-11-10", run_id: "a" })
  ).json()) as { fence: number };
  await call(
    "artifact",
    { period: "2026-11-10", fence, key: "snapshot", value: snapshot },
    "PUT",
  );
  await call(
    "artifact",
    {
      period: "2026-11-10",
      fence,
      key: "selection",
      value: selection ?? {
        completed: true,
        eligible_count: 1,
        decisions: [
          {
            repository,
            is_ai: true,
            ai_relevance_evidence: "README",
            outcome: "selected",
          },
        ],
      },
    },
    "PUT",
  );
  return fence;
}
describe("atomic report publication", () => {
  it("publishes only the bound one-off report after cutoff with truthful time and successful inclusion history", async () => {
    await prepare();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T04:00:00Z"));
    const lateReport = { ...report, collected_at: "2026-11-10T02:05:00Z" };
    const hash = Array.from(
      new Uint8Array(
        await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(JSON.stringify(lateReport)),
        ),
      ),
      (v) => v.toString(16).padStart(2, "0"),
    ).join("");
    env.MAINTENANCE_PUBLICATION_TOKEN = "one-off-secret";
    env.MAINTENANCE_PUBLICATION_DATE = "2026-11-10";
    env.MAINTENANCE_REPORT_HASH = hash;
    const stub = env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    );
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (instance) => {
        // Test-only access configures the already-created DO instance's bindings.
        Object.assign((instance as unknown as { env: Env }).env, {
          MAINTENANCE_PUBLICATION_TOKEN: "one-off-secret",
          MAINTENANCE_PUBLICATION_DATE: "2026-11-10",
          MAINTENANCE_REPORT_HASH: hash,
        });
      },
    );
    const state = (await (
      await call("state?period=2026-11-10", undefined, "GET")
    ).json()) as { artifacts: { selection: unknown } };
    const body = {
      period: "2026-11-10",
      report: lateReport,
      snapshot: { ...snapshot, collected_at: lateReport.collected_at },
      selection: state.artifacts.selection,
    };
    const send = () =>
      SELF.fetch("https://example.com/_automation/maintenance-publish", {
        method: "POST",
        headers: { ...headers, "X-Maintenance-Token": "one-off-secret" },
        body: JSON.stringify(body),
      });
    expect(
      (
        await SELF.fetch(
          "https://example.com/_automation/maintenance-publish",
          { method: "POST", headers, body: JSON.stringify(body) },
        )
      ).status,
    ).toBe(403);
    body.report = { ...lateReport, scope: "altered" };
    expect((await send()).status).toBe(409);
    body.report = lateReport;
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-11T04:00:00Z"));
    expect((await send()).status).toBe(409);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T04:00:00Z"));
    const correctSelection = body.selection;
    body.selection = { completed: false };
    expect((await send()).status).toBe(400);
    body.selection = correctSelection;
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, storageState) => {
        const sync = storageState.storage.sync.bind(storageState.storage);
        let writes = 0;
        vi.spyOn(storageState.storage, "sync").mockImplementation(async () => {
          await sync();
          if (++writes === 2)
            vi.spyOn(Date, "now").mockReturnValue(
              Date.parse("2026-11-10T16:00:00Z"),
            );
        });
      },
    );
    expect((await send()).status).toBe(409);
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ history: [], publication: null });
    vi.restoreAllMocks();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T04:00:00Z"));
    const response = await send();
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      report: {
        published_at: "2026-11-10T04:00:00.000Z",
        maintenance_publication: { report_hash: hash },
      },
      idempotent: false,
    });
    expect(await (await send()).json()).toMatchObject({ idempotent: true });
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "应维护者要求补发，未满足09:00准点发布",
    );
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ history: [{ report_date: "2026-11-10" }] });
  });
  it("durably records a locally stopped monetary call only when authoritative remaining funds cannot cover its bounded estimate", async () => {
    const fence = await prepare();
    const args = {
      period: "2026-11-10",
      fence,
      input_tokens: 100,
      output_tokens: 100,
      limit_micros: 30_000_000,
    };
    expect(await (await call("budget-notice", args)).json()).toEqual({
      generation_exhausted: false,
    });
    await call("reserve", { ...args, key: "large", amount_micros: 29_999_900 });
    await call("usage", {
      period: args.period,
      key: "large",
      actual_micros: 4800,
    });
    expect(
      await (
        await call("budget-notice", { ...args, output_tokens: 8001 })
      ).json(),
    ).toEqual({ error: "invalid_request" });
    expect(await (await call("budget-notice", args)).json()).toEqual({
      generation_exhausted: true,
    });
    const state = await (
      await call("state?period=2026-11-10", undefined, "GET")
    ).json();
    expect(state).toMatchObject({
      month: { reserved_micros: 29_999_900, generation_exhausted: true },
    });
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      'data-exhausted-month="2026-11"',
    );
  });
  it.each([
    { accounting_blocked: true, pending: [], error: "accounting_blocked" },
    {
      accounting_blocked: false,
      pending: ["unsettled"],
      error: "accounting_pending",
    },
  ])(
    "does not reinterpret unresolved accounting as a monthly budget notice: $error",
    async ({ error, ...blockers }) => {
      const fence = await prepare();
      await storeRecord("month:2026-11", {
        reserved_micros: 29_999_999,
        ...blockers,
      });
      expect(
        await (
          await call("budget-notice", {
            period: "2026-11-10",
            fence,
            input_tokens: 1,
            output_tokens: 1,
            limit_micros: 30_000_000,
          })
        ).json(),
      ).toEqual({ error });
      expect(
        await (await SELF.fetch("https://example.com/")).text(),
      ).not.toContain('data-exhausted-month="2026-11"');
    },
  );
  it("serves a homepage-only static status script without exposing private state", async () => {
    await prepare();
    const home = await SELF.fetch("https://example.com/");
    expect(home.headers.get("Content-Security-Policy")).toContain(
      "script-src 'self'",
    );
    expect(await home.text()).toContain(
      '<script src="/home-status.js" defer></script>',
    );
    const script = await SELF.fetch("https://example.com/home-status.js");
    expect(script.status).toBe(200);
    expect(script.headers.get("Content-Type")).toContain("javascript");
    expect(await script.text()).toContain("window.location.reload()");
    expect(
      await (await SELF.fetch("https://example.com/archive/")).text(),
    ).not.toContain("home-status.js");
  });
  it("shows a truthful exhausted-month hint after an unaffordable reservation while keeping old issues readable", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    await call("publish", { period: "2026-11-10", fence, hash });
    const args = {
      period: "2026-11-10",
      fence,
      input_tokens: 1,
      output_tokens: 1,
    };
    await call("reserve", { ...args, key: "large", amount_micros: 29_999_900 });
    await call("usage", {
      period: args.period,
      key: "large",
      actual_micros: 48,
    });
    expect(
      await (await SELF.fetch("https://example.com/")).text(),
    ).not.toContain('data-exhausted-month="2026-11"');
    expect(
      await (
        await call("reserve", { ...args, key: "next", amount_micros: 480 })
      ).json(),
    ).toEqual({ error: "budget_exhausted" });
    const home = await (await SELF.fetch("https://example.com/")).text();
    expect(home).toContain('data-exhausted-month="2026-11"');
    expect(home).toContain("当月生成额度已用尽");
    expect(home).toContain("example/alpha");
    const archive = await SELF.fetch("https://example.com/archive/2026-11-10/");
    expect(archive.status).toBe(200);
    expect(await archive.text()).not.toContain("当月生成额度已用尽");
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-12-01T01:00:00Z"));
    expect(
      await (await SELF.fetch("https://example.com/")).text(),
    ).not.toContain('data-exhausted-month="2026-11"');
  });
  it("accepts the canonical explicit unknown reason text", async () => {
    const fence = await prepare();
    expect(
      (
        await call("stage", {
          period: "2026-11-10",
          fence,
          report: {
            ...report,
            entries: [{ ...entry, reason: "暂未找到明确原因" }],
          },
        })
      ).status,
    ).toBe(200);
  });
  it.each([
    {
      is_ai: null,
      ai_relevance_evidence: "",
      outcome: "excluded",
      reason: "unknown_relevance",
      expected: 200,
    },
    {
      is_ai: false,
      ai_relevance_evidence: "",
      outcome: "excluded",
      reason: "not_ai",
      expected: 400,
    },
    {
      is_ai: true,
      ai_relevance_evidence: "",
      outcome: "selected",
      expected: 400,
    },
    {
      is_ai: null,
      ai_relevance_evidence: "",
      outcome: "selected",
      reason: "unknown_relevance",
      expected: 400,
    },
    {
      is_ai: null,
      ai_relevance_evidence: "",
      outcome: "excluded",
      reason: "not_ai",
      expected: 400,
    },
  ])(
    "validates unknown exclusions separately from supported verdicts: $is_ai/$outcome/$reason",
    async ({ expected, ...decision }) => {
      const fence = await prepare({
        completed: true,
        eligible_count: 0,
        decisions: [{ repository, ...decision }],
      });
      expect(
        (
          await call("stage", {
            period: "2026-11-10",
            fence,
            report: { ...report, entries: [], entry_count: 0 },
          })
        ).status,
      ).toBe(expected);
    },
  );
  it("keeps staged blobs private then publishes one immutable canonical date and history event", async () => {
    const fence = await prepare();
    const staged = await call("stage", { period: "2026-11-10", fence, report });
    expect(staged.status).toBe(200);
    const { hash } = (await staged.json()) as { hash: string };
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "尚未发布",
    );
    expect((await SELF.fetch("https://example.com/blobs/" + hash)).status).toBe(
      404,
    );
    const published = await call("publish", {
      period: "2026-11-10",
      fence,
      hash,
    });
    expect(published.status).toBe(200);
    const canonical = await published.json();
    expect(canonical).toMatchObject({
      idempotent: false,
      report: { published_at: "2026-11-10T00:15:00.000Z" },
    });
    expect(
      await (
        await call("publish", { period: "2026-11-10", fence, hash })
      ).json(),
    ).toMatchObject({ idempotent: true });
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ history: [{ report_date: "2026-11-10" }] });
    const html = await (
      await SELF.fetch("https://example.com/archive/2026-11-10/")
    ).text();
    expect(html).toContain("暂未找到明确原因");
    expect(html).toContain("example/alpha");
  });
  it("rejects forged metrics, sources and unfinished generation", async () => {
    const fence = await prepare();
    for (const bad of [
      {
        ...report,
        entries: [{ ...entry, heat: { stars_today: 999, stars_total: 100 } }],
      },
      { ...report, entries: [{ ...entry, sources: ["https://evil.com/a"] }] },
      { ...report, entries: [], entry_count: 0 },
    ]) {
      expect(
        (await call("stage", { period: "2026-11-10", fence, report: bad }))
          .status,
      ).toBe(400);
    }
  });
  it("derives today failure without a generation task and keeps archive free of failure banners", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    await call("publish", { period: "2026-11-10", fence, hash });
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-11T01:00:00Z"));
    const home = await SELF.fetch("https://example.com/");
    expect(home.headers.get("Cache-Control")).toBe("no-store");
    expect(await home.text()).toContain("今日日报生成失败");
    expect(
      await (
        await SELF.fetch("https://example.com/archive/2026-11-10/")
      ).text(),
    ).not.toContain("今日日报生成失败");
    expect(
      (await call("publish", { period: "2026-11-09", fence, hash })).status,
    ).toBe(409);
  });
  it("correction preserves publication time and original history without changing selections", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    await call("publish", { period: "2026-11-10", fence, hash });
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-11T02:00:00Z"));
    const corrected = await call("correct", {
      period: "2026-11-10",
      report: { ...report, entries: [{ ...entry, introduction: "更正介绍" }] },
      correction_note: "修正事实",
    });
    expect(corrected.status).toBe(200);
    expect(await corrected.json()).toMatchObject({
      report: {
        published_at: "2026-11-10T00:15:00.000Z",
        correction_note: "修正事实",
      },
    });
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({
      history: [{ entries: [{ introduction: "介绍" }] }],
      publication: { entries: [{ introduction: "更正介绍" }] },
    });
    expect(
      (
        await call("correct", {
          period: "2026-11-09",
          report,
          correction_note: "missing",
        })
      ).status,
    ).toBe(409);
  });
  it("persists usage underestimation block across fresh runners and allows old public reads", async () => {
    const fence = await prepare();
    await call("reserve", {
      period: "2026-11-10",
      fence,
      key: "r",
      amount_micros: 480,
      input_tokens: 10,
      output_tokens: 10,
    });
    expect(
      await (
        await call("usage", {
          period: "2026-11-10",
          key: "r",
          actual_micros: 481,
        })
      ).json(),
    ).toEqual({ accounting_blocked: true });
    expect(
      (
        await call("reserve", {
          period: "2026-11-10",
          fence,
          key: "r2",
          amount_micros: 480,
          input_tokens: 10,
          output_tokens: 10,
        })
      ).status,
    ).toBe(409);
    expect((await SELF.fetch("https://example.com/")).status).toBe(200);
    expect(
      await (await SELF.fetch("https://example.com/")).text(),
    ).not.toContain('data-exhausted-month="2026-11"');
  });
});
async function storeRecord(key: string, value: unknown) {
  const stub = env.PUBLICATION_AUTHORITY.get(
    env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
  );
  await runInDurableObject<PublicationAuthority, void>(
    stub,
    async (_instance, state) => {
      state.storage.sql.exec(
        "INSERT OR REPLACE INTO records(key,value) VALUES (?,?)",
        key,
        JSON.stringify(value),
      );
      await state.storage.sync();
    },
  );
}
describe("cutoff, crash recovery and ledger integrity", () => {
  it("rolls back tentative publication when sync crosses the cutoff", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    const stub = env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    );
    let count = 0;
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, state) => {
        const sync = state.storage.sync.bind(state.storage);
        vi.spyOn(state.storage, "sync").mockImplementation(async () => {
          await sync();
          if (++count === 2)
            vi.spyOn(Date, "now").mockReturnValue(
              Date.parse("2026-11-10T01:00:00Z"),
            );
        });
      },
    );
    expect(
      (await call("publish", { period: "2026-11-10", fence, hash })).status,
    ).toBe(409);
    const state = await (
      await call("state?period=2026-11-10", undefined, "GET")
    ).json();
    expect(state).toMatchObject({ history: [], publication: null });
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "今日日报生成失败",
    );
    expect(
      (await SELF.fetch("https://example.com/archive/2026-11-10/")).status,
    ).toBe(404);
  });
  it("reconciles crashed intents before any public read even after the deadline", async () => {
    await prepare();
    await storeRecord("intent", { period: "2026-11-10" });
    await storeRecord("head", "2026-11-10");
    await storeRecord("issue:2026-11-10", {
      hash: "tentative",
      published_at: "2026-11-10T00:20:00Z",
    });
    await storeRecord("inclusion:2026-11-10", report);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T01:01:00Z"));
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "尚未发布",
    );
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ history: [], publication: null });
  });
  it("rejects publications inside safety margin and unfinished stages remain private", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T00:59:56Z"));
    expect(
      (await call("publish", { period: "2026-11-10", fence, hash })).status,
    ).toBe(409);
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "尚未发布",
    );
  });
  it("fails closed on deleted monthly state and malformed identity state", async () => {
    await prepare();
    const stub = env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    );
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, state) => {
        state.storage.sql.exec("DELETE FROM records WHERE key='month:2026-11'");
      },
    );
    expect(
      (await call("state?period=2026-11-10", undefined, "GET")).status,
    ).toBe(503);
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, state) => {
        await state.storage.put("installation", "wrong");
      },
    );
    expect((await SELF.fetch("https://example.com/")).status).toBe(503);
  });
  it("blocks paid admission while a previous unknown dispatch lacks accounted usage", async () => {
    const fence = await prepare(),
      args = {
        period: "2026-11-10",
        fence,
        amount_micros: 480,
        input_tokens: 1,
        output_tokens: 1,
      };
    expect((await call("reserve", { ...args, key: "unknown" })).status).toBe(
      200,
    );
    expect(
      await (await call("reserve", { ...args, key: "second" })).json(),
    ).toEqual({ error: "accounting_pending" });
    await call("usage", {
      period: "2026-11-10",
      key: "unknown",
      actual_micros: 48,
      accounting_blocked: true,
    });
    expect(
      await (await call("reserve", { ...args, key: "second" })).json(),
    ).toEqual({ error: "accounting_blocked" });
  });
  it("uses shared calendar-month cap across separate days and restores a new month budget", async () => {
    const fence = await prepare();
    await call("reserve", {
      period: "2026-11-10",
      fence,
      key: "a",
      amount_micros: 30_000_000,
      input_tokens: 1,
      output_tokens: 1,
    });
    await call("usage", { period: "2026-11-10", key: "a", actual_micros: 48 });
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-11T00:15:00Z"));
    const next = (await (
      await call("begin", { period: "2026-11-11", run_id: "b" })
    ).json()) as { fence: number };
    expect(
      await (
        await call("reserve", {
          period: "2026-11-11",
          fence: next.fence,
          key: "b",
          amount_micros: 480,
          input_tokens: 1,
          output_tokens: 1,
        })
      ).json(),
    ).toEqual({ error: "budget_exhausted" });
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-12-01T00:15:00Z"));
    await call("begin", { period: "2026-12-01", run_id: "a" });
    expect(
      (
        await call("reserve", {
          period: "2026-12-01",
          fence: 1,
          key: "c",
          amount_micros: 480,
          input_tokens: 1,
          output_tokens: 1,
        })
      ).status,
    ).toBe(200);
  });
  it("publishes true zero and missing metrics honestly with escaped facts", async () => {
    await install();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T00:15:00Z"));
    const period = "2026-11-10",
      fence = 1;
    await call("begin", { period, run_id: "a" });
    await call(
      "artifact",
      { period, fence, key: "snapshot", value: { ...snapshot, entries: [] } },
      "PUT",
    );
    await call(
      "artifact",
      {
        period,
        fence,
        key: "selection",
        value: { completed: true, eligible_count: 0, decisions: [] },
      },
      "PUT",
    );
    const { hash } = (await (
      await call("stage", {
        period,
        fence,
        report: { ...report, entries: [], entry_count: 0 },
      })
    ).json()) as { hash: string };
    expect((await call("publish", { period, fence, hash })).status).toBe(200);
    expect(await (await SELF.fetch("https://example.com/")).text()).toContain(
      "今日无符合收录条件的项目",
    );
  });
  it("does not let a first-day missing task look successful", async () => {
    await install();
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-10T01:00:00Z"));
    const text = await (await SELF.fetch("https://example.com/")).text();
    expect(text).toContain("尚未发布");
    expect(text).toContain("今日日报生成失败");
    expect(
      await (await SELF.fetch("https://example.com/archive/")).text(),
    ).not.toContain("今日日报生成失败");
  });
  // Forty real SQLite reservation/settlement durability cycles can exceed the
  // default five seconds on Windows under filesystem load; retain every cycle.
  it("limits calls even with cheap settled transport outcomes", async () => {
    const fence = await prepare();
    for (let i = 0; i < 40; i++) {
      expect(
        (
          await call("reserve", {
            period: "2026-11-10",
            fence,
            key: String(i),
            amount_micros: 480,
            input_tokens: 1,
            output_tokens: 1,
          })
        ).status,
      ).toBe(200);
      await call("usage", {
        period: "2026-11-10",
        key: String(i),
        actual_micros: 48,
      });
    }
    expect(
      await (
        await call("reserve", {
          period: "2026-11-10",
          fence,
          key: "41",
          amount_micros: 480,
          input_tokens: 1,
          output_tokens: 1,
        })
      ).json(),
    ).toEqual({ error: "call_limit" });
  }, 15000);
});
async function stageDay(
  period: string,
  stars: number | null,
  extra: Record<string, unknown> = {},
  releases: unknown[] = [],
) {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse(period + "T00:15:00Z"));
  const args = { period, fence: 1 };
  await call("begin", { period, run_id: "a" });
  const snap = {
    ...snapshot,
    collected_at: period + "T00:10:00Z",
    entries: [{ ...snapshot.entries[0], stars_today: stars }],
  };
  await call("artifact", { ...args, key: "snapshot", value: snap }, "PUT");
  await call(
    "artifact",
    {
      ...args,
      key: "selection",
      value: {
        completed: true,
        eligible_count: 1,
        decisions: [
          {
            repository,
            is_ai: true,
            ai_relevance_evidence: "README",
            outcome: "selected",
            releases,
          },
        ],
      },
    },
    "PUT",
  );
  return call("stage", {
    ...args,
    report: {
      ...report,
      report_date: period,
      collected_at: snap.collected_at,
      entries: [
        { ...entry, ...extra, heat: { stars_today: stars, stars_total: 100 } },
      ],
    },
  });
}
describe("authoritative inclusion window", () => {
  it.each([
    {
      previous: "2026-11-03",
      old: 10,
      stars: 10,
      basis: undefined,
      expected: 200,
    },
    {
      previous: "2026-11-02",
      old: 10,
      stars: 10,
      basis: undefined,
      expected: 200,
    },
    {
      previous: "2026-11-04",
      old: 10,
      stars: 10,
      basis: undefined,
      expected: 400,
    },
    {
      previous: "2026-11-04",
      old: 10,
      stars: 20,
      basis: "stars_doubled",
      expected: 200,
    },
    {
      previous: "2026-11-04",
      old: 0,
      stars: 20,
      basis: "stars_doubled",
      expected: 400,
    },
    {
      previous: "2026-11-04",
      old: null,
      stars: 20,
      basis: "stars_doubled",
      expected: 400,
    },
    {
      previous: "2026-11-04",
      old: 10,
      stars: null,
      basis: "stars_doubled",
      expected: 400,
    },
  ])(
    "uses period09:00 boundaries and safe stars: $previous/$old/$stars",
    async ({ previous, old, stars, basis, expected }) => {
      await install();
      const staged = await stageDay(previous, old);
      expect(staged.status).toBe(200);
      const { hash } = (await staged.json()) as { hash: string };
      await call("publish", { period: previous, fence: 1, hash });
      const result = await stageDay("2026-11-10", stars, {
        repeat_change: "变化",
        previous_report_url: `/archive/${previous}/`,
        ...(basis ? { repeat_basis: basis } : {}),
      });
      expect(result.status).toBe(expected);
    },
  );
  it("admits only a new formal GitHub release and preserves original history after factual correction", async () => {
    await install();
    const { hash } = (await (await stageDay("2026-11-04", 10)).json()) as {
      hash: string;
    };
    await call("publish", { period: "2026-11-04", fence: 1, hash });
    const releases = [
      {
        draft: false,
        prerelease: false,
        published_at: "2026-11-09T01:00:00Z",
        html_url: "https://github.com/example/alpha/releases/tag/v2",
      },
    ];
    const result = await stageDay(
      "2026-11-10",
      10,
      {
        repeat_change: "正式版本更新",
        previous_report_url: "/archive/2026-11-04/",
        repeat_basis: "new_formal_release",
      },
      releases,
    );
    expect(result.status).toBe(200);
  });
});
describe("validated state limits and exposure gates", () => {
  it("rejects missing completed selection and all eligible analysis failures", async () => {
    const fence = await prepare();
    await storeRecord("period:2026-11-10", {
      runs: { a: 1 },
      fence: 1,
      reservations: {},
      artifacts: {
        snapshot,
        selection: {
          completed: true,
          eligible_count: 1,
          decisions: [
            {
              repository,
              is_ai: true,
              ai_relevance_evidence: "README",
              outcome: "analysis_failed",
            },
          ],
        },
      },
    });
    expect(
      await (
        await call("stage", {
          period: "2026-11-10",
          fence,
          report: { ...report, entries: [], entry_count: 0 },
        })
      ).json(),
    ).toEqual({ error: "all_analyses_failed" });
  });
  it("retains prior content and history when final confirmation sync crosses cutoff", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    const stub = env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    );
    let count = 0;
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, state) => {
        const sync = state.storage.sync.bind(state.storage);
        vi.spyOn(state.storage, "sync").mockImplementation(async () => {
          await sync();
          if (++count === 3)
            vi.spyOn(Date, "now").mockReturnValue(
              Date.parse("2026-11-10T01:00:00Z"),
            );
        });
      },
    );
    expect(
      await (
        await call("publish", { period: "2026-11-10", fence, hash })
      ).json(),
    ).toEqual({ error: "deadline_crossed" });
    expect(
      await (await call("state?period=2026-11-10", undefined, "GET")).json(),
    ).toMatchObject({ history: [], publication: null });
  });
  it("blocks public requests while the publication persistence barrier is unresolved", async () => {
    const fence = await prepare();
    const { hash } = (await (
      await call("stage", { period: "2026-11-10", fence, report })
    ).json()) as { hash: string };
    const stub = env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    );
    let release: () => void = () => {};
    let entered: () => void = () => {};
    const reached = new Promise<void>((r) => (entered = r));
    const barrier = new Promise<void>((r) => (release = r));
    let count = 0;
    await runInDurableObject<PublicationAuthority, void>(
      stub,
      async (_instance, state) => {
        const sync = state.storage.sync.bind(state.storage);
        vi.spyOn(state.storage, "sync").mockImplementation(async () => {
          await sync();
          if (++count === 2) {
            entered();
            await barrier;
          }
        });
      },
    );
    const publishing = call("publish", { period: "2026-11-10", fence, hash });
    await reached;
    let publicResolved = false;
    const publicRead = SELF.fetch("https://example.com/").then((response) => {
      publicResolved = true;
      return response;
    });
    await Promise.resolve();
    expect(publicResolved).toBe(false);
    release();
    expect((await publishing).status).toBe(200);
    expect(await (await publicRead).text()).toContain("example/alpha");
  });
  it("handles malformed private requests without breaking subsequent public reads", async () => {
    await install();
    const response = await SELF.fetch("https://example.com/_automation/begin", {
      method: "POST",
      headers,
      body: "{invalid",
    });
    expect(response.status).toBe(400);
    expect((await SELF.fetch("https://example.com/")).status).toBe(200);
    expect((await call("begin", { period: "bad", run_id: "a" })).status).toBe(
      400,
    );
    expect(
      (await call("begin", { period: "2026-99-99", run_id: "a" })).status,
    ).toBe(400);
  });
  it("stops storage growth before account quota rather than deleting archive data", async () => {
    const fence = await prepare();
    await storeRecord("capacity", 800 * 1024 * 1024);
    expect(
      (
        await call(
          "artifact",
          { period: "2026-11-10", fence, key: "extra", value: { x: 1 } },
          "PUT",
        )
      ).status,
    ).toBe(409);
    expect((await SELF.fetch("https://example.com/")).status).toBe(200);
  });
});
