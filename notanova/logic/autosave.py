from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QTabWidget
from core.settings import settings_manager
import time

class AutosaveManager(QObject):
    """Manages the autosave functionality."""
    requestSave = pyqtSignal(int) # Emits tab index to be saved

    def __init__(self, tab_widget: QTabWidget, parent=None):
        super().__init__(parent)
        self.tab_widget = tab_widget # Reference to the main QTabWidget
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_and_save)
        self._is_active = False
        self.interval_sec = 0

        self.load_interval() # Initial load

        # Connect settings changes
        settings_manager.settingsChanged.connect(self._handle_settings_change)


    def _handle_settings_change(self, key):
        if key == "autosave_interval_sec":
            self.load_interval()

    def load_interval(self):
        """Loads interval from settings and restarts timer if needed."""
        try:
            # Ensure value is int
            self.interval_sec = int(settings_manager.get("autosave_interval_sec"))
        except (ValueError, TypeError):
            print("Warning: Invalid autosave interval in settings, disabling.")
            self.interval_sec = 0
            settings_manager.set("autosave_interval_sec", 0) # Correct setting

        was_active = self._is_active
        if self._timer.isActive():
            self._timer.stop()
            self._is_active = False

        if self.interval_sec > 0:
            self._timer.start(self.interval_sec * 1000) # QTimer takes milliseconds
            self._is_active = True
            if not was_active:
                print(f"Autosave enabled with interval: {self.interval_sec} seconds.")
        else:
            if was_active:
                print("Autosave disabled.")

    def start(self):
        """Starts the autosave timer if interval is positive."""
        self.load_interval() # Ensure interval is current and start timer

    def stop(self):
        """Stops the autosave timer."""
        if self._timer.isActive():
            self._timer.stop()
            self._is_active = False
            print("Autosave timer stopped.")

    def check_and_save(self):
        """Checks all open tabs for modifications and triggers save if needed."""
        if not self._is_active or self.interval_sec <= 0:
            return # Do nothing if disabled

        # print(f"Autosave check ({time.strftime('%H:%M:%S')})...") # Debug log
        num_tabs = self.tab_widget.count()
        for i in range(num_tabs):
            try:
                editor_widget = self.tab_widget.widget(i)
                # Check if it's a valid EditorWidget (using isinstance is safest)
                # Avoid importing EditorWidget here to prevent circular deps, use duck typing cautiously
                if (editor_widget is not None and
                    hasattr(editor_widget, 'is_modified') and callable(getattr(editor_widget, 'is_modified')) and
                    hasattr(editor_widget, 'file_path')):

                    if editor_widget.is_modified():
                        # Only autosave files that have already been saved once (have a path)
                        if editor_widget.file_path:
                            # print(f"Autosaving tab {i}: {editor_widget.file_path}") # Log which file is being saved
                            self.requestSave.emit(i)
                        # else: # Don't log skipped unsaved files every time unless debugging
                        #     pass

            except Exception as e:
                 print(f"Error during autosave check for tab {i}: {e}")
                 # Avoid crashing the autosave loop, continue to next tab
                 continue

    def force_save_all(self):
        """Forces saving of all modified tabs immediately."""
        print("Forcing save all modified tabs...")
        num_tabs = self.tab_widget.count()
        saved_count = 0
        skipped_count = 0
        for i in range(num_tabs):
            try:
                editor_widget = self.tab_widget.widget(i)
                if (editor_widget is not None and
                    hasattr(editor_widget, 'is_modified') and callable(getattr(editor_widget, 'is_modified')) and
                    hasattr(editor_widget, 'file_path')):

                    if editor_widget.is_modified():
                        # Check if file has a path (can be saved without dialog)
                        if editor_widget.file_path:
                            print(f"Force saving tab {i}: {editor_widget.file_path}")
                            self.requestSave.emit(i)
                            saved_count += 1
                        else:
                            tab_title = self.tab_widget.tabText(i).replace(" *", "")
                            print(f"Skipping force save for unsaved new file in tab {i} ('{tab_title}').")
                            skipped_count += 1
            except Exception as e:
                 print(f"Error during force save for tab {i}: {e}")

        if saved_count > 0:
             print(f"Requested save for {saved_count} modified tabs.")
        if skipped_count > 0:
             print(f"Skipped {skipped_count} unsaved new tabs.")
        if saved_count == 0 and skipped_count == 0:
             print("No modified tabs found to force save.")
