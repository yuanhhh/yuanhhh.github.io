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
vm.runInContext(`${script}\nthis.chartFunctions = { format, seriesValue, renderTable, renderChart, validate, trendPath, lineState, parseBrowserHistory, yuanToFen };`, context);
const { format, seriesValue, renderTable, renderChart, validate, trendPath, lineState, parseBrowserHistory, yuanToFen } = context.chartFunctions;

const values = { sh: 20000000000, sz: 30000000000, star: 10000000000 };

function chartElement(id) {
  return {
    id,
    innerHTML: "",
    attrs: {},
    classList: { toggle(name, enabled) { this[name] = enabled; } },
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
  assert.equal(lineState["hourly-chart"].enabled, true);
});

test("两张图可切换曲线模式，分时默认叠加趋势线", () => {
  const rows = [{ date: "2026-11-03", values }];
  const daily = chartElement("daily-chart"), hourly = chartElement("hourly-chart");
  renderChart(daily, { innerHTML: "" }, rows, row => row.date, "sum", "bar");
  assert.equal(lineState["daily-chart"].enabled, false);
  renderChart(daily, { innerHTML: "" }, rows, row => row.date, "sum", "line");
  assert.equal(daily.classList["line-mode"], true);
  assert.equal(lineState["daily-chart"].enabled, true);
  assert.match(daily.attrs["aria-label"], /曲线图/);
  renderChart(hourly, { innerHTML: "" }, rows, row => row.date, "sum", "bar");
  assert.equal(hourly.classList["line-mode"], false);
  assert.equal(lineState["hourly-chart"].enabled, true);
  renderChart(hourly, { innerHTML: "" }, rows, row => row.date, "sum", "line");
  assert.equal(hourly.classList["line-mode"], true);
});

test("缺失时段会断开曲线，不会推测未发生的数据", () => {
  assert.equal(trendPath([{ x: 1, y: 2 }, { x: 3, y: 4 }, null, { x: 5, y: 6 }]), "M1.00,2.00 L3.00,4.00 M5.00,6.00");
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

test("分时数据必须同时包含四个小时和八个半小时时段", () => {
  const rows = Array.from({ length: 30 }, () => ({ date: "2026-11-03", values }));
  const period = { period: "09:30–10:00", values };
  const payload = { request_id: "test", daily: rows, unit: "分", metric: "turnover", intraday: { hourly: Array(4).fill(period), half_hourly: Array(8).fill(period) } };
  assert.doesNotThrow(() => validate(payload, "test"));
  assert.throws(() => validate({ ...payload, intraday: { hourly: Array(4).fill(period) } }, "test"));
});

test("历史分时必须属于所列交易日且包含完整时段", () => {
  const rows = Array.from({ length: 30 }, () => ({ date: "2026-11-03", values }));
  const period = { period: "09:30–10:00", values };
  const history = { date: "2026-11-03", hourly: Array(4).fill(period), half_hourly: Array(8).fill(period) };
  const payload = { request_id: "test", daily: rows, unit: "分", metric: "turnover", intraday: null, intraday_history: { "2026-11-03": history } };
  assert.doesNotThrow(() => validate(payload, "test"));
  assert.throws(() => validate({ ...payload, intraday_history: { "2026-11-03": { ...history, half_hourly: Array(7).fill(period) } } }, "test"));
  assert.throws(() => validate({ ...payload, intraday_history: { "2026-10-30": { ...history, date: "2026-10-30" } } }, "test"));
});

test("浏览器历史分时取真实成交额，汇总为 8 个半小时和 4 个小时", () => {
  const date = "2026-11-02";
  const ends = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"];
  const rows = ends.map((time, index) => `${date} ${time},1,1,1,1,999,${index + 1}.50`);
  const results = Object.fromEntries(["sh", "sz", "star"].map(key => [key, { rc: 0, data: { klines: rows } }]));
  const history = parseBrowserHistory(results, [date, "2026-10-30"]);
  assert.equal(history[date].half_hourly.length, 8);
  assert.equal(history[date].half_hourly[0].values.sh, 150);
  assert.equal(history[date].hourly[0].values.sh, 400);
  assert.equal(history[date].as_of, `${date} 15:00`);
  assert.equal(history["2026-10-30"], undefined);
  assert.equal(yuanToFen("810009457713.90"), 81000945771390);
  assert.throws(() => yuanToFen("1.234"));
  assert.throws(() => yuanToFen("NaN"));
});

test("浏览器历史分时缺失一只指数时不能用零替代", () => {
  const date = "2026-11-02";
  const ends = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"];
  const rows = ends.map(time => `${date} ${time},1,1,1,1,999,1.00`);
  const results = { sh: { rc: 0, data: { klines: rows } }, sz: { rc: 0, data: { klines: rows } }, star: { rc: 0, data: { klines: rows.slice(0, -1) } } };
  assert.equal(Object.keys(parseBrowserHistory(results, [date])).length, 0);
});

test("zero is not rendered as a positive-height bar", () => {
  const chart = chartElement("hourly-chart"), axis = { innerHTML: "" };
  renderChart(chart, axis, [{ period: "09:30–10:30", values: { sh: 0, sz: 0, star: 0 } }], row => row.period, "sum");
  assert.match(chart.innerHTML, /class="bar sum zero" data-series="sum" style="height:0%"/);
  assert.match(chart.innerHTML, /class="bar-value">0\.00/);
});
