# screenshot-tool

Flameshot-style region cropper for GNOME/Wayland (miku), with one key difference:
**the hotkey shows a transparent frame first — no screenshot happens until you
press the capture button.**

## Flow

1. `Super+Alt+A` (hotkey) or `./shot` — the previous frame reappears exactly where
   you left it (position + size, remembered across restarts). Nothing is captured yet.
   First run, or a saved rect that no longer fits the desktop: crosshair, pull the
   rectangle from nothing.
2. Move it by the body (Move tool / default), resize by corner grips, expand to
   fullscreen with `⛶`. Arrow keys nudge it by 1 px.
   Capture history is on `Shift+←`/`Shift+→` (also the `◀ n/m ▶` buttons).
3. Annotate: Pen, Arrow, Box, Text; color + stroke-width cycle; `↶` undo.
4. Capture with the green **camera** button or **Ctrl+C** (end of the toolbar,
   side configurable):
   - the screen region under the frame is captured, drawings baked in,
   - saved to the configured folder **and** copied to the clipboard,
   - the transparent background becomes the captured image.
5. Mousedown on the camera button = capture; **keep holding** and you drag the
   captured shot around; body-drag is enabled automatically (Move).
6. Post-capture: **opacity** number field (drag it sideways to scrub, click
   to type), **Pin** — captures start *unpinned*: the
   shot may hang off the screen edges and other windows cover it. Pinning
   keeps it above everything instead (a pinned shot stays on-screen — that is
   the window manager's rule, not a choice). History `◀ n/m ▶`
   (`Shift+←`/`Shift+→`) — historical shots can be re-copied to the
   clipboard with **Copy** (never automatic). Size label shows the real pixel
   size. **Reset** clears drawings and returns the frame to its default size.
   The `⦚` tools button in the top bar collapses all toolbars into one small
   button at the frame's top-right corner; clicking it brings them back
   (drawing always expands them).
7. `✕` / `Esc` hides the frame; the app stays resident so the hotkey is instant.
   Fully quit with `./shot quit`.

## Files

- `shot` — launcher/CLI: `shot [toggle|show|hide|capture|quit|status]`
- `screenshot_tool/` — the app (Python + PySide6, XWayland window)
- `install.sh` — venv, desktop entry, portal permission grant, GNOME hotkey

## Settings — `~/.config/screenshot-tool/settings.json`

| key | default | meaning |
|---|---|---|
| `save_dir` | `~/Pictures/Screenshots` | where captures are saved |
| `capture_button_corner` | `bottom-right` | which end of the toolbar the camera button sits on (`…-left` / `…-right`) |
| `default_width/height` | `640` / `400` | initial frame size |
| `default_opacity` | `100` | captured image opacity % |
| `color` / `stroke_width` | red / 3 | initial pen settings |
| `filename_pattern` | `shot_%Y%m%d_%H%M%S` | strftime pattern |
| `format` | `png` | output format |
| `history_limit` | `20` | in-session history size |
| `last_frame` | — | last frame rect `[x, y, w, h]`, managed by the app (restore source) |

## How capture works on this machine (GNOME 46 / Wayland)

`org.gnome.Shell.Screenshot` is blocked for unsandboxed apps, and the portal's
Allow-dialog refuses to show for unfocused callers ("Only the focused app is
allowed to show a system access dialog"). The working path, set up by
`install.sh`:

1. The launcher cold-starts the app inside a systemd user unit
   `app-screenshot\x2dtool@*.service` → xdg-desktop-portal derives app-id
   `screenshot-tool` (the `\x2d` escaping of the dash is required).
2. A persistent permission-store grant is written:
   table `screenshot`, id `screenshot`, app-id `screenshot-tool` → `['yes']`.
3. The app calls `org.freedesktop.portal.Screenshot` with `interactive=false`;
   the response carries `~/Pictures/Screenshot.png`, which the app claims
   (renames into your save dir) immediately.

Run outside that unit (bare `python -m screenshot_tool`), capture will fail
with portal response code 2 — use `./shot`.

## Troubleshooting

- Grant missing? `gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore --object-path /org/freedesktop/impl/portal/PermissionStore --method org.freedesktop.impl.portal.PermissionStore.Lookup screenshot screenshot`
  should list `'screenshot-tool': ['yes']`. Reinstall or re-run the SetPermission line from `install.sh`.
- Hotkey not responding: `gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings`
  and the per-key schema; GNOME picks new bindings up within seconds, else re-login.
- Capture failed with response 2: the app was started outside the systemd unit —
  always start via `./shot`.
