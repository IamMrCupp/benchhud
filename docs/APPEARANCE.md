# Appearance

benchhud uses burnwright's palette, light or dark, so the HUD, burnwright and the
web tools round the bench (intake, recon, parts) look like one family.

Pick the look from **View → Appearance**:

- **System** — light or dark, following the OS. Changes when the OS does.
- **Light** / **Dark** — fixed, whatever the OS says.
- **Themes** — any theme files in the themes folder.

The choice is saved to `[shell] appearance` in `config.toml` (`"system"`,
`"light"`, `"dark"` or `"theme:<file>"`). It's `"dark"` until you change it, so an
existing rig doesn't turn white the first time it meets a light-mode OS.

The instrument screens don't change with it: the meter's LCD cells, the power
chart and the power-on signature image keep their colours, because they match the
stream overlays — and a scope's channel colours need a dark screen to read.

## Custom themes

A theme is one JSON file — burnwright's format, so a file written for burnwright
or the web tools drops straight in:

```json
{
  "name": "Amber terminal",
  "base": "dark",
  "colors": {
    "bg": "#120d02",
    "panel": "#1c1406",
    "fg": "#ffb000",
    "accent": "#ffb000"
  }
}
```

- `base` is `"light"` or `"dark"`: what everything the file doesn't set falls back to.
- `colors` sets any of burnwright's `--bw-*` properties, with or without the
  prefix. benchhud uses `bg`, `panel`, `border`, `fg`, `muted`, `control-bg`,
  `control-border`, `control-hover`, `accent`, `accent-soft`, `danger`,
  `danger-soft`, `warning`, `warning-soft`, `warning-border`, `ok` and `ok-soft`.
  The rest (the hex editor's, the diff view's) are accepted and ignored, so a
  burnwright theme reads without complaint.
- Colours are hex (`#rgb`, `#rrggbb`, with or without alpha), `rgb()` or `hsl()`
  with plain numbers. Nothing else — no colour names, `var()` or `url()`. A theme
  sets colours and can't touch anything else.

Themes live in a `themes` folder beside `config.toml`, or wherever
`BENCHHUD_THEMES_DIR` points. **View → Appearance → Open themes folder** opens it
(and makes it the first time). A new or edited file shows up the next time the
menu opens; no restart. A file that can't be used is listed at the bottom of the
menu with the reason, and an entry it skipped is in the theme's tooltip.
