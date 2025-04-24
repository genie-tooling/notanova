import os
import sys
import subprocess
import shutil # For which()
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QGroupBox,
                             QComboBox, QSpinBox, QPushButton, QDialogButtonBox,
                             QLineEdit, QFileDialog, QLabel, QFontComboBox,
                             QHBoxLayout, QCheckBox, QTabWidget, QWidget, QMessageBox)
from PyQt6.QtGui import QFont, QIntValidator, QPalette, QAction, QColor
from PyQt6.QtCore import Qt, QDir, QStandardPaths, QSize
from PyQt6.QtMultimedia import QMediaDevices # For audio device listing

from core.settings import settings_manager, APP_NAME, ORG_NAME
from ui.toolbar import load_icon # For consistent icons

# Import availability flags (or check dynamically)
from core.llm import LLAMA_CPP_AVAILABLE
from core.transcription import WHISPER_AVAILABLE
from core.spellcheck import LANGUAGETOOL_AVAILABLE, HUNSPELL_AVAILABLE, ASPELL_AVAILABLE

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setMinimumWidth(600)
        self.setProperty("darkMode", settings_manager.is_dark_mode())
        self._initial_settings = {} # Store initial values to track changes

        self.tab_widget = QTabWidget(self)
        layout = QVBoxLayout(self); layout.addWidget(self.tab_widget)

        self.create_appearance_tab()
        self.create_editor_tab()
        self.create_files_tab()
        self.create_audio_tab()
        self.create_ai_tab()
        self.create_cloud_tab()

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        self.button_box.accepted.connect(self.apply_settings); self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.apply_settings)
        layout.addWidget(self.button_box)

        self.load_settings()
        self._store_initial_settings() # Store after loading
        self.apply_button.setEnabled(False) # Initially disabled

        # Connect signals from input widgets to detect changes
        self._connect_change_signals()


    def _store_initial_settings(self):
        """Stores the current values of all settings fields."""
        self._initial_settings = self._get_current_field_values()

    def _get_current_field_values(self):
        """Reads the current values from all UI fields."""
        # This needs to collect values from ALL input widgets across tabs
        return {
            "use_system_theme": self.system_theme_check.isChecked(),
            "theme": self.theme_combo.currentText(),
            "font_family": self.font_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "language": self.language_edit.text(),
            "default_save_path": self.default_save_path_edit.text(),
            "autosave_interval_sec": self.autosave_spin.value(),
            "session_restore": self.session_restore_check.isChecked(),
            "audio_input_device": self.audio_input_combo.currentData(),
            "audio_output_device": self.audio_output_combo.currentData(),
            "llm_model_path": self.llm_path_edit.text(),
            "whisper_model_version": self.whisper_model_combo.currentText(),
            "spellcheck_engine": self.spellcheck_combo.currentText(),
            "google_client_secret_path": self.gdrive_secret_edit.text(),
        }

    def _connect_change_signals(self):
        """Connect signals from widgets to the _on_setting_changed slot."""
        # Appearance
        self.system_theme_check.toggled.connect(self._on_setting_changed)
        self.theme_combo.currentTextChanged.connect(self._on_setting_changed)
        # Editor
        self.font_combo.currentFontChanged.connect(self._on_setting_changed)
        self.font_size_spin.valueChanged.connect(self._on_setting_changed)
        self.language_edit.textChanged.connect(self._on_setting_changed)
        # Files & Session
        self.default_save_path_edit.textChanged.connect(self._on_setting_changed)
        self.autosave_spin.valueChanged.connect(self._on_setting_changed)
        self.session_restore_check.stateChanged.connect(self._on_setting_changed)
        # Audio
        self.audio_input_combo.currentIndexChanged.connect(self._on_setting_changed)
        self.audio_output_combo.currentIndexChanged.connect(self._on_setting_changed)
        # AI
        self.llm_path_edit.textChanged.connect(self._on_setting_changed)
        self.whisper_model_combo.currentTextChanged.connect(self._on_setting_changed)
        self.spellcheck_combo.currentTextChanged.connect(self._on_setting_changed)
        # Cloud
        self.gdrive_secret_edit.textChanged.connect(self._on_setting_changed)

    def _on_setting_changed(self):
        """Called when any setting widget's value changes. Enables Apply button."""
        # Compare current field values with initial values
        current_values = self._get_current_field_values()
        if current_values != self._initial_settings:
            self.apply_button.setEnabled(True)
        else:
            self.apply_button.setEnabled(False)


    def create_appearance_tab(self):
        tab = QWidget(); layout = QFormLayout(tab); layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.tab_widget.addTab(tab, load_icon("preferences-desktop-theme"), "Appearance")

        self.system_theme_check = QCheckBox("Use System Theme (Experimental)")
        self.system_theme_check.setToolTip(
            "Attempt to use the OS's default look and feel.\n"
            "May result in inconsistencies or missing styles."
        )
        layout.addRow(self.system_theme_check)

        self.theme_combo = QComboBox(); self.theme_combo.addItems(["light", "dark"]); self.theme_combo.setToolTip("Select color theme (disabled if system theme is used).")
        layout.addRow("Custom Theme:", self.theme_combo)

        # Disable theme combo when system theme is checked
        self.system_theme_check.toggled.connect(self.theme_combo.setDisabled)


    def create_editor_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); self.tab_widget.addTab(tab, load_icon("preferences-desktop-font"), "Editor")
        font_group = QGroupBox("Font"); font_form = QFormLayout(font_group); font_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        font_layout = QHBoxLayout(); self.font_combo = QFontComboBox(); self.font_combo.setToolTip("Editor font family.")
        self.font_size_spin = QSpinBox(); self.font_size_spin.setRange(6, 72); self.font_size_spin.setSuffix(" pt"); self.font_size_spin.setToolTip("Editor font size.")
        font_layout.addWidget(self.font_combo, 3); font_layout.addWidget(self.font_size_spin, 1); font_form.addRow("Editor Font:", font_layout); layout.addWidget(font_group)
        spell_group = QGroupBox("Language"); spell_form = QFormLayout(spell_group); spell_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.language_edit = QLineEdit(); self.language_edit.setPlaceholderText("e.g., en-US, de-DE"); self.language_edit.setToolTip("Language code (BCP 47) for spell check.")
        spell_form.addRow("Spell Check Language:", self.language_edit); layout.addWidget(spell_group); layout.addStretch(1)

    def create_files_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); self.tab_widget.addTab(tab, load_icon("folder-saved-search"), "Files & Session")
        paths_group = QGroupBox("Local Storage"); paths_form = QFormLayout(paths_group); paths_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.default_save_path_edit = QLineEdit(); self.default_save_path_edit.setToolTip("Default directory for saving new notes.")
        self.default_save_path_button = QPushButton(); self.default_save_path_button.setIcon(load_icon("folder-open")); self.default_save_path_button.setToolTip("Browse..."); self.default_save_path_button.clicked.connect(self._browse_default_save_path)
        save_path_layout = QHBoxLayout(); save_path_layout.addWidget(self.default_save_path_edit); save_path_layout.addWidget(self.default_save_path_button)
        paths_form.addRow("Default Save Path:", save_path_layout)
        self.autosave_spin = QSpinBox(); self.autosave_spin.setRange(0, 3600); self.autosave_spin.setSuffix(" seconds"); self.autosave_spin.setSpecialValueText("Disabled"); self.autosave_spin.setToolTip("Autosave interval (0=disabled).")
        paths_form.addRow("Autosave Interval:", self.autosave_spin); layout.addWidget(paths_group)
        session_group = QGroupBox("Session"); session_form = QFormLayout(session_group)
        self.session_restore_check = QCheckBox("Restore last session on startup"); self.session_restore_check.setToolTip("Reopen tabs from previous session.")
        session_form.addRow(self.session_restore_check); layout.addWidget(session_group)
        data_loc_group = QGroupBox("Application Data Locations"); data_loc_form = QFormLayout(data_loc_group); data_loc_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.notebook_path_display=self._create_readonly_path_display(settings_manager.get("notebook_data_file")); data_loc_form.addRow("Notebook Data:",self.notebook_path_display)
        self.session_path_display=self._create_readonly_path_display(settings_manager.get("last_session_file")); data_loc_form.addRow("Session Data:",self.session_path_display)
        self.gdrive_map_path_display=self._create_readonly_path_display(settings_manager.get("gdrive_note_map_file")); data_loc_form.addRow("GDrive Map:",self.gdrive_map_path_display)
        self.gdrive_token_path_display=self._create_readonly_path_display(settings_manager.get("google_credentials_path")); data_loc_form.addRow("GDrive Token:",self.gdrive_token_path_display)
        layout.addWidget(data_loc_group); layout.addStretch(1)

    def _create_readonly_path_display(self, path):
        line=QLineEdit(path); line.setReadOnly(True); line.setToolTip(path); line.setStyleSheet("QLineEdit[readOnly=\"true\"] { background-color: palette(window); border: 1px solid palette(mid); color: palette(mid); }")
        return line

    def create_audio_tab(self):
        tab = QWidget(); layout = QFormLayout(tab); layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.tab_widget.addTab(tab, load_icon("audio-card"), "Audio Devices")
        input_group = QGroupBox("Microphone (for Transcription)"); input_form = QFormLayout(input_group)
        self.audio_input_combo = QComboBox(); self.audio_input_combo.setToolTip("Microphone for voice transcription.")
        self.populate_audio_devices(self.audio_input_combo, input_devices=True); input_form.addRow("Input Device:", self.audio_input_combo); layout.addWidget(input_group)
        output_group = QGroupBox("Speaker (for Text-to-Speech - Future)"); output_form = QFormLayout(output_group)
        self.audio_output_combo = QComboBox(); self.audio_output_combo.setToolTip("Speaker for TTS (Not Implemented).")
        self.populate_audio_devices(self.audio_output_combo, input_devices=False); output_form.addRow("Output Device:", self.audio_output_combo); self.audio_output_combo.setEnabled(False)
        output_form.addRow(QLabel("(TTS not implemented)")); layout.addWidget(output_group)

    def populate_audio_devices(self, combo_box: QComboBox, input_devices: bool):
        combo_box.clear(); combo_box.addItem("Default Device", "")
        devices = QMediaDevices.audioInputs() if input_devices else QMediaDevices.audioOutputs()
        default_device = QMediaDevices.defaultAudioInput() if input_devices else QMediaDevices.defaultAudioOutput()
        for device in devices:
            desc = device.description()
            if device == default_device: desc += " (Default)"
            combo_box.addItem(desc, device.description()) # Store description as data

    def create_ai_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); self.tab_widget.addTab(tab, load_icon("applications-science"), "AI Tools")
        # --- LLM ---
        llm_group=QGroupBox("Local LLM (Text Correction)"); llm_form=QFormLayout(llm_group); llm_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        llm_h_layout=QHBoxLayout(); llm_status_layout=QHBoxLayout() # Layout for status
        self.llm_path_edit=QLineEdit(); self.llm_path_edit.setPlaceholderText("Path to .gguf"); self.llm_path_edit.setToolTip("GGUF model for llama.cpp")
        self.llm_path_button=QPushButton(); self.llm_path_button.setIcon(load_icon("document-open")); self.llm_path_button.setToolTip("Browse..."); self.llm_path_button.clicked.connect(self._browse_llm_path)
        llm_h_layout.addWidget(self.llm_path_edit); llm_h_layout.addWidget(self.llm_path_button)
        llm_form.addRow("Model Path:", llm_h_layout)
        self.llm_status_label = QLabel(); self._update_status_label(self.llm_status_label, LLAMA_CPP_AVAILABLE, "llama-cpp-python library")
        llm_status_layout.addWidget(QLabel("Status:")); llm_status_layout.addWidget(self.llm_status_label); llm_status_layout.addStretch()
        llm_form.addRow(llm_status_layout); layout.addWidget(llm_group)
        # --- Whisper ---
        whisper_group=QGroupBox("Voice Transcription (Whisper)"); whisper_form=QFormLayout(whisper_group); whisper_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        whisper_status_layout = QHBoxLayout()
        self.whisper_model_combo=QComboBox(); whisper_models=["tiny","base","small","medium","large","tiny.en","base.en","small.en","medium.en"]; self.whisper_model_combo.addItems(whisper_models)
        self.whisper_model_combo.setToolTip("Whisper model size.\nSmaller=faster, Larger=accurate.\n'.en'=English-only."); whisper_form.addRow("Whisper Model:", self.whisper_model_combo)
        self.whisper_status_label = QLabel(); self._update_status_label(self.whisper_status_label, WHISPER_AVAILABLE, "openai-whisper library & ffmpeg")
        whisper_status_layout.addWidget(QLabel("Status:")); whisper_status_layout.addWidget(self.whisper_status_label); whisper_status_layout.addStretch()
        whisper_form.addRow(whisper_status_layout); layout.addWidget(whisper_group)
        # --- Spell Check ---
        spell_group=QGroupBox("Spell & Grammar Check"); spell_form=QFormLayout(spell_group); spell_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        spell_h_layout = QHBoxLayout(); spell_status_layout = QHBoxLayout()
        self.spellcheck_combo=QComboBox(); available=["none"]; eng_map = {}
        if shutil.which('aspell'): available.append("aspell"); eng_map["aspell"] = True
        else: eng_map["aspell"] = False
        if HUNSPELL_AVAILABLE: available.append("hunspell"); eng_map["hunspell"] = True
        else: eng_map["hunspell"] = False
        if LANGUAGETOOL_AVAILABLE: available.append("languagetool"); eng_map["languagetool"] = True
        else: eng_map["languagetool"] = False
        self.spellcheck_combo.addItems(available); self.spellcheck_combo.setToolTip("Select spell/grammar engine.")
        spell_h_layout.addWidget(self.spellcheck_combo)
        self.spell_status_label = QLabel(); spell_status_layout.addWidget(QLabel("Status:")); spell_status_layout.addWidget(self.spell_status_label); spell_status_layout.addStretch()
        self.spellcheck_combo.currentTextChanged.connect(lambda text: self._update_spellcheck_status(eng_map.get(text, text == "none"))) # Update status on change
        spell_form.addRow("Checker Engine:", spell_h_layout); spell_form.addRow(spell_status_layout); layout.addWidget(spell_group); layout.addStretch(1)

    def _update_status_label(self, label: QLabel, available: bool, requirement: str):
        """Updates a status label with color based on availability."""
        if available:
            label.setText("<font color='green'>Available</font>")
            label.setToolTip(f"{requirement} found and appears operational.")
        else:
            label.setText("<font color='orange'>Not Available</font>")
            label.setToolTip(f"{requirement} not found or failed to initialize. Please install/configure.")

    def _update_spellcheck_status(self, available: bool):
        """Specific update for spellcheck status label."""
        if self.spellcheck_combo.currentText() == "none":
             self.spell_status_label.setText("<font color='gray'>Disabled</font>")
             self.spell_status_label.setToolTip("Spellcheck is disabled.")
        elif available:
            self.spell_status_label.setText("<font color='green'>Available</font>")
            self.spell_status_label.setToolTip(f"{self.spellcheck_combo.currentText()} backend found.")
        else:
            self.spell_status_label.setText("<font color='orange'>Not Available</font>")
            self.spell_status_label.setToolTip(f"{self.spellcheck_combo.currentText()} backend not found or not functional. Please install/configure.")

    def create_cloud_tab(self):
        tab=QWidget(); layout=QVBoxLayout(tab); self.tab_widget.addTab(tab,load_icon("cloud","network-server"),"Cloud Sync")
        cloud_group=QGroupBox("Google Drive Integration"); cloud_form=QFormLayout(cloud_group); cloud_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.gdrive_secret_edit=QLineEdit(); self.gdrive_secret_edit.setPlaceholderText("Path to client_secret.json"); self.gdrive_secret_edit.setToolTip("Path to Google client_secret.json.")
        self.gdrive_secret_button=QPushButton(); self.gdrive_secret_button.setIcon(load_icon("document-open")); self.gdrive_secret_button.setToolTip("Browse..."); self.gdrive_secret_button.clicked.connect(self._browse_gdrive_secret)
        secret_layout=QHBoxLayout(); secret_layout.addWidget(self.gdrive_secret_edit); secret_layout.addWidget(self.gdrive_secret_button); cloud_form.addRow("Client Secret Path:",secret_layout)
        gdrive_info=QLabel("Requires Google libs & Drive API."); cloud_form.addRow(gdrive_info); cloud_form.addRow(QLabel("-"*40))
        self.clear_token_button=QPushButton("Clear Google Auth Token"); self.clear_token_button.setToolTip("Forces re-authentication."); self.clear_token_button.clicked.connect(self._clear_gdrive_token); cloud_form.addRow(self.clear_token_button)
        layout.addWidget(cloud_group); layout.addStretch(1)

    def load_settings(self):
        use_system = settings_manager.get("use_system_theme")
        self.system_theme_check.setChecked(use_system)
        self.theme_combo.setCurrentText(settings_manager.get("theme"))
        self.theme_combo.setDisabled(use_system) # Initial disabled state

        font=settings_manager.get_font(); self.font_combo.setCurrentFont(font); self.font_size_spin.setValue(font.pointSize()); self.language_edit.setText(settings_manager.get("language"))
        self.default_save_path_edit.setText(settings_manager.get("default_save_path")); self.autosave_spin.setValue(settings_manager.get("autosave_interval_sec")); self.session_restore_check.setChecked(settings_manager.get("session_restore"))
        self.notebook_path_display.setText(settings_manager.get("notebook_data_file")); self.notebook_path_display.setToolTip(settings_manager.get("notebook_data_file"))
        self.session_path_display.setText(settings_manager.get("last_session_file")); self.session_path_display.setToolTip(settings_manager.get("last_session_file"))
        self.gdrive_map_path_display.setText(settings_manager.get("gdrive_note_map_file")); self.gdrive_map_path_display.setToolTip(settings_manager.get("gdrive_note_map_file"))
        self.gdrive_token_path_display.setText(settings_manager.get("google_credentials_path")); self.gdrive_token_path_display.setToolTip(settings_manager.get("google_credentials_path"))
        # Load Audio
        in_desc = settings_manager.get("audio_input_device"); idx = self.audio_input_combo.findData(in_desc); self.audio_input_combo.setCurrentIndex(idx if idx != -1 else 0)
        out_desc = settings_manager.get("audio_output_device"); idx = self.audio_output_combo.findData(out_desc); self.audio_output_combo.setCurrentIndex(idx if idx != -1 else 0)
        # Load AI
        self.llm_path_edit.setText(settings_manager.get("llm_model_path"))
        self.whisper_model_combo.setCurrentText(settings_manager.get("whisper_model_version"))
        spell_engine = settings_manager.get("spellcheck_engine")
        if self.spellcheck_combo.findText(spell_engine) == -1: spell_engine = "none"; settings_manager.set("spellcheck_engine", "none")
        self.spellcheck_combo.setCurrentText(spell_engine)
        self._update_spellcheck_status(self.spellcheck_combo.currentText() != "none") # Update initial status
        # Load Cloud
        self.gdrive_secret_edit.setText(settings_manager.get("google_client_secret_path"))

    def apply_settings(self):
        print("Applying settings..."); current_values = self._get_current_field_values(); changed = []
        def check_set(key, val): # Helper
            if self._initial_settings.get(key) != val: settings_manager.set(key, val); changed.append(key)

        check_set("use_system_theme", current_values["use_system_theme"])
        check_set("theme", current_values["theme"])
        new_font=self.font_combo.currentFont(); new_font.setPointSize(self.font_size_spin.value())
        if new_font != settings_manager.get_font(): settings_manager.set_font(new_font); changed.extend(["font_family", "font_size"])
        check_set("language", current_values["language"])
        check_set("default_save_path", current_values["default_save_path"])
        check_set("autosave_interval_sec", current_values["autosave_interval_sec"])
        check_set("session_restore", current_values["session_restore"])
        check_set("audio_input_device", current_values["audio_input_device"])
        check_set("audio_output_device", current_values["audio_output_device"])
        check_set("llm_model_path", current_values["llm_model_path"])
        check_set("whisper_model_version", current_values["whisper_model_version"])
        check_set("spellcheck_engine", current_values["spellcheck_engine"])
        check_set("google_client_secret_path", current_values["google_client_secret_path"])

        if changed:
            print(f"Settings applied: {', '.join(changed)}"); self._store_initial_settings(); self.apply_button.setEnabled(False)
        else: print("No settings changed.")

    # --- File/Directory Browsing Helpers ---
    def _browse_file(self, caption, current_path, filter_str):
        dir_=os.path.dirname(current_path) if current_path and os.path.isdir(os.path.dirname(current_path)) else QDir.homePath()
        fpath, _ = QFileDialog.getOpenFileName(self, caption, dir_, filter_str); return fpath
    def _browse_directory(self, caption, current_path):
        dir_ = current_path if current_path and os.path.isdir(current_path) else QDir.homePath()
        dpath = QFileDialog.getExistingDirectory(self, caption, dir_, QFileDialog.Option.ShowDirsOnly); return dpath
    def _browse_llm_path(self):
        if fpath:=self._browse_file("Select LLM Model",self.llm_path_edit.text(),"GGUF(*.gguf);;All(*)"): self.llm_path_edit.setText(fpath); self._on_setting_changed()
    def _browse_gdrive_secret(self):
        if fpath:=self._browse_file("Select Client Secret",self.gdrive_secret_edit.text(),"JSON(*.json);;All(*)"): self.gdrive_secret_edit.setText(fpath); self._on_setting_changed()
    def _browse_default_save_path(self):
        if dpath:=self._browse_directory("Select Default Save Dir",self.default_save_path_edit.text()): self.default_save_path_edit.setText(dpath); self._on_setting_changed()

    def _clear_gdrive_token(self):
        token_path = settings_manager.get("google_credentials_path")
        if token_path and os.path.exists(token_path):
            confirm = QMessageBox.question(self,"Confirm Clear","Delete GDrive token?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if confirm == QMessageBox.StandardButton.Yes:
                try: os.remove(token_path); self.gdrive_token_path_display.setText("(Cleared)"); QMessageBox.information(self,"Token Cleared","Token cleared.")
                except OSError as e: print(f"Err remove token: {e}"); QMessageBox.critical(self,"Error",f"Cannot remove token:\n{e}")
        else: QMessageBox.information(self,"Clear Token","No token file found.")
