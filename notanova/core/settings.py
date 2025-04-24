import json
import os
import sys
from PyQt6.QtCore import QSettings, QStandardPaths, QObject, pyqtSignal, QDir
from PyQt6.QtGui import QFont, QColor

# Import constants from entry point to avoid circular dependencies if core needs them early
try:
    from ..notanova import APP_NAME, ORG_NAME
except ImportError: # Fallback if run standalone or structure changes
    APP_NAME = "NotaNova"
    ORG_NAME = "NotaNovaOrg"


def _get_default_data_dir():
    """Gets the base application data directory reliably."""
    # Prefer AppDataLocation for persistent data
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    # Fallback path construction if standard paths fail (less common now)
    if not path:
        path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    if not path:
        path = os.path.expanduser("~")
        print("Warning: Could not find standard data location, using home directory.", file=sys.stderr)
        # Use a hidden directory in home as a last resort
        return os.path.join(path, f".{ORG_NAME}", APP_NAME)
    # Ensure consistent structure using Org/App names
    # On Linux, AppDataLocation might already include Org/App, check for duplication
    if os.path.basename(os.path.dirname(path)) == ORG_NAME and os.path.basename(path) == APP_NAME:
         return path
    else:
         return os.path.join(path, ORG_NAME, APP_NAME)


DEFAULT_SETTINGS = {
    "use_system_theme": False, # Add this line
    "theme": "light",  # light or dark
    "font_family": "Sans Serif", # Use a generic family name initially
    "font_size": 10,
    # AI Tools
    "llm_model_path": "",
    "whisper_model_version": "base", # tiny, base, small, medium, large, *.en
    "spellcheck_engine": "none", # none, aspell, hunspell, languagetool
    "language": "en-US", # Use standard BCP 47 format
    "audio_input_device": "", # Empty string means default device
    "audio_output_device": "", # For future TTS
    # Files & Sync
    "default_save_path": QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or os.path.expanduser("~"),
    "autosave_interval_sec": 60, # 0 to disable
    "session_restore": True,
    # Cloud Sync
    "google_client_secret_path": "",
    # App Data Paths (calculated relative to data dir)
    "google_credentials_path": os.path.join(_get_default_data_dir(), "google_token.pickle"), # Default token location
    "last_session_file": os.path.join(_get_default_data_dir(), "session.json"),
    "notebook_data_file": os.path.join(_get_default_data_dir(), "notebooks.json"),
    "gdrive_note_map_file": os.path.join(_get_default_data_dir(), "gdrive_map.json"),
}

class SettingsManager(QObject):
    settingsChanged = pyqtSignal(str) # Emit key when a setting changes

    def __init__(self, parent=None):
        super().__init__(parent)
        # Use QSettings for easy cross-platform storage (INI format is human-readable)
        self.q_settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG_NAME, APP_NAME)
        print(f"Using settings file: {self.q_settings.fileName()}")
        self._ensure_default_dirs()

    def _ensure_default_dirs(self):
        """Ensure application data directories exist."""
        base_data_dir = _get_default_data_dir()
        if not QDir(base_data_dir).exists():
            try:
                if QDir().mkpath(base_data_dir):
                    print(f"Created application data directory: {base_data_dir}")
                else:
                    print(f"Error: Failed to create application data directory {base_data_dir}", file=sys.stderr)
            except Exception as e:
                print(f"Exception creating application data directory {base_data_dir}: {e}", file=sys.stderr)

    def get(self, key, default_override=None):
        """Get a setting value, handling type conversion and defaults."""
        if key not in DEFAULT_SETTINGS and default_override is None:
            print(f"Warning: Accessing unknown setting key '{key}'", file=sys.stderr)
            return None

        default = default_override if default_override is not None else DEFAULT_SETTINGS.get(key)
        # QSettings stores values; retrieve with type awareness if possible
        value = self.q_settings.value(key, defaultValue=default)

        # Type correction/validation
        if default is not None and value is not None:
            expected_type = type(default)
            if not isinstance(value, expected_type):
                try:
                    if expected_type is bool:
                         # Handle string 'true'/'false' from INI file
                         if isinstance(value, str): value = value.lower() == 'true'
                         else: value = bool(value) # Try converting other types like int
                    elif expected_type is int: value = int(value)
                    elif expected_type is float: value = float(value)
                    else:
                         # If type mismatch is complex, maybe just use default
                         # print(f"Warning: Type mismatch for setting '{key}'. Got {type(value)}, expected {expected_type}. Using default.", file=sys.stderr)
                         value = default
                except (ValueError, TypeError):
                    # print(f"Warning: Could not convert setting '{key}' value '{value}' to type {expected_type}. Using default.", file=sys.stderr)
                    value = default # Use default if conversion fails


        # Handle case where setting exists but is None/empty when default is not
        if value is None or value == "": # Treat empty string from INI as needing default sometimes
             if default is not None and default != "":
                  return default

        return value

    def set(self, key, value):
        """Set a setting value and emit signal if changed."""
        # Ensure key is known? Optional strictness.
        # if key not in DEFAULT_SETTINGS:
        #     print(f"Warning: Setting unknown key '{key}'", file=sys.stderr)

        current_value = self.q_settings.value(key)
        # Compare carefully (e.g., string 'true' vs bool True)
        new_value_str = str(value).lower() if isinstance(value, bool) else value
        current_value_str = str(current_value).lower() if isinstance(current_value, bool) else current_value

        if current_value_str != new_value_str:
            self.q_settings.setValue(key, value)
            # self.q_settings.sync() # Sync can be deferred, happens on destruction or explicitly
            self.settingsChanged.emit(key)

    def get_font(self) -> QFont:
        """Get the configured editor font."""
        font = QFont()
        font.setFamily(self.get("font_family"))
        font_size = self.get("font_size")
        if isinstance(font_size, int) and font_size > 0:
            font.setPointSize(font_size)
        else:
            print(f"Warning: Invalid font size '{font_size}', using default.", file=sys.stderr)
            font.setPointSize(DEFAULT_SETTINGS["font_size"])
            self.set("font_size", DEFAULT_SETTINGS["font_size"]) # Correct the setting
        return font

    def set_font(self, font: QFont):
        """Set the editor font."""
        self.set("font_family", font.family())
        self.set("font_size", font.pointSize())

    def is_dark_mode(self) -> bool:
        """Check if dark mode is enabled."""
        return self.get("theme") == "dark"

    def should_use_system_theme(self) -> bool:
        """Check if system theme integration is enabled."""
        return self.get("use_system_theme")

    def load_stylesheet(self) -> str:
        """Load the appropriate theme QSS file."""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd() # Fallback

        # Navigate up from core directory to find assets
        qss_path = os.path.join(script_dir, '..', 'assets', 'theme.qss')
        qss_path = os.path.normpath(qss_path)

        if not os.path.exists(qss_path):
             # Try path relative to main script location if different
             try:
                 main_script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
                 alt_qss_path = os.path.join(main_script_dir, 'assets', 'theme.qss')
                 if os.path.exists(alt_qss_path): qss_path = alt_qss_path
             except Exception: pass

        try:
            with open(qss_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            print(f"Error: Stylesheet 'theme.qss' not found. Looked near: {qss_path}", file=sys.stderr)
            return "" # Return empty string if QSS not found

# Global instance
settings_manager = SettingsManager()
