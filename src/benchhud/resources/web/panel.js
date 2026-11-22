// Shared by every browser-source page: subscribe to one panel's live feed, and
// reload the page when the benchhud behind it has changed.
//
// EventSource reconnects by itself, so a page left in an OBS scene picks the
// feed back up when benchhud restarts. While the feed is down the page is
// handed `null` and shows placeholders: a frozen reading on stream would pass
// for a live one.

"use strict";

function subscribe(topic, render) {
  const source = new EventSource(`/events/${topic}`);
  source.onmessage = (event) => {
    document.body.classList.remove("offline");
    render(JSON.parse(event.data));
  };
  source.onerror = () => {
    document.body.classList.add("offline");
    render(null);
  };
  reloadWhenStale(source);
  render(null);
}

// Reconnecting isn't reloading (#501). OBS keeps this page loaded across a
// benchhud upgrade, so without this it goes on drawing the old layout with the
// new data. The server stamps each page with a build, a hash of the files the
// pages are made of, and opens every feed by naming its own. When they differ,
// the page is older than the benchhud it's talking to.

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

function reloadWhenStale(source) {
  let checking = false;
  source.addEventListener("build", (event) => {
    const build = event.data;
    const mine = loadedBuild();
    if (!build || !mine || build === mine) return;
    if (checking || alreadyReloadedFor(build)) return;
    checking = true;
    // Ask for the page first. A reload that fails leaves an error page with no
    // script on it, and that never comes back by itself.
    fetch(location.href, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) return;
        rememberReloadFor(build);
        location.reload();
      })
      .catch(() => {})
      .finally(() => { checking = false; });
  });
}
