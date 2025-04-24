import sys
import os
import json
import uuid
import re
import time
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTabWidget, QDockWidget, QMessageBox, QStatusBar,
                             QLabel, QFileDialog, QSplitter, QApplication,
                             QProgressDialog, QInputDialog, QSizePolicy, QComboBox,
                             QToolButton, QMenu, QWidgetAction, QPushButton, QStyleFactory)
from PyQt6.QtGui import (QAction, QIcon, QKeySequence, QCloseEvent, QFont, QGuiApplication,
                         QActionGroup, QPalette, QTextCursor)
from PyQt6.QtCore import (Qt, QSize, QTimer, QUrl, QSettings, QStandardPaths, QPoint,
                          QByteArray, QCoreApplication, pyqtSlot)

from ui.notebook_tree import NotebookTree, ITEM_ID_ROLE, ITEM_TYPE_ROLE, NOTE_FILE_PATH_ROLE
from ui.editor_widget import EditorWidget
from ui.settings_dialog import SettingsDialog
from ui.toolbar import create_main_toolbar, load_icon

from core.settings import settings_manager, APP_NAME, ORG_NAME
from core.cloud_sync import GoogleDriveSync, gdrive_mapper
from core.spellcheck import SpellCheckManager
from core.llm import LLMManager
from core.transcription import TranscriptionManager

