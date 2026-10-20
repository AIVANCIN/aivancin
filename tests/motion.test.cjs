const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const source = readFileSync(resolve(__dirname, "../src/aivancin/static/motion.js"), "utf8");

function boot({ reduced = false, saved = null, search = "", blocked = false } = {}) {
  const events = {}, queryEvents = {}, writes = [];
  const inputs = ["auto", "force", "reduce"].map(value => ({
    value, checked: false, addEventListener(type, handler) { this[type] = handler; },
  }));
  const setting = { hidden: true, querySelectorAll: () => inputs };
  const document = {
    documentElement: { dataset: {} }, hidden: false,
    addEventListener: (type, handler) => { events[type] = handler; },
    querySelectorAll: () => [setting],
  };
  const query = { matches: reduced, addEventListener: (type, handler) => { queryEvents[type] = handler; } };
  const localStorage = {
    getItem() { if (blocked) throw Error("blocked"); return saved; },
    setItem(key, value) { if (blocked) throw Error("blocked"); writes.push([key, value]); },
  };
  runInNewContext(source, { document, window: { matchMedia: () => query, location: { search } }, localStorage, URLSearchParams });
  return { document, data: document.documentElement.dataset, query, queryEvents, events, inputs, setting, writes };
}

for (const reduced of [false, true]) {
  for (const [saved, expected] of [[null, reduced ? "off" : "on"], ["force", "on"], ["reduce", "off"]]) {
    test(`${saved || "Device default"}, OS reduced=${reduced}: ${expected} before DOM ready`, () => {
      const app = boot({ reduced, saved });
      assert.equal(app.data.motion, expected);
      assert.equal(app.data.motionMode, saved || "auto");
    });
  }
}
test("URL wins over saved choice and persists under the app key", () => {
  for (const mode of ["auto", "force", "reduce"]) {
    const app = boot({ saved: "reduce", search: `?motion=${mode}` });
    assert.equal(app.data.motionMode, mode);
    assert.deepEqual(app.writes, [["aivancin-motion", mode]]);
  }
  assert.equal(boot({ saved: "force", search: "?motion=invalid" }).data.motionMode, "force");
  assert.equal(boot({ saved: "invalid" }).data.motionMode, "auto");
});
test("blocked storage still allows keyboard-driven radio changes", () => {
  const app = boot({ blocked: true, reduced: true });
  app.events.DOMContentLoaded();
  assert.equal(app.setting.hidden, false);
  assert.equal(app.inputs[0].checked, true);
  app.inputs[1].checked = true;
  app.inputs[1].change();
  assert.equal(app.data.motion, "on");
  assert.equal(app.data.motionMode, "force");
});
test("Device reacts to OS changes; Full stays on; visibility pauses motion", () => {
  const app = boot();
  app.query.matches = true;
  app.queryEvents.change();
  assert.equal(app.data.motion, "off");
  app.events.DOMContentLoaded();
  app.inputs[1].checked = true;
  app.inputs[1].change();
  app.queryEvents.change();
  assert.equal(app.data.motion, "on");
  app.document.hidden = true;
  app.events.visibilitychange();
  assert.equal(app.data.motionPaused, "true");
  app.document.hidden = false;
  app.events.visibilitychange();
  assert.equal(app.data.motionPaused, "false");
});
