#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import traceback
# --- Use PyQt6 consistently ---
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QLockFile, QDir, QStandardPaths, Qt, QDateTime
# --- Application Constants ---
APP_NAME = "NotaNova"
ORG_NAME = "NotaNovaOrg" # Or your specific org name

# Attempt to set Application paths early for QSettings etc.
QApplication.setApplicationName(APP_NAME)
QApplication.setOrganizationName(ORG_NAME)
# QApplication.setApplicationVersion("0.1.0") # Optional

# --- Import Core Components (after setting AppName/OrgName) ---
from core.settings import settings_manager # Import global instance
from ui.main_window import MainWindow

# --- Global Exception Handling ---
def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    """Log unhandled exceptions and show a user-friendly message."""
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    timestamp = QDateTime.currentDateTime().toString(Qt.DateFormat.ISODateWithMs)
    full_error_msg = f"--- {timestamp} ---\nUnhandled Exception:\n{error_msg}\n"

    # Log to stderr first (safest)
    print(full_error_msg, file=sys.stderr)

    # Log to a file
    log_file_path = "(unknown)"
    try:
        # Use consistent AppDataLocation via QStandardPaths
        log_dir_base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        if not log_dir_base: # Fallback if AppDataLocation fails
             log_dir_base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
        if not log_dir_base: # Final fallback
             log_dir_base = os.path.join(os.path.expanduser("~"), f".{ORG_NAME}", APP_NAME, "logs")
        else:
             log_dir_base = os.path.join(log_dir_base, "logs") # Put logs in a subdir

        if not QDir(log_dir_base).exists():
            if not QDir().mkpath(log_dir_base):
                 print(f"Could not create log directory: {log_dir_base}", file=sys.stderr)
                 # Try logging in the base data directory if sub-dir creation fails
                 log_dir_base = os.path.dirname(log_dir_base)

        log_file_path = os.path.join(log_dir_base, "notanova_crash.log")
        with open(log_file_path, "a", encoding='utf-8') as f:
            f.write(full_error_msg)
    except IOError as e:
        print(f"Could not write crash log to file '{log_file_path}': {e}", file=sys.stderr)
    except Exception as e:
         print(f"Unexpected error during crash log writing: {e}", file=sys.stderr)

    # Show a user-friendly message (carefully, avoid triggering another exception)
    try:
        # Ensure QApplication exists before showing GUI message
        app_instance = QApplication.instance()
        if not app_instance:
             # If QApplication hasn't started or already died, can't show GUI message
             print("Cannot show error dialog: QApplication instance not available.", file=sys.stderr)
        else:
             error_dialog = QMessageBox()
             error_dialog.setIcon(QMessageBox.Icon.Critical)
             error_dialog.setWindowTitle("Application Error")
             error_dialog.setText(f"A critical error occurred:\n\n{exc_value}\n\nDetails have been logged to:\n{log_file_path}\nPlease restart the application.")
             # error_dialog.setDetailedText(error_msg) # Optional: Show full traceback
             error_dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
             error_dialog.exec()
    except Exception as e:
        # Avoid RecursionError if QMessageBox fails
        print(f"Could not display error message box: {e}", file=sys.stderr)

    sys.exit(1) # Exit after logging/showing message

# --- Main Application Logic ---
def main():
    # Set high DPI scaling attributes (best done early)
    # QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True) # Usually default now
    # QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True) # Usually default now

    # --- Single Instance Check ---
    lock_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    if not lock_dir: # Fallback if temp location fails
        lock_dir = QDir.tempPath()

    # Use a simpler lock file name, letting OS handle user separation
    lock_file_path = os.path.join(lock_dir, f"{ORG_NAME}_{APP_NAME}.lock")
    lock_file = QLockFile(lock_file_path)
    lock_file.setStaleLockTime(0) # Check immediately if lock is stale

    if not lock_file.tryLock(100): # Try to lock for 100ms
        # Check if lock is held by a running process
        is_locked, pid, hostname, appname = lock_file.getLockInfo()
        if is_locked and pid > 0 : # Check PID is valid (might be -1 or 0 if stale/unreadable)
            print(f"Another instance of {APP_NAME} (PID: {pid} on {hostname}) is already running.", file=sys.stderr)
            # Need a temporary app instance to show message box if main one hasn't started
            tmp_app_for_msg = QApplication.instance() or QApplication([])
            QMessageBox.warning(None, "Already Running", f"Another instance of {APP_NAME} is already running.")
            tmp_app_for_msg = None # Release temporary app if created
            sys.exit(0)
        else:
            # Lock file exists but seems stale or info is unreadable
            print("Stale or unreadable lock file found, attempting removal...", file=sys.stderr)
            if lock_file.removeStaleLock():
                print("Stale lock removed.")
                if not lock_file.tryLock(100):
                    print("Could not acquire lock after removing stale lock. Exiting.", file=sys.stderr)
                    tmp_app_for_msg = QApplication.instance() or QApplication([])
                    QMessageBox.critical(None, "Lock Error", "Could not acquire application lock after removing stale lock.")
                    tmp_app_for_msg = None
                    sys.exit(1)
                # Successfully locked after removing stale lock
            else:
                print("Could not remove stale lock file. Exiting.", file=sys.stderr)
                tmp_app_for_msg = QApplication.instance() or QApplication([])
                QMessageBox.critical(None, "Lock Error", f"Could not remove potentially stale lock file:\n{lock_file_path}\n\nAnother instance might be starting, or you may need to remove it manually.")
                tmp_app_for_msg = None
                sys.exit(1)

    print(f"Acquired instance lock: {lock_file_path}")
    # Keep lock_file alive until app quits (it will be destroyed when main scope exits)

    # --- Setup Application ---
    app = QApplication(sys.argv)

    # --- Setup Exception Hook ---
    # Set this *after* QApplication is created and before MainWindow
    sys.excepthook = handle_unhandled_exception

    # --- Load Settings and Stylesheet ---
    # SettingsManager instance is global, load theme early
    try:
        # Theme application handled inside MainWindow._apply_theme based on settings
        pass # No need to apply stylesheet here, MainWindow does it
    except Exception as e:
         print(f"Warning: Error during initial setup (stylesheet loading deferred): {e}", file=sys.stderr)

    # --- Create and Show Main Window ---
    try:
        main_window = MainWindow() # MainWindow will apply theme internally
        main_window.show()
    except Exception as e:
         # Handle potential errors during MainWindow initialization
         handle_unhandled_exception(type(e), e, e.__traceback__)
         # No sys.exit here, handle_unhandled_exception already does it

    # --- Start Event Loop ---
    exit_code = app.exec()

    # --- Cleanup ---
    # Lock file is released automatically when lock_file goes out of scope
    # Explicitly release? lock_file.unlock() might be needed in some edge cases, but usually not.
    print("Application finished.")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
