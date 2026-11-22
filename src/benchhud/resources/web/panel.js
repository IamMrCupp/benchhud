// Shared by every browser-source page: subscribe to one panel's live feed, and
// reload the page when the benchhud behind it has changed.
//
// EventSource reconnects by itself, so a page left in an OBS scene picks the
// feed back up when benchhud restarts. While the feed is down the page is
// handed `null` and shows placeholders: a frozen reading on stream would pass
// for a live one.

"use strict";

function subscribe(topic, render) {
  openFeed(
    `/events/${topic}`,
    (event) => {
      document.body.classList.remove("offline");
      render(JSON.parse(event.data));
    },
    () => {
      document.body.classList.add("offline");
      render(null);
    },
  );
  render(null);
}

// Reconnecting isn't reloading (#501). OBS keeps this page loaded across a
// benchhud upgrade, so without this it goes on drawing the old layout with the
// new data. The server stamps each page with a build, a hash of the files the
// pages are made of, and opens every feed by naming its own. When they differ,
// the page is older than the benchhud it's talking to.

// How long to wait for the page before giving up on a check, and how long to
// leave it before asking again.
const CHECK_TIMEOUT_MS = 10000;
const RETRY_MS = 5000;

// The build this page was served from. Empty when the server didn't stamp it.
function loadedBuild() {
  const meta = document.querySelector('meta[name="benchhud-build"]');
  return meta ? meta.content : "";
}

// One reload per build, per page. If a reload ever comes back with the old page
// (a cache in between, say), a second one won't help, and a page that blinks
// every few seconds on stream is worse than a stale one. The record is kept
// under the page's own path: storage is shared by every page in a tab, and one
// page's reload mustn't count as another's.
const RELOADED_FOR = `benchhud-reloaded-for:${location.pathname}`;

function alreadyReloadedFor(build) {
  try {
    return sessionStorage.getItem(RELOADED_FOR) === build;
  } catch {
    return false;
  }
}

function rememberReloadFor(build) {
  try {
    sessionStorage.setItem(RELOADED_FOR, build);
  } catch {
    // No storage: the reload goes ahead without the guard.
  }
}

// A build the server wouldn't serve this page for. Left alone for the rest of
// this page's life: asking again gets the same answer.
let refusedBuild = "";

function isStale(build) {
  const mine = loadedBuild();
  if (!build || !mine || build === mine) return false;
  return build !== refusedBuild && !alreadyReloadedFor(build);
}

// Is this page's own address being served right now? True, false (the server
// said no), or null (no answer in time). A reload that fails leaves an error
// page with no script on it, and that never comes back by itself.
function pageIsServed() {
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), CHECK_TIMEOUT_MS);
  return fetch(location.href, { cache: "no-store", signal: control.signal })
    // Read the body to the end, so the connection is free for the reload.
    .then((response) => response.text().then(() => response.ok))
    .catch(() => null)
    .finally(() => clearTimeout(timer));
}

// Open a feed. `onMessage` gets each event; `onDown` is called whenever the
// feed stops, and is where the page blanks what it shows.
function openFeed(url, onMessage, onDown) {
  const source = new EventSource(url);
  source.onmessage = onMessage;
  source.onerror = onDown;
  source.addEventListener("build", (event) => {
    const build = event.data;
    if (!isStale(build)) return;
    // Give the connection up first. A browser allows six to one host and every
    // feed holds one, so with six pages open neither the check nor the reload
    // would ever get through while this page kept its own.
    source.close();
    onDown();
    pageIsServed().then((served) => {
      if (served) {
        rememberReloadFor(build);
        location.reload();
      } else if (served === false) {
        refusedBuild = build;
        openFeed(url, onMessage, onDown);
      } else {
        setTimeout(() => openFeed(url, onMessage, onDown), RETRY_MS);
      }
    });
  });
}
