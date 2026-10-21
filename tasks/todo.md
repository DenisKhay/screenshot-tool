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
- [x] Tools: single drawing panel (bottom edge): pen, arrow, rect, fill,
      text, color, width, undo; right bar holds frame/view ops (move,
      1:1, bake, redraw, reset, fullscreen)
- [x] Width picker: child-panel dropdown from the icon (slider +
      live preview, jump-to-click). Qt.Popup panels never map under the
      bypass/OR overlay — child widgets only (text_edit pattern).
      Text editor anchors at the click point (was content.tl+pos).
- [x] Text size: independent from stroke width — dedicated dropdown
      (A-arrows button next to T, enabled only for the text tool,
      8..96px jump slider + live "Aa" preview); TextShape takes a real
      pixel size. settings key: text_size (default 18).
- [x] Text editor: releasable — click-away commits and is consumed (the
      click used to fall through to the text tool and instantly reopen
      an editor); switching tools commits; Esc cancels. While focused
      it DRAGS to reposition (TextEditor: plain click = caret, drag =
      move); commit uses the dragged position. debug IPC: dumps state
      to the journal.
- [x] Text commit alignment: shape anchors at the editor's real
      baseline (font metrics: ascent + vertical centering) and draws
      at the baseline point — no vertical jump on commit at any size
      (cross-correlated editor grab vs committed capture: ~1px).
- [x] Pin button jump: _apply_pin still performed the managed-window-era
      sticker hug (setGeometry around the shot) — every pin click shrank
      the full-desktop overlay (window 4480x1440 -> 713x413), jumped the
      frame, clipped the right bar and flipped drags into window-move
      mode. Pin is now purely the always-on-top marker (inherent to the
      bypass overlay); _apply_pin re-asserts full-desktop geometry only.
      Repro/verify via debug IPC (now includes window geometry).
- [x] Pin done RIGHT (two window modes): mutter always keeps the ACTIVE
      window above OR windows, so no amount of X stacking gives real
      pin semantics for a bypass overlay (verified empirically —
      XRaise/lower/timer all lose to the active window). Architecture:
      draw/selection = OR full-desktop overlay; captured shot = MANAGED
      sticker window hugging the shot; pin toggles _NET_WM_STATE_ABOVE
      on it (mutter-enforced above-layer). Mode switches at capture/
      redraw/fullscreen preserve the frame's global position, re-assert
      geometry (settle 60/200/500ms) and the input mask (winId changes
      on flag switch). toggle_fullscreen from sticker re-showed the
      hidden overlay. EWMH debugging lesson: read the _NET_WM_STATE
      property and check atoms — do not probe a property literally
      named _NET_WM_STATE_ABOVE.
- [x] "Occasional crash on screenshot" + "Ctrl+C sometimes dead" (Oct 22):
      journal forensics showed 49/49 captures OK, zero process deaths —
      no real crashes. Two actual mechanisms:
      (1) capture_screen() blocked the GUI thread on q.get() for up to
      10s (portal stall = app looks crashed). Fixed: portal runs in a
      worker thread, result marshalled to the GUI thread via a Signal;
      _session_gen guard drops results that race open_draw/start_redraw/
      open_at/toggle_fullscreen.
      (2) after the sticker flag-switch the native window is recreated
      and activateWindow raced mutter's async map — keyboard focus
      sometimes stayed elsewhere, so Ctrl+C went to another app. Fixed:
      claim_focus() after every summon/mode switch (XSetInputFocus
      direct for OR windows, activateWindow + one re-assert for the
      managed sticker).
      (3) bonus: sticker->sticker re-capture left the window HIDDEN
      (_enter_sticker_mode early path skipped show()) — the shot
      vanished, reading as a crash. Fixed: both paths share show/mask/
      above-state now.
- [x] Ctrl+C reliability: (1) single-instance guarantee — ipc.serve used
      to force-unlink the socket, so a cold start during a busy capture
      stole it and orphaned the previous overlay (stacked overlays,
      key focus split: "Ctrl+C not always works"); serve now refuses a
      live owner's socket and main() hands the command to it instead.
      (2) Ctrl+C while the text editor is focused: QLineEdit claimed
      clipboard keys via ShortcutOverride — editor now yields Copy so
      Ctrl+C commits and captures. (3) letter/undo/arrow shortcuts are
      disabled while editing so typing m/p/a/r/f/t + Ctrl+Z works.
- [x] Toolbar UX: tooltips always shown (WA_AlwaysShowToolTips — the
      OR window is never Qt-active, tooltips were flaky); clear-history
      and delete-current icons redone as a deletion family (stacked
      frames + x / picture + x); vertical separators between button
      groups (top: capture | history | settings; bottom: tools |
      size/stroke | undo). Regrouped after review: top = capture/copy/
      pin/opacity | prev/counter/next/clear-history | delete-current |
      settings/close;
      bottom = tools incl. text-size | color/width | undo.
- [x] Capture button (corner configurable): press = save to folder + clipboard
- [x] Post-capture: opacity slider, pin, history ◀ ▶ + copy, size label, reset (real px)
- [x] Invocation restores the previous frame: `_last_rect` was written on
      dismiss/draw-enter but never read — every invoke went straight to draw
      mode. open_at_cursor now reopens the last frame ready to adjust (no-op
      when a live frame is already shown; falls back to draw mode on first
      run, corrupt value, or a rect that no longer fits the desktop). The rect
      persists as `last_frame` in settings.json — survives restarts/reboots —
      and is remembered on Esc/✕, `shot hide`, entering draw mode, and
      capture. Also: _xsetup raised its no-display check after XFlush(NULL) —
      any headless run SIGSEGV'd; check moved up. Offscreen test (7 checks,
      XDG_CONFIG_HOME-sandboxed) + live journal verify (cold-start content
      rect == persisted rect) both pass.
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
