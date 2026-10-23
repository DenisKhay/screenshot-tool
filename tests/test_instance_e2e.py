"""End-to-end single-instance / IPC tests with REAL subprocesses.

Regression being pinned down: a command starting while the resident was
still importing PySide saw no socket and no owner — and became a second
resident that stole the IPC socket. Orphan overlays ignored hotkeys and
buttons; three residents were found live at once.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "bin" / "python"


def launch(cmd, runtime_dir, extra_delay=0.0):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["SCREENSHOT_TOOL_RUNTIME_DIR"] = str(runtime_dir)
    env["SCREENSHOT_TOOL_CONFIG_DIR"] = str(runtime_dir / "config")
    if extra_delay:
        time.sleep(extra_delay)
    return subprocess.Popen(
        [str(PY), "-m", "screenshot_tool", cmd],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def owner_pid(runtime_dir):
    """PID from the lock file, validated as alive (zombies count as dead —
    the test parent may not have reaped them yet); None otherwise."""
    lock = Path(runtime_dir) / "screenshot-tool.lock"
    try:
        pid = int(lock.read_text().strip())
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.split(") ", 1)[1].split()[0] == "Z":
            return None
        return pid
    except (OSError, ValueError):
        return None


def wait_until(pred, timeout=10.0, step=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


@pytest.fixture()
def runtime_dir(tmp_path):
    rd = tmp_path / "runtime"
    rd.mkdir()
    return rd


def kill_residents(runtime_dir):
    pid = owner_pid(runtime_dir)
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    wait_until(lambda: owner_pid(runtime_dir) is None, timeout=5)


def test_concurrent_launches_produce_exactly_one_resident(runtime_dir):
    """Five simultaneous cold starts: exactly one may survive as resident.
    The old racy guard produced three live residents at once."""
    procs = [launch("show", runtime_dir, extra_delay=i * 0.05) for i in range(5)]
    assert wait_until(lambda: owner_pid(runtime_dir) is not None, timeout=15)
    owner = owner_pid(runtime_dir)
    assert wait_until(lambda: all(p.poll() is not None for p in procs if p.pid != owner),
                      timeout=15), "losing clients neither exited nor handed over"
    alive = [p for p in procs if p.poll() is None]
    assert len(alive) == 1, f"{len(alive)} residents survived"
    assert alive[0].pid == owner
    time.sleep(1.0)
    assert alive[0].poll() is None, "resident died after startup"
    kill_residents(runtime_dir)
    assert wait_until(lambda: alive[0].poll() is not None, timeout=5), \
        "quit via lock takeover failed"


def test_client_flood_all_delivered_one_resident(runtime_dir):
    """20 rapid clients against a live resident: all exit 0 (delivered — a
    client that fails delivery becomes a resident and never exits), and the
    resident count stays at one."""
    resident = launch("show", runtime_dir)
    assert wait_until(lambda: owner_pid(runtime_dir) is not None, timeout=15)
    time.sleep(1.0)  # let it finish importing and bind the socket
    clients = [launch("toggle", runtime_dir, extra_delay=0.02) for _ in range(20)]
    for c in clients:
        assert c.wait(timeout=15) == 0
    assert resident.poll() is None, "resident died under client flood"
    assert resident.pid == owner_pid(runtime_dir)
    kill_residents(runtime_dir)


def test_owner_death_lets_a_client_take_over(runtime_dir):
    """Owner SIGKILLed before binding its socket: the next client must
    acquire the lock and become the resident (no permanent lock-out)."""
    victim = launch("show", runtime_dir)
    assert wait_until(lambda: owner_pid(runtime_dir) is not None, timeout=15)
    os.kill(owner_pid(runtime_dir), 9)      # SIGKILL: worst case, no cleanup
    assert wait_until(lambda: owner_pid(runtime_dir) is None, timeout=5)
    client = launch("show", runtime_dir)
    assert wait_until(lambda: owner_pid(runtime_dir) == client.pid, timeout=15), \
        "client could not take over after owner death"
    kill_residents(runtime_dir)


def test_quit_releases_lock_for_the_next_instance(runtime_dir):
    first = launch("show", runtime_dir)
    assert wait_until(lambda: owner_pid(runtime_dir) is not None, timeout=15)
    stopper = launch("quit", runtime_dir)
    assert stopper.wait(timeout=15) == 0
    assert wait_until(lambda: owner_pid(runtime_dir) is None, timeout=5), \
        "lock not released after quit"
    assert first.poll() is not None
    second = launch("show", runtime_dir)
    assert wait_until(lambda: owner_pid(runtime_dir) == second.pid, timeout=15)
    kill_residents(runtime_dir)
