import os
import sys
from PyQt6.QtWidgets import (QToolBar, QComboBox, QWidgetAction, QToolButton,
                             QMenu, QWidget, QSizePolicy, QApplication, QStyle)
from PyQt6.QtGui import QAction, QIcon, QFont, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, Qt, QSize

# Helper function to load icons (assuming icons are themed or in assets/icons)
def load_icon(name: str, fallback_name: str = None) -> QIcon:
    """Loads an icon using QIcon.fromTheme, with fallback path or standard pixmap."""
    icon = QIcon.fromTheme(name)
    if not icon.isNull(): return icon
    try: # Fallback 1: Local assets/icons relative to this script file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up one level from 'ui' to project root, then to 'assets/icons'
        assets_dir = os.path.normpath(os.path.join(script_dir, '..', 'assets', 'icons'))
        for ext in ['.png', '.svg']:
            ipath = os.path.join(assets_dir, f'{name}{ext}')
            if os.path.exists(ipath):
                # print(f"Loaded icon: {ipath}") # Debug
                return QIcon(ipath)
    except Exception as e: print(f"Warning: Error finding local assets path: {e}")

    # Fallback 2: Standard Pixmap from Qt Style
    if fallback_name:
        sp_map = { "document-new": QStyle.StandardPixmap.SP_FileIcon, "document-save": QStyle.StandardPixmap.SP_DialogSaveButton,
                   "document-save-as": QStyle.StandardPixmap.SP_DialogSaveButton, "document-open": QStyle.StandardPixmap.SP_DialogOpenButton,
                   "document-export": QStyle.StandardPixmap.SP_ArrowRight, "folder-new": QStyle.StandardPixmap.SP_FileDialogNewFolder,
                   "folder-open": QStyle.StandardPixmap.SP_DirOpenIcon, "application-exit": QStyle.StandardPixmap.SP_DialogCloseButton,
                   "edit-undo": QStyle.StandardPixmap.SP_ArrowBack, "edit-redo": QStyle.StandardPixmap.SP_ArrowForward,
                   "edit-cut": QStyle.StandardPixmap.SP_ToolBarCutButton, "edit-copy": QStyle.StandardPixmap.SP_ToolBarCopyButton,
                   "edit-paste": QStyle.StandardPixmap.SP_ToolBarPasteButton, "edit-select-all": QStyle.StandardPixmap.SP_DialogResetButton,
                   "edit-rename": QStyle.StandardPixmap.SP_LineEditClearButton, "edit-delete": QStyle.StandardPixmap.SP_TrashIcon,
                   "edit-repair": QStyle.StandardPixmap.SP_DialogApplyButton, "format-text-bold": QStyle.StandardPixmap.SP_ToolBarBoldButton,
                   "format-text-italic": QStyle.StandardPixmap.SP_ToolBarItalicButton, "format-text-strikethrough": QStyle.StandardPixmap.SP_DialogCancelButton,
                   "format-text-code": QStyle.StandardPixmap.SP_ComputerIcon, "format-list-unordered": QStyle.StandardPixmap.SP_ToolBarUnorderedListButton,
                   "format-list-ordered": QStyle.StandardPixmap.SP_ToolBarOrderedListButton, "format-indent-more": QStyle.StandardPixmap.SP_ToolBarIndentButton,
                   "format-justify-fill": QStyle.StandardPixmap.SP_ToolBarJustifyButton, "insert-link": QStyle.StandardPixmap.SP_ToolBarLinkButton,
                   "insert-image": QStyle.StandardPixmap.SP_DriveHDIcon, "insert-table": QStyle.StandardPixmap.SP_FileDialogDetailedView,
                   "insert-code-block": QStyle.StandardPixmap.SP_FileDialogContentsView, "insert-horizontal-rule": QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton,
                   "insert-object": QStyle.StandardPixmap.SP_FileDialogNewFolder, "applications-science": QStyle.StandardPixmap.SP_ComputerIcon,
                   "media-record": QStyle.StandardPixmap.SP_MediaPlay, "audio-input-microphone": QStyle.StandardPixmap.SP_MediaVolume,
                   "media-playback-stop": QStyle.StandardPixmap.SP_MediaStop, "tools-check-spelling": QStyle.StandardPixmap.SP_DialogHelpButton,
                   "cloud": QStyle.StandardPixmap.SP_CloudIcon, "network-server": QStyle.StandardPixmap.SP_DriveNetIcon,
                   "cloud-auth": QStyle.StandardPixmap.SP_ComputerIcon, "preferences-system-network": QStyle.StandardPixmap.SP_NetworkIcon,
                   "cloud-download": QStyle.StandardPixmap.SP_ArrowDown, "folder-download": QStyle.StandardPixmap.SP_ArrowDown,
                   "cloud-upload": QStyle.StandardPixmap.SP_ArrowUp, "folder-upload": QStyle.StandardPixmap.SP_ArrowUp,
                   "preferences-system": QStyle.StandardPixmap.SP_ComputerIcon, "preferences-desktop-theme": QStyle.StandardPixmap.SP_DesktopIcon,
                   "preferences-desktop-font": QStyle.StandardPixmap.SP_DesktopIcon, "folder-saved-search": QStyle.StandardPixmap.SP_DirLinkIcon,
                   "view-task": QStyle.StandardPixmap.SP_DialogYesButton, "checkbox": QStyle.StandardPixmap.SP_DialogYesButton,
                   "help-about": QStyle.StandardPixmap.SP_MessageBoxInformation, "help-about-qt": QStyle.StandardPixmap.SP_DesktopIcon,
                   "code-context": QStyle.StandardPixmap.SP_ComputerIcon, "code-block-tag": QStyle.StandardPixmap.SP_FileDialogContentsView,
                   "horizontal-line": QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton, "list-add": QStyle.StandardPixmap.SP_FileDialogNewFolder,
                   "system-run": QStyle.StandardPixmap.SP_ComputerIcon, "audio-card": QStyle.StandardPixmap.SP_MediaVolume, # Placeholder for Audio Card
                   "folder": QStyle.StandardPixmap.SP_DirIcon, # For NotebookTree
                   "text-markdown": QStyle.StandardPixmap.SP_FileIcon, # For NotebookTree
                   "text-x-generic": QStyle.StandardPixmap.SP_FileIcon, # Fallback
                   "folder-symbolic": QStyle.StandardPixmap.SP_DirIcon, # Fallback
                 }
        if sp_enum := sp_map.get(fallback_name):
            if app := QApplication.instance():
                try:
                    style = app.style()
                    if style:
                         pixmap = style.standardPixmap(sp_enum)
                         if not pixmap.isNull(): return QIcon(pixmap)
                    else: print("Warning: QApplication style not available for standard pixmap.")
                except Exception as e: print(f"Warning: Error getting standard pixmap {fallback_name}: {e}")

    # Final fallback: Simple placeholder
    print(f"Icon '{name}' (fallback '{fallback_name}') not found. Using placeholder.")
    pixmap = QPixmap(16, 16); pixmap.fill(QColor("#cccccc")); return QIcon(pixmap)


