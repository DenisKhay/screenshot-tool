# Screenshot Tool (flameshot-style cropper for GNOME/Wayland)

Transparent frame → annotate → capture-on-button → floating image (opacity, pin, history).

## Status: WORKING, end-to-end verified on miku
- `shot capture` → file saved to ~/Pictures/Screenshots + clipboard byte-identical ✓
- Portal round trip silent, ~0.5s ✓
- XWayland window: self-positioning ✓, always-on-top over native Wayland
  windows ✓ (visual stacking test + `_NET_WM_STATE_ABOVE` present)
- Toggle/hide/quit via IPC ✓
- Hotkey Super+Alt+A registered (needs Denis's hands to confirm feel)

## Key findings (window path on miku, 2026-10-17)
- Mutter clamps managed XWayland toplevels into the primary monitor's
  workarea: requested 4480x1440+0+0 → mapped 4440x1048+40+392 (392 =
  eDP-1 y-offset 360 + panel 32). Top ~392px of DP-1 unreachable.
  FIX: Qt.X11BypassWindowManagerHint (override-redirect) — geometry
  honored 1:1, keyboard still delivered.
- Input-only X shape went STALE across hide/show: _update_mask ran while
  hidden (skipped), then draw mode would have applied an empty region.
  FIX: draw mode = full input (None region), re-applied after show().
- Frame drag jerked violently: drag moved content AND window by the same
  delta = shot moved 2x cursor speed (feedback via window-relative
  coords). Was a keep-inside-clamped-overlay hack; unnecessary now.
  FIX: drag moves exactly ONE thing — content rect (full-desktop
  window) or the hugged window 1:1 (global grab offset). Repaints are
  limited to the frame's old/new band, not the whole 4480x1440 overlay.

## Key findings (capture path on miku)
- `org.gnome.Shell.Screenshot` D-Bus: AccessDenied for unsandboxed apps (GNOME 46).
- Portal `Screenshot()` needs a permission-store grant; WITHOUT it the portal
  falls back to an Allow dialog which dies with "Only the focused app is allowed
  to show a system access dialog" → response code 2. This is what silently broke
  the Sep-8 vault recipe (the old '' → yes grant stopped matching: shell-spawned
  processes now derive app-ids from cmdline/systemd units instead).
- App-id comes from the systemd user unit: `app-screenshot\x2dtool@N.service`
  → app id `screenshot-tool` (unescaped dashes parse as `tool`!).
- Grant: table `screenshot`, id `screenshot`, app-id `screenshot-tool` → ['yes']
  (SetPermission via gdbus; install.sh idempotent).
- Verified: unit + grant → `RESPONSE code: 0`, uri `~/Pictures/Screenshot.png`
  (portal overwrites it each time; tool claims/renames + deletes it).
- PySide6 6.12 QtDBus signal-subscription API is unusable for this (connect()
  bytes-slot forms rejected) → capture.py uses jeepney (pure python) instead.

## Done
- [x] App skeleton: settings.json, unix-socket IPC, CLI (toggle/show/showat/hide/capture/quit/status)
- [x] Capture backend: portal via jeepney, crop by frame rect, DPR-aware, drawings composited
- [x] Window: transparent frame, 4 corner grips, body-drag move, toolbar strip
      (adaptive icon toolbar: below / above / inside overlay), fullscreen expand
- [x] Tools: single drawing panel (bottom edge): move, pen, arrow, rect,
      fill, text, color, width, undo — all drawing instruments in one
      place; right bar holds frame/view ops (1:1, bake, redraw, reset,
      fullscreen)
- [x] Capture button (corner configurable): press = save to folder + clipboard
- [x] Post-capture: opacity slider, pin, history ◀ ▶ + copy, size label, reset (real px)
- [x] install.sh: venv+PySide6+jeepney, desktop entry + icon, grant, hotkey merge
- [x] Self-test end-to-end

## Not yet machine-verified (Denis should try by hand)
- Drawing feel (pen/arrow/rect/text), undo
- Hold-drag after capture (startSystemMove after hide/show — X grab may be lost)
- Pin (chrome hides, stays on top), opacity slider, history navigation + copy
- Hotkey Super+Alt+A from GNOME

## Known limitations
- Mixed-DPI multi-monitor crops assume uniform scale (both his screens are 1×)
- Must launch via `./shot` (systemd unit) — bare `python -m screenshot_tool`
  gets no app-id → portal denies with code 2
- Drawing tools disabled while viewing a historical shot (view+copy only)
