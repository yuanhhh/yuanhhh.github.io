const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const html = readFileSync(`${__dirname}/index.html`, "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const document = {
  getElementById: () => ({ addEventListener() {} }),
  querySelectorAll: () => [],
};
const context = vm.createContext({ document, setTimeout, TextDecoder, Uint8Array });
vm.runInContext(`${script}\nthis.chartFunctions = { format, seriesValue, renderTable, renderChart, validate };`, context);
const { format, seriesValue, renderTable, renderChart, validate } = context.chartFunctions;

const values = { sh: 20000000000, sz: 30000000000, star: 10000000000 };

function chartElement(id) {
  return {
    id,
    innerHTML: "",
    attrs: {},
    classList: { toggle(_name, enabled) { this.single = enabled; } },
    setAttribute(name, value) { this.attrs[name] = value; },
  };
}

test("沪深合计不重复计算科创板，且以亿元显示", () => {
  assert.equal(seriesValue(values, "sum"), 50000000000);
  assert.equal(format(seriesValue(values, "sum")), "5.00");
  const table = { innerHTML: "" };
  renderTable(table, [{ date: "2026-11-03", values }], row => row.date);
  assert.match(table.innerHTML, /<td>2\.00<\/td><td>3\.00<\/td><td>1\.00<\/td><td>5\.00<\/td>/);
  assert.equal((table.innerHTML.match(/<td>/g) || []).length, 5);
});

test("grouped chart draws four series with a shared labelled scale", () => {
  const chart = chartElement("daily-chart"), axis = { innerHTML: "" };
  renderChart(chart, axis, [{ date: "2026-11-03", values }], row => row.date.slice(5), "all");
  assert.equal((chart.innerHTML.match(/class="bar /g) || []).length, 4);
  assert.match(chart.innerHTML, /class="bar sum"[^>]*title="2026-11-03 · 沪深合计：5\.00 亿元"/);
  assert.match(axis.innerHTML, />8\.00<\/span>/);
  assert.match(axis.innerHTML, />0\.00<\/span>/);
  assert.equal(chart.classList.single, false);
});

test("分时沪深合计也只使用沪市和深市", () => {
  const chart = chartElement("hourly-chart"), axis = { innerHTML: "" };
  const rows = [{ period: "09:30–10:30", values, partial: true }];
  renderChart(chart, axis, rows, row => row.period, "sum");
  assert.match(chart.innerHTML, /class="bar-value">5\.00/);
  assert.match(chart.attrs["aria-label"], /沪深合计/);
});

test("individual series rescales and shows values and partial status", () => {
  const chart = chartElement("hourly-chart"), axis = { innerHTML: "" };
  const rows = [{ period: "09:30–10:30", values, partial: true }, { period: "10:30–11:30", values: null }];
  renderChart(chart, axis, rows, row => row.period, "star");
  assert.equal((chart.innerHTML.match(/class="bar /g) || []).length, 1);
  assert.match(chart.innerHTML, /class="bar star"/);
  assert.match(chart.innerHTML, /class="bar-value">1\.00/);
  assert.match(chart.innerHTML, /进行中/);
  assert.match(axis.innerHTML, />1\.20<\/span>/);
  assert.match(chart.attrs["aria-label"], /科创板.*亿元/);
  assert.equal(chart.classList.single, true);
  const table = { innerHTML: "" };
  renderTable(table, rows, row => row.period);
  assert.match(table.innerHTML, /<td>—<\/td><td>—<\/td><td>—<\/td><td>—<\/td>/);
});

test("rejects old volume payloads so they cannot be labelled as turnover", () => {
  const rows = Array.from({ length: 30 }, () => ({ date: "2026-11-03", values }));
  const payload = { request_id: "test", daily: rows, unit: "分", metric: "turnover", intraday: null };
  assert.doesNotThrow(() => validate(payload, "test"));
  assert.throws(() => validate({ ...payload, unit: "手", metric: undefined }, "test"));
  assert.throws(() => validate({ ...payload, unit: "手" }, "test"));
  assert.throws(() => validate({ ...payload, metric: "volume" }, "test"));
});

test("zero is not rendered as a positive-height bar", () => {
  const chart = chartElement("hourly-chart"), axis = { innerHTML: "" };
  renderChart(chart, axis, [{ period: "09:30–10:30", values: { sh: 0, sz: 0, star: 0 } }], row => row.period, "sum");
  assert.match(chart.innerHTML, /class="bar sum zero" style="height:0%"/);
  assert.match(chart.innerHTML, /class="bar-value">0\.00/);
});
