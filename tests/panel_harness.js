// Runs the real panel.js against stand-ins for the browser, for test_panel_js.py.
//
//   node panel_harness.js <panel.js> <scenario.json>
//
// The scenario says what the page was stamped with, what the storage holds and
// how each fetch ends, then lists what the feed does. The result, on stdout,
// says what the page did about it.

"use strict";

const fs = require("node:fs");
const vm = require("node:vm");

const [script, scenarioFile] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));

const result = { reloads: 0, fetches: [], renders: [], offline: false, sources: [] };

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    result.sources.push(url);
    FakeEventSource.last = this;
  }
  addEventListener(name, handler) {
    (this.listeners[name] ||= []).push(handler);
  }
  dispatch(name, data) {
    for (const handler of this.listeners[name] || []) handler({ data });
  }
}

const stored = new Map(Object.entries(scenario.stored || {}));
const sessionStorage = {
  getItem(key) {
    if (scenario.storage === "throws") throw new Error("storage is off");
    return stored.has(key) ? stored.get(key) : null;
  },
  setItem(key, value) {
    if (scenario.storage === "throws") throw new Error("storage is off");
    stored.set(key, String(value));
  },
};

const outcomes = [...(scenario.fetch || [])];
function fetch(url, options) {
  result.fetches.push({ url, options });
  const outcome = outcomes.shift() || "ok";
  if (outcome === "hang") return new Promise(() => {});
  if (outcome === "reject") return Promise.reject(new Error("connection refused"));
  return Promise.resolve({ ok: outcome === "ok" });
}

const context = vm.createContext({
  EventSource: FakeEventSource,
  sessionStorage,
  fetch,
  location: {
    href: "http://127.0.0.1:8780/panel/job?credit=0",
    pathname: "/panel/job",
    reload() { result.reloads += 1; },
  },
  document: {
    querySelector(selector) {
      if (selector !== 'meta[name="benchhud-build"]') throw new Error(`asked for ${selector}`);
      return scenario.loaded == null ? null : { content: scenario.loaded };
    },
    body: {
      classList: {
        add(name) { if (name === "offline") result.offline = true; },
        remove(name) { if (name === "offline") result.offline = false; },
      },
    },
  },
  render(data) { result.renders.push(data); },
});

vm.runInContext(fs.readFileSync(script, "utf8"), context, { filename: script });
vm.runInContext('subscribe("job", render);', context);
const source = FakeEventSource.last;

const settle = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  for (const step of scenario.steps || []) {
    if ("build" in step) source.dispatch("build", step.build);
    else if ("message" in step) source.onmessage({ data: JSON.stringify(step.message) });
    else if (step.error) source.onerror();
    if (!step.together) await settle();
  }
  await settle();
  result.stored = Object.fromEntries(stored);
  process.stdout.write(JSON.stringify(result));
})();
