"""The now-playing banner (#251), against busctl output captured on the bench."""

from __future__ import annotations

import http.client
import json

import pytest

from benchhud.browser_sources.hub import TopicHub
from benchhud.browser_sources.server import BrowserSourceServer
from benchhud.config import Config
from benchhud.media.now_playing import (
    NowPlaying,
    NowPlayingWatcher,
    Track,
    list_players,
    payload,
    read_player,
)

CHROME = "org.mpris.MediaPlayer2.chromium.instance260806"
VLC = "org.mpris.MediaPlayer2.vlc"

# Verbatim from the bench, Chrome playing a SoundCloud mix.
CHROME_PROPS = (
    '{"type":"a{sv}","data":{"mpris:artUrl":{"type":"s","data":"file:///tmp/.com.google.Chrome.2QRqFg"},'
    '"mpris:length":{"type":"x","data":16635657089},"mpris:trackid":{"type":"o","data":'
    '"/org/chromium/MediaPlayer2/TrackList/Track987C26F26AB1F40E01B06F30AD36D8C5"},'
    '"xesam:album":{"type":"s","data":""},"xesam:artist":{"type":"as","data":["Alegria"]},'
    '"xesam:title":{"type":"s","data":"Alegria - Voices In My Head (studio mix)"}}}\n'
    '{"type":"s","data":"Playing"}\n'
)


def props(title: str, artist: str, status: str, art: str = "") -> str:
    meta = {
        "xesam:title": {"type": "s", "data": title},
        "xesam:artist": {"type": "as", "data": [artist]},
    }
    if art:
        meta["mpris:artUrl"] = {"type": "s", "data": art}
    return (
        json.dumps({"type": "a{sv}", "data": meta})
        + "\n"
        + json.dumps({"type": "s", "data": status})
    )


class Bus:
    """A fake ``busctl``: which players exist and what each reports."""

    def __init__(self, players: dict[str, str]) -> None:
        self.players = players

    def __call__(self, argv: list[str]) -> str:
        if argv[-1] == "list":
            names = [{"name": n} for n in self.players] + [{"name": "org.freedesktop.Notify"}]
            return json.dumps(names)
        return self.players.get(argv[4], "")


def test_reads_the_bench_chrome_track() -> None:
    track = read_player(CHROME, Bus({CHROME: CHROME_PROPS}))
    assert track.title == "Alegria - Voices In My Head (studio mix)"
    assert track.artist == "Alegria" and track.playing
    assert track.art_path == "/tmp/.com.google.Chrome.2QRqFg"
    assert track.length_s == pytest.approx(16635.657089)
    assert track.display_title == "Voices In My Head (studio mix)"  # no repeated artist


def test_only_mpris_players_are_listed() -> None:
    assert list_players(Bus({CHROME: "", VLC: ""})) == [CHROME, VLC]
    assert list_players(lambda _argv: "") == []


def test_a_player_with_nothing_loaded_is_skipped() -> None:
    assert read_player(VLC, Bus({VLC: props("", "", "Stopped")})) is None
    assert read_player(VLC, Bus({VLC: "garbage"})) is None


def test_display_title_leaves_other_titles_alone() -> None:
    track = Track("Voices In My Head", "Alegria", "", None, None, "Playing", CHROME)
    assert track.display_title == "Voices In My Head"
    en_dash = chr(0x2013)
    dashed = Track(f"Alegria {en_dash} Live at Space", "Alegria", "", None, None, "Playing", CHROME)
    assert dashed.display_title == "Live at Space"


def test_the_playing_player_wins_and_a_paused_track_stays() -> None:
    bus = Bus({CHROME: props("Mix A", "Jill", "Playing"), VLC: props("Other", "X", "Paused")})
    source = NowPlaying(bus)
    assert source.poll().title == "Mix A"
    bus.players[CHROME] = props("Mix A", "Jill", "Paused")
    track = source.poll()
    assert track.title == "Mix A" and not track.playing  # not VLC's paused track
    bus.players[VLC] = props("Other", "X", "Playing")
    assert source.poll().title == "Other"


def test_a_closed_player_marks_the_track_stopped() -> None:
    bus = Bus({CHROME: props("Mix A", "Jill", "Playing")})
    source = NowPlaying(bus)
    source.poll()
    del bus.players[CHROME]
    assert source.poll().status == "Stopped"


def test_payload_hides_when_paused_unless_asked() -> None:
    track = Track("Mix A", "Jill", "", "/tmp/art", None, "Paused", CHROME)
    assert (
        payload(track, hide_when_paused=True, label="Now playing", art_version=1)["visible"]
        is False
    )
    body = payload(track, hide_when_paused=False, label="On air", art_version=3)
    assert body["visible"] and body["label"] == "On air" and not body["playing"]
    assert body["art"] == "/panel/nowplaying/art?v=3"
    assert payload(None, hide_when_paused=True, label="x", art_version=0) == {
        "visible": False,
        "label": "x",
    }


def test_the_watcher_publishes_changes_only_and_bumps_the_art(tmp_path) -> None:
    art = tmp_path / "art"
    art.write_bytes(b"\x89PNG\r\n\x1a\n...")
    bus = Bus({CHROME: props("Mix A", "Jill", "Playing", f"file://{art}")})
    sent: list[dict] = []
    watcher = NowPlayingWatcher(sent.append, source=NowPlaying(bus))
    watcher.step()
    watcher.step()  # nothing changed: nothing sent
    assert len(sent) == 1 and sent[0]["title"] == "Mix A"
    first_art = sent[0]["art"]
    bus.players[CHROME] = props("Mix B", "Jill", "Playing", f"file://{art}")  # same art file
    watcher.step()
    assert sent[-1]["title"] == "Mix B" and sent[-1]["art"] != first_art  # cache busted
    assert watcher.art() == (b"\x89PNG\r\n\x1a\n...", "image/png")


def test_art_that_isnt_an_image_is_never_served(tmp_path) -> None:
    secret = tmp_path / "notes.txt"
    secret.write_text("not an image")
    bus = Bus({CHROME: props("Mix A", "Jill", "Playing", f"file://{secret}")})
    watcher = NowPlayingWatcher(lambda _b: None, source=NowPlaying(bus))
    watcher.step()
    assert watcher.art() is None


def test_web_art_urls_are_ignored() -> None:
    bus = Bus({CHROME: props("Mix A", "Jill", "Playing", "https://example.com/a.jpg")})
    assert read_player(CHROME, bus).art_path is None


def test_the_server_serves_the_page_and_the_art() -> None:
    server = BrowserSourceServer(
        TopicHub(), 0, now_playing_art=lambda: (b"\x89PNGdata", "image/png")
    )
    server.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", "/panel/nowplaying")
        page = conn.getresponse()
        assert page.status == 200 and b"nowplaying" in page.read()
        conn.request("GET", "/panel/nowplaying/art?v=2")
        art = conn.getresponse()
        assert art.status == 200 and art.getheader("Content-Type") == "image/png"
        assert art.read() == b"\x89PNGdata"
    finally:
        server.stop()


def test_no_art_is_a_404() -> None:
    server = BrowserSourceServer(TopicHub(), 0)
    server.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", "/panel/nowplaying/art")
        assert conn.getresponse().status == 404
    finally:
        server.stop()


def test_config_defaults_and_round_trip() -> None:
    cfg = Config()
    assert cfg.now_playing.enabled and cfg.now_playing.hide_when_paused
    back = Config.from_dict(
        {"now_playing": {"enabled": False, "label": "Spinning", "hide_when_paused": False}}
    )
    assert not back.now_playing.enabled and back.now_playing.label == "Spinning"