def create_main_toolbar(parent_window) -> QToolBar:
    """Creates and configures the main application toolbar."""
    toolbar = QToolBar("Main Toolbar", parent_window); toolbar.setObjectName("MainToolbar")
    toolbar.setMovable(True); toolbar.setFloatable(True); toolbar.setIconSize(QSize(18, 18))
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly) # Icons only

    # --- File / Basic Edit ---
    toolbar.addAction(parent_window.new_note_action)
    toolbar.addAction(parent_window.save_note_action)
    toolbar.addSeparator()
    toolbar.addAction(parent_window.undo_action)
    toolbar.addAction(parent_window.redo_action)
    toolbar.addSeparator()

    # --- Formatting ---
    toolbar.addAction(parent_window.bold_action)
    toolbar.addAction(parent_window.italic_action)
    toolbar.addAction(parent_window.strikethrough_action)
    toolbar.addAction(parent_window.inline_code_action)
    toolbar.addSeparator()

    # --- Heading Combo ---
    heading_combo = QComboBox(toolbar); heading_combo.setObjectName("HeadingComboBox"); heading_combo.setToolTip("Set Heading Level")
    heading_combo.addItem("Paragraph"); [heading_combo.addItem(f"Heading {i}") for i in range(1, 7)]
    heading_combo.activated.connect(parent_window.apply_heading_from_toolbar)
    parent_window.heading_combo = heading_combo # Store ref on main window for state updates
    toolbar.addWidget(heading_combo)
    toolbar.addSeparator()

    # --- Lists / Block ---
    toolbar.addAction(parent_window.bullet_list_action)
    toolbar.addAction(parent_window.numbered_list_action)
    toolbar.addAction(parent_window.blockquote_action)
    toolbar.addAction(parent_window.checkbox_action)
    toolbar.addSeparator()

    # --- Insertions Dropdown ---
    ins_btn = QToolButton(toolbar); ins_btn.setIcon(load_icon("insert-object", "list-add")); ins_btn.setToolTip("Insert Elements...")
    ins_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup); ins_menu = QMenu(ins_btn)
    ins_menu.addAction(parent_window.link_action); ins_menu.addAction(parent_window.image_action); ins_menu.addAction(parent_window.table_action)
    ins_menu.addAction(parent_window.code_block_action); ins_menu.addAction(parent_window.hr_action); ins_btn.setMenu(ins_menu)
    toolbar.addWidget(ins_btn)
    toolbar.addSeparator()

    # --- AI Tools (Direct Buttons) ---
    toolbar.addAction(parent_window.fix_text_action) # Add Fix Text directly
    toolbar.addAction(parent_window.transcribe_action) # Add Transcribe directly
    toolbar.addSeparator() # Add separator after AI tools

    # --- Spacer ---
    spacer = QWidget(toolbar); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)

    # --- Right-aligned Actions ---
    toolbar.addAction(parent_window.spell_check_action)
    toolbar.addAction(parent_window.settings_action)

    # Connect visibility toggle action from main window
    parent_window.toggle_toolbar_action.setChecked(not toolbar.isHidden())
    parent_window.toggle_toolbar_action.triggered.connect(toolbar.setVisible)
    toolbar.visibilityChanged.connect(parent_window.toggle_toolbar_action.setChecked)

    return toolbar
