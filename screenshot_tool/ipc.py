import os
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "screenshot-tool.sock"


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
    """Blocking accept loop; call from a daemon thread. handler(str) is called per command."""
    import socket
    if owner_alive():
        # never steal a live owner's socket: this process must stay the
        # single instance (a stolen socket orphans the previous overlay)
        raise RuntimeError("another screenshot-tool instance owns the IPC socket")
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
