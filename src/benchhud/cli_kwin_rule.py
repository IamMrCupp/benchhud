"""``benchhud kwin-rule``: make KDE open the HUD on the big display (#529).

On Wayland a window can't place itself, so the HUD opens on whichever display
has focus: start it while OBS on the small display is focused and it lands
there. KWin's "force to screen" rule doesn't decide where a new window opens.
A starting position does.

This writes that position into KWin's window rules, for the window class
``benchhud``, and asks KWin to reload them. It reads the display layout from
``kscreen-doctor`` and edits ``kwinrulesrc`` through KDE's own ``kwriteconfig6``,
so it never parses or rewrites the file itself. A rule that already matches
the HUD is reused, and only its position is touched.

KWin has two layouts for that file, and writing the wrong one loses rules:

- Up to Plasma 6.7 a rule only counts when ``[General] rules`` lists its group,
  and KWin drops groups that aren't listed.
- From Plasma 6.8 every group is a rule and ``[General] Order`` holds the order.
  A ``rules`` list that isn't empty reads as an old file to migrate, and KWin
  then deletes every group the list doesn't name.

So the list is only written to a file that already has one, or to an empty
file under a KWin older than 6.8. (KWin's ``src/rulebooksettings.cpp``, v6.7.6
and v6.8.0.)

Qt-free, like the other commands. The position is where the display sits in
the desktop today: run it again after rearranging the displays.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

RULES_FILE = "kwinrulesrc"
WINDOW_CLASS = "benchhud"
#: The group a new rule goes in. An existing rule for the HUD keeps its own.
NEW_GROUP = "benchhud-display"
#: KWin's rule policies, as it stores them.
APPLY_INITIALLY = "3"
#: ``wmclassmatch``: the class must equal ours exactly.
MATCH_EXACT = "1"
#: ``types``: normal windows only.
NORMAL_WINDOWS = "1"
#: KWin's "don't match on this" for a rule's title, role or machine.
UNIMPORTANT = frozenset({"", "0"})
#: The first KWin with the every-group-is-a-rule file (6.7.90 was its beta).
NEW_FORMAT_FROM = (6, 7, 90)
TOOLS = ("kscreen-doctor", "kreadconfig6", "kwriteconfig6")
TOOL_TIMEOUT_S = 10.0

#: Runs one tool and returns its stdout; raises on a failure.
Run = Callable[[Sequence[str]], str]


class KwinRuleError(Exception):
    """The rule can't be worked out or written; the message says why."""


@dataclass(frozen=True)
class Display:
    """One enabled display, in the desktop's own (scaled) coordinates."""

    name: str
    x: int
    y: int
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


