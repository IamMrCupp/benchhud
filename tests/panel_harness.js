// Runs the real panel.js against stand-ins for the browser, for test_panel_js.py.
//
//   node panel_harness.js <panel.js> <scenario.json>
//
// The scenario says what the page was stamped with, what the storage holds and
// how each fetch ends, then lists what the feed does and how time passes. The
// result, on stdout, says what the page did about it.
//
// Timers are the harness's own: nothing here waits on a real clock.

"use strict";

const fs = require("node:fs");
const vm = require("node:vm");

const [script, scenarioFile] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));

const result = { reloads: [], fetches: [], renders: [], offline: false, feeds: [], closed: 0 };

// --- the feed ---------------------------------------------------------------

const feeds = [];
const openFeeds = () => feeds.filter((feed) => !feed.closed).length;

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.listeners = {};
    feeds.push(this);
    result.feeds.push(url);
  }
  addEventListener(name, handler) {
    (this.listeners[name] ||= []).push(handler);
  }
  close() {
    if (!this.closed) result.closed += 1;
    this.closed = true;
  }
  // A closed EventSource delivers nothing more, as in a browser.
  dispatch(name, data) {
    if (this.closed) return;
    if (name === "message") this.onmessage({ data });
    else if (name === "error") this.onerror({});
    else for (const handler of this.listeners[name] || []) handler({ data });
  }
}

// --- time -------------------------------------------------------------------

let now = 0;
let nextTimer = 1;
const timers = new Map();
const setTimeout_ = (callback, delay) => {
  timers.set(nextTimer, { at: now + (delay || 0), callback });
  return nextTimer++;
};
const clearTimeout_ = (id) => timers.delete(id);
const settle = () => new Promise((resolve) => setImmediate(resolve));

async function advance(ms) {
  const until = now + ms;
  for (;;) {
    const due = [...timers.entries()]
      .filter(([, timer]) => timer.at <= until)
      .sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    timers.delete(due[0]);
    now = due[1].at;
    due[1].callback();
    await settle();
  }
  now = until;
}

// --- storage and fetch ------------------------------------------------------

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
  const outcome = outcomes.shift() || "ok";
  const call = {
    url,
    cache: options.cache,
    at: now,
    openFeeds: openFeeds(),
    bodyRead: false,
    aborted: false,
  };
  result.fetches.push(call);
  if (outcome === "reject") return Promise.reject(new Error("connection refused"));
  if (outcome === "hang") {
    // Never answers; ends only when the page gives up on it.
    return new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        call.aborted = true;
        reject(new Error("aborted"));
      });
    });
  }
  return Promise.resolve({
    ok: outcome === "ok",
    text() {
      call.bodyRead = true;
      return Promise.resolve("");
    },
  });
}

// --- the page ---------------------------------------------------------------

const context = vm.createContext({
  EventSource: FakeEventSource,
  AbortController,
  setTimeout: setTimeout_,
  clearTimeout: clearTimeout_,
  sessionStorage,
  fetch,
  location: {
    href: "http://127.0.0.1:8780/panel/job?credit=0",
    pathname: "/panel/job",
    reload() { result.reloads.push({ at: now, openFeeds: openFeeds() }); },
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

(async () => {
  for (const step of scenario.steps || []) {
    // Events go to the newest feed: the one a browser would be delivering on.
    const feed = feeds[feeds.length - 1];
    if ("build" in step) feed.dispatch("build", step.build);
    else if ("message" in step) feed.dispatch("message", JSON.stringify(step.message));
    else if (step.error) feed.dispatch("error");
    else if ("advance" in step) await advance(step.advance);
    if (!step.together) await settle();
  }
  await settle();
  result.stored = Object.fromEntries(stored);
  result.openFeeds = openFeeds();
  result.pendingTimers = timers.size;
  process.stdout.write(JSON.stringify(result));
})();
