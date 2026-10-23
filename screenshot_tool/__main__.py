import argparse
import faulthandler
import logging
faulthandler.enable()
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from . import ipc
from .ipc import send_command, serve

REMOTE_ONLY = {"quit", "status"}


def build_tray(win):
    """Resident StatusNotifier tray icon (AppIndicator): the app's taskbar
    presence while nothing is on screen. Left click toggles the frame."""
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon
    from .icons import app_icon

    tray = QSystemTrayIcon(app_icon(), win)
    menu = QMenu()
    act_toggle = menu.addAction("Show / hide frame")
    act_capture = menu.addAction("Capture")
    menu.addSeparator()
    act_quit = menu.addAction("Quit")
    act_toggle.triggered.connect(lambda: win.ipc_command.emit("toggle"))
    act_capture.triggered.connect(lambda: win.ipc_command.emit("capture-tray"))
    act_quit.triggered.connect(lambda: win.ipc_command.emit("quit"))
    tray.setContextMenu(menu)
    tray.setToolTip("Screenshot Tool")
    tray.activated.connect(
        lambda reason: win.ipc_command.emit("toggle")
        if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    return tray


def main() -> int:
    ap = argparse.ArgumentParser(prog="screenshot_tool", description="Flameshot-style region cropper")
    ap.add_argument("command", nargs="?", default="toggle",
                    help="toggle (default) | show | showat:X,Y | hide | capture | quit | status")
    argv = [a[2:] if a.startswith("--") and a[2:] in {"toggle", "show", "hide", "capture", "quit", "status"}
            else a for a in sys.argv[1:]]  # accept --toggle style flags
    args = ap.parse_args(argv)
    cmd = args.command
    known = {"toggle", "show", "hide", "capture", "quit", "status"}
    if not cmd.startswith("showat:") and cmd not in known:
        ap.error(f"unknown command: {cmd}")

    # Single instance via a process-lifetime lock, acquired BEFORE the heavy
    # PySide import: a second command started while the resident was still
    # importing saw no socket and no owner — and became a second resident
    # that stole the IPC socket (orphan overlays, dead buttons).
    lock_fd = ipc.try_acquire_lock()
    deadline = time.monotonic() + 6.0
    while lock_fd is None:
        if send_command(cmd):
            print(f"sent: {cmd}")
            return 0
        lock_fd = ipc.try_acquire_lock()
        if lock_fd is not None:
            break  # the previous owner died: we take over
        if time.monotonic() > deadline:
            print("another instance is running but is not answering", file=sys.stderr)
            return 1
        time.sleep(0.15)

    if cmd in REMOTE_ONLY:
        print("not running")
        return 0

    # lock held: this process is the one true resident — import everything
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .settings import Settings
    from .window import SnipperWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("screenshot-tool")
    app.setApplicationDisplayName("Screenshot Tool")
    app.setQuitOnLastWindowClosed(False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = Settings()
    win = SnipperWindow(settings)

    tray = build_tray(win)  # noqa: F841 — kept alive for the app's lifetime

    threading.Thread(target=serve, args=(lambda c: win.ipc_command.emit(c),), daemon=True).start()
    if cmd in ("toggle", "show"):
        win.open_at_cursor()
    elif cmd.startswith("showat:"):
        xs, ys = cmd.split(":", 1)[1].split(",")
        win.open_at(int(xs), int(ys))
    elif cmd == "capture":
        win.open_at_cursor()
        QTimer.singleShot(1200, win.do_capture)
        QTimer.singleShot(5000, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