from logic.autosave import AutosaveManager
from logic.exporter import Exporter, PANDOC_AVAILABLE

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - AI Markdown Notes")
        self.setWindowIcon(load_icon("notanova-logo", "text-x-generic"))
        self._rename_context = {"item_id": None, "old_name": None}
        self._instruct_ai_context = {"start": -1, "end": -1, "editor": None}
        self._ai_progress_dialog = None # Progress dialog for *LLM* tasks ONLY
        self._active_ai_manager = None # Track which manager is running for cancellation

        # --- Initialize Core Managers ---
        self.cloud_sync = GoogleDriveSync(self); self.spell_check_manager = SpellCheckManager(self)
        self.llm_manager = LLMManager(self); self.transcription_manager = TranscriptionManager(self)
        self.exporter = Exporter(self)

        # --- Setup UI Widgets ---
        self.notebook_tree = NotebookTree(self); self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True); self.tab_widget.setMovable(True); self.tab_widget.setUsesScrollButtons(True)
        self.notebook_dock = QDockWidget("Notebooks", self); self.notebook_dock.setObjectName("NotebookDock")
        self.notebook_dock.setWidget(self.notebook_tree); self.notebook_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.notebook_dock)
        self.setCentralWidget(self.tab_widget)
        self.status_bar = QStatusBar(self); self.setStatusBar(self.status_bar)
        self._active_word_count_label = QLabel("Words: -"); self._active_char_count_label = QLabel("Chars: -")
        self._active_cursor_pos_label = QLabel("Ln: -, Col: -"); self.ai_status_label = QLabel("AI: Idle")
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status_bar.addWidget(spacer); self.status_bar.addPermanentWidget(self._active_word_count_label)
        self.status_bar.addPermanentWidget(self._active_char_count_label); self.status_bar.addPermanentWidget(self._active_cursor_pos_label)
        self.status_bar.addPermanentWidget(self.ai_status_label)

        # --- Create Actions, Menus, Toolbar *BEFORE* Applying Theme/Font ---
        self._create_actions()
        self._create_menu_bar()
        self.main_toolbar = create_main_toolbar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.main_toolbar)

        # --- Apply Theme and Font ---
        self._apply_theme()
        self._apply_font()

        # --- Initialize Autosave ---
        self.autosave_manager = AutosaveManager(self.tab_widget, self); self.autosave_manager.requestSave.connect(self.save_note_in_tab)

        # --- Connections ---
        self.tab_widget.tabCloseRequested.connect(self.close_tab); self.tab_widget.currentChanged.connect(self.on_tab_changed)
        self.notebook_tree.noteOpened.connect(self.open_note_in_tab); self.notebook_tree.noteCreated.connect(self._handle_new_note_item)
        self.notebook_tree.renameEditingStarted.connect(self._store_rename_context); self.notebook_tree.itemRenamed.connect(self._handle_item_renamed)
        self.notebook_tree.itemDeleted.connect(self._handle_item_deleted); self.notebook_tree.structureChanged.connect(self.notebook_tree.save_notebook_structure)
        settings_manager.settingsChanged.connect(self._handle_settings_change)
        self.cloud_sync.syncError.connect(self.show_status_error); self.cloud_sync.listFilesComplete.connect(self._handle_gdrive_list)
        self.cloud_sync.uploadComplete.connect(self._handle_gdrive_upload); self.cloud_sync.downloadComplete.connect(self._handle_gdrive_download)
        self.cloud_sync.authenticationComplete.connect(self._handle_gdrive_auth)
        # LLM Signals
        self.llm_manager.fixComplete.connect(self._handle_llm_fix_complete); self.llm_manager.fixError.connect(self._handle_llm_error)
        self.llm_manager.instructionComplete.connect(self._handle_llm_instruction_complete); self.llm_manager.instructionError.connect(self._handle_llm_error)
        self.llm_manager.statusUpdate.connect(self._update_ai_status)
        # Transcription Signals
        self.transcription_manager.transcriptionComplete.connect(self._handle_transcription_complete); self.transcription_manager.transcriptionError.connect(self._handle_transcription_error)
        self.transcription_manager.statusUpdate.connect(self._update_ai_status)
        # SpellCheck Signals
        self.spell_check_manager.checkComplete.connect(self._handle_spellcheck_complete); self.spell_check_manager.checkError.connect(self.show_status_error)

        # --- Restore State and Start ---
        self.restore_geometry_and_state()
        if settings_manager.get("session_restore"): self.restore_session()
        if self.tab_widget.count() == 0: self.add_new_tab()
        self.autosave_manager.start()
        QTimer.singleShot(0, lambda: self.on_tab_changed(self.tab_widget.currentIndex())) # Initial update

    def _create_actions(self):
        # (Action creation code remains the same as previous correct version)
        self.new_note_action=QAction(load_icon("document-new"),"&New Note",self);self.new_note_action.setShortcut(QKeySequence.StandardKey.New);self.new_note_action.setStatusTip("Create a new note");self.new_note_action.triggered.connect(self.create_new_note_in_tree)
        self.new_notebook_action=QAction(load_icon("folder-new"),"New &Notebook",self);self.new_notebook_action.setStatusTip("Create a new notebook");self.new_notebook_action.triggered.connect(self.create_new_notebook_in_tree)
        self.save_note_action=QAction(load_icon("document-save"),"&Save",self);self.save_note_action.setShortcut(QKeySequence.StandardKey.Save);self.save_note_action.setStatusTip("Save the current note");self.save_note_action.triggered.connect(self.save_current_note)
        self.save_as_action=QAction(load_icon("document-save-as"),"Save &As...",self);self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs);self.save_as_action.setStatusTip("Save the current note with a new name or location");self.save_as_action.triggered.connect(self.save_current_note_as)
        self.export_action_menu=QMenu(self);self.export_action=QAction(load_icon("document-export"),"&Export As...",self);self.export_action.setStatusTip("Export the current note to another format");self.export_action.setMenu(self.export_action_menu)
        self.settings_action=QAction(load_icon("configure","preferences-system"),"&Settings...",self);self.settings_action.setShortcut(QKeySequence.StandardKey.Preferences);self.settings_action.setStatusTip("Configure application settings");self.settings_action.triggered.connect(self.show_settings_dialog)
        self.exit_action=QAction(load_icon("application-exit"),"E&xit",self);self.exit_action.setShortcut(QKeySequence.StandardKey.Quit);self.exit_action.setStatusTip("Exit the application");self.exit_action.triggered.connect(self.close)
        self.undo_action=QAction(load_icon("edit-undo"),"&Undo",self);self.undo_action.setShortcut(QKeySequence.StandardKey.Undo);self.undo_action.triggered.connect(lambda:self.current_editor_widget().undo() if self.current_editor_widget() else None)
        self.redo_action=QAction(load_icon("edit-redo"),"&Redo",self);self.redo_action.setShortcut(QKeySequence.StandardKey.Redo);self.redo_action.triggered.connect(lambda:self.current_editor_widget().redo() if self.current_editor_widget() else None)
        self.cut_action=QAction(load_icon("edit-cut"),"Cu&t",self);self.cut_action.setShortcut(QKeySequence.StandardKey.Cut);self.cut_action.triggered.connect(lambda:self.current_editor_widget().cut() if self.current_editor_widget() else None)
        self.copy_action=QAction(load_icon("edit-copy"),"&Copy",self);self.copy_action.setShortcut(QKeySequence.StandardKey.Copy);self.copy_action.triggered.connect(lambda:self.current_editor_widget().copy() if self.current_editor_widget() else None)
        self.paste_action=QAction(load_icon("edit-paste"),"&Paste",self);self.paste_action.setShortcut(QKeySequence.StandardKey.Paste);self.paste_action.triggered.connect(lambda:self.current_editor_widget().paste() if self.current_editor_widget() else None)
        self.select_all_action=QAction(load_icon("edit-select-all"),"Select &All",self);self.select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll);self.select_all_action.triggered.connect(lambda:self.current_editor_widget().selectAll() if self.current_editor_widget() else None)
        self.bold_action=QAction(load_icon("format-text-bold"),"&Bold",self);self.bold_action.setShortcut(QKeySequence.StandardKey.Bold);self.bold_action.triggered.connect(lambda:self.current_editor_widget().format_bold() if self.current_editor_widget() else None)
        self.italic_action=QAction(load_icon("format-text-italic"),"&Italic",self);self.italic_action.setShortcut(QKeySequence.StandardKey.Italic);self.italic_action.triggered.connect(lambda:self.current_editor_widget().format_italic() if self.current_editor_widget() else None)
        self.strikethrough_action=QAction(load_icon("format-text-strikethrough"),"&Strikethrough",self);self.strikethrough_action.setShortcut(QKeySequence("Ctrl+Shift+S"));self.strikethrough_action.triggered.connect(lambda:self.current_editor_widget().format_strikethrough() if self.current_editor_widget() else None)
        self.inline_code_action=QAction(load_icon("format-text-code","code-context"),"Inline &Code",self);self.inline_code_action.setShortcut(QKeySequence("Ctrl+`"));self.inline_code_action.triggered.connect(lambda:self.current_editor_widget().format_inline_code() if self.current_editor_widget() else None)
        self.heading_actions=[QAction(f"Heading {i}",self) for i in range(1,7)]; [act.triggered.connect(lambda c=False,l=i+1:self.apply_heading_from_toolbar(l)) for i,act in enumerate(self.heading_actions)]
        self.bullet_list_action=QAction(load_icon("format-list-unordered"),"&Bullet List",self);self.bullet_list_action.setShortcut(QKeySequence("Ctrl+Shift+8"));self.bullet_list_action.triggered.connect(lambda:self.current_editor_widget().format_bullet_list() if self.current_editor_widget() else None)
        self.numbered_list_action=QAction(load_icon("format-list-ordered"),"&Numbered List",self);self.numbered_list_action.setShortcut(QKeySequence("Ctrl+Shift+7"));self.numbered_list_action.triggered.connect(lambda:self.current_editor_widget().format_numbered_list() if self.current_editor_widget() else None)
        self.blockquote_action=QAction(load_icon("format-indent-more"),"Bloc&kquote",self);self.blockquote_action.setShortcut(QKeySequence("Ctrl+'"));self.blockquote_action.triggered.connect(lambda:self.current_editor_widget().format_blockquote() if self.current_editor_widget() else None)
        self.checkbox_action=QAction(load_icon("checkbox","view-task"),"Checkbox &List Item",self);self.checkbox_action.setShortcut(QKeySequence("Ctrl+Shift+L"));self.checkbox_action.triggered.connect(lambda:self.current_editor_widget().insert_checkbox() if self.current_editor_widget() else None)
        self.link_action=QAction(load_icon("insert-link"),"Insert &Link",self);self.link_action.setShortcut(QKeySequence(Qt.Modifier.CTRL|Qt.Key.Key_K));self.link_action.triggered.connect(lambda:self.current_editor_widget().insert_link() if self.current_editor_widget() else None)
        self.image_action=QAction(load_icon("insert-image"),"Insert &Image",self);self.image_action.setShortcut(QKeySequence("Ctrl+Shift+I"));self.image_action.triggered.connect(lambda:self.current_editor_widget().insert_image() if self.current_editor_widget() else None)
        self.table_action=QAction(load_icon("insert-table"),"Insert &Table",self);self.table_action.setShortcut(QKeySequence("Ctrl+Shift+T"));self.table_action.triggered.connect(lambda:self.current_editor_widget().insert_table() if self.current_editor_widget() else None)
        self.code_block_action=QAction(load_icon("insert-code-block","code-block-tag"),"Insert C&ode Block",self);self.code_block_action.setShortcut(QKeySequence("Ctrl+Shift+C"));self.code_block_action.triggered.connect(lambda:self.current_editor_widget().insert_code_block() if self.current_editor_widget() else None)
        self.hr_action=QAction(load_icon("insert-horizontal-rule"),"Insert Horizontal &Rule",self);self.hr_action.setShortcut(QKeySequence("Ctrl+Shift+R"));self.hr_action.triggered.connect(lambda:self.current_editor_widget().insert_horizontal_rule() if self.current_editor_widget() else None)
        self.toggle_notebook_tree_action=QAction("Toggle &Notebook Panel",self);self.toggle_notebook_tree_action.setCheckable(True);self.toggle_notebook_tree_action.setChecked(not self.notebook_dock.isHidden());self.toggle_notebook_tree_action.triggered.connect(self.toggle_notebook_panel);self.notebook_dock.visibilityChanged.connect(self.toggle_notebook_tree_action.setChecked)
        self.toggle_toolbar_action=QAction("Toggle &Toolbar",self);self.toggle_toolbar_action.setCheckable(True); # Connected in create_main_toolbar
        self.fix_text_action=QAction(load_icon("ai-fix-text","edit-repair"),"&Fix Grammar/Style (LLM)",self);self.fix_text_action.setStatusTip("Use LLM to improve selected text or the entire note");self.fix_text_action.triggered.connect(self.run_llm_fix)
        self.transcribe_action=QAction(load_icon("media-record","audio-input-microphone"),"&Record / Transcribe",self);self.transcribe_action.setStatusTip("Record audio using microphone and transcribe to text");self.transcribe_action.setCheckable(True);self.transcribe_action.triggered.connect(self.toggle_transcription)
        self.spell_check_action=QAction(load_icon("tools-check-spelling"),"Check &Spelling/Grammar",self);self.spell_check_action.setShortcut(QKeySequence("F7"));self.spell_check_action.setStatusTip("Check spelling and grammar in the current note");self.spell_check_action.triggered.connect(self.run_spell_check)
        self.gdrive_auth_action=QAction(load_icon("cloud-auth","preferences-system-network"),"&Authenticate Google Drive",self);self.gdrive_auth_action.setStatusTip("Log in to Google Drive to enable cloud sync");self.gdrive_auth_action.triggered.connect(self.cloud_sync.initiate_authentication_flow)
        self.gdrive_list_action=QAction(load_icon("cloud-download","folder-download"),"&Open from Google Drive",self);self.gdrive_list_action.setStatusTip("List and open notes from Google Drive");self.gdrive_list_action.triggered.connect(self.cloud_sync.list_files)
        self.gdrive_upload_action=QAction(load_icon("cloud-upload","folder-upload"),"&Save to Google Drive",self);self.gdrive_upload_action.setStatusTip("Upload the current note to Google Drive");self.gdrive_upload_action.triggered.connect(self.upload_current_note_to_gdrive)
        self.about_action=QAction(load_icon("help-about"),"&About NotaNova",self);self.about_action.setStatusTip("Show information about NotaNova");self.about_action.triggered.connect(self.show_about_dialog)
        self.about_qt_action=QAction(load_icon("help-about-qt","preferences-system"),"About &Qt",self);self.about_qt_action.setStatusTip("Show information about the Qt framework");self.about_qt_action.triggered.connect(QApplication.instance().aboutQt)

    def _create_menu_bar(self):
        # (Menu bar creation code remains the same)
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File"); file_menu.addAction(self.new_note_action); file_menu.addAction(self.new_notebook_action)
        file_menu.addSeparator(); file_menu.addAction(self.save_note_action); file_menu.addAction(self.save_as_action)
        self.export_action_menu.addAction(".md (Markdown)", lambda: self.export_current_note('md'))
        self.export_action_menu.addAction(".html (HTML)", lambda: self.export_current_note('html'))
        self.export_action_menu.addAction(".pdf (PDF)", lambda: self.export_current_note('pdf'))
        docx_action = self.export_action_menu.addAction(".docx (Word - needs Pandoc)", lambda: self.export_current_note('docx'))
        docx_action.setEnabled(PANDOC_AVAILABLE); file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        cloud_menu = file_menu.addMenu(load_icon("cloud", "network-server"), "&Cloud Sync (Google Drive)")
        cloud_menu.addAction(self.gdrive_auth_action); cloud_menu.addAction(self.gdrive_list_action); cloud_menu.addAction(self.gdrive_upload_action)
        file_menu.addSeparator(); file_menu.addAction(self.settings_action); file_menu.addSeparator(); file_menu.addAction(self.exit_action)
        edit_menu = menu_bar.addMenu("&Edit"); edit_menu.addAction(self.undo_action); edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator(); edit_menu.addAction(self.cut_action); edit_menu.addAction(self.copy_action); edit_menu.addAction(self.paste_action)
        edit_menu.addSeparator(); edit_menu.addAction(self.select_all_action)
        format_menu = menu_bar.addMenu("F&ormat"); format_menu.addAction(self.bold_action); format_menu.addAction(self.italic_action)
        format_menu.addAction(self.strikethrough_action); format_menu.addAction(self.inline_code_action); format_menu.addSeparator()
        heading_menu = format_menu.addMenu("&Heading"); heading_menu.addAction("Paragraph", lambda: self.apply_heading_from_toolbar(0))
        for action in self.heading_actions: heading_menu.addAction(action)
        format_menu.addSeparator(); format_menu.addAction(self.bullet_list_action); format_menu.addAction(self.numbered_list_action)
        format_menu.addAction(self.blockquote_action); format_menu.addAction(self.checkbox_action)
        insert_menu = menu_bar.addMenu("&Insert"); insert_menu.addAction(self.link_action); insert_menu.addAction(self.image_action)
        insert_menu.addAction(self.table_action); insert_menu.addAction(self.code_block_action); insert_menu.addAction(self.hr_action)
        view_menu = menu_bar.addMenu("&View"); view_menu.addAction(self.toggle_notebook_tree_action); view_menu.addAction(self.toggle_toolbar_action)
        view_menu.addSeparator(); theme_menu = view_menu.addMenu("&Theme"); theme_group = QActionGroup(self)
        light_action = theme_menu.addAction("Light"); light_action.setCheckable(True); light_action.setActionGroup(theme_group)
        dark_action = theme_menu.addAction("Dark"); dark_action.setCheckable(True); dark_action.setActionGroup(theme_group)
        light_action.triggered.connect(lambda: self.set_theme("light")); dark_action.triggered.connect(lambda: self.set_theme("dark"))
        dark_action.setChecked(True) if settings_manager.is_dark_mode() else light_action.setChecked(True)
        tools_menu = menu_bar.addMenu("&Tools"); tools_menu.addAction(self.fix_text_action); tools_menu.addAction(self.transcribe_action)
        tools_menu.addSeparator(); tools_menu.addAction(self.spell_check_action)
        help_menu = menu_bar.addMenu("&Help"); help_menu.addAction(self.about_action); help_menu.addAction(self.about_qt_action)

    # --- Action Handlers / Methods ---

    def current_editor_widget(self) -> EditorWidget | None:
        return self.tab_widget.currentWidget() if isinstance(self.tab_widget.currentWidget(), EditorWidget) else None

    def add_new_tab(self, file_path=None, content="", item_id=None, set_current=True):
        norm_file_path = os.path.normpath(file_path) if file_path else None
        for i in range(self.tab_widget.count()): # Check existing tabs
            widget = self.tab_widget.widget(i)
            if isinstance(widget, EditorWidget):
                 wp = widget.file_path; wid = widget.property("item_id")
                 # Check if file path matches
                 if norm_file_path and wp and os.path.normpath(wp)==norm_file_path:
                     self.tab_widget.setCurrentIndex(i); return widget
                 # Check if item_id matches (for unsaved files or files not yet loaded by path)
                 elif item_id and wid == item_id:
                      self.tab_widget.setCurrentIndex(i); return widget
                 # Check if unsaved tab matches requested unsaved item_id
                 elif not norm_file_path and item_id and not wp and wid == item_id:
                      self.tab_widget.setCurrentIndex(i); return widget

        editor = EditorWidget(file_path, self) # Pass self as parent
        if item_id is None and file_path: print(f"Warning: Opening external file '{file_path}' without associated item ID.")
        editor.setProperty("item_id", item_id)

        if content: # Explicit content provided (e.g., from GDrive download)
            editor.set_content(content)
        elif file_path and os.path.exists(file_path): # Existing local file
            editor.load_file(file_path)
        elif file_path: # File path provided but doesn't exist (e.g., from old session)
            editor.set_content(f"# File Not Found\nCould not load: {file_path}", False)
        # Else: New empty tab (no file_path, no content)

        tab_index = self.tab_widget.addTab(editor, "Loading...")
        self.update_tab_title(tab_index) # Set initial title

        # Use lambda to capture current editor instance for the connection
        editor.contentModified.connect(lambda modified, ed=editor: self.on_editor_modification_changed(ed, modified))
        editor.saveRequested.connect(lambda ed=editor: self.save_note_in_tab(self.tab_widget.indexOf(ed)))
        editor.cursorPositionChanged.connect(self._update_active_editor_status)
        editor.aiInstructionRequested.connect(self.run_llm_instruction)

        if set_current:
            self.tab_widget.setCurrentIndex(tab_index)
            editor.editor.setFocus() # Focus the editor when tab becomes current

        self._update_ui_state() # Update actions based on new tab state
        return editor

    def update_tab_title(self, index):
        if not (0 <= index < self.tab_widget.count()): return
        widget = self.tab_widget.widget(index)
        if not isinstance(widget, EditorWidget): return

        item_id = widget.property("item_id")
        item = self.notebook_tree.find_item_by_id(item_id) if item_id else None

        title = "Untitled"
        if item:
            title = item.text() # Use name from tree if available
        elif widget.file_path:
             title = os.path.splitext(os.path.basename(widget.file_path))[0] # Use filename part

        if widget.is_modified():
            title += " *" # Add modification indicator

        self.tab_widget.setTabText(index, title)
        self.tab_widget.setTabToolTip(index, widget.file_path or "Unsaved Note") # Add tooltip

        # Update window title if this is the current tab
        if index == self.tab_widget.currentIndex():
            self.update_window_title()

    @pyqtSlot(EditorWidget, bool) # Ensure correct slot signature
    def on_editor_modification_changed(self, editor_widget: EditorWidget, modified: bool):
        # This slot now receives both arguments correctly from the lambda
        idx = self.tab_widget.indexOf(editor_widget)
        if idx != -1:
            self.update_tab_title(idx)
        self._update_ui_state()

    def save_note_in_tab(self, index: int, force_dialog=False) -> bool:
        if not (0 <= index < self.tab_widget.count()): return False
        widget = self.tab_widget.widget(index)
        if not isinstance(widget, EditorWidget): return False

        item_id = widget.property("item_id")
        # Assign a new UUID if the note doesn't have one yet (important for new notes)
        if item_id is None:
             item_id = str(uuid.uuid4())
             widget.setProperty("item_id", item_id)
             print(f"Assigned new Item ID {item_id} to tab {index}")

        fpath = widget.file_path
        save_as = force_dialog or not fpath # Force dialog if no path exists or explicitly requested

        if save_as:
            current_tab_text = self.tab_widget.tabText(index).replace(" *", "")
            # Suggest filename based on tab text, ensuring .md extension
            cname = f"{current_tab_text}.md" if not current_tab_text.lower().endswith(".md") else current_tab_text
            default_save_dir = settings_manager.get("default_save_path")
            spath = os.path.join(default_save_dir, cname)

            new_fpath, selected_filter = QFileDialog.getSaveFileName(self, "Save Note As", spath, "Markdown Files (*.md);;All Files (*)")
            if not new_fpath: return False # User cancelled

            # Ensure .md extension if Markdown filter was selected
            if not os.path.splitext(new_fpath)[1] and "(*.md)" in selected_filter:
                 new_fpath += '.md'
            fpath = new_fpath

        # Save the content
        try:
            content = widget.get_content()
            os.makedirs(os.path.dirname(fpath), exist_ok=True) # Ensure directory exists
            with open(fpath, 'w', encoding='utf-8') as f: f.write(content)

            # Update widget state
            widget.file_path = fpath
            widget.set_modified(False)
            self.update_tab_title(index) # Update title (removes '*')

            # Update or create corresponding item in the notebook tree
            item = self.notebook_tree.find_item_by_id(item_id)
            new_name = os.path.splitext(os.path.basename(fpath))[0]
            if item:
                self.notebook_tree.update_note_metadata(item_id, file_path=fpath, name=new_name)
                if item.text() != new_name: item.setText(new_name) # Ensure tree item name matches
            else:
                # If no item exists, create one (likely a newly saved file)
                # Determine parent (root or selected notebook)
                parent_item = self.notebook_tree.get_parent_for_new_item()
                self.notebook_tree.create_or_update_note_item(parent_item, item_id, new_name, fpath)

            # Update base URL for relative paths in preview
            abs_path=os.path.abspath(fpath); bdir=os.path.dirname(abs_path);
            widget._base_url = QUrl.fromLocalFile(bdir + os.path.sep);
            widget.update_preview() # Refresh preview with new base URL

            self.show_status_message(f"Saved: {os.path.basename(fpath)}", 3000)
            self.save_session() # Update session state
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save note to:\n{fpath}\n\nError: {e}")
            import traceback; traceback.print_exc()
            return False

    def save_current_note(self):
        idx = self.tab_widget.currentIndex()
        if idx != -1:
            self.save_note_in_tab(idx)
        self._update_ui_state()

    def save_current_note_as(self):
        idx = self.tab_widget.currentIndex()
        if idx != -1:
            self.save_note_in_tab(idx, force_dialog=True)
        self._update_ui_state()

    def close_tab(self, index):
        if not (0 <= index < self.tab_widget.count()): return
        widget = self.tab_widget.widget(index)

        # Handle non-editor widgets cleanly if they ever exist
        if not isinstance(widget, EditorWidget):
             print(f"Closing non-editor tab {index}")
             self.tab_widget.removeTab(index)
             widget.deleteLater()
             return

        # Check for modifications
        if widget.is_modified():
            self.raise_(); self.activateWindow() # Bring window to front
            tab_title = self.tab_widget.tabText(index).replace(" *", "")
            reply = QMessageBox.question(self, "Save Changes?",
                                         f"The note '{tab_title}' has unsaved changes.\nDo you want to save them?",
                                         QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                         QMessageBox.StandardButton.Cancel ) # Default to Cancel

            if reply == QMessageBox.StandardButton.Save:
                if not self.save_note_in_tab(index):
                    # Save failed, prevent closing the tab
                    return
            elif reply == QMessageBox.StandardButton.Cancel:
                # User cancelled, do not close the tab
                return
            # Else: Discard changes, proceed to close

        print(f"Closing tab {index}")
        self.tab_widget.removeTab(index)
        widget.deleteLater() # Schedule widget for deletion
        self._update_ui_state()
        self.save_session() # Save session after closing a tab

    def closeEvent(self, event: QCloseEvent):
        unsaved_tabs = []
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if isinstance(widget, EditorWidget) and widget.is_modified():
                unsaved_tabs.append({
                    "index": i,
                    "name": self.tab_widget.tabText(i).replace(" *", "")
                })

        if unsaved_tabs:
            self.raise_(); self.activateWindow()
            names = "\n- ".join([item['name'] for item in unsaved_tabs])
            reply = QMessageBox.warning(self, "Unsaved Changes",
                                        f"You have unsaved changes in the following notes:\n\n- {names}\n\nDo you want to save them before exiting?",
                                        QMessageBox.StandardButton.SaveAll | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                        QMessageBox.StandardButton.Cancel) # Default to Cancel

            if reply == QMessageBox.StandardButton.SaveAll:
                for item in unsaved_tabs:
                    if not self.save_note_in_tab(item["index"]):
                        # If saving fails for any tab, ask user if they still want to quit
                        cont_reply = QMessageBox.critical(self, "Save Failed",
                                                          f"Failed to save '{item['name']}'.\n\nContinue closing and discard changes for this note?",
                                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                                          QMessageBox.StandardButton.No)
                        if cont_reply == QMessageBox.StandardButton.No:
                            event.ignore() # Abort closing
                            return
                        # Else: Continue closing, changes for this failed save are lost
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore() # Abort closing
                return
            # Else: Discard all changes (QMessageBox.StandardButton.Discard)

        # Proceed with closing
        print("Saving session and notebook structure before closing...")
        self.save_session()
        self.notebook_tree.save_notebook_structure()
        self.spell_check_manager.cleanup() # Clean up spell checker resources
        self.save_geometry_and_state() # Save window position etc.
        print("Closing application.")
        event.accept() # Allow closing


    def open_note_in_tab(self, file_path, item_id):
        self.add_new_tab(file_path=file_path, item_id=item_id)
        # Status message depends on whether the file exists
        if file_path and os.path.exists(file_path):
             msg = f"Opened: {os.path.basename(file_path)}"
        elif file_path: # Path provided but file doesn't exist
             msg = f"Opened (Not Found): {os.path.basename(file_path)}"
        else: # New unsaved note
            item = self.notebook_tree.find_item_by_id(item_id)
            name = item.text() if item else "Untitled"
            msg = f"Opened new unsaved note: {name}"
        self.show_status_message(msg, 3000)

    def create_new_note_in_tree(self):
        parent_item = self.notebook_tree.get_parent_for_new_item()
        self.notebook_tree.create_new_note(parent_item=parent_item)
        # _handle_new_note_item will be called via signal

    def _handle_new_note_item(self, item_id):
        self.open_note_in_tab(file_path=None, item_id=item_id) # Open the newly created note

    def _store_rename_context(self, item_id, old_name):
        self._rename_context = {"item_id": item_id, "old_name": old_name}

    def _handle_item_renamed(self, item_id, new_name):
        # Retrieve context
        stored_id = self._rename_context.get("item_id")
        old_name = self._rename_context.get("old_name")
        self._rename_context = {} # Clear context immediately

        if not item_id or item_id != stored_id or old_name is None:
            print(f"Warning: Rename context mismatch or missing. ID: {item_id}, Stored ID: {stored_id}")
            # Still update tab title just in case, but skip file rename
            self._update_renamed_tab_title(item_id, new_name)
            return

        item = self.notebook_tree.find_item_by_id(item_id)
        if not item:
             print(f"Error: Renamed item with ID {item_id} not found in tree.")
             return

        item_type = item.data(ITEM_TYPE_ROLE)
        if item_type == 'note':
            # *** Use the imported constant ***
            old_fpath = item.data(NOTE_FILE_PATH_ROLE)
            # Rename the associated file only if it exists and has a path
            if old_fpath and os.path.exists(old_fpath):
                # Construct new path based on the new name
                dir_name = os.path.dirname(old_fpath)
                # Ensure the new name has a .md extension
                new_fname_base = new_name
                if new_fname_base.lower().endswith(".md"):
                    new_fname_base = new_fname_base[:-3] # Remove extension if user added it

                new_fname = f"{new_fname_base}.md"
                new_fpath = os.path.join(dir_name, new_fname)

                # Only rename if the path actually changes
                if os.path.normpath(old_fpath) != os.path.normpath(new_fpath):
                    try:
                        if os.path.exists(new_fpath):
                            raise FileExistsError(f"A file named '{new_fname}' already exists in this location.")
                        print(f"Renaming file: '{old_fpath}' -> '{new_fpath}'")
                        os.rename(old_fpath, new_fpath)
                        # Update metadata in the tree item
                        self.notebook_tree.update_note_metadata(item_id, file_path=new_fpath)
                        # Update any open tab corresponding to this item
                        self._update_renamed_tab(item_id, new_fpath, new_name)
                    except Exception as e:
                        QMessageBox.critical(self, "Rename Error", f"Could not rename the note file:\n{e}\n\nReverting name in tree.")
                        # Revert the name in the tree item if file rename fails
                        item.setText(old_name)
                        return # Stop further processing
                else:
                    # Path didn't change (e.g., only case change on case-insensitive FS)
                    # Still update metadata/tab title if name differs from old name
                    self._update_renamed_tab_title(item_id, new_name) # Update title in case case changed
            else:
                 # Item exists in tree but has no file path (unsaved) or path doesn't exist
                 # Just update the tab title if it's open
                 self._update_renamed_tab_title(item_id, new_name)

        elif item_type == 'notebook':
             # No file system action needed for notebooks, just save structure
             print(f"Renamed notebook '{old_name}' to '{new_name}'")

        # Save structure regardless of item type after rename
        self.notebook_tree.save_notebook_structure()
        self.save_session() # Save session in case tab titles changed

    def _update_renamed_tab(self, item_id, new_fpath, new_name):
         """Updates file path and title for an open tab after rename."""
         for i in range(self.tab_widget.count()):
             widget = self.tab_widget.widget(i)
             if isinstance(widget, EditorWidget) and widget.property("item_id") == item_id:
                  print(f"Updating open tab {i} for renamed item {item_id}")
                  widget.file_path = new_fpath
                  self.update_tab_title(i) # Update title based on new name/path
                  # Update base URL for preview if path changed
                  if new_fpath:
                       abs_p = os.path.abspath(new_fpath)
                       bdir = os.path.dirname(abs_p)
                       widget._base_url = QUrl.fromLocalFile(bdir + os.path.sep)
                       widget.update_preview()
                  break # Assume only one tab per item_id

    def _update_renamed_tab_title(self, item_id, new_name):
         """Updates only the title for an open tab (e.g., unsaved note rename)."""
         for i in range(self.tab_widget.count()):
             widget = self.tab_widget.widget(i)
             if isinstance(widget, EditorWidget) and widget.property("item_id") == item_id:
                  self.update_tab_title(i) # Update title based on new name
                  break

    def _handle_item_deleted(self, item_id):
        """Closes the tab corresponding to a deleted item."""
        for i in range(self.tab_widget.count() - 1, -1, -1): # Iterate backwards for safe removal
            widget = self.tab_widget.widget(i)
            if isinstance(widget, EditorWidget) and widget.property("item_id") == item_id:
                print(f"Closing tab for deleted item: {item_id}")
                self.tab_widget.removeTab(i)
                widget.deleteLater()
                self._update_ui_state() # Update UI after closing tab
                self.save_session() # Save session state
                break # Stop after finding the tab

    def create_new_notebook_in_tree(self):
        parent_item = self.notebook_tree.get_parent_for_new_item()
        self.notebook_tree.create_new_notebook(parent_item=parent_item)

    def export_current_note(self, format_):
        editor = self.current_editor_widget()
        if not editor:
            QMessageBox.warning(self, "Export Error", "No active note selected to export.")
            return

        iid = editor.property("item_id")
        content = editor.get_content()
        spath = editor.file_path # Original path, if exists
        sname = "Untitled"

        # Determine suggested name
        if iid and (item := self.notebook_tree.find_item_by_id(iid)):
             sname = item.text() # Use name from tree
        elif spath:
             sname = os.path.splitext(os.path.basename(spath))[0] # Use filename

        try:
            if format_ == 'md':
                self.exporter.export_to_md(content, sname + ".md")
            elif format_ == 'html':
                self.exporter.export_to_html(content, sname + ".html", source_file_path=spath)
            elif format_ == 'pdf':
                self.exporter.export_to_pdf(content, sname + ".pdf", source_file_path=spath)
            elif format_ == 'docx':
                self.exporter.export_to_docx(content, sname + ".docx")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"An unexpected error occurred during export:\n{e}")
            import traceback; traceback.print_exc()

    def show_settings_dialog(self):
        dialog = SettingsDialog(self)
        dialog.exec() # Settings are applied via signals or Apply/OK button internally

    def _handle_settings_change(self, key):
        print(f"Settings changed: {key}")
        if key in ["theme", "use_system_theme"]: # Add use_system_theme here
            self._apply_theme()
        elif key in ["font_family", "font_size"]:
            self._apply_font() # Font applies globally regardless of theme type
        elif key == "audio_input_device":
             print("Audio input device changed, re-initializing TranscriptionManager...")
             # Re-create or re-initialize the manager
             # Simple approach: just create a new one (old one should get garbage collected)
             # Make sure to disconnect old signals if necessary, though GC should handle it.
             self.transcription_manager = TranscriptionManager(self)
             # Reconnect signals for the new manager instance
             self.transcription_manager.transcriptionComplete.connect(self._handle_transcription_complete)
             self.transcription_manager.transcriptionError.connect(self._handle_transcription_error)
             self.transcription_manager.statusUpdate.connect(self._update_ai_status)
             self._update_ui_state() # Update UI based on new manager state
        elif key in ["llm_model_path", "whisper_model_version", "google_client_secret_path", "spellcheck_engine", "language"]:
             # Update UI state that depends on these settings (e.g., enable/disable actions)
             self._update_ui_state()


    def _apply_theme(self):
        """Applies the current theme or attempts to use system theme."""
        app = QApplication.instance()
        if not app:
            print("Warning: QApplication instance not found during theme apply.")
            return

        use_system = settings_manager.should_use_system_theme()

        if use_system:
            print("Applying System Theme (Clearing QSS)...")
            app.setStyleSheet("") # Clear custom stylesheet
            # Optional: Try explicitly setting a system style (unreliable)
            # keys = QStyleFactory.keys()
            # system_style_key = "Fusion" # Default fallback
            # if sys.platform == "win32" and "windowsvista" in keys: system_style_key = "windowsvista"
            # elif sys.platform == "darwin" and "macOS" in keys: system_style_key = "macOS"
            # print(f"Attempting to set system style: {system_style_key}")
            # try: app.setStyle(QStyleFactory.create(system_style_key))
            # except Exception as e: print(f"Failed to set system style: {e}")

            # Reset darkMode property on main window and potentially others
            self.setProperty("darkMode", False)

        else:
            # Apply custom QSS theme
            dark = settings_manager.is_dark_mode()
            print(f"Applying Custom Theme: {'dark' if dark else 'light'}")

            stylesheet = settings_manager.load_stylesheet()
            if not stylesheet:
                 print("Warning: Custom stylesheet is empty. UI might look inconsistent.")

            app.setStyleSheet(stylesheet) # Apply custom QSS

            # Set the darkMode property for QSS selectors
            self.setProperty("darkMode", dark)
            for i in range(self.tab_widget.count()):
                 widget = self.tab_widget.widget(i)
                 if widget: widget.setProperty("darkMode", dark)
            # Set on other relevant widgets if they exist
            if hasattr(self, 'notebook_dock'): self.notebook_dock.setProperty("darkMode", dark)
            if hasattr(self, 'notebook_tree'): self.notebook_tree.setProperty("darkMode", dark)
            # ... etc ...

        # Re-polish the application to apply style changes
        style = app.style()
        if style:
            style.unpolish(app)
            style.polish(app)
        else:
            print("Warning: Could not get application style for polishing.")


        # Explicitly update editor widgets as their content rendering depends on palette/style
        new_palette = app.palette() # Get the potentially updated palette
        for i in range(self.tab_widget.count()):
             widget = self.tab_widget.widget(i)
             if isinstance(widget, EditorWidget):
                  # Palette might have changed, update components relying on it
                  widget.editor.setPalette(new_palette) # Ensure editor gets new palette
                  widget.preview.setPalette(new_palette) # Ensure preview gets new palette
                  widget.highlighter.palette = widget.editor.palette() # Update highlighter palette
                  widget.highlighter._setup_formats() # Re-create formats based on new palette
                  widget.highlighter.rehighlight() # Re-run highlighter
                  widget._update_preview_background() # Update preview background explicitly
                  widget.update_preview() # Re-render HTML preview

        # Update other widgets if they show visual artifacts
        self.update() # Trigger repaint of the main window


    def set_theme(self, theme_name):
         if theme_name != settings_manager.get("theme"):
             settings_manager.set("theme", theme_name)
             # _apply_theme will be called via the settingsChanged signal

    def _apply_font(self):
        font = settings_manager.get_font()
        print(f"Applying application font: {font.family()} {font.pointSize()}pt")
        QApplication.instance().setFont(font) # Set global default font

        # Apply font specifically to existing editors
        for i in range(self.tab_widget.count()):
             widget = self.tab_widget.widget(i)
             if isinstance(widget, EditorWidget):
                  widget.set_editor_font(font) # EditorWidget handles its own font setting

    def toggle_notebook_panel(self, checked):
        self.notebook_dock.setVisible(checked)

    def toggle_main_toolbar(self, checked):
         if self.main_toolbar:
             self.main_toolbar.setVisible(checked)
             # Sync the check state of the action
             self.toggle_toolbar_action.setChecked(checked)

    def show_about_dialog(self):
        about_text = f"""
        <h2>{APP_NAME}</h2>
        <p>Version 0.1.0</p>
        <p>&copy; 2024 {ORG_NAME}</p>
        <p>An AI-Powered Markdown Note-Taking Application.</p>
        <p>Built with Python and the PyQt6 framework.</p>
        """
        QMessageBox.about(self, f"About {APP_NAME}", about_text)

    def show_status_message(self, message, timeout=3000):
        self.status_bar.showMessage(message, timeout)
        print(f"Status: {message}")

    def show_status_error(self, message):
        # Ensure message is a string
        msg_str = str(message)
        # Optionally clean up common prefixes
        display_msg = msg_str[len("Error:"):].strip() if msg_str.lower().startswith("error:") else msg_str
        self.status_bar.showMessage(f"Error: {display_msg}", 5000)
        print(f"ERROR: {display_msg}")
        # Show a popup for significant errors
        QMessageBox.warning(self, "Error", display_msg)


    def _update_ui_state(self):
        """Update the enabled/disabled state of actions based on context."""
        editor = self.current_editor_widget()
        has_editor = editor is not None
        is_mod = has_editor and editor.is_modified()
        doc = editor.editor.document() if has_editor else None
        cursor = editor.editor.textCursor() if has_editor else None
        has_sel = cursor.hasSelection() if cursor else False

        # File Actions
        self.save_note_action.setEnabled(is_mod)
        self.save_as_action.setEnabled(has_editor)
        self.export_action.setEnabled(has_editor)

        # Edit Actions
        self.undo_action.setEnabled(doc.isUndoAvailable() if doc else False)
        self.redo_action.setEnabled(doc.isRedoAvailable() if doc else False)
        self.cut_action.setEnabled(has_sel)
        self.copy_action.setEnabled(has_sel)
        self.paste_action.setEnabled(editor.editor.canPaste() if has_editor else False)
        self.select_all_action.setEnabled(has_editor)

        # Formatting Actions
        fmt_acts = [self.bold_action, self.italic_action, self.strikethrough_action,
                    self.inline_code_action, self.bullet_list_action, self.numbered_list_action,
                    self.blockquote_action, self.checkbox_action, self.link_action,
                    self.image_action, self.table_action, self.code_block_action,
                    self.hr_action] + self.heading_actions
        for action in fmt_acts:
            action.setEnabled(has_editor)

        # AI Actions
        self.fix_text_action.setEnabled(has_editor and self.llm_manager.is_available())
        # Transcribe action state is handled dynamically in _update_ai_status
        self._update_ai_status(self.ai_status_label.text()[len("AI: "):]) # Update button state based on current status

        # Other Tools
        self.spell_check_action.setEnabled(has_editor and self.spell_check_manager.is_checker_active())

        # Cloud Actions
        cloud_cfg = self.cloud_sync.is_configured()
        cloud_token = self.cloud_sync.has_token()
        self.gdrive_auth_action.setEnabled(cloud_cfg)
        self.gdrive_list_action.setEnabled(cloud_cfg and cloud_token)
        self.gdrive_upload_action.setEnabled(has_editor and cloud_cfg and cloud_token)

        # Update heading combo state
        if has_editor and hasattr(self, 'heading_combo'):
             current_block_fmt = cursor.blockFormat() if cursor else None
             heading_level = current_block_fmt.headingLevel() if current_block_fmt else 0
             # Prevent signal emission during programmatic update
             self.heading_combo.blockSignals(True)
             self.heading_combo.setCurrentIndex(heading_level) # 0=Paragraph, 1-6=H1-H6
             self.heading_combo.blockSignals(False)
             self.heading_combo.setEnabled(True)
        elif hasattr(self, 'heading_combo'):
             self.heading_combo.blockSignals(True)
             self.heading_combo.setCurrentIndex(0)
             self.heading_combo.blockSignals(False)
             self.heading_combo.setEnabled(False)


    def on_tab_changed(self, index):
        # Called when the current tab changes
        self._update_ui_state() # Update action states
        self._update_active_editor_status() # Update status bar labels
        self.update_window_title() # Update window title
        # Focus the editor in the newly selected tab
        editor = self.current_editor_widget()
        if editor:
            editor.editor.setFocus()

    def _update_active_editor_status(self):
        """Update status bar labels based on the current editor."""
        editor = self.current_editor_widget()
        if editor:
            self._active_word_count_label.setText(editor.word_count_label.text())
            self._active_char_count_label.setText(editor.char_count_label.text())
            self._active_cursor_pos_label.setText(editor.cursor_pos_label.text())
        else:
            # Reset labels if no editor is active
            self._active_word_count_label.setText("Words: -")
            self._active_char_count_label.setText("Chars: -")
            self._active_cursor_pos_label.setText("Ln: -, Col: -")

    def update_window_title(self):
        base = APP_NAME
        title = base
        editor = self.current_editor_widget()
        if editor:
            tab_index = self.tab_widget.currentIndex()
            tab_text = self.tab_widget.tabText(tab_index).replace(" *", "") # Clean name
            title = f"{tab_text} - {base}"
            if editor.file_path:
                try:
                    # Add parent directory for context if path exists
                    parent_dir = os.path.basename(os.path.dirname(editor.file_path))
                    if parent_dir:
                        title = f"{tab_text} ({parent_dir}) - {base}"
                except Exception: pass # Ignore path errors
            if editor.is_modified():
                title = "*" + title # Add modification marker
        self.setWindowTitle(title)

    def save_session(self):
        if not settings_manager.get("session_restore"): return
        data = {
            "open_tabs": [],
            "current_tab_index": self.tab_widget.currentIndex()
        }
        fpath = settings_manager.get("last_session_file")
        tpath = fpath + ".tmp"
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if isinstance(widget, EditorWidget):
                data["open_tabs"].append({
                    "file_path": widget.file_path, # Can be None
                    "item_id": widget.property("item_id") # Can be None initially, but should be set on save
                })
        try:
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(tpath, 'w', encoding='utf-8') as f:
                 json.dump(data, f, indent=2)
            os.replace(tpath, fpath)
            # print(f"Session saved: {fpath}") # Reduce noise
        except Exception as e:
            print(f"Error saving session: {e}")

    def restore_session(self):
        fpath = settings_manager.get("last_session_file")
        if not os.path.exists(fpath):
            print("No session file found to restore.")
            return
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print("Restoring session...")
            tabs_to_open = data.get("open_tabs", [])
            current_tab_index = data.get("current_tab_index", -1)
            opened_keys = set() # Track (path, id) tuples to avoid duplicates

            for info in tabs_to_open:
                fpath = info.get("file_path")
                item_id = info.get("item_id")
                key = (fpath, item_id) # Use tuple as key

                if key in opened_keys: continue # Skip duplicates
                opened_keys.add(key)

                # Prioritize opening existing files
                if fpath and os.path.exists(fpath):
                    self.add_new_tab(file_path=fpath, item_id=item_id, set_current=False)
                elif item_id: # If no path or path invalid, try opening by ID (unsaved or moved)
                     self.add_new_tab(file_path=None, item_id=item_id, set_current=False)
                     # If path was provided but invalid, add a note about it
                     if fpath:
                          print(f"Session Warning: File not found for path '{fpath}' (ID: {item_id}). Opening as unsaved.")
                          editor = self.tab_widget.widget(self.tab_widget.count() - 1)
                          if editor and isinstance(editor, EditorWidget):
                              editor.set_content(f"# File Not Found\nOriginal path: {fpath}\n\n" + editor.get_content(), False)

            # Set the active tab after opening all tabs
            if 0 <= current_tab_index < self.tab_widget.count():
                self.tab_widget.setCurrentIndex(current_tab_index)
            elif self.tab_widget.count() > 0:
                 self.tab_widget.setCurrentIndex(0) # Fallback to first tab

            print(f"Session restored: {self.tab_widget.count()} tabs opened.")
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error restoring session from {fpath}: {e}")
            # Optionally back up corrupted session file
            backup_path = fpath + f".corrupted.{int(time.time())}"
            try: os.rename(fpath, backup_path)
            except OSError: pass
        except Exception as e:
            print(f"Unexpected error restoring session: {e}")
            import traceback; traceback.print_exc()

        self._update_ui_state() # Update UI after restoring


    def save_geometry_and_state(self):
        settings = QSettings(ORG_NAME, APP_NAME)
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        print("Window geometry and state saved.")

    def restore_geometry_and_state(self):
        settings = QSettings(ORG_NAME, APP_NAME)
        emptyBA = QByteArray() # Default value for QSettings

        geom_val = settings.value("geometry", defaultValue=emptyBA)
        if isinstance(geom_val, QByteArray) and not geom_val.isEmpty():
             if self.restoreGeometry(geom_val): print("Window geometry restored.")
             else: print("Warning: Failed to restore window geometry.")
        else:
            # Default size if no geometry saved
            self.resize(1200, 800)
            # Optional: Center window on screen
            # screen_geo = QGuiApplication.primaryScreen().availableGeometry()
            # self.move(screen_geo.center() - self.rect().center())

        state_val = settings.value("windowState", defaultValue=emptyBA)
        if isinstance(state_val, QByteArray) and not state_val.isEmpty():
             if self.restoreState(state_val): print("Window state restored.")
             else: print("Warning: Failed to restore window state.")

        # Update toggle actions after state restoration (deferred slightly)
        QTimer.singleShot(0, self._update_restored_ui_state)

    def _update_restored_ui_state(self):
        """Update UI elements related to restored state."""
        self.toggle_notebook_tree_action.setChecked(not self.notebook_dock.isHidden())
        if self.main_toolbar:
             self.toggle_toolbar_action.setChecked(not self.main_toolbar.isHidden())

    # --- AI/Cloud Handlers ---

    def _show_ai_progress(self, title: str, label: str, can_cancel=True):
        """Shows or updates a modal progress dialog for *LLM* tasks."""
        if self._ai_progress_dialog is None:
            self._ai_progress_dialog = QProgressDialog(label, "Cancel", 0, 0, self) # Indeterminate
            self._ai_progress_dialog.setWindowTitle(title)
            self._ai_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._ai_progress_dialog.setAutoReset(True)
            self._ai_progress_dialog.setAutoClose(True)
            self._ai_progress_dialog.canceled.connect(self._request_ai_cancel)
            self._ai_progress_dialog.setMinimumDuration(500) # Only show if task > 0.5s

        self._ai_progress_dialog.setWindowTitle(title)
        self._ai_progress_dialog.setLabelText(label)
        self._ai_progress_dialog.setCancelButtonText("Cancel" if can_cancel else None) # Hide cancel if not cancellable
        self._ai_progress_dialog.setValue(0) # Reset progress for indeterminate
        self._ai_progress_dialog.show()

    def _update_ai_progress_label(self, label: str):
        """Updates the label of the AI progress dialog if visible."""
        if self._ai_progress_dialog is not None and self._ai_progress_dialog.isVisible():
            self._ai_progress_dialog.setLabelText(label)

    def _hide_ai_progress(self):
        """Hides the AI progress dialog."""
        if self._ai_progress_dialog is not None:
            self._ai_progress_dialog.reset() # Hides and resets
        self._active_ai_manager = None # Clear active manager when progress hides

    def _request_ai_cancel(self):
        """Slot connected to the progress dialog's cancel button."""
        print("AI task cancellation requested via dialog.")
        if self._active_ai_manager == "llm" and self.llm_manager:
            self.llm_manager.cancel_current_task()
        elif self._active_ai_manager == "transcription" and self.transcription_manager:
            # Cancellation for transcription might be handled differently now (e.g., stop button)
            # self.transcription_manager.cancel_current_task()
            print("Transcription cancellation via dialog not implemented (use Stop button).")
        else:
            print("Warning: No active AI manager found to cancel or cancellation not supported.")
        # Progress dialog auto-closes on cancel, hide might be redundant
        self._hide_ai_progress()


    def run_llm_fix(self):
        editor = self.current_editor_widget();
        if not editor or not self.llm_manager.is_available(): return

        text = editor.editor.textCursor().selectedText()
        is_selection = bool(text)
        if not text:
            reply = QMessageBox.question(self, "Fix Text", "No text selected. Fix the entire note?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                text = editor.get_content()
            else:
                return # User cancelled

        if not text.strip():
            self.show_status_message("Nothing to fix.", 2000)
            return

        # Store context for applying the fix
        self._instruct_ai_context = {
             "start": editor.editor.textCursor().selectionStart() if is_selection else 0,
             "end": editor.editor.textCursor().selectionEnd() if is_selection else len(editor.get_content()),
             "editor": editor,
             "type": "fix"
        }

        self.show_status_message("Sending text to LLM for correction...")
        self._active_ai_manager = "llm" # Mark LLM as active
        # Status update signal will trigger progress dialog *for LLM*
        if not self.llm_manager.fix_text_async(text):
             self._handle_llm_error("Failed to start LLM fix task.") # Also hides progress
             self._instruct_ai_context = {} # Clear context on immediate failure


    def _handle_llm_fix_complete(self, corrected_text):
        ctx = self._instruct_ai_context
        editor = ctx.get("editor")
        self._hide_ai_progress() # Hide progress on completion

        if editor is None or editor != self.current_editor_widget() or ctx.get("type") != "fix":
            print("LLM fix context mismatch or editor changed. Result ignored.")
            self.show_status_message("LLM fix completed but context changed.", 4000)
            self._instruct_ai_context = {}
            return

        start_pos = ctx.get("start", -1)
        end_pos = ctx.get("end", -1)

        cursor = editor.editor.textCursor()
        if start_pos != -1 and end_pos != -1:
             cursor.setPosition(start_pos)
             cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
             cursor.insertText(corrected_text)
             msg = "Text correction applied."
        else: # Fallback if context was invalid (shouldn't happen with fix logic)
            print("Warning: Invalid context for LLM fix result.")
            editor.set_content(corrected_text, True) # Replace all as fallback
            msg = "Note fixed (context lost)."

        self.show_status_message(msg, 3000)
        self._instruct_ai_context = {} # Clear context


    def run_llm_instruction(self, selected_text, instruction):
        editor = self.current_editor_widget()
        if not editor or not self.llm_manager.is_available():
            self.show_status_error("LLM not available or no active editor.")
            return

        cursor = editor.editor.textCursor()
        # Store context for applying the result
        self._instruct_ai_context = {
            "start": cursor.selectionStart(),
            "end": cursor.selectionEnd(),
            "editor": editor,
            "type": "instruction"
        }

        self.show_status_message(f"Sending instruction to LLM...")
        self._active_ai_manager = "llm" # Mark LLM as active
        # Status update signal will trigger progress dialog *for LLM*
        if not self.llm_manager.instruct_ai_async(selected_text, instruction):
            self._handle_llm_error("Failed to start LLM instruction task.")
            self._instruct_ai_context = {} # Clear context on immediate failure


    def _handle_llm_instruction_complete(self, result_text):
        ctx = self._instruct_ai_context
        editor = ctx.get("editor")
        self._hide_ai_progress() # Hide progress on completion

        if editor is None or editor != self.current_editor_widget() or ctx.get("type") != "instruction":
            print("LLM instruction context mismatch or editor changed.")
            # Optionally copy to clipboard as fallback
            QApplication.clipboard().setText(result_text)
            self.show_status_message("AI result ready (context changed, copied).", 4000)
            self._instruct_ai_context = {}
            return

        start_pos = ctx.get("start", -1)
        end_pos = ctx.get("end", -1)

        if start_pos != -1 and end_pos != -1:
            cursor = editor.editor.textCursor()
            cursor.setPosition(start_pos)
            cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(result_text)
            self.show_status_message("AI instruction applied.", 3000)
        else:
             print("Warning: Invalid context for LLM instruction result.")
             # Don't replace whole doc for instruction, maybe just insert at end? Or copy.
             QApplication.clipboard().setText(result_text)
             self.show_status_message("AI result ready (context lost, copied).", 4000)

        self._instruct_ai_context = {} # Clear context


    def _handle_llm_error(self, error_msg):
        """Handles errors reported by the LLMManager."""
        self._hide_ai_progress() # Hide progress on error
        self.show_status_error(f"LLM Error: {error_msg}")
        self._instruct_ai_context = {} # Clear context


    # --- Transcription ---
    def toggle_transcription(self, checked):
        """Handles clicks on the checkable transcribe action/button."""
        if not self.transcription_manager.is_available():
             self.show_status_error("Transcription service is not available.")
             self.transcribe_action.setChecked(False) # Ensure button state is correct
             return

        if checked: # User wants to start recording
            # Don't mark transcription as active manager here, let status signal handle it
            if not self.transcription_manager.start_recording():
                 # If starting failed, uncheck the button
                 self.transcribe_action.setChecked(False)
                 # Error message should be handled by the manager via statusUpdate/transcriptionError
        else: # User wants to stop recording
            # Stopping recording might trigger transcription
            if not self.transcription_manager.stop_recording_and_transcribe():
                 # If stopping failed (e.g., not recording), ensure button reflects actual state
                 self.transcribe_action.setChecked(self.transcription_manager.is_recording())
                 # Error message handled by manager if relevant


    def _handle_transcription_complete(self, text):
        # No progress dialog to hide for transcription
        editor = self.current_editor_widget()
        if not editor:
            # No editor open, maybe copy to clipboard or show in dialog
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "Transcription Complete", f"Transcription result copied to clipboard:\n\n{text[:200]}...")
            return

        cursor = editor.editor.textCursor()
        # Insert with a leading space if not at the start of a line/selection
        insert_text = (" " + text) if not cursor.atBlockStart() and cursor.positionInBlock() > 0 else text
        cursor.insertText(insert_text)
        self.show_status_message("Transcription inserted.", 3000)


    def _handle_transcription_error(self, error_msg):
        # No progress dialog to hide for transcription
        self.show_status_error(f"Transcription Error: {error_msg}")
        # Ensure transcribe button state is correct after error
        self.transcribe_action.setChecked(self.transcription_manager.is_recording())


    # --- Status & UI Updates ---
    @pyqtSlot(str)
    def _update_ai_status(self, status: str):
        """Update the AI status label and manage UI elements like buttons and progress dialog."""
        # Update the main status label
        self.ai_status_label.setText(f"AI: {status}")

        # --- Update Transcribe Button State ---
        if hasattr(self, 'transcribe_action'): # Check if action exists
            if self.transcription_manager.is_available():
                is_rec = self.transcription_manager.is_recording()
                is_transcribing = self.transcription_manager.is_transcribing()

                # Enable button unless actively transcribing
                self.transcribe_action.setEnabled(not is_transcribing)
                # Set checked state based on actual recording state
                self.transcribe_action.setChecked(is_rec)

                # Update icon and tooltip
                icon_name = "media-playback-stop" if is_rec else "media-record"
                fallback = "media-stop" if is_rec else "audio-input-microphone"
                icon = load_icon(icon_name, fallback)
                tip = "Stop Recording" if is_rec else ("Transcribing..." if is_transcribing else "Record audio and transcribe")
                self.transcribe_action.setIcon(icon)
                self.transcribe_action.setToolTip(tip)
            else:
                 # Disable if transcription is not available
                 self.transcribe_action.setEnabled(False)
                 self.transcribe_action.setChecked(False)
                 self.transcribe_action.setIcon(load_icon("media-record","audio-input-microphone"))
                 self.transcribe_action.setToolTip("Transcription unavailable")

        # --- Manage Progress Dialog *ONLY FOR LLM* ---
        is_llm_busy_status = any(kw in status for kw in ["LLM", "Fixing", "Instructing", "Generating"])
        is_final_status = any(kw in status for kw in ["Idle", "Error", "Complete", "Cancelled", "Unavailable"]) # Added Unavailable

        if is_llm_busy_status and self._active_ai_manager == "llm":
             title = "LLM Task"
             can_cancel = True # LLM tasks are cancellable
             if self._ai_progress_dialog is None or not self._ai_progress_dialog.isVisible():
                  self._show_ai_progress(title, status, can_cancel)
             else: # Update existing dialog
                  self._ai_progress_dialog.setWindowTitle(title)
                  self._update_ai_progress_label(status)
                  self._ai_progress_dialog.setCancelButtonText("Cancel" if can_cancel else None)

        elif is_final_status and self._active_ai_manager == "llm":
             # Hide progress dialog only if it was shown for an LLM task
             self._hide_ai_progress()
        elif is_final_status and self._active_ai_manager is not None:
             # Ensure active manager is cleared if some other final status occurs
             # and potentially hide progress if it was somehow shown for transcription (shouldn't happen now)
             self._hide_ai_progress()


    # --- Spell Check ---
    def run_spell_check(self):
        editor = self.current_editor_widget()
        content = editor.get_content() if editor else None
        if not editor or not content or not content.strip():
            self.show_status_message("Nothing to spell check.", 2000)
            return

        self.show_status_message("Starting spell check...")
        if not self.spell_check_manager.check_text_async(content):
             # Error should be emitted by manager if start fails
             self.show_status_message("Failed to start spell check.", 3000)


    def _handle_spellcheck_complete(self, results: list):
        if not results:
            self.show_status_message("Spell check complete. No issues found.", 3000)
            return

        # Display results in a message box (consider a dedicated panel later)
        limit = 15
        msg = f"Found {len(results)} potential issue(s):\n\n"
        for i, res in enumerate(results[:limit]):
            suggestions = f"\n  Suggest: {', '.join(res.suggestions)}" if res.suggestions else ""
            message = f" ({res.message})" if res.message else ""
            msg += f"- L{res.line} C{res.start_col + 1}: '{res.word}'{message}{suggestions}\n"

        if len(results) > limit:
            msg += f"\n...and {len(results) - limit} more issues."

        QMessageBox.information(self, "Spell Check Results", msg)


    # --- Google Drive ---
    def _handle_gdrive_auth(self, success: bool):
        msg = "Google Drive Authentication Successful." if success else "Google Drive Authentication Failed."
        (QMessageBox.information if success else QMessageBox.warning)(self, "Google Drive", msg)
        self.show_status_message(msg, 3000)
        self._update_ui_state() # Update GDrive action states

    def _handle_gdrive_list(self, files: list):
        if not files:
            QMessageBox.information(self, "Google Drive", "No Markdown or text files found in your Google Drive.")
            return

        # Create list of display names and store corresponding file IDs
        items = [f"{f['name']} ({f.get('mimeType','?')})" for f in files]
        file_map = {display_name: f for display_name, f in zip(items, files)}

        chosen_item, ok = QInputDialog.getItem(self, "Open from Google Drive", "Select a note to download:", items, 0, False)

        if ok and chosen_item:
            selected_file = file_map.get(chosen_item)
            if not selected_file:
                self.show_status_error("Invalid selection.")
                return

            file_id = selected_file['id']
            file_name = selected_file['name']

            # Determine local save path
            local_dir = settings_manager.get("default_save_path")
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, file_name)

            # Ensure .md extension if needed
            if not local_path.lower().endswith(('.md', '.txt')):
                 local_path += ".md"

            # Check for local overwrite
            if os.path.exists(local_path):
                reply = QMessageBox.question(self, "Confirm Overwrite",
                                             f"The file '{os.path.basename(local_path)}' already exists locally.\nOverwrite it?",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No:
                    self.show_status_message("Download cancelled.", 2000)
                    return

            self.show_status_message(f"Downloading '{file_name}' from Google Drive...")
            self.cloud_sync.download_file(file_id, local_path)


    def _handle_gdrive_download(self, gdrive_id, local_path):
        """Handle successful download of a GDrive file."""
        self.show_status_message(f"Downloaded: {os.path.basename(local_path)}", 3000)

        # Check if this GDrive ID is already mapped in the tree
        item = self.notebook_tree.find_item_by_gdrive_id(gdrive_id)
        note_name = os.path.splitext(os.path.basename(local_path))[0]

        if item: # Existing item found - update its path and name
            item_id = item.data(ITEM_ID_ROLE)
            print(f"Updating existing tree item {item_id} for GDrive download {gdrive_id}")
            self.notebook_tree.update_note_metadata(item_id, file_path=local_path, name=note_name)
            if item.text() != note_name: item.setText(note_name) # Sync tree name

            # Reload the file if it's already open in a tab
            reloaded = False
            for i in range(self.tab_widget.count()):
                 widget = self.tab_widget.widget(i)
                 if isinstance(widget, EditorWidget) and widget.property("item_id") == item_id:
                      print(f"Reloading content in open tab {i} for downloaded file.")
                      widget.load_file(local_path)
                      reloaded = True
                      self.tab_widget.setCurrentIndex(i) # Bring tab to front
                      break
            # If not open, open it now
            if not reloaded:
                 self.open_note_in_tab(local_path, item_id)
        else: # No existing item - create a new one in the tree
            print(f"Creating new tree item for GDrive download {gdrive_id}")
            parent_item = self.notebook_tree.get_parent_for_new_item() # Add to root or selected notebook
            new_item_id = str(uuid.uuid4()) # Generate new local ID
            new_item = self.notebook_tree.create_or_update_note_item(parent_item, new_item_id, note_name, local_path, gdrive_id)
            if new_item:
                 self.open_note_in_tab(local_path, new_item_id) # Open the newly created item
            else:
                 self.show_status_error("Failed to create tree item for downloaded note.")


    def upload_current_note_to_gdrive(self):
        editor = self.current_editor_widget()
        if not editor:
            self.show_status_error("No active note to upload.")
            return

        # Ensure the note is saved locally first
        if editor.is_modified() or not editor.file_path:
            reply = QMessageBox.question(self, "Save Before Upload?",
                                         "The note needs to be saved locally before uploading to Google Drive.\nSave now?",
                                         QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                                         QMessageBox.StandardButton.Save)
            if reply != QMessageBox.StandardButton.Save or not self.save_current_note():
                self.show_status_message("Upload cancelled.", 2000)
                return
            # Check again if file_path is valid after save attempt
            if not editor.file_path:
                 self.show_status_error("Failed to get file path after saving. Cannot upload.")
                 return

        item_id = editor.property("item_id")
        if not item_id:
             # This shouldn't happen if saved correctly, but check anyway
             self.show_status_error("Note has no associated ID. Cannot determine Google Drive mapping.")
             return

        file_name = os.path.basename(editor.file_path)
        self.show_status_message(f"Uploading '{file_name}' to Google Drive...")
        # Pass local item ID to worker for mapping update on success
        self.cloud_sync.upload_file(editor.file_path, item_id)


    def _handle_gdrive_upload(self, local_item_id, gdrive_file_id):
        """Handle successful upload confirmation."""
        # Mapping is already updated by cloud_sync via the mapper
        item = self.notebook_tree.find_item_by_id(local_item_id)
        name = item.text() if item else "Note"
        self.show_status_message(f"'{name}' uploaded successfully to Google Drive.", 3000)
        # Update metadata in tree item if necessary (already done by mapper, but good practice)
        self.notebook_tree.update_note_metadata(local_item_id, gdrive_id=gdrive_file_id)


    # --- Toolbar/Menu Handlers ---
    def apply_heading_from_toolbar(self, level):
        editor = self.current_editor_widget()
        if not editor: return

        if 0 <= level <= 6:
            if level == 0: # Paragraph - Remove heading prefix
                cursor = editor.editor.textCursor()
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                txt = cursor.selectedText()
                # Remove leading hashes and spaces
                cleaned = re.sub(r"^\s*#+\s*", "", txt)
                if cleaned != txt: # Only insert if text changed
                    cursor.insertText(cleaned)
                else:
                    cursor.clearSelection() # Avoid deleting if no change
                cursor.endEditBlock()
            else: # Apply H1-H6
                editor.format_heading(level)
        editor.editor.setFocus() # Return focus to editor


    def get_editor_content_by_id(self, item_id):
        """Retrieves content from an open editor tab by item ID."""
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if isinstance(widget, EditorWidget) and widget.property("item_id") == item_id:
                return widget.get_content()
        return None # Not found or not open
