import { DurableObject } from "cloudflare:workers";
import { HOME_STATUS_SCRIPT } from "./home-status";
import {
  REPORT_STATUS,
  SELECTION_OUTCOME,
  UNKNOWN_RELEVANCE,
  REPEAT_BASIS,
  UNKNOWN_REASON,
  USAGE_STATUS,
} from "./contracts";
export interface Env {
  PUBLICATION_AUTHORITY: DurableObjectNamespace<PublicationAuthority>;
  PUBLICATION_AUTOMATION_TOKEN: string;
  PUBLICATION_INSTALLATION_ID: string;
  PUBLICATION_OBJECT_NAME: string;
  PUBLICATION_BOOTSTRAP_TOKEN?: string;
  MAINTENANCE_PUBLICATION_TOKEN?: string;
  MAINTENANCE_PUBLICATION_DATE?: string;
  MAINTENANCE_REPORT_HASH?: string;
}
type RecordValue = Record<string, unknown>;
interface Reservation {
  amount_micros: number;
  input_tokens: number;
  output_tokens: number;
  actual_micros?: number;
  usage_status?: string;
}
interface Period {
  runs: Record<string, number>;
  fence: number;
  artifacts: Record<string, unknown>;
  reservations: Record<string, Reservation>;
}
interface Month {
  reserved_micros: number;
  accounting_blocked: boolean;
  pending: string[];
  generation_exhausted?: boolean;
}
interface Registry {
  periods: string[];
  months: string[];
}
class Fault extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
function need(
  condition: unknown,
  message = "invalid_request",
  status = 400,
): asserts condition {
  if (!condition) throw new Fault(status, message);
}
function object(value: unknown): RecordValue {
  need(typeof value === "object" && value !== null && !Array.isArray(value));
  return value as RecordValue;
}
function text(value: unknown): string {
  need(typeof value === "string" && value.length > 0 && value.length <= 32000);
  return value;
}
function integer(
  value: unknown,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
): number {
  need(
    typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= min &&
      value <= max,
  );
  return value;
}
function date(value: unknown): string {
  const result = text(value);
  need(
    /^\d{4}-\d{2}-\d{2}$/.test(result) &&
      Number.isFinite(Date.parse(result)) &&
      new Date(result).toISOString().slice(0, 10) === result,
  );
  return result;
}
function today(): string {
  return new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
}
function deadline(period: string): number {
  return Date.parse(period + "T01:00:00Z");
}
function before(period: string, margin = 0): void {
  need(
    period === today() && Date.now() < deadline(period) - margin,
    "deadline_or_date",
    409,
  );
}
function json(value: unknown): string {
  return JSON.stringify(value);
}
function list(value: unknown): unknown[] {
  need(Array.isArray(value));
  return value;
}
function timestamp(value: unknown): number {
  const v = text(value);
  need(/(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v)));
  return Date.parse(v);
}
function github(value: unknown): string {
  const v = text(value);
  let url: URL;
  try {
    url = new URL(v);
  } catch {
    throw new Fault(400, "invalid_source");
  }
  need(
    url.protocol === "https:" &&
      url.host === "github.com" &&
      !url.username &&
      !url.password,
    "invalid_source",
  );
  return v;
}
function metric(value: unknown): number | null {
  return value === null ? null : integer(value);
}
function snapshotValid(
  value: unknown,
  period: string,
  maintenance = false,
): RecordValue {
  const snap = object(value);
  need(
    snap.complete === true &&
      snap.source === "https://github.com/trending?since=daily",
    "invalid_snapshot",
  );
  const collected = timestamp(snap.collected_at);
  need(
    new Date(collected + 8 * 3600000).toISOString().slice(0, 10) === period &&
      (maintenance || collected < deadline(period)) &&
      collected <= Date.now(),
    "snapshot_time",
  );
  const repos = new Set<string>();
  let rank = 0;
  for (const value of list(snap.entries)) {
    const e = object(value);
    need(integer(e.rank, 1) === ++rank, "snapshot_order");
    const repo = text(e.repository);
    need(
      /^[\w.-]+\/[\w.-]+$/.test(repo) && !repos.has(repo),
      "snapshot_repository",
    );
    repos.add(repo);
    need(github(e.repository_url) === "https://github.com/" + repo);
    metric(e.stars_today);
    metric(e.stars_total);
  }
  return snap;
}
function escape(value: unknown): string {
  return String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ] ?? c,
  );
}
function page(content: string): string {
  return (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI 开源项目热点日报</title><style>body{font:16px/1.7 system-ui;margin:auto;max-width:900px;padding:16px;background:#f4f6f8;color:#17202a}article{background:white;padding:20px;margin:16px 0;border-radius:8px}a{overflow-wrap:anywhere}h1{font-size:clamp(1.5rem,4vw,2.2rem)}@media(max-width:600px){body{padding:10px}article{padding:12px}}</style></head><body><main>' +
    content +
    "</main></body></html>"
  );
}
function render(report: RecordValue): string {
  let content =
    "<h1>AI 开源项目热点日报</h1><p>日报日期：" +
    escape(report.report_date) +
    " · 实际发布时间：" +
    escape(report.published_at) +
    "</p><p>采集时间：" +
    escape(report.collected_at) +
    " · 来源：GitHub Trending Daily（全部编程语言，滚动统计） · 项目数：" +
    escape(report.entry_count) +
    '</p><a href="/archive/">历史归档</a>';
  if (report.corrected_at)
    content +=
      "<p>更正时间：" +
      escape(report.corrected_at) +
      "；" +
      escape(report.correction_note) +
      "</p>";
  const entries = list(report.entries);
  if (report.maintenance_publication)
    content += '<p role="status">应维护者要求补发，未满足09:00准点发布</p>';
  if (!entries.length)
    content += "<p>今日无符合收录条件的项目。已完成完整榜单筛选。</p>";
  for (const value of entries) {
    const e = object(value),
      heat = object(e.heat);
    content +=
      "<article><h2>" +
      escape(e.repository) +
      "</h2><p>" +
      escape(e.project_type) +
      " · 榜单第 " +
      escape(e.rank) +
      " 位</p>";
    for (const [key, label] of Object.entries({
      introduction: "介绍",
      problem_solved: "解决的问题",
      audience: "适用读者",
      characteristics: "特点",
    }))
      content += "<p>" + label + "：" + escape(e[key]) + "</p>";
    content +=
      "<p>当日新增 Star：" +
      escape(heat.stars_today ?? "未获取") +
      "；累计 Star：" +
      escape(heat.stars_total ?? "未获取") +
      "</p><p>走红原因：" +
      escape(e.reason_has_evidence ? e.reason : "暂未找到明确原因") +
      "</p>";
    if (e.repeat_change)
      content +=
        "<p>" +
        escape(e.repeat_change) +
        ' · <a href="' +
        escape(e.previous_report_url) +
        '">上次介绍</a></p>';
    content +=
      '<a href="' +
      escape(e.repository_url) +
      '">查看仓库</a>' +
      list(e.sources)
        .map((url) => ' · <a href="' + escape(url) + '">来源</a>')
        .join("") +
      "</article>";
  }
  return content;
}
interface Issue {
  hash: string;
  published_at: string;
}
interface Intent {
  period: string;
  head?: string;
  issue?: Issue;
  inclusion?: RecordValue;
}
/** Serialized private ledger and public report authority; unavailable until explicit install. */
export class PublicationAuthority extends DurableObject<Env> {
  private setup(): void {
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    );
  }
  private read<T>(key: string): T | undefined {
    const row = this.ctx.storage.sql
      .exec<{ value: string }>("SELECT value FROM records WHERE key=?", key)
      .toArray()[0];
    if (!row) return undefined;
    try {
      return JSON.parse(row.value) as T;
    } catch {
      throw new Fault(503, "corrupt_authority");
    }
  }
  private write(key: string, value: unknown): void {
    const encoded = json(value),
      previous =
        this.ctx.storage.sql
          .exec<{
            bytes: number;
          }>(
            "SELECT length(CAST(value AS BLOB)) AS bytes FROM records WHERE key=?",
            key,
          )
          .toArray()[0]?.bytes ?? 0;
    const capacity = this.read<number>("capacity") ?? 0;
    const next = capacity + new TextEncoder().encode(encoded).length - previous;
    need(
      next <= 800 * 1024 * 1024 || next <= capacity,
      "storage_capacity",
      409,
    );
    this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec(
        "INSERT INTO records(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key,
        encoded,
      );
      this.ctx.storage.sql.exec(
        "INSERT INTO records(key,value) VALUES ('capacity',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        String(next),
      );
    });
  }
  private allocate(period: string, bytes: number): void {
    const key = "bytes:" + period,
      used = this.read<number>(key) ?? 0;
    need(used + bytes <= 2 * 1024 * 1024, "period_size", 413);
    this.write(key, used + bytes);
  }
  private period(period: string): Period {
    const registry = this.read<Registry>("registry");
    need(registry, "corrupt_authority", 503);
    const found = this.read<Period>("period:" + period);
    need(found || !registry.periods.includes(period), "missing_period", 503);
    if (found)
      need(
        typeof found.fence === "number" &&
          found.runs &&
          found.artifacts &&
          found.reservations,
        "corrupt_period",
        503,
      );
    return (
      found ?? {
        runs: {},
        fence: 0,
        artifacts: {},
        reservations: {},
      }
    );
  }
  private month(period: string): Month {
    const registry = this.read<Registry>("registry");
    need(registry, "corrupt_authority", 503);
    const found = this.read<Month>("month:" + period.slice(0, 7));
    need(
      found || !registry.months.includes(period.slice(0, 7)),
      "missing_month",
      503,
    );
    if (found)
      need(
        Number.isSafeInteger(found.reserved_micros) &&
          found.reserved_micros >= 0 &&
          typeof found.accounting_blocked === "boolean" &&
          Array.isArray(found.pending),
        "corrupt_month",
        503,
      );
    return (
      found ?? {
        reserved_micros: 0,
        accounting_blocked: false,
        pending: [],
      }
    );
  }
  private remove(key: string): void {
    const bytes =
      this.ctx.storage.sql
        .exec<{
          bytes: number;
        }>(
          "SELECT length(CAST(value AS BLOB)) AS bytes FROM records WHERE key=?",
          key,
        )
        .toArray()[0]?.bytes ?? 0;
    this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec("DELETE FROM records WHERE key=?", key);
      this.ctx.storage.sql.exec(
        "UPDATE records SET value=? WHERE key='capacity'",
        String((this.read<number>("capacity") ?? 0) - bytes),
      );
    });
  }
  private history(): RecordValue[] {
    return this.ctx.storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM records WHERE key LIKE 'inclusion:%' ORDER BY key DESC",
      )
      .toArray()
      .map((row) => object(JSON.parse(row.value)));
  }
  private issueReport(period: string): RecordValue | undefined {
    const issue = this.read<Issue>("issue:" + period);
    if (!issue) return undefined;
    const report = this.read<RecordValue>("blob:" + issue.hash);
    need(report, "missing_blob", 503);
    return report;
  }
  private rollback(intent: Intent): void {
    if (intent.head) this.write("head", intent.head);
    else this.remove("head");
    if (intent.issue) this.write("issue:" + intent.period, intent.issue);
    else this.remove("issue:" + intent.period);
    if (intent.inclusion)
      this.write("inclusion:" + intent.period, intent.inclusion);
    else this.remove("inclusion:" + intent.period);
    this.remove("intent");
  }
  private validateReport(
    value: unknown,
    period: string,
    p: Period,
    correction = false,
    maintenance = false,
  ): RecordValue {
    const report = object(value),
      snapshot = snapshotValid(p.artifacts.snapshot, period, maintenance);
    need(
      maintenance || correction || !report.maintenance_publication,
      "maintenance_input",
    );
    need(
      report.contract_version === "1.0" &&
        report.report_date === period &&
        report.timezone === "Asia/Shanghai" &&
        report.source === snapshot.source &&
        report.collected_at === snapshot.collected_at,
      "report_identity",
    );
    need(
      typeof report.scope === "string" &&
        Object.values(REPORT_STATUS).some(
          (status) => status === text(report.status),
        ),
      "report_status",
    );
    const entries = list(report.entries);
    need(
      entries.length <= 5 && report.entry_count === entries.length,
      "report_count",
    );
    const selection = object(p.artifacts.selection);
    need(selection.completed === true, "incomplete_selection");
    const decisions = list(selection.decisions).map(object),
      source = list(snapshot.entries).map(object);
    need(decisions.length === source.length, "incomplete_selection");
    const selected: string[] = [];
    let eligible = 0;
    for (let i = 0; i < decisions.length; i++) {
      const d = decisions[i];
      need(
        d.repository === source[i].repository &&
          (typeof d.is_ai === "boolean" || d.is_ai === null),
        "selection_order",
      );
      if (d.is_ai === null) {
        need(
          d.outcome === SELECTION_OUTCOME.EXCLUDED &&
            d.reason === UNKNOWN_RELEVANCE,
          "unknown_selection",
        );
        need(
          typeof d.ai_relevance_evidence === "string",
          "missing_relevance_evidence",
        );
      } else {
        need(
          text(d.ai_relevance_evidence).trim().length > 0,
          "missing_relevance_evidence",
        );
      }
      need(
        Object.values(SELECTION_OUTCOME).some(
          (outcome) => outcome === text(d.outcome),
        ),
        "selection_outcome",
      );
      if (d.outcome !== SELECTION_OUTCOME.EXCLUDED) {
        need(d.is_ai === true, "not_ai");
        eligible++;
      }
      if (d.outcome === SELECTION_OUTCOME.SELECTED)
        selected.push(text(d.repository));
    }
    need(
      selection.eligible_count === eligible &&
        json(selected) === json(entries.map((v) => object(v).repository)),
      "selection_mismatch",
    );
    need(entries.length > 0 || eligible === 0, "all_analyses_failed");
    let rank = 0;
    const history = this.history();
    for (const value of entries) {
      const e = object(value),
        repo = text(e.repository),
        raw = source.find((s) => s.repository === repo);
      need(
        raw && integer(e.rank, 1) > rank && e.rank === raw.rank,
        "report_order",
      );
      rank = e.rank as number;
      need(e.repository_url === raw.repository_url, "repository_url");
      for (const key of [
        "ai_relevance_evidence",
        "project_type",
        "introduction",
        "problem_solved",
        "audience",
        "characteristics",
      ])
        text(e[key]);
      const heat = object(e.heat);
      need(
        metric(heat.stars_today) === raw.stars_today &&
          metric(heat.stars_total) === raw.stars_total,
        "metric_mismatch",
      );
      const sources = list(e.sources);
      need(sources.length > 0, "missing_sources");
      sources.forEach(github);
      need(typeof e.reason_has_evidence === "boolean", "reason_evidence");
      if (e.reason_has_evidence) text(e.reason);
      else
        need(
          e.reason === null || e.reason === UNKNOWN_REASON,
          "unknown_reason",
        );
      const previous = history.find(
        (h) =>
          text(h.report_date) < period &&
          list(h.entries).some((v) => object(v).repository === repo),
      );
      if (previous && !correction) {
        text(e.repeat_change);
        need(
          e.previous_report_url === `/archive/${text(previous.report_date)}/`,
          "previous_link",
        );
        if (
          deadline(period) - deadline(text(previous.report_date)) <
          168 * 3600000
        ) {
          const old = object(
              list(previous.entries).find((v) => object(v).repository === repo),
            ),
            stars = object(old.heat).stars_today;
          const doubled =
            typeof stars === "number" &&
            stars > 0 &&
            typeof heat.stars_today === "number" &&
            heat.stars_today >= stars * 2 &&
            heat.stars_today > stars;
          const decision = decisions.find((d) => d.repository === repo);
          const releases =
            decision?.releases === undefined
              ? []
              : list(decision.releases).map(object);
          const release = releases.some(
            (r) =>
              r.draft === false &&
              r.prerelease === false &&
              timestamp(r.published_at) >
                deadline(text(previous.report_date)) &&
              timestamp(r.published_at) <= Date.now() &&
              github(r.html_url).startsWith(
                "https://github.com/" + repo + "/releases/",
              ),
          );
          need(
            (doubled && e.repeat_basis === REPEAT_BASIS.STARS_DOUBLED) ||
              (release && e.repeat_basis === REPEAT_BASIS.FORMAL_RELEASE),
            "recent_duplicate",
          );
        }
      }
    }
    return report;
  }
  private async blob(report: RecordValue): Promise<string> {
    const bytes = new TextEncoder().encode(json(report));
    need(bytes.length <= 512 * 1024, "report_size", 413);
    const hash = Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      (v) => v.toString(16).padStart(2, "0"),
    ).join("");
    if (!this.read("blob:" + hash))
      this.ctx.storage.transactionSync(() => {
        this.allocate(date(report.report_date), bytes.length);
        this.write("blob:" + hash, report);
      });
    return hash;
  }
  private async publish(
    period: string,
    report: RecordValue,
    correction = false,
    maintenance = false,
  ): Promise<Response> {
    const checkAdmission = () => {
      if (maintenance) need(period === today(), "maintenance_date", 409);
      else if (!correction) before(period, 5000);
    };
    checkAdmission();
    const previous = this.read<Issue>("issue:" + period),
      head = this.read<string>("head");
    need(
      correction ? previous !== undefined : !head || head <= period,
      "newer_publication",
      409,
    );
    if (!correction && previous)
      return Response.json({
        report: this.issueReport(period),
        url: `/archive/${period}/`,
        idempotent: true,
      });
    const canonical = {
      ...report,
      ...(correction && this.issueReport(period)?.maintenance_publication
        ? {
            maintenance_publication:
              this.issueReport(period)!.maintenance_publication,
          }
        : {}),
      status: REPORT_STATUS.PUBLISHED,
      published_at:
        previous?.published_at ?? new Date(Date.now()).toISOString(),
    };
    const hash = await this.blob(canonical);
    checkAdmission();
    const intent: Intent = {
      period,
      head,
      issue: previous,
      inclusion: this.read<RecordValue>("inclusion:" + period),
    };
    this.write("intent", intent);
    await this.ctx.storage.sync();
    checkAdmission();
    this.ctx.storage.transactionSync(() => {
      this.write("issue:" + period, {
        hash,
        published_at: canonical.published_at,
      });
      if (!correction) {
        this.write("inclusion:" + period, canonical);
        this.write("head", period);
      }
    });
    await this.ctx.storage.sync();
    if (
      !correction &&
      (period !== today() || (!maintenance && Date.now() >= deadline(period)))
    ) {
      this.ctx.storage.transactionSync(() => this.rollback(intent));
      await this.ctx.storage.sync();
      throw new Fault(409, "deadline_crossed");
    }
    this.remove("intent");
    await this.ctx.storage.sync();
    if (
      !correction &&
      (period !== today() || (!maintenance && Date.now() >= deadline(period)))
    ) {
      this.ctx.storage.transactionSync(() => this.rollback(intent));
      await this.ctx.storage.sync();
      throw new Fault(409, "deadline_crossed");
    }
    return Response.json({
      report: canonical,
      url: `/archive/${period}/`,
      idempotent: false,
    });
  }
  private fence(period: string, p: Period, b: RecordValue): void {
    before(period);
    need(b.fence === p.fence && p.fence > 0, "stale_fence", 409);
  }
  /** Serialize all writes and reads, including post-persistence cutoff checks. */
  async fetch(request: Request): Promise<Response> {
    return this.ctx.blockConcurrencyWhile(async () => {
      try {
        this.setup();
        const url = new URL(request.url),
          route = url.pathname;
        if (route.startsWith("/_automation/"))
          need(
            request.headers.get("Authorization") ===
              `Bearer ${this.env.PUBLICATION_AUTOMATION_TOKEN}` &&
              request.headers.get("X-Installation-ID") ===
                this.env.PUBLICATION_INSTALLATION_ID,
            "unauthorized",
            401,
          );
        const b = request.method === "GET" ? {} : object(await request.json());
        const identity = await this.ctx.storage.get<string>("installation");
        if (route === "/_automation/install" && request.method === "POST") {
          need(
            this.env.PUBLICATION_BOOTSTRAP_TOKEN &&
              b.bootstrap_token === this.env.PUBLICATION_BOOTSTRAP_TOKEN,
            "bootstrap_disabled",
            403,
          );
          need(
            identity === undefined &&
              this.ctx.storage.sql
                .exec("SELECT key FROM records LIMIT 1")
                .toArray().length === 0,
            "already_installed",
            409,
          );
          need(
            this.env.PUBLICATION_INSTALLATION_ID &&
              !this.env.PUBLICATION_INSTALLATION_ID.startsWith("REPLACE"),
            "authority_unavailable",
            503,
          );
          this.write("registry", { periods: [], months: [] });
          await this.ctx.storage.put(
            "installation",
            this.env.PUBLICATION_INSTALLATION_ID,
          );
          return Response.json({ installed: true });
        }
        need(
          identity === this.env.PUBLICATION_INSTALLATION_ID,
          "authority_unavailable",
          503,
        );
        need(this.read<Registry>("registry"), "corrupt_authority", 503);
        const pending = this.read<Intent>("intent");
        if (pending) {
          this.ctx.storage.transactionSync(() => this.rollback(pending));
          await this.ctx.storage.sync();
        }
        if (!route.startsWith("/_automation/")) {
          need(request.method === "GET", "method", 405);
          if (route === "/home-status.js")
            return new Response(HOME_STATUS_SCRIPT, {
              headers: {
                "Content-Type": "text/javascript; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
              },
            });
          const head = this.read<string>("head");
          let content: string;
          if (route === "/") {
            const report = head ? this.issueReport(head) : undefined;
            content = report
              ? render(report)
              : "<h1>AI 开源项目热点日报</h1><p>尚未发布</p>";
            const missing =
              Date.now() >= deadline(today()) &&
              (!report ||
                report.report_date !== today() ||
                (timestamp(report.published_at) >= deadline(today()) &&
                  !report.maintenance_publication));
            const month = today().slice(0, 7);
            const exhausted =
              this.read<Month>("month:" + month)?.generation_exhausted === true;
            content =
              `<p id="today-status" role="status" data-maintenance-date="${report?.maintenance_publication ? escape(report.report_date) : ""}" data-report-date="${escape(report?.report_date ?? "")}" data-published-at="${escape(report?.published_at ?? "")}"${missing ? "" : " hidden"}>今日日报生成失败</p>` +
              `<p id="month-status" role="status" data-exhausted-month="${exhausted ? month : ""}"${exhausted ? "" : " hidden"}>当月生成额度已用尽</p>` +
              content +
              '<script src="/home-status.js" defer></script>';
          } else if (route === "/archive/" || route === "/archive") {
            content =
              "<h1>历史归档</h1>" +
              this.history()
                .map(
                  (h) =>
                    `<p><a href="/archive/${escape(h.report_date)}/">${escape(h.report_date)}</a></p>`,
                )
                .join("");
          } else {
            const match =
              /^\/archive\/(\d{4}-\d{2}-\d{2})\/(?:index.html)?$/.exec(route);
            const report = match ? this.issueReport(match[1]) : undefined;
            need(report, "not_found", 404);
            content = render(report);
          }
          return new Response(page(content), {
            headers: {
              "Content-Type": "text/html; charset=utf-8",
              "Cache-Control": "no-store",
              "Content-Security-Policy":
                "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
              "X-Content-Type-Options": "nosniff",
            },
          });
        }
        const period = date(b.period ?? url.searchParams.get("period")),
          p = this.period(period);
        if (route === "/_automation/maintenance-publish") {
          need(
            request.method === "POST" &&
              this.env.MAINTENANCE_PUBLICATION_TOKEN &&
              request.headers.get("X-Maintenance-Token") ===
                this.env.MAINTENANCE_PUBLICATION_TOKEN,
            "maintenance_disabled",
            403,
          );
          need(
            period === "2026-11-10" &&
              period === today() &&
              period === this.env.MAINTENANCE_PUBLICATION_DATE,
            "maintenance_date",
            409,
          );
          const report = object(b.report);
          const reportBytes = new TextEncoder().encode(json(report));
          need(reportBytes.length <= 512 * 1024, "report_size", 413);
          const hash = Array.from(
            new Uint8Array(await crypto.subtle.digest("SHA-256", reportBytes)),
            (v) => v.toString(16).padStart(2, "0"),
          ).join("");
          need(
            hash === this.env.MAINTENANCE_REPORT_HASH,
            "maintenance_hash",
            409,
          );
          const previous = this.issueReport(period);
          if (previous) {
            need(
              object(previous.maintenance_publication).report_hash === hash,
              "already_published",
              409,
            );
            return Response.json({
              report: previous,
              url: `/archive/${period}/`,
              idempotent: true,
            });
          }
          need(
            !report.maintenance_publication && report.published_at === null,
            "maintenance_input",
          );
          const candidate = {
            ...p,
            artifacts: {
              ...p.artifacts,
              snapshot: b.snapshot,
              selection: b.selection,
            },
          };
          this.validateReport(report, period, candidate, false, true);
          const registry = this.read<Registry>("registry")!;
          if (!registry.periods.includes(period)) registry.periods.push(period);
          this.ctx.storage.transactionSync(() => {
            this.write("period:" + period, candidate);
            this.write("registry", registry);
          });
          return await this.publish(
            period,
            {
              ...report,
              maintenance_publication: {
                report_hash: hash,
                note: "应维护者要求补发，未满足09:00准点发布",
              },
            },
            false,
            true,
          );
        }
        if (route === "/_automation/state" && request.method === "GET")
          return Response.json({
            period,
            attempts: Object.keys(p.runs).length,
            fence: p.fence,
            artifacts: p.artifacts,
            reservations: p.reservations,
            month: this.month(period),
            history: this.history(),
            publication: this.issueReport(period) ?? null,
          });
        need(
          request.method === "POST" ||
            (request.method === "PUT" && route === "/_automation/artifact"),
          "method",
          405,
        );
        if (route === "/_automation/begin") {
          before(period);
          const run = text(b.run_id);
          need(
            !["__proto__", "constructor", "prototype", "toString"].includes(
              run,
            ),
            "invalid_key",
          );
          if (p.runs[run] !== undefined)
            return Response.json({
              period,
              run_id: run,
              fence: p.runs[run],
              attempts: p.runs[run],
            });
          need(Object.keys(p.runs).length < 3, "attempt_limit", 409);
          p.runs[run] = ++p.fence;
          const registry = this.read<Registry>("registry")!;
          if (!registry.periods.includes(period)) registry.periods.push(period);
          if (!registry.months.includes(period.slice(0, 7))) {
            registry.months.push(period.slice(0, 7));
            this.write("month:" + period.slice(0, 7), this.month(period));
          }
          this.ctx.storage.transactionSync(() => {
            this.write("period:" + period, p);
            this.write("registry", registry);
          });
          await this.ctx.storage.sync();
          before(period);
          return Response.json({
            period,
            run_id: run,
            fence: p.fence,
            attempts: p.fence,
          });
        }
        if (route === "/_automation/usage") {
          const key = text(b.key),
            actual = integer(b.actual_micros),
            reservation = p.reservations[key];
          need(reservation, "missing_reservation", 409);
          const usageStatus = text(b.usage_status ?? USAGE_STATUS.REPORTED);
          need(
            Object.values(USAGE_STATUS).some(
              (status) => status === usageStatus,
            ),
            "usage_status",
          );
          need(
            usageStatus !== USAGE_STATUS.UNAVAILABLE ||
              actual === reservation.amount_micros,
            "unknown_usage_bound",
          );
          need(
            reservation.actual_micros === undefined ||
              (reservation.actual_micros === actual &&
                (reservation.usage_status === undefined ||
                  reservation.usage_status === usageStatus)),
            "usage_conflict",
            409,
          );
          const month = this.month(period);
          if (
            reservation.actual_micros === undefined &&
            actual > reservation.amount_micros
          ) {
            month.reserved_micros += actual - reservation.amount_micros;
            month.accounting_blocked = true;
          }
          if (b.accounting_blocked === true) month.accounting_blocked = true;
          month.pending = month.pending.filter(
            (id) => id !== period + ":" + key,
          );
          reservation.actual_micros = actual;
          reservation.usage_status = usageStatus;
          this.write("period:" + period, p);
          this.write("month:" + period.slice(0, 7), month);
          await this.ctx.storage.sync();
          return Response.json({
            accounting_blocked: month.accounting_blocked,
          });
        }
        if (route === "/_automation/correct") {
          const original = this.issueReport(period);
          need(original, "missing_publication", 409);
          const candidate = object(b.report);
          need(
            json(list(candidate.entries).map((v) => object(v).repository)) ===
              json(list(original.entries).map((v) => object(v).repository)),
            "correction_selection",
            409,
          );
          const corrected = this.validateReport(
            candidate,
            period,
            p,
            true,
            Boolean(original.maintenance_publication),
          );
          return await this.publish(
            period,
            {
              ...corrected,
              corrected_at: new Date(Date.now()).toISOString(),
              correction_note: text(b.correction_note),
            },
            true,
          );
        }
        if (
          route === "/_automation/publish" &&
          this.read<Issue>("issue:" + period)
        )
          return Response.json({
            report: this.issueReport(period),
            url: `/archive/${period}/`,
            idempotent: true,
          });
        this.fence(period, p, b);
        if (route === "/_automation/budget-notice") {
          const input = integer(b.input_tokens, 1, 32000),
            output = integer(b.output_tokens, 1, 8000),
            limit = integer(b.limit_micros, 0, 30_000_000),
            month = this.month(period);
          need(!month.accounting_blocked, "accounting_blocked", 409);
          need(month.pending.length === 0, "accounting_pending", 409);
          const exhausted =
            month.reserved_micros + input * 12 + output * 36 > limit;
          if (exhausted) {
            month.generation_exhausted = true;
            this.write("month:" + period.slice(0, 7), month);
            await this.ctx.storage.sync();
          }
          return Response.json({ generation_exhausted: exhausted });
        }
        if (route === "/_automation/stage") {
          const report = this.validateReport(b.report, period, p);
          const hash = await this.blob(report);
          await this.ctx.storage.sync();
          before(period);
          return Response.json({ hash });
        }
        if (route === "/_automation/publish") {
          const report = this.read<RecordValue>("blob:" + text(b.hash));
          need(report, "missing_stage", 409);
          this.validateReport(report, period, p);
          return await this.publish(period, report);
        }

        if (route === "/_automation/reserve") {
          const key = text(b.key),
            amount = integer(b.amount_micros, 1, 30_000_000),
            input = integer(b.input_tokens, 1, 32000),
            output = integer(b.output_tokens, 1, 8000);
          need(amount >= input * 12 + output * 36, "under_reserved");
          need(
            !["__proto__", "constructor", "prototype", "toString"].includes(
              key,
            ),
            "invalid_key",
          );
          const existing = p.reservations[key];
          if (existing) {
            need(
              existing.amount_micros === amount &&
                existing.input_tokens === input &&
                existing.output_tokens === output,
              "reservation_conflict",
              409,
            );
            return Response.json({ dispatch: false, reservation: existing });
          }
          const month = this.month(period);
          need(!month.accounting_blocked, "accounting_blocked", 409);
          need(month.pending.length === 0, "accounting_pending", 409);
          need(Object.keys(p.reservations).length < 40, "call_limit", 409);
          if (month.reserved_micros + amount > 30_000_000) {
            month.generation_exhausted = true;
            this.write("month:" + period.slice(0, 7), month);
            await this.ctx.storage.sync();
            throw new Fault(409, "budget_exhausted");
          }
          const reservation = {
            amount_micros: amount,
            input_tokens: input,
            output_tokens: output,
          };
          p.reservations[key] = reservation;
          month.reserved_micros += amount;
          month.pending.push(period + ":" + key);
          this.ctx.storage.transactionSync(() => {
            this.write("period:" + period, p);
            this.write("month:" + period.slice(0, 7), month);
          });
          await this.ctx.storage.sync();
          before(period);
          return Response.json({ dispatch: true, reservation });
        }
        if (route === "/_automation/artifact") {
          const key = text(b.key);
          need(
            !["__proto__", "constructor", "prototype", "toString"].includes(
              key,
            ),
            "invalid_key",
          );
          need(b.value !== undefined);
          const existing = p.artifacts[key];
          if (existing !== undefined) {
            need(json(existing) === json(b.value), "artifact_conflict", 409);
            return Response.json({ stored: true });
          }
          need(
            new TextEncoder().encode(json(b.value)).length <= 512 * 1024,
            "artifact_size",
            413,
          );
          need(
            new TextEncoder().encode(json(p.artifacts)).length +
              new TextEncoder().encode(json(b.value)).length <=
              2 * 1024 * 1024,
            "period_size",
            413,
          );
          if (key === "snapshot") snapshotValid(b.value, period);
          p.artifacts[key] = b.value;
          this.ctx.storage.transactionSync(() => {
            this.allocate(
              period,
              new TextEncoder().encode(json(b.value)).length,
            );
            this.write("period:" + period, p);
          });
          await this.ctx.storage.sync();
          before(period);
          return Response.json({ stored: true });
        }
        throw new Fault(404, "not_found");
      } catch (error) {
        if (error instanceof SyntaxError)
          return Response.json({ error: "invalid_json" }, { status: 400 });
        if (error instanceof Fault)
          return Response.json(
            { error: error.message },
            { status: error.status },
          );
        throw error;
      }
    });
  }
}
export default {
  /** Route public reads and authenticated automation into the one pinned object. */
  async fetch(request: Request, env: Env): Promise<Response> {
    if (
      new URL(request.url).pathname.startsWith("/_automation/") &&
      (request.headers.get("Authorization") !==
        `Bearer ${env.PUBLICATION_AUTOMATION_TOKEN}` ||
        request.headers.get("X-Installation-ID") !==
          env.PUBLICATION_INSTALLATION_ID)
    )
      return Response.json({ error: "unauthorized" }, { status: 401 });
    return env.PUBLICATION_AUTHORITY.get(
      env.PUBLICATION_AUTHORITY.idFromName(env.PUBLICATION_OBJECT_NAME),
    ).fetch(request);
  },
};
