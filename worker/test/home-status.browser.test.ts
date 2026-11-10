import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { runInNewContext } from "node:vm";
import { resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import { createInstrumenter } from "istanbul-lib-instrument";
import { createCoverageMap, type CoverageMapData } from "istanbul-lib-coverage";
import { createContext } from "istanbul-lib-report";
import { create } from "istanbul-reports";
import { HOME_STATUS_SCRIPT } from "../src/home-status";

// This script is emitted as text by the Worker, so module-level instrumentation
// cannot see its executable branches. Instrument the actual served body too.
const scriptPath = resolve("coverage/browser/home-status.js");
const instrumented = createInstrumenter().instrumentSync(
  HOME_STATUS_SCRIPT,
  scriptPath,
);
const scriptCoverage: CoverageMapData = {};
let instrumentScript = false;
afterAll(() => {
  const coverageMap = createCoverageMap(scriptCoverage);
  const summary = coverageMap.getCoverageSummary();
  const dir = resolve("coverage/browser");
  mkdirSync(dir, { recursive: true });
  writeFileSync(scriptPath, HOME_STATUS_SCRIPT);
  writeFileSync(
    resolve(dir, "coverage-final.json"),
    JSON.stringify(coverageMap.toJSON()),
  );
  const context = createContext({ dir, coverageMap });
  create("html").execute(context);
  create("text").execute(context);
  expect(summary.lines.pct).toBeGreaterThanOrEqual(80);
  expect(summary.branches.pct).toBeGreaterThanOrEqual(80);
});

function browser(
  at: string,
  reportDate = "",
  publishedAt = "",
  exhaustedMonth = "",
  maintenanceDate = "",
) {
  let now = Date.parse(at);
  const failure = {
    hidden: true,
    dataset: { reportDate, publishedAt, maintenanceDate },
  };
  const budget = { hidden: false, dataset: { exhaustedMonth } };
  const listeners = new Map<string, () => void>();
  const timers: { callback: () => void; delay: number }[] = [];
  let timerId = 0;
  let reloads = 0;
  const document = {
    visibilityState: "visible",
    getElementById: (id: string) => (id === "today-status" ? failure : budget),
    addEventListener: (event: string, callback: () => void) =>
      listeners.set(event, callback),
  };
  const globals = {
    __coverage__: scriptCoverage,
    Date: class extends Date {
      static now() {
        return now;
      }
    },
    document,
    window: {
      location: { reload: () => reloads++ },
      addEventListener: (event: string, callback: () => void) =>
        listeners.set(event, callback),
    },
    setTimeout: (callback: () => void, delay: number) => {
      timers.push({ callback, delay });
      return ++timerId;
    },
    clearTimeout: () => {
      timers.length = 0;
    },
  };
  runInNewContext(
    instrumentScript ? instrumented : HOME_STATUS_SCRIPT,
    globals,
  );
  return {
    failure,
    budget,
    timers,
    listeners,
    document,
    move: (at: string) => {
      now = Date.parse(at);
    },
    reloads: () => reloads,
  };
}

describe.each([false, true])(
  "executed public homepage script (instrumented: %s)",
  (instrument) => {
    it("keeps a truthful maintenance issue separate from missing today while reevaluating the next day", () => {
      const page = browser(
        "2026-11-10T04:00:00Z",
        "2026-11-10",
        "2026-11-10T03:00:00Z",
        "",
        "2026-11-10",
      );
      expect(page.failure.hidden).toBe(true);
      page.move("2026-11-11T01:00:00Z");
      page.timers[0].callback();
      expect(page.failure.hidden).toBe(false);
    });
    beforeEach(() => {
      instrumentScript = instrument;
    });
    it("independently shows missing issue at exactly Beijing 09:00 and refreshes the existing page", () => {
      const page = browser("2026-11-10T00:59:59.000Z");
      expect(page.failure.hidden).toBe(true);
      expect(page.timers[0].delay).toBe(1000);
      page.move("2026-11-10T01:00:00.000Z");
      page.timers.shift()!.callback();
      expect(page.failure.hidden).toBe(false);
      expect(page.reloads()).toBe(1);
      expect(page.timers[0].delay).toBe(60000);
    });
    it.each([
      ["2026-11-10", "2026-11-10T00:59:59Z", true],
      ["2026-11-10", "2026-11-10T01:00:00Z", false],
      ["2026-11-10", "not-a-date", false],
      ["2026-11-09", "2026-11-09T00:30:00Z", false],
    ])(
      "uses actual on-time publication metadata %s/%s",
      (date, published, hidden) => {
        const page = browser("2026-11-10T01:00:00Z", date, published);
        expect(page.failure.hidden).toBe(hidden);
        expect(page.timers[0].delay).toBe(60000);
      },
    );
    it("refreshes an on-time page to obtain new published content within a minute", () => {
      const page = browser("2026-11-10T00:30:00Z");
      expect(page.timers[0].delay).toBe(60000);
      page.move("2026-11-10T00:31:00Z");
      page.timers[0].callback();
      expect(page.reloads()).toBe(1);
      expect(page.failure.hidden).toBe(true);
    });
    it("resets yesterday's failure and exhausted-month hint at Beijing midnight", () => {
      const page = browser(
        "2026-11-30T15:59:59Z",
        "2026-11-29",
        "2026-11-29T00:30:00Z",
        "2026-11",
      );
      expect(page.failure.hidden).toBe(false);
      expect(page.budget.hidden).toBe(false);
      expect(page.timers[0].delay).toBe(1000);
      page.move("2026-11-30T16:00:00Z");
      page.timers[0].callback();
      expect(page.failure.hidden).toBe(true);
      expect(page.budget.hidden).toBe(true);
      expect(page.reloads()).toBe(1);
    });
    it("reevaluates suspended pages on focus, visibility and back-forward restoration without accumulating timers", () => {
      const page = browser("2026-11-10T00:30:00Z");
      page.move("2026-11-10T02:00:00Z");
      page.listeners.get("focus")!();
      expect(page.failure.hidden).toBe(false);
      page.document.visibilityState = "hidden";
      page.listeners.get("visibilitychange")!();
      expect(page.reloads()).toBe(1);
      page.document.visibilityState = "visible";
      page.listeners.get("visibilitychange")!();
      expect(page.reloads()).toBe(2);
      const pageshow = page.listeners.get("pageshow")! as unknown as (event: {
        persisted: boolean;
      }) => void;
      pageshow({ persisted: false });
      expect(page.reloads()).toBe(2);
      pageshow({ persisted: true });
      expect(page.reloads()).toBe(3);
      expect(page.timers).toHaveLength(1);
      expect(page.timers[0].delay).toBe(60000);
    });
  },
);
