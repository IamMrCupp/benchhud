# Vendored web libraries

Copied from npm so the pages work with the bench offline. Nothing loads from a
CDN at stream time.

| File | Package | Version | Licence |
|---|---|---|---|
| `xterm.js`, `xterm.css` | `@xterm/xterm` | 6.0.0 | MIT (`LICENSE.xterm`) |
| `addon-fit.js` | `@xterm/addon-fit` | 0.11.0 | MIT (`LICENSE.addon-fit`) |

To update: `npm pack @xterm/xterm@<v> @xterm/addon-fit@<v>`, copy `lib/xterm.js`,
`css/xterm.css` and `lib/addon-fit.js` from the tarballs, and update this table.
