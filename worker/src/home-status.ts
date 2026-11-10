// Served only as a same-origin static script. It reads publication metadata,
// never private generation state, and refreshes the existing HTML route.
export const HOME_STATUS_SCRIPT = `(() => {
  const status = document.getElementById("today-status");
  const budget = document.getElementById("month-status");
  let timer;
  function update() {
    const now = Date.now();
    const day = new Date(now + 28800000).toISOString().slice(0, 10);
    const cutoff = Date.parse(day + "T01:00:00Z");
    status.hidden = !(now >= cutoff &&
      (status.dataset.reportDate !== day ||
       (!(Date.parse(status.dataset.publishedAt) < cutoff) && status.dataset.maintenanceDate !== day)));
    budget.hidden = budget.dataset.exhaustedMonth !== day.slice(0, 7);
    return Math.min(60000, now < cutoff ? cutoff - now : Infinity,
      Date.parse(day + "T16:00:00Z") - now);
  }
  function refresh() {
    schedule();
    window.location.reload();
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, update());
  }
  schedule();
  window.addEventListener("focus", refresh);
  window.addEventListener("pageshow", (event) => { if (event.persisted) refresh(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
})();`;
