"""Single-instance lock: a second process must never become a resident."""
import os

import pytest

from screenshot_tool import ipc
from screenshot_tool.ipc import send_command


def test_lock_is_exclusive_and_auto_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "LOCK_PATH", tmp_path / "st.lock")
    first = ipc.try_acquire_lock()
    assert first is not None
    assert ipc.try_acquire_lock() is None, "second acquire must fail"
    os.close(first)                          # process death equivalent
    second = ipc.try_acquire_lock()
    assert second is not None, "lock not released after owner death"
    os.close(second)


def test_serve_refuses_without_instance_lock(tmp_path, monkeypatch):
    """serve() without the lock is what used to allow socket theft."""
    monkeypatch.setattr(ipc, "SOCKET_PATH", tmp_path / "st.sock")
    monkeypatch.setattr(ipc, "_lock_fd", None)
    with pytest.raises(RuntimeError):
        ipc.serve(lambda cmd: None)


def test_serve_binds_over_stale_socket_file(tmp_path, monkeypatch):
    """A SIGKILLed owner leaves the socket file behind; a new resident must
    replace it, not crash on bind."""
    import socket
    import threading
    monkeypatch.setattr(ipc, "SOCKET_PATH", tmp_path / "st.sock")
    monkeypatch.setattr(ipc, "LOCK_PATH", tmp_path / "st.lock")

    dummy = socket.socket(socket.AF_UNIX)
    dummy.bind(str(tmp_path / "st.sock"))    # stale file left behind

    fd = ipc.try_acquire_lock()
    received = []
    t = threading.Thread(target=ipc.serve, args=(received.append,), daemon=True)
    t.start()
    import time
    deadline = time.time() + 5
    delivered = False
    while time.time() < deadline and not delivered:
        if not t.is_alive():
            raise AssertionError("serve thread died")
        try:
            c = socket.socket(socket.AF_UNIX)
            c.settimeout(1)
            c.connect(str(tmp_path / "st.sock"))
            c.sendall(b"probe")
            c.close()
            delivered = True
        except OSError:
            time.sleep(0.1)   # serve thread may not have bound yet
    assert delivered, "client could not reach the new resident"
    # the connection may sit in the backlog before the serve thread processes
    # it — wait for the handler instead of racing it
    deadline = time.time() + 2
    while "probe" not in received and time.time() < deadline:
        time.sleep(0.05)
    assert "probe" in received
    os.close(fd)
