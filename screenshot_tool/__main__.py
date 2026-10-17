import argparse
import faulthandler
import logging
faulthandler.enable()
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .ipc import send_command, serve
from .settings import Settings
from .window import SnipperWindow

REMOTE_ONLY = {"quit", "status"}


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

    if send_command(cmd):
        print(f"sent: {cmd}")
        return 0
    if cmd in REMOTE_ONLY:
        print("not running")
        return 0

    from .ipc import owner_alive
    if owner_alive():
        # owner alive but didn't answer within 1s (busy in a capture):
        # never run as a second instance — hand the command over instead
        if send_command(cmd, timeout=3):
            print(f"sent (busy owner): {cmd}")
            return 0
        print("another instance is running but is not answering", file=sys.stderr)
        return 1

    app = QApplication(sys.argv[:1])
    app.setApplicationName("screenshot-tool")
    app.setQuitOnLastWindowClosed(False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = Settings()
    win = SnipperWindow(settings)

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
