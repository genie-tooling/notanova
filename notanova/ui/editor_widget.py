import os
import re
from PyQt6.QtWidgets import (QWidget, QTextEdit, QVBoxLayout, QSplitter, QLabel,
                             QHBoxLayout, QFrame, QSizePolicy, QMenu, QInputDialog, QLineEdit)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl, QEvent, QPoint
from PyQt6.QtGui import (QSyntaxHighlighter, QTextDocument, QTextFormat, QColor,
                        QFont, QTextCharFormat, QAction, QFontMetrics, QPalette, QTextCursor,
                        QKeySequence)

from logic.formatter import markdown_to_html, apply_formatting, insert_block_element, MarkdownHighlighter, get_pygments_css, PYGMENTS_AVAILABLE
from core.settings import settings_manager

class EditorWidget(QWidget):
    """Widget combining a Markdown editor and a live preview."""
    contentModified = pyqtSignal(bool)
    saveRequested = pyqtSignal()
    cursorPositionChanged = pyqtSignal()
    aiInstructionRequested = pyqtSignal(str, str) # selected_text, instruction

    def __init__(self, file_path=None, parent=None):
        super().__init__(parent)
        self.file_path = file_path; self._is_modified = False; self._is_loading = False; self._base_url = QUrl()
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.layout.addWidget(self.splitter)
        # Create Core Widgets
        self.editor = QTextEdit(); self.editor.setAcceptRichText(False); self.editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setStyleSheet("QTextEdit { border: none; padding: 8px; }")
        self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self.preview = QWebEngineView(); self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu); self.preview.setStyleSheet("QWebEngineView { border: none; }")
        self.highlighter = MarkdownHighlighter(self.editor.document()); self.highlighter.palette = self.palette()
        self.set_editor_font(settings_manager.get_font()); self._update_preview_background()
        # Add widgets
        self.splitter.addWidget(self.editor); self.splitter.addWidget(self.preview)
        self.splitter.setSizes([int(self.width() * 0.55) if self.width() > 0 else 300, int(self.width() * 0.45) if self.width() > 0 else 250])
        # Status Bar Elements
        self.word_count_label = QLabel("Words: 0"); self.char_count_label = QLabel("Chars: 0"); self.cursor_pos_label = QLabel("Ln: 1, Col: 1")
        # Connections
        self.editor.textChanged.connect(self.on_text_changed); self.editor.cursorPositionChanged.connect(self._emit_cursor_signal)
        self.editor.cursorPositionChanged.connect(self.update_cursor_pos_label); self.editor.document().modificationChanged.connect(self._sync_modification_state)
        self._update_timer = QTimer(self); self._update_timer.setSingleShot(True); self._update_timer.setInterval(300); self._update_timer.timeout.connect(self.update_preview)
        save_action = QAction("Save", self); save_action.setShortcut(QKeySequence.StandardKey.Save); save_action.triggered.connect(self.saveRequested.emit); self.addAction(save_action)
        self.editor.installEventFilter(self); self.preview.installEventFilter(self)
        # Initial state
        if self.file_path: self.load_file(self.file_path)
        else: QTimer.singleShot(0, self.update_preview); self.update_status_labels()

    def _show_editor_context_menu(self, point: QPoint):
        menu = self.editor.createStandardContextMenu(point)
        if self.editor.textCursor().hasSelection():
            menu.addSeparator()
            instruct_ai_action = QAction("Instruct AI...", self)
            instruct_ai_action.triggered.connect(self._handle_instruct_ai)
            menu.addAction(instruct_ai_action)
        menu.exec(self.editor.mapToGlobal(point))

    def _handle_instruct_ai(self):
        selected_text = self.editor.textCursor().selectedText()
        if not selected_text: return
        instruction, ok = QInputDialog.getText(self, "Instruct AI", "Instruction:", QLineEdit.EchoMode.Normal, "")
        if ok and instruction.strip(): self.aiInstructionRequested.emit(selected_text, instruction.strip())
        else: print("AI Instruction cancelled.")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.PaletteChange:
            new_palette=self.palette();
            if obj==self.editor: self.highlighter.palette=new_palette; self.highlighter._setup_formats(); self.highlighter.rehighlight()
            self._update_preview_background(); self.update_preview(); return False
        return super().eventFilter(obj, event)
    def _update_tab_stop_width(self): metrics=QFontMetrics(self.editor.font()); space=metrics.horizontalAdvance(' '); self.editor.setTabStopDistance(max(4.0,space*4))
    def _emit_cursor_signal(self): self.cursorPositionChanged.emit()
    def _update_preview_background(self):
        if hasattr(self,'preview') and self.preview and self.preview.page(): self.preview.page().setBackgroundColor(self.palette().color(QPalette.ColorRole.Base))
    def _sync_modification_state(self, modified):
         if not self._is_loading and modified!=self._is_modified: self._is_modified=modified; self.contentModified.emit(modified)
    def on_text_changed(self):
        if not self._is_loading: self._update_timer.start(); self.update_status_labels()
    def update_preview(self):
        if not hasattr(self,'preview') or not self.preview: return
        if self._is_loading: return
        text=self.editor.toPlainText(); html=markdown_to_html(text,use_pygments=PYGMENTS_AVAILABLE); full_html=self._get_preview_html_template(html)
        base = self._base_url if self._base_url.isValid() else QUrl()
        if page:=self.preview.page(): page.setHtml(full_html,baseUrl=base)
        else: self.preview.setHtml(full_html,baseUrl=base) # Fallback
    def _get_preview_html_template(self, body):
        p=self.palette(); bg=p.color(QPalette.ColorRole.Base).name(); fg=p.color(QPalette.ColorRole.Text).name(); link=p.color(QPalette.ColorRole.Link).name()
        altbg=p.color(QPalette.ColorRole.AlternateBase).name(); mid=p.color(QPalette.ColorRole.Mid).name() if hasattr(p,'color') and p.color(QPalette.ColorRole.Mid).isValid() else ("#a0a0a0" if not settings_manager.is_dark_mode() else "#5a5a5a")
        midl=p.color(QPalette.ColorRole.Midlight).name() if hasattr(p,'color') and p.color(QPalette.ColorRole.Midlight).isValid() else ("#d0d0d0" if not settings_manager.is_dark_mode() else "#4a4a4a")
        btnbg=p.color(QPalette.ColorRole.Button).name() if hasattr(p,'color') and p.color(QPalette.ColorRole.Button).isValid() else ("#e1e1e1" if not settings_manager.is_dark_mode() else "#4a4a4a")
        fnt=self.editor.font(); ff=fnt.family(); fs=f"{fnt.pointSize()}pt" if fnt.pointSize()>0 else f"{fnt.pixelSize()}px"
        pstyle='native' if settings_manager.is_dark_mode() else 'default'; pcss=get_pygments_css(style=pstyle)
        style=f"""<style>:root{{ color-scheme:{'dark' if settings_manager.is_dark_mode() else 'light'};--bg-color:{bg};--text-color:{fg};--link-color:{link};--alt-bg-color:{altbg};--border-color:{mid};--hr-color:{midl};--table-header-bg:{btnbg};}} body{{ background-color:var(--bg-color);color:var(--text-color);font-family:"{ff}",sans-serif;font-size:{fs};line-height:1.65;padding:20px;margin:0 auto;max-width:800px;}} a{{ color:var(--link-color);text-decoration:none;}} a:hover{{ text-decoration:underline;}} h1,h2,h3,h4,h5,h6{{ margin-top:1.5em;margin-bottom:0.5em;border-bottom:1px solid var(--hr-color);padding-bottom:0.3em;}} pre{{ border:1px solid var(--border-color);padding:12px;border-radius:4px;overflow:auto;background-color:var(--alt-bg-color);}} code{{ font-family:monospace;font-size:90%;}} pre > code{{ font-size:100%;background:none;border:none;padding:0;}} code:not(pre > code){{ background-color:var(--alt-bg-color);color:var(--text-color);padding:0.2em 0.4em;margin:0 0.1em;border-radius:3px;border:1px solid var(--border-color);}} table{{ border-collapse:collapse;margin:1.2em 0;width:auto;border:1px solid var(--border-color);}} th,td{{ border:1px solid var(--border-color);padding:8px 12px;}} th{{ background-color:var(--table-header-bg);font-weight:bold;}} blockquote{{ border-left:5px solid var(--border-color);padding-left:15px;color:var(--text-color);opacity:0.85;margin:0 0 1em 0;font-style:italic;}} blockquote > p:last-child{{ margin-bottom:0;}} img{{ max-width:100%;height:auto;display:block;margin:1em 0;border-radius:3px;}} hr{{ border:none;border-top:2px solid var(--hr-color);margin:2.5em 0;}} ul.task-list{{ padding-left:1.5em;list-style:none;}} li.task-list-item input[type="checkbox"]{{ margin-right:0.6em;vertical-align:middle;transform:scale(1.1);}} {pcss} </style>"""
        base=self._base_url.toString(QUrl.UrlFormattingOption.PreferLocalFile)
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><base href="{base}">{style}</head><body>{body}</body></html>"""
    def update_status_labels(self): text=self.editor.toPlainText();cc=len(text);wc=len(re.findall(r'\b\w+\b',text,re.UNICODE));self.char_count_label.setText(f"Chars: {cc}");self.word_count_label.setText(f"Words: {wc}")
    def update_cursor_pos_label(self): c=self.editor.textCursor();l=c.blockNumber()+1;col=c.positionInBlock();self.cursor_pos_label.setText(f"Ln: {l}, Col: {col+1}")
    def load_file(self, fpath):
        if not fpath or not os.path.exists(fpath): err=f"Not found: {fpath}"; print(f"Err: {err}"); self.editor.setPlainText(f"# Error\n{err}"); self.set_modified(False); self._is_loading=False; self.file_path=fpath; self._base_url=QUrl(); self.update_preview(); self.update_status_labels(); return
        try: 
            self._is_loading=True; 
            with open(fpath,'r',encoding='utf-8') as f: content=f.read(); self.editor.setPlainText(content); self.file_path=fpath; self.editor.document().setModified(False); self._is_modified=False; abs_p=os.path.abspath(self.file_path); bdir=os.path.dirname(abs_p); self._base_url=QUrl.fromLocalFile(bdir+os.path.sep); self.update_preview(); self.update_status_labels(); self.editor.document().clearUndoRedoStacks(); self.editor.moveCursor(QTextCursor.MoveOperation.Start); self.update_cursor_pos_label(); print(f"Loaded: {fpath}")
        except Exception as e: print(f"Err load {fpath}: {e}"); import traceback; traceback.print_exc(); self.editor.setPlainText(f"# Err load\n{e}"); self.editor.document().setModified(False); self._is_modified=False
        finally: self._is_loading=False; self.update_status_labels(); self.update_cursor_pos_label()
    def get_content(self): return self.editor.toPlainText()
    def set_content(self, content, is_modified=False):
        try: self._is_loading=True; self.editor.setPlainText(content); self.editor.document().setModified(is_modified); self._is_modified=is_modified; self.update_preview(); self.update_status_labels(); self.editor.document().clearUndoRedoStacks(); self.editor.moveCursor(QTextCursor.MoveOperation.Start); self.update_cursor_pos_label()
        finally: self._is_loading=False; self.update_status_labels()
    def is_modified(self): return self.editor.document().isModified()
    def set_modified(self, mod): self.editor.document().setModified(mod) if self.editor.document().isModified()!=mod else None
    def set_editor_font(self, font): self.editor.setFont(font); self._update_tab_stop_width(); hasattr(self,'highlighter') and self.highlighter.rehighlight(); hasattr(self,'preview') and self.update_preview()
    # Formatting Actions
    def format_bold(self): apply_formatting(self.editor, "**", requires_selection=False)
    def format_italic(self): apply_formatting(self.editor, "*", requires_selection=False)
    def format_strikethrough(self): apply_formatting(self.editor, "~~", requires_selection=False)
    def format_inline_code(self): apply_formatting(self.editor, "`", requires_selection=False)
    def format_bullet_list(self): apply_formatting(self.editor, "- ", block_format=True, requires_selection=False)
    def format_numbered_list(self): apply_formatting(self.editor, "1. ", block_format=True, requires_selection=False)
    def format_blockquote(self): apply_formatting(self.editor, "> ", block_format=True, requires_selection=False)
    def format_heading(self, level): 1<=level<=6 and apply_formatting(self.editor, "#"*level+" ", block_format=True, requires_selection=False)
    def insert_link(self):
        c=self.editor.textCursor(); t=c.selectedText()
        # *** CORRECTED ARGUMENT ORDER ***
        if c.hasSelection() and not (t.startswith('[') and '](' in t) and not t.startswith('!['):
            apply_formatting(self.editor, f"[{t}](", suffix="url)", requires_selection=True)
        else:
            apply_formatting(self.editor, "[", suffix="link text](url)", requires_selection=False)
    def insert_image(self):
        c=self.editor.textCursor(); t=c.selectedText()
        # *** CORRECTED ARGUMENT ORDER ***
        if c.hasSelection() and not (t.startswith('[') and '](' in t) and not t.startswith('!['):
            apply_formatting(self.editor, f"![{t}](", suffix="image_url)", requires_selection=True)
        else:
            apply_formatting(self.editor, "![alt text](", suffix="image_url)", requires_selection=False)
    def insert_horizontal_rule(self): insert_block_element(self.editor, "hr")
    def insert_code_block(self): insert_block_element(self.editor, "code_block")
    def insert_table(self): insert_block_element(self.editor, "table")
    def insert_checkbox(self): insert_block_element(self.editor, "checkbox")
    # Edit Actions
    def undo(self): self.editor.undo(); 
    def redo(self): self.editor.redo(); 
    def cut(self): self.editor.cut()
    def copy(self): self.editor.copy(); 
    def paste(self): self.editor.paste();
    def selectAll(self): self.editor.selectAll()
    def update_status_bar(self): self.update_status_labels(); self.update_cursor_pos_label()
    def update_cursor_pos(self): self.update_cursor_pos_label()
    def insert_ai_result(self, start_pos, end_pos, result_text):
        cursor = self.editor.textCursor(); cursor.setPosition(start_pos)
        cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor); cursor.insertText(result_text)
