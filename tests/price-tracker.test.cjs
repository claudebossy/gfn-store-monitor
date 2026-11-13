const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = readFileSync(path.join(__dirname, "..", "site", "index.html"), "utf8");
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const DAY = 86400000;
const sample = (day, price, available = true) => ({
  timestamp: new Date(Date.UTC(2026, 0, day)).toISOString(),
  price,
  available
});
const products = [
  {
    id: "quest-steam",
    label: "Qu\u00eate: Builder's - PC (Steam)",
    title: "Qu\u00eate edition",
    page_url: "https://example.com/quest",
    current: { ...sample(31, 15), discount_percent: 50 },
    history: [sample(31, 15), sample(1, 25), sample(30, 20), sample(10, 5, false)]
  },
  {
    id: "quest-xbox",
    label: "Qu\u00eate - Xbox",
    page_url: "https://example.com/xbox",
    current: { ...sample(31, 10, false), discount_percent: 60 },
    history: [sample(1, 20), sample(31, 10, false), sample(30, 10, false)]
  },
  {
    id: "another",
    label: "Another Game",
    page_url: "https://example.com/another",
    current: { ...sample(31, 30), discount_percent: 10 },
    history: [sample(30, 25), sample(31, 30)]
  },
  {
    id: "new",
    label: "New Game",
    page_url: "https://example.com/new",
    current: sample(31, 40),
    history: [sample(31, 40)]
  }
];

async function loadPage(data = { currency: "CHF", products }) {
  const nodes = new Map();
  class Node {
    constructor() {
      this.children = [];
      this.listeners = new Map();
      this.attributes = {};
      this.style = { setProperty() {} };
      this.classList = { toggle() {} };
      this.value = "";
      this.checked = false;
      this.dataset = {};
    }
    set id(id) { nodes.set(id, this); this._id = id; }
    get id() { return this._id; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    fire(name) { this.listeners.get(name)(); }
    focus() {}
  }
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Node());
    return nodes.get(id);
  };
  const periods = [7, 30, 90, 0].map((days) => {
    const button = new Node();
    button.dataset.days = String(days);
    return button;
  });
  class Chart {
    constructor(canvas, config) {
      this.data = config.data;
      this.options = config.options;
      this.original = { ...config.options.scales.x };
      this.scales = { x: {} };
      this.update();
    }
    update() {
      this.scales.x.min = this.options.scales.x.min;
      this.scales.x.max = this.options.scales.x.max;
    }
    resetZoom() {
      Object.assign(this.options.scales.x, this.original);
      this.update();
    }
    destroy() {}
  }
  const context = vm.createContext({
    Intl, Date, Set, Map, Chart,
    document: {
      documentElement: {},
      getElementById: node,
      createElement: () => new Node(),
      querySelector: node,
      querySelectorAll: () => periods
    },
    getComputedStyle: () => ({ getPropertyValue: () => "#000000" }),
    fetch: async () => ({ ok: true, json: async () => structuredClone(data) })
  });
  node("product-sort").value = "name";
  node("product-filter").value = "all";
  vm.runInContext(script, context);
  await new Promise(setImmediate);
  const evaluate = (code) => vm.runInContext(code, context);
  const json = (code) => JSON.parse(JSON.stringify(evaluate(code)));
  return { node, periods, evaluate, json };
}

test("search matches accent-insensitive words in any order, editions, and punctuation", async () => {
  const page = await loadPage();
  assert.deepEqual(
    page.json('filterProducts(trackerData.products, "STEAM quete builders", "all", false).map(p => p.id)'),
    ["quest-steam"]
  );
  assert.equal(page.evaluate('filterProducts(trackerData.products, "xbox quete", "all", false).length'), 1);
  assert.equal(page.evaluate('filterProducts(trackerData.products, "missing", "all", false).length'), 0);
});

test("recorded lows exclude unavailable samples and changes use the previous chronological sample", async () => {
  const page = await loadPage();
  const stats = page.json('productStats.get("quest-steam")');
  assert.equal(stats.lowest, 15);
  assert.equal(stats.atLow, true);
  assert.equal(stats.change, -5);
  assert.equal(page.evaluate('productStats.get("quest-xbox").atLow'), false);
  assert.equal(page.evaluate('productStats.get("new").change'), null);
  assert.deepEqual(
    page.json('filterProducts(trackerData.products, "", "price-drop", false).map(p => p.id)'),
    ["quest-steam"]
  );
  assert.equal(page.evaluate('filterProducts(trackerData.products, "", "in-stock", false).length'), 3);
  assert.equal(page.evaluate('filterProducts(trackerData.products, "", "lowest", false).length'), 2);
});

