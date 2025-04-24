import sys
import os
from PyQt6.QtWidgets import QApplication # Use PyQt6
from notanova.ui.main_window import MainWindow # Adjust import path assuming notanova/ is the package

def main():
    # Need to ensure AppName/OrgName are set BEFORE MainWindow/SettingsManager are imported
    # This is handled inside notanova/notanova.py now.
    app = QApplication(sys.argv)
    # Settings and theme application are handled inside MainWindow now
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    # It's better practice to run the main script inside the package
    # This top-level script might cause import issues if not structured carefully.
    # Recommend running: python -m notanova.notanova
    print("Warning: Running the top-level notanova.py might lead to import issues.")
    print("Recommend running 'python -m notanova.notanova' instead.")
    # For direct execution attempt:
    try:
         # Ensure the package directory is in the path if running this directly
         package_dir = os.path.dirname(os.path.abspath(__file__))
         if package_dir not in sys.path:
             sys.path.insert(0, package_dir)
         from notanova.notanova import main as package_main
         package_main()
    except ImportError as e:
         print(f"ImportError: Could not run package main. {e}")
         print("Please try running 'python -m notanova.notanova'")
         sys.exit(1)

