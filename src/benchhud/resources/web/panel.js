// Shared by every browser-source page: subscribe to one panel's live feed.
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
  render(null);
}