test("sorting uses price, discount, and numeric drops with missing changes last", async () => {
  const page = await loadPage();
  assert.deepEqual(
    page.json('sortProducts(trackerData.products, "price").map(p => p.id)'),
    ["quest-xbox", "quest-steam", "another", "new"]
  );
  assert.deepEqual(
    page.json('sortProducts(trackerData.products, "discount").map(p => p.id)'),
    ["quest-xbox", "quest-steam", "another", "new"]
  );
  assert.deepEqual(
    page.json('sortProducts(trackerData.products, "price-drop").map(p => p.id)'),
    ["quest-steam", "quest-xbox", "another", "new"]
  );
});

test("filtering preserves hidden selections and bulk actions affect only matches", async () => {
  const page = await loadPage();
  page.node("product-search").value = "steam";
  page.node("product-search").fire("input");
  assert.equal(page.evaluate("visibleProducts.length"), 1);
  assert.equal(page.evaluate("selectedIds.size"), 4);
  page.node("clear-matches").fire("click");
  assert.deepEqual(page.json("Array.from(selectedIds)"), ["quest-xbox", "another", "new"]);
  page.node("select-all").fire("click");
  assert.equal(page.evaluate("selectedIds.size"), 4);
  page.node("clear-all").fire("click");
  assert.equal(page.evaluate("selectedIds.size"), 0);
  assert.equal(page.node("chart-empty").hidden, false);
  assert.equal(page.node("zoom-in").disabled, true);
  page.node("select-all").fire("click");
  assert.deepEqual(page.json("Array.from(selectedIds)"), ["quest-steam"]);
  page.node("selected-only").checked = true;
  page.node("selected-only").fire("change");
  assert.equal(page.evaluate("visibleProducts.length"), 1);
  page.node("reset-filters").fire("click");
  assert.equal(page.evaluate("visibleProducts.length"), 4);
  assert.equal(page.evaluate("selectedIds.size"), 1);
});

test("Show only replaces comparison and dataset colors survive selection and sorting", async () => {
  const page = await loadPage();
  const color = page.evaluate('productStats.get("quest-steam").color');
  page.node("product-search").value = "steam";
  page.node("product-search").fire("input");
  const row = page.node("product-selector").children[0];
  row.children[1].children.at(-1).fire("click");
  assert.deepEqual(page.json("Array.from(selectedIds)"), ["quest-steam"]);
  assert.equal(page.evaluate("chart.data.datasets[0].borderColor"), color);
  assert.equal(page.node("summary").children[0].children.length, 1);
  page.node("product-sort").value = "price";
  page.node("product-sort").fire("change");
  assert.equal(page.evaluate("chart.data.datasets[0].borderColor"), color);
});

test("chart presets, zoom and pan clamp to selected history and reset restores it", async () => {
  const page = await loadPage();
  page.periods[0].fire("click");
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), 7 * DAY);
  const end = page.evaluate("chart.scales.x.max");
  page.node("zoom-in").fire("click");
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), 3.5 * DAY);
  const start = page.evaluate("chart.scales.x.min");
  page.node("pan-back").fire("click");
  assert.ok(page.evaluate("chart.scales.x.min") < start);
  for (let i = 0; i < 30; i++) page.node("pan-back").fire("click");
  assert.equal(page.evaluate("chart.scales.x.min"), page.evaluate("timeBounds.min"));
  assert.equal(page.node("pan-back").disabled, true);
  page.node("reset-zoom").fire("click");
  assert.equal(page.evaluate("chart.scales.x.max"), end);
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), 30 * DAY);
  assert.equal(page.node("zoom-out").disabled, true);
  assert.equal(page.periods[3].attributes["aria-pressed"], "true");
  for (let i = 0; i < 20; i++) page.node("zoom-in").fire("click");
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), DAY / 24);
  assert.equal(page.node("zoom-in").disabled, true);
  const customWindow = page.json("({ min: chart.scales.x.min, max: chart.scales.x.max })");
  page.evaluate('selectedIds.delete("another"); renderChart()');
  assert.deepEqual(
    page.json("({ min: chart.scales.x.min, max: chart.scales.x.max })"),
    customWindow
  );
});

