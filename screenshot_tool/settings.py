import json
import os
from pathlib import Path

DEFAULTS = {
    "save_dir": "~/Pictures/Screenshots",
    "capture_button_corner": "bottom-right",  # top-left | top-right | bottom-left | bottom-right
    "default_width": 640,
    "default_height": 400,
    "default_opacity": 100,  # percent, applied to captured image
    "color": "#e53935",
    "stroke_width": 3,
    "text_size": 18,
    "filename_pattern": "shot_%Y%m%d_%H%M%S",
    "format": "png",
    "history_limit": 20,
}

CONFIG_DIR = Path(os.environ.get(
    "SCREENSHOT_TOOL_CONFIG_DIR",
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))) / "screenshot-tool"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.load()

    def load(self):
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            if isinstance(data, dict):
                self.update(data)
        except (OSError, ValueError):
            pass

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(self, indent=2) + "\n")

    def save_dir_path(self) -> Path:
        return Path(os.path.expanduser(str(self["save_dir"])))
