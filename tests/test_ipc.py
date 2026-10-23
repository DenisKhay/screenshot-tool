"""Single-instance lock: a second process must never become a resident."""
import os

from screenshot_tool import ipc


def test_lock_is_exclusive_and_auto_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "LOCK_PATH", tmp_path / "st.lock")
    first = ipc.try_acquire_lock()
    assert first is not None
    assert ipc.try_acquire_lock() is None, "second acquire must fail"
    os.close(first)                          # process death equivalent
    second = ipc.try_acquire_lock()
    assert second is not None, "lock not released after owner death"
    os.close(second)
