"""Screen capture via the xdg-desktop-portal Screenshot portal (GNOME/Wayland-safe).

Requires the caller process to run with app-id `screenshot-tool`
(systemd user unit `app-screenshot\\x2dtool@*.service`) and a permission-store
grant: table `screenshot`, id `screenshot`, app-id `screenshot-tool` -> ['yes'].
install.sh sets both up. D-Bus is done with jeepney (pure python) because
PySide6's QtDBus signal-subscription API is unusable for this in 6.12.
"""
import queue
import threading
import time

from jeepney import DBusAddress, new_method_call
from jeepney.bus_messages import MatchRule
from jeepney.io.blocking import open_dbus_connection
from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage

TIMEOUT_S = 10.0
PORTAL_ADDR = DBusAddress(
    "/org/freedesktop/portal/desktop",
    bus_name="org.freedesktop.portal.Desktop",
    interface="org.freedesktop.portal.Screenshot",
)


class CaptureError(Exception):
    pass


def _portal_capture_raw() -> str:
    """Worker: run the portal round trip, return the captured file path."""
    import uuid

    token = "st" + uuid.uuid4().hex[:8]
    conn = open_dbus_connection()
    try:
        unique = conn.unique_name  # ":1.745"
        req_path = f"/org/freedesktop/portal/desktop/request/{unique[1:].replace('.', '_')}/{token}"

        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=req_path,
        )
        with conn.filter(rule) as matches:
            conn.send_message(
                new_method_call(
                    PORTAL_ADDR,
                    "Screenshot",
                    "sa{sv}",
                    ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
                )
            )
            try:
                msg = conn.recv_until_filtered(matches, timeout=TIMEOUT_S)
            except TimeoutError:
                raise CaptureError("portal timeout") from None
            if len(msg.body) >= 2:
                code, results = msg.body[0], msg.body[1]
            else:
                code, results = msg.body[0], {}
    finally:
        conn.close()

    if code != 0:
        raise CaptureError(f"portal response code {code} (denied or failed)")
    uri = (results or {}).get("uri", "")
    if isinstance(uri, tuple) and len(uri) == 2:  # jeepney variant: (signature, value)
        uri = uri[1]
    if not isinstance(uri, str) or not uri.startswith("file://"):
        raise CaptureError(f"portal returned no uri: {results}")
    return uri[len("file://"):]


def capture_screen() -> tuple[QImage, str]:
    """Full-desktop capture. Returns (image, source_file_path). The source file is
    the portal's own output (~/Pictures/Screenshot.png); the caller should move or
    delete it."""
    q: queue.SimpleQueue = queue.SimpleQueue()

    def worker():
        try:
            q.put(_portal_capture_raw())
        except BaseException as e:  # noqa: BLE001 - marshalled to the caller
            q.put(e)

    threading.Thread(target=worker, daemon=True).start()
    try:
        res = q.get(timeout=TIMEOUT_S)
    except queue.Empty:
        raise CaptureError("portal timeout") from None
    if isinstance(res, BaseException):
        if isinstance(res, CaptureError):
            raise res
        raise CaptureError(str(res)) from res

    img = QImage(res)
    if img.isNull():
        raise CaptureError(f"captured file unreadable: {res}")
    return img, res


def virtual_geometry() -> QRect:
    """Full virtual desktop rect across all monitors."""
    primary = QGuiApplication.primaryScreen()
    if primary is None:
        return QRect(0, 0, 0, 0)
    return primary.virtualGeometry()


def device_pixel_ratio(screen) -> float:
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    return float(dpr) if dpr else 1.0


def crop_image(img: QImage, logical_rect: QRect, dpr: float) -> QImage:
    """Crop a physical-pixel capture by a logical-rect within the virtual desktop."""
    vg = virtual_geometry()
    x = round((logical_rect.x() - vg.x()) * dpr)
    y = round((logical_rect.y() - vg.y()) * dpr)
    w = round(logical_rect.width() * dpr)
    h = round(logical_rect.height() * dpr)
    region = QRect(x, y, w, h).intersected(img.rect())
    if region.isEmpty():
        raise CaptureError("frame rect outside captured area")
    return img.copy(region)
