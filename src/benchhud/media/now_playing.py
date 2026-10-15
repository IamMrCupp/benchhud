"""Now playing, from the desktop's media players (#251).

On Linux, every player that follows the MPRIS standard publishes its track on
the session bus: Chrome does for SoundCloud (through the page's Media Session),
and so do VLC, Spotify and most others. This reads it with ``busctl --json``,
part of systemd, so there's no new dependency and no SoundCloud API (closed to
new apps anyway).

The player that's *playing* wins. When none is, the last track stays (marked
paused), so a muted tab elsewhere never takes over the banner.

The runner is injectable: tests feed it ``busctl`` output captured on the bench.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

log = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_PATH = "/org/mpris/MediaPlayer2"
_PLAYER = "org.mpris.MediaPlayer2.Player"

#: Runs a command and returns its stdout ("" on failure).
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    album: str
    art_path: str | None  # a local file the player keeps current, if any
    length_s: float | None
    status: str  # Playing | Paused | Stopped
    player: str

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    @property
    def display_title(self) -> str:
        """The title without a leading "Artist - ", which SoundCloud mixes repeat."""
        for artist in filter(None, [self.artist]):
            for sep in (" - ", " \u2013 ", " \u2014 "):  # hyphen, en dash, em dash
                prefix = f"{artist}{sep}"
                if self.title.lower().startswith(prefix.lower()) and len(self.title) > len(prefix):
                    return self.title[len(prefix) :]
        return self.title


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("now playing: %s failed: %s", argv[0], exc)
        return ""
    return done.stdout


def list_players(run: Runner = _run) -> list[str]:
    output = run(["busctl", "--user", "--json=short", "list"])
    try:
        names = [entry.get("name", "") for entry in json.loads(output or "[]")]
    except (ValueError, AttributeError):
        return []
    return sorted(name for name in names if name.startswith(MPRIS_PREFIX))


def _art_path(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme != "file" or not parts.path:
        return None  # an http URL would mean fetching from the internet; not here
    return unquote(parts.path)


def read_player(name: str, run: Runner = _run) -> Track | None:
    """One player's track, or None if it has nothing (or doesn't answer)."""
    output = run(
        [
            *("busctl", "--user", "--json=short", "get-property", name, _PATH, _PLAYER),
            *("Metadata", "PlaybackStatus"),
        ]
    )
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    try:
        metadata = json.loads(lines[0])["data"]
        status = json.loads(lines[1])["data"]
    except (ValueError, KeyError, TypeError):
        return None

    def field(key: str, default=None):
        value = metadata.get(key)
        return value.get("data", default) if isinstance(value, dict) else default

    title = str(field("xesam:title", "") or "").strip()
    if not title:
        return None
    artists = field("xesam:artist", []) or []
    artist = ", ".join(str(a) for a in artists) if isinstance(artists, list) else str(artists)
    length = field("mpris:length")
    return Track(
        title=title,
        artist=artist.strip(),
        album=str(field("xesam:album", "") or "").strip(),
        art_path=_art_path(str(field("mpris:artUrl", "") or "")),
        length_s=length / 1_000_000 if isinstance(length, int | float) and length > 0 else None,
        status=str(status),
        player=name,
    )


class NowPlaying:
    """Picks the track to show from all players, remembering the last one."""

    def __init__(self, run: Runner = _run) -> None:
        self._run = run
        self._last: Track | None = None

    def poll(self) -> Track | None:
        tracks = [t for t in (read_player(p, self._run) for p in list_players(self._run)) if t]
        playing = [t for t in tracks if t.playing]
        if playing:
            # Stick with the player already shown if it's still playing.
            same = [t for t in playing if self._last and t.player == self._last.player]
            self._last = (same or playing)[0]
        elif self._last is not None:
            current = next((t for t in tracks if t.player == self._last.player), None)
            self._last = current or replace(self._last, status="Stopped")
        return self._last


def payload(track: Track | None, *, hide_when_paused: bool, label: str, art_version: int) -> dict:
    """The browser source's JSON: only what goes on screen."""
    if track is None or (hide_when_paused and not track.playing):
        return {"visible": False, "label": label}
    return {
        "visible": True,
        "label": label,
        "title": track.display_title,
        "artist": track.artist,
        "art": f"/panel/nowplaying/art?v={art_version}" if track.art_path else None,
        "playing": track.playing,
    }


SUPPORTED = sys.platform.startswith("linux")


class NowPlayingWatcher:
    """Polls on a background thread and hands each change to ``publish``."""

    def __init__(
        self,
        publish: Callable[[dict], None],
        *,
        hide_when_paused: bool = True,
        label: str = "Now playing",
        interval: float = 1.0,
        source: NowPlaying | None = None,
    ) -> None:
        self._publish = publish
        self._hide = hide_when_paused
        self._label = label
        self._interval = interval
        self._source = source or NowPlaying()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_payload: dict | None = None
        self._art: str | None = None
        self._art_key: tuple[str | None, str] | None = None
        self._art_version = 0
        self._lock = threading.Lock()

    def art(self) -> tuple[bytes, str] | None:
        """The current artwork and its content type, for the server to send."""
        with self._lock:
            path = self._art
        if not path:
            return None
        try:
            data = Path(path).read_bytes()
        except OSError:
            return None
        if data.startswith(b"\x89PNG"):
            return data, "image/png"
        if data.startswith(b"\xff\xd8"):
            return data, "image/jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return data, "image/webp"
        return None  # not an image: never serve it

    def step(self) -> None:
        """One poll: publish if anything on screen changed."""
        track = self._source.poll()
        # Chrome reuses one art file across tracks, so a new title means new art
        # even at the same path: the version busts the page's image cache.
        key = (track.art_path, track.display_title) if track else None
        with self._lock:
            if key != self._art_key:
                self._art_key = key
                self._art = track.art_path if track else None
                self._art_version += 1
            version = self._art_version
        body = payload(track, hide_when_paused=self._hide, label=self._label, art_version=version)
        if body != self._last_payload:
            self._last_payload = body
            self._publish(body)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="now-playing", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # a bad poll must never take the thread down
                log.exception("now playing: poll failed")
            self._stop.wait(self._interval)