def displays(layout: str) -> list[Display]:
    """The enabled displays in ``kscreen-doctor -j`` output."""
    try:
        outputs = json.loads(layout)["outputs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise KwinRuleError(f"can't read the display layout from kscreen-doctor: {exc}") from exc
    if not isinstance(outputs, list):
        raise KwinRuleError("can't read the display layout from kscreen-doctor: no list of outputs")
    found = []
    for output in outputs:
        try:
            if not (output.get("enabled") and output.get("connected", True)):
                continue
            scale = float(output.get("scale") or 1.0)
            width = output["size"]["width"] / scale
            height = output["size"]["height"] / scale
            found.append(
                Display(
                    name=str(output["name"]),
                    x=int(output["pos"]["x"]),
                    y=int(output["pos"]["y"]),
                    width=width,
                    height=height,
                )
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError) as exc:
            raise KwinRuleError(f"can't read a display from kscreen-doctor: {exc!r}") from exc
    return found


def choose(found: Sequence[Display], name: str | None = None) -> Display:
    """The named display, or the biggest one (the first of equals)."""
    if not found:
        raise KwinRuleError("kscreen-doctor lists no enabled display")
    if name is None:
        return max(found, key=lambda display: display.area)
    for display in found:
        if display.name == name:
            return display
    names = ", ".join(display.name for display in found)
    raise KwinRuleError(f"no enabled display called {name!r} (there is: {names})")


def _read(run: Run, group: str, key: str) -> str:
    return run(["kreadconfig6", "--file", RULES_FILE, "--group", group, "--key", key]).strip()


def _write(run: Run, group: str, key: str, value: str) -> None:
    # "--" so a position left of or above the first display ("-1920,0") isn't read as an option.
    run(["kwriteconfig6", "--file", RULES_FILE, "--group", group, "--key", key, "--", value])


def _names(text: str) -> list[str]:
    return [name for name in text.split(",") if name]


def kwin_version(run: Run, which: Callable[[str], str | None]) -> tuple[int, ...] | None:
    """The installed KWin's version, or None when it can't be told."""
    for tool in ("kwin_wayland", "kwin_x11"):
        if not which(tool):
            continue
        try:
            words = run([tool, "--version"]).split()
        except KwinRuleError:
            continue
        for word in words:
            parts = word.split(".")
            if len(parts) >= 2 and all(part.isdigit() for part in parts):
                return tuple(int(part) for part in parts)
    return None


@dataclass(frozen=True)
class RuleBook:
    """What the rules file holds: the groups KWin knows about, and which layout it is."""

    groups: tuple[str, ...]
    #: True when a new rule also has to be added to ``[General] rules``.
    listed: bool


def rule_book(run: Run, which: Callable[[str], str | None]) -> RuleBook:
    """Work out the file's layout; refuse when a write could lose somebody's rules."""
    rules = _names(_read(run, "General", "rules"))
    if rules:
        # The old layout, or an old file a newer KWin hasn't migrated yet. A
        # list that stays complete is right for both.
        return RuleBook(tuple(rules), listed=True)
    order = _names(_read(run, "General", "Order"))
    if order:
        return RuleBook(tuple(order), listed=False)
    count = _read(run, "General", "count")
    if count not in {"", "0"}:
        raise KwinRuleError(
            f"{RULES_FILE} has {count} rules in the layout from before rule lists; "
            "open System Settings > Window Rules once so KWin updates the file, then run this again"
        )
    version = kwin_version(run, which)
    if version is None:
        raise KwinRuleError(
            f"{RULES_FILE} has no rule list and KWin's version can't be read, "
            "so it isn't safe to tell which layout to write"
        )
    return RuleBook((), listed=version < NEW_FORMAT_FROM)


def hud_group(run: Run, groups: Sequence[str]) -> str | None:
    """The existing rule that is for the HUD's window and nothing else, if any.

    A rule that's switched off, or narrowed to one title, role or machine, is
    somebody's own and is left alone.
    """
    for group in groups:
        if (
            _read(run, group, "wmclass") == WINDOW_CLASS
            and _read(run, group, "wmclassmatch") == MATCH_EXACT
            and _read(run, group, "Enabled").lower() != "false"
            and _read(run, group, "titlematch") in UNIMPORTANT
            and _read(run, group, "windowrolematch") in UNIMPORTANT
            and _read(run, group, "clientmachinematch") in UNIMPORTANT
        ):
            return group
    return None


def new_group_name(run: Run, groups: Sequence[str]) -> str:
    """A group name nothing in the file uses, listed or not."""
    name, n = NEW_GROUP, 2
    while name in groups or _read(run, name, "wmclass") or _read(run, name, "Description"):
        name, n = f"{NEW_GROUP}-{n}", n + 1
    return name


def write_rule(run: Run, display: Display, book: RuleBook) -> str:
    """Give the HUD's rule a starting position on ``display``; return its group."""
    group = hud_group(run, book.groups)
    is_new = group is None
    if group is None:
        group = new_group_name(run, book.groups)
        _write(run, group, "Description", "benchhud opens on the big display")
        _write(run, group, "wmclass", WINDOW_CLASS)
        _write(run, group, "wmclassmatch", MATCH_EXACT)
        _write(run, group, "wmclasscomplete", "false")
        _write(run, group, "types", NORMAL_WINDOWS)
    _write(run, group, "position", f"{display.x},{display.y}")
    _write(run, group, "positionrule", APPLY_INITIALLY)
    if is_new and book.listed:
        # Listed last, once the rule is whole. Never in the new layout: a list
        # there makes KWin delete every rule it doesn't name.
        groups = [*book.groups, group]
        _write(run, "General", "rules", ",".join(groups))
        _write(run, "General", "count", str(len(groups)))
    return group


def screen_note(run: Run, group: str) -> str | None:
    """A line about a screen setting the reused rule also has, which is left as it is."""
    if _read(run, group, "screenrule") in UNIMPORTANT:
        return None
    screen = _read(run, group, "screen")
    return f"note: [{group}] also has a screen setting (screen={screen}); left as it is"


def reload_kwin(run: Run, which: Callable[[str], str | None] = shutil.which) -> bool:
    """Ask the running KWin to read its rules again. False when it couldn't be asked."""
    for tool in ("qdbus6", "qdbus-qt6", "qdbus"):
        if which(tool):
            try:
                run([tool, "org.kde.KWin", "/KWin", "reconfigure"])
            except KwinRuleError:
                return False
            return True
    return False


def run_tool(command: Sequence[str]) -> str:
    """Run one KDE tool; its stdout, or a KwinRuleError saying what went wrong."""
    try:
        done = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=TOOL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KwinRuleError(f"{command[0]}: {exc}") from exc
    if done.returncode != 0:
        detail = done.stderr.strip() or f"exit code {done.returncode}"
        raise KwinRuleError(f"{command[0]}: {detail}")
    return done.stdout


USAGE = """\
usage: benchhud kwin-rule [--output NAME] [--dry-run]

Make KDE open the benchhud window on the biggest display, whichever display
has focus when it starts. Writes a starting position into KWin's window rules
and asks KWin to reload them. Run it again after rearranging the displays.

  --output NAME  use this display (a name from `kscreen-doctor -o`), not the biggest
  --dry-run      say what would be written, and write nothing
"""


def main(
    argv: Sequence[str],
    out: Callable[[str], None] | None = None,
    err: Callable[[str], None] | None = None,
    platform: str | None = None,
    run: Run = run_tool,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """``benchhud kwin-rule``: exit code 0 when the rule is written (or would be)."""
    out = out or print
    err = err or (lambda text: print(text, file=sys.stderr))
    name: str | None = None
    dry_run = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg in {"-h", "--help"}:
            out(USAGE.rstrip("\n"))
            return 0
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--output" and args and not args[0].startswith("-"):
            name = args.pop(0)
        elif arg.startswith("--output=") and arg != "--output=":
            name = arg.split("=", 1)[1]
        elif arg.startswith("--output"):
            err("benchhud kwin-rule: --output needs a display name")
            return 2
        else:
            err(f"benchhud kwin-rule: don't know what to do with {arg!r}")
            err(USAGE.rstrip("\n"))
            return 2
    if not (platform or sys.platform).startswith("linux"):
        err("benchhud kwin-rule: KWin is Linux only")
        return 1
    missing = [tool for tool in TOOLS if not which(tool)]
    if missing:
        err(f"benchhud kwin-rule: needs {', '.join(missing)} (is this a KDE Plasma 6 desktop?)")
        return 1
    try:
        display = choose(displays(run(["kscreen-doctor", "-j"])), name)
        where = f"{display.name} at {display.x},{display.y}"
        book = rule_book(run, which)
        if dry_run:
            group = hud_group(run, book.groups)
            rule = f"the existing rule [{group}]" if group else "a new rule"
            out(f"would set {rule} to open benchhud on {where}")
            return 0
        group = write_rule(run, display, book)
        note = screen_note(run, group)
    except KwinRuleError as exc:
        err(f"benchhud kwin-rule: {exc}")
        return 1
    out(f"rule [{group}]: benchhud opens on {where}")
    if note:
        out(note)
    if reload_kwin(run, which):
        out("KWin reloaded its rules; the next start of benchhud uses it")
    else:
        out("couldn't ask KWin to reload; it takes effect at the next login")
    return 0