test("chart handles a single sample, missing history, and invalid prices", async () => {
  const page = await loadPage();
  page.evaluate('selectedIds = new Set(["new"]); renderChart()');
  assert.equal(page.evaluate("timeBounds.max - timeBounds.min"), DAY);
  page.node("zoom-in").fire("click");
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), DAY / 2);
  const missing = await loadPage({
    currency: "CHF",
    products: [{
      id: "empty", label: "Empty game", current: sample(31, null),
      history: [sample(31, null), { timestamp: "invalid", price: 2 }]
    }]
  });
  assert.equal(missing.evaluate("timeBounds"), null);
  assert.equal(missing.node("chart-empty").hidden, false);
  assert.equal(missing.node("zoom-in").disabled, true);
  assert.equal(missing.evaluate('productStats.get("empty").atLow'), false);
  assert.equal(missing.evaluate("formatMoney(null, 'CHF')"), "Price unavailable");
});

test("an empty catalog shows an actionable empty state and disables chart navigation", async () => {
  const page = await loadPage({ currency: "CHF", products: [] });
  assert.match(page.node("product-selector").children[0].textContent, /No products are configured/);
  assert.equal(page.node("chart-empty").hidden, false);
  assert.equal(page.node("select-all").disabled, true);
  assert.equal(page.node("pan-back").disabled, true);
  assert.equal(page.node("reset-zoom").disabled, true);
});

test("insights list current lows and order in-stock drops by their size", async () => {
  const page = await loadPage({
    currency: "CHF",
    products: [
      ...products,
      {
        id: "big-drop", label: "Big drop", current: sample(31, 18),
        history: [sample(1, 10), sample(30, 30), sample(31, 18)]
      },
      {
        id: "unavailable-drop", label: "Unavailable drop", current: sample(31, 1, false),
        history: [sample(30, 20), sample(31, 1, false)]
      }
    ]
  });
  const labels = (id) => page.node(id).children.map((row) =>
    row.children[0].children[0].textContent
  );
  assert.deepEqual(labels("insight-lows"), [products[0].label, "New Game"]);
  assert.deepEqual(labels("insight-drops"), ["Big drop", products[0].label]);
  assert.equal(page.node("insight-low-count").textContent, "(2)");
  assert.equal(page.node("insight-drop-count").textContent, "(2)");
  assert.equal(
    page.node("insight-drops").children[0].children[0].children[2].textContent,
    `Down ${page.evaluate('formatMoney(12, "CHF")')} since previous sample`
  );
});

test("insights ignore browsing and comparison filters and can focus a game's full history", async () => {
  const page = await loadPage();
  page.node("product-search").value = "another";
  page.node("product-search").fire("input");
  page.node("selected-only").checked = true;
  page.node("clear-all").fire("click");
  assert.equal(page.evaluate("visibleProducts.length"), 0);
  assert.equal(page.node("insight-lows").children.length, 2);
  assert.equal(page.node("insight-drops").children.length, 1);
  page.evaluate("periodDays = 7");
  page.node("insight-drops").children[0].children[1].fire("click");
  assert.deepEqual(page.json("Array.from(selectedIds)"), ["quest-steam"]);
  assert.equal(page.evaluate("chart.data.datasets.length"), 1);
  assert.equal(page.evaluate("chart.scales.x.max - chart.scales.x.min"), 30 * DAY);
  assert.equal(page.evaluate("periodDays"), 0);
  assert.equal(page.node("product-search").value, "another");
});

test("insights show explicit empty states when no current opportunities exist", async () => {
  const page = await loadPage({ currency: "CHF", products: [products[1], products[2]] });
  assert.equal(page.node("insight-low-count").textContent, "(0)");
  assert.equal(page.node("insight-drop-count").textContent, "(0)");
  assert.match(page.node("insight-lows").children[0].textContent, /No in-stock products/);
  assert.match(page.node("insight-drops").children[0].textContent, /since the previous sample/);
});
