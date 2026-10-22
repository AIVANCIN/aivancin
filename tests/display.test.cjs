const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const source = readFileSync(resolve(__dirname, "../src/aivancin/static/display.js"), "utf8");

/* Boots display.js against a minimal document: the three controls of the Display & accessibility sheet. */
function boot({ saved = {}, contrastQuery = false, blocked = false } = {}) {
  const events = {}, queryEvents = {}, writes = [];
  const control = extra => ({ ...extra, attrs: {}, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    setAttribute(name, value) { this.attrs[name] = value; } });
  const els = {
    "text-size": control({ value: "0" }), "text-size-val": control({ textContent: "" }),
    "bold-text": control({ checked: false }), "more-contrast": control({ checked: false }),
  };
  const document = {
    documentElement: { dataset: {} },
    addEventListener: (type, handler) => { events[type] = handler; },
    getElementById: id => els[id] || null,
  };
  const query = { matches: contrastQuery, addEventListener: (type, handler) => { queryEvents[type] = handler; } };
  const localStorage = {
    getItem(key) { if (blocked) throw Error("blocked"); return key in saved ? saved[key] : null; },
    setItem(key, value) { if (blocked) throw Error("blocked"); writes.push([key, value]); },
  };
  runInNewContext(source, { document, window: { matchMedia: () => query }, localStorage, Number, Math, String });
  return { data: document.documentElement.dataset, els, events, query, queryEvents, writes };
}

test("defaults before DOM ready: default size, regular weight, standard contrast", () => {
  const app = boot();
  assert.equal(app.data.textSize, "0");
  assert.equal(app.data.bold, "off");
  assert.equal(app.data.contrast, "standard");
});

test("saved choices apply before first paint; invalid sizes fall back to default", () => {
  const app = boot({ saved: { "aivancin-text-size": "4", "aivancin-bold-text": "on", "aivancin-contrast": "on" } });
  assert.deepEqual([app.data.textSize, app.data.bold, app.data.contrast], ["4", "on", "more"]);
  for (const bad of ["5", "-1", "2.5", "large", ""]) assert.equal(boot({ saved: { "aivancin-text-size": bad } }).data.textSize, "0");
});

test("contrast follows the device until the person chooses, then keeps the choice", () => {
  const app = boot({ contrastQuery: true });
  assert.equal(app.data.contrast, "more");
  app.events.DOMContentLoaded();
  assert.equal(app.els["more-contrast"].checked, true);
  app.query.matches = false;
  app.queryEvents.change();
  assert.equal(app.data.contrast, "standard");
  assert.equal(app.els["more-contrast"].checked, false);
  app.els["more-contrast"].checked = true;
  app.els["more-contrast"].listeners.change({ target: app.els["more-contrast"] });
  app.queryEvents.change();
  assert.equal(app.data.contrast, "more");
  assert.deepEqual(app.writes, [["aivancin-contrast", "on"]]);
  assert.equal(boot({ contrastQuery: true, saved: { "aivancin-contrast": "off" } }).data.contrast, "standard");
});

test("the slider sets the size, names it for assistive technology and saves it", () => {
  const app = boot();
  app.events.DOMContentLoaded();
  assert.equal(app.els["text-size"].attrs["aria-valuetext"], "Default, 100%");
  app.els["text-size"].value = "3";
  app.els["text-size"].listeners.input();
  assert.equal(app.data.textSize, "3");
  assert.equal(app.els["text-size-val"].textContent, "Extra large");
  assert.equal(app.els["text-size"].attrs["aria-valuetext"], "Extra large, 150%");
  assert.deepEqual(app.writes, [["aivancin-text-size", "3"]]);
  app.els["text-size"].value = "9";
  app.els["text-size"].listeners.input();
  assert.equal(app.data.textSize, "4");
});

test("bold text toggles and blocked storage still lets every control work for the visit", () => {
  const app = boot({ blocked: true });
  app.events.DOMContentLoaded();
  app.els["bold-text"].checked = true;
  app.els["bold-text"].listeners.change({ target: app.els["bold-text"] });
  assert.equal(app.data.bold, "on");
  app.els["text-size"].value = "2";
  app.els["text-size"].listeners.input();
  assert.equal(app.data.textSize, "2");
  app.els["more-contrast"].checked = true;
  app.els["more-contrast"].listeners.change({ target: app.els["more-contrast"] });
  assert.equal(app.data.contrast, "more");
});
