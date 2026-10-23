import os
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get(
    "SCREENSHOT_TOOL_RUNTIME_DIR",
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")))
SOCKET_PATH = RUNTIME_DIR / "screenshot-tool.sock"
LOCK_PATH = SOCKET_PATH.with_suffix(".lock")
_lock_fd = None  # set while this process owns the instance lock


def try_acquire_lock() -> int | None:
    """Process-lifetime single-instance lock. Returns the fd held until the
    process exits, or None when another instance owns it. flock is released
    by the kernel even on SIGKILL, so stale locks are impossible."""
    import fcntl
    global _lock_fd
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _lock_fd = fd
    return fd


def send_command(command: str, timeout: float = 1.0) -> bool:
    import socket
    if not SOCKET_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(SOCKET_PATH))
            s.sendall(command.encode())
            return True
    except OSError:
        return False


def owner_alive(timeout: float = 0.3) -> bool:
    """True if some process is listening on the IPC socket right now."""
    import socket
    if not SOCKET_PATH.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(str(SOCKET_PATH))
        return True
    except OSError:
        return False
    finally:
        s.close()


def serve(handler):
    """Blocking accept loop; call from a daemon thread after the instance
    lock is held. handler(str) is called per command."""
    import socket
    if _lock_fd is None:
        raise RuntimeError(
            "serve() requires the instance lock — a resident must have "
            "acquired it via try_acquire_lock or it can steal sockets")
    if owner_alive():
        # never steal a live owner's socket: this process must stay the
        # single instance (a stolen socket orphans the previous overlay)
        raise RuntimeError("another screenshot-tool instance owns the IPC socket")
    print("SERVE: passed guards, binding", SOCKET_PATH, flush=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCKET_PATH))
    srv.listen(4)
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    data = conn.recv(256).decode(errors="replace").strip()
                except OSError:
                    continue
                if data:
                    handler(data)
    finally:
        srv.close()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
