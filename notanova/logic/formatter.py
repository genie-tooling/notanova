import markdown
import re
from PyQt6.QtGui import (QTextCursor, QTextBlockFormat, QTextCharFormat, QFont,
                         QColor, QSyntaxHighlighter, QTextDocument, QFontMetrics, QPalette)
from PyQt6.QtCore import Qt, QRegularExpression

# Import Pygments if available for better code highlighting
try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import HtmlFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False
    print("Warning: Pygments library not found. Code syntax highlighting will be basic.")
    print("Install using: pip install Pygments")


# --- Markdown Conversion ---

def markdown_to_html(md_text: str, use_pygments=True) -> str:
    """Converts Markdown text to HTML using python-markdown with extensions."""
    extensions = [
        'markdown.extensions.fenced_code',    # ```python ... ```
        'markdown.extensions.tables',         # Tables
        'markdown.extensions.nl2br',          # Newlines to <br>
        'markdown.extensions.sane_lists',     # Predictable lists
        'markdown.extensions.attr_list',      # Attributes {#id .class}
        'markdown.extensions.md_in_html',     # Allow markdown inside HTML
        'markdown.extensions.footnotes',      # Footnotes [^1]
        'markdown.extensions.toc',            # Table of contents [TOC]
        'pymdownx.betterem',      # Better * and _ emphasis
        'pymdownx.tilde',         # Strikethrough ~~text~~
        'pymdownx.caret',         # Insert ^^text^^
        'pymdownx.mark',          # Highlight ==text==
        'pymdownx.smartsymbols',  # Symbols (c) -> ©
        'pymdownx.tasklist',      # Checkboxes - [x] item
        'pymdownx.magiclink',     # Autolink URLs/emails
        'pymdownx.superfences',   # Enhanced fenced code blocks
        'pymdownx.highlight',     # Pygments highlighting via superfences
    ]
    extension_configs = {
        'markdown.extensions.toc': { 'anchorlink': True, },
        'pymdownx.tasklist': { 'clickable_checkbox': False, 'custom_checkbox': True, },
        'pymdownx.superfences': {
            'custom_fences': [ # Example for Mermaid
                { 'name': 'mermaid', 'class': 'mermaid',
                  'format': lambda src, lang, cls, opts, md, **kwargs: f'<pre class="mermaid">{src}</pre>' }
            ]
        },
         'pymdownx.highlight': {
             'use_pygments': PYGMENTS_AVAILABLE and use_pygments,
             'noclasses': False, # Use classes with Pygments for external CSS
             'css_class': 'highlight ppygments', # Consistent class name
             'guess_lang': True,
         },
    }

    try:
        html = markdown.markdown(
            md_text, extensions=extensions, extension_configs=extension_configs, output_format='html5'
        )
        return html
    except Exception as e:
        print(f"Error converting Markdown to HTML: {e}")
        import traceback; traceback.print_exc()
        return f"<pre>Error rendering Markdown:\n{e}\n\n{md_text}</pre>"

def get_pygments_css(style='default') -> str:
    """Generates CSS for Pygments code highlighting."""
    if not PYGMENTS_AVAILABLE: return ""
    try:
        formatter = HtmlFormatter(style=style, cssclass="ppygments", noclasses=False)
        return formatter.get_style_defs('.ppygments')
    except Exception as e:
        print(f"Error getting Pygments CSS for style '{style}': {e}")
        return ""

# --- QTextEdit Formatting Helpers ---

def apply_formatting(editor, prefix, suffix=None, block_format=False, requires_selection=True):
    """Applies/Toggles formatting. Handles selection/no-selection, block/inline."""
    cursor = editor.textCursor()
    doc = editor.document()
    has_selection = cursor.hasSelection()

    if not has_selection and requires_selection and not block_format:
        if suffix is None: suffix = prefix
        cursor.insertText(prefix + suffix)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, len(suffix))
        editor.setTextCursor(cursor)
        return

    cursor.beginEditBlock()
    try:
        if block_format:
            start_block_nr = doc.findBlock(cursor.selectionStart()).blockNumber()
            end_pos = cursor.selectionEnd()
            end_block = doc.findBlock(end_pos)
            # Adjust end block if selection ends exactly at the start of the next block
            if end_pos == end_block.position() and end_pos > cursor.selectionStart():
                end_block = end_block.previous()
            end_block_nr = end_block.blockNumber()

            all_prefixed = True
            temp_cursor_check = QTextCursor(doc.findBlockByNumber(start_block_nr))
            for block_num in range(start_block_nr, end_block_nr + 1):
                current_block = doc.findBlockByNumber(block_num)
                if not current_block.isValid(): break
                line_text = current_block.text()
                if not line_text.lstrip().startswith(prefix.strip()):
                    if prefix == "- " and line_text.lstrip().startswith("- ["): pass # Allow checkbox within list toggle
                    else: all_prefixed = False; break

            # Apply/remove prefix to each block
            for block_num in range(start_block_nr, end_block_nr + 1):
                current_block = doc.findBlockByNumber(block_num)
                if not current_block.isValid(): continue
                temp_cursor = QTextCursor(current_block)
                temp_cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                line_text = current_block.text()
                stripped_line = line_text.lstrip()

                if all_prefixed: # Remove prefix
                    prefix_to_remove = prefix.strip()
                    if stripped_line.startswith(prefix_to_remove):
                        prefix_len = len(prefix_to_remove)
                        prefix_start_index = line_text.find(prefix_to_remove)
                        temp_cursor.setPosition(current_block.position() + prefix_start_index)
                        temp_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, prefix_len)
                        # Remove trailing space if prefix had one
                        if prefix.endswith(' ') and temp_cursor.position() < current_block.position() + current_block.length():
                             next_char_cursor = QTextCursor(temp_cursor)
                             next_char_cursor.movePosition(QTextCursor.MoveOperation.Right)
                             if next_char_cursor.positionInBlock() > temp_cursor.positionInBlock(): # Moved right successfully
                                 char_after = doc.characterAt(temp_cursor.position())
                                 if char_after == ' ':
                                      temp_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                        temp_cursor.removeSelectedText()
                else: # Add prefix
                    if prefix.startswith("#"): # Handle headings: replace existing
                        temp_cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                        current_line = temp_cursor.selectedText()
                        cleaned_line = re.sub(r"^\s*#+\s*", "", current_line)
                        temp_cursor.insertText(prefix + cleaned_line)
                        temp_cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
                    elif not stripped_line.startswith(prefix.rstrip()): # Avoid double prefixes
                        temp_cursor.insertText(prefix)

            # Restore selection (approximate)
            final_start_block = doc.findBlockByNumber(start_block_nr)
            final_end_block = doc.findBlockByNumber(end_block_nr)
            if final_start_block.isValid() and final_end_block.isValid():
                 cursor.setPosition(final_start_block.position())
                 cursor.setPosition(final_end_block.position() + final_end_block.length() -1, QTextCursor.MoveMode.KeepAnchor)
            else: cursor.clearSelection()

        else: # Inline formatting
            if suffix is None: suffix = prefix
            selected_text = cursor.selectedText()
            start_pos = cursor.selectionStart()
            end_pos = cursor.selectionEnd()
            is_wrapped = False
            # Check if exact selection is wrapped
            if selected_text.startswith(prefix) and selected_text.endswith(suffix): is_wrapped = True
            # Check if markers are just outside selection
            elif start_pos >= len(prefix) and end_pos < doc.characterCount() - len(suffix):
                 cursor_check = QTextCursor(cursor); cursor_check.setPosition(start_pos - len(prefix))
                 cursor_check.setPosition(end_pos + len(suffix), QTextCursor.MoveMode.KeepAnchor)
                 if cursor_check.selectedText() == prefix + selected_text + suffix:
                     is_wrapped = True; start_pos -= len(prefix); end_pos += len(suffix) # Adjust positions

            if is_wrapped: # Remove markers
                cursor.setPosition(start_pos); cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                original_text = cursor.selectedText()[len(prefix):-len(suffix)]
                cursor.insertText(original_text)
                cursor.setPosition(start_pos); cursor.setPosition(start_pos + len(original_text), QTextCursor.MoveMode.KeepAnchor)
            else: # Apply markers
                cursor.insertText(prefix + selected_text + suffix)
                cursor.setPosition(start_pos); cursor.setPosition(start_pos + len(prefix) + len(selected_text) + len(suffix), QTextCursor.MoveMode.KeepAnchor)

    except Exception as e: print(f"Error applying formatting: {e}"); import traceback; traceback.print_exc()
    finally: cursor.endEditBlock(); editor.setTextCursor(cursor)

def insert_text_at_cursor(editor, text):
    editor.textCursor().insertText(text)

def insert_block_element(editor, element_type):
    """Inserts block elements, ensuring separation."""
    cursor = editor.textCursor(); cursor.beginEditBlock(); doc = editor.document()
    current_block = cursor.block(); text_to_insert = ""; cursor_offset = -1

    if element_type == "hr": text_to_insert = "---"
    elif element_type == "code_block": text_to_insert = "```python\n\n```"; cursor_offset = text_to_insert.find('\n') + 1
    elif element_type == "table": text_to_insert = "| Header 1 | Header 2 |\n|----------|----------|\n| Cell 1   | Cell 2   |"
    elif element_type == "checkbox": # Handle as block format prefix
        apply_formatting(editor, "- [ ] ", block_format=True, requires_selection=False); cursor.endEditBlock(); return
    else: cursor.endEditBlock(); return

    needs_nl_before = cursor.position() > 0 and (cursor.positionInBlock() > 0 or doc.findBlock(cursor.position()-1).length() > 0)
    insert_pos = cursor.position()
    if needs_nl_before: cursor.insertText("\n"); insert_pos += 1

    cursor.insertText(text_to_insert); inserted_len = len(text_to_insert)
    cursor.setPosition(insert_pos + inserted_len) # Position after insertion

    needs_nl_after = cursor.block().next().isValid() and cursor.block().next().length() > 0
    if needs_nl_after: cursor.insertText("\n")

    if cursor_offset != -1: cursor.setPosition(insert_pos + cursor_offset)
    else: cursor.setPosition(insert_pos + inserted_len) # Default to end

    editor.setTextCursor(cursor); cursor.endEditBlock()


# --- Basic Markdown Syntax Highlighter ---
class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.doc = document
        self.palette = QPalette() # Use default palette initially
        self._setup_formats()
        self.rules = self._setup_rules()
        self.code_block_start_re = QRegularExpression(r"^(?P<indent>\s*)```\s*(?P<lang>[a-zA-Z0-9_+-]*)\s*$")
        self.code_block_end_re = QRegularExpression(r"^(?P<indent>\s*)```\s*$")

    def _setup_formats(self):
        """Define QTextCharFormats based on palette or defaults."""
        self.formats = {}
        fg = self.palette.color(QPalette.ColorRole.Text); bg = self.palette.color(QPalette.ColorRole.Base)
        alt_bg = self.palette.color(QPalette.ColorRole.AlternateBase); link = self.palette.color(QPalette.ColorRole.Link)
        comment = QColor(fg); comment.setAlpha(180) # Dimmed
        bold_font=QFont(self.doc.defaultFont()); bold_font.setBold(True)
        italic_font=QFont(self.doc.defaultFont()); italic_font.setItalic(True)
        bold_italic_font=QFont(bold_font); bold_italic_font.setItalic(True)
        code_font=QFont("monospace"); code_font.setPointSize(self.doc.defaultFont().pointSize())

        self.formats["bold"]=f=QTextCharFormat(); f.setFont(bold_font); f.setForeground(fg)
        self.formats["italic"]=f=QTextCharFormat(); f.setFont(italic_font); f.setForeground(fg)
        self.formats["bold_italic"]=f=QTextCharFormat(); f.setFont(bold_italic_font); f.setForeground(fg)
        self.formats["strike"]=f=QTextCharFormat(); f.setFontStrikeOut(True); f.setForeground(comment)
        self.formats["heading"]=f=QTextCharFormat(); f.setFont(bold_font); f.setForeground(QColor("#4E9A06")) # Green
        self.formats["blockquote"]=f=QTextCharFormat(); f.setFontItalic(True); f.setForeground(comment)
        self.formats["hr"]=f=QTextCharFormat(); f.setForeground(comment); f.setBackground(alt_bg)
        self.formats["list_marker"]=f=QTextCharFormat(); f.setFont(bold_font); f.setForeground(QColor("#F57900")) # Orange
        self.formats["code"]=f=QTextCharFormat(); f.setFont(code_font); f.setBackground(alt_bg); f.setForeground(fg)
        self.formats["link_text"]=f=QTextCharFormat(); f.setForeground(link); f.setFontUnderline(False)
        self.formats["link_url"]=f=QTextCharFormat(); f.setForeground(comment); f.setFontUnderline(False)
        self.formats["link_title"]=f=QTextCharFormat(); f.setForeground(comment); f.setFontItalic(True)
        self.formats["code_block_bg"]=f=QTextCharFormat(); f.setBackground(alt_bg.darker(105))
        self.formats["code_block_fence"]=f=QTextCharFormat(); f.setFont(code_font); f.setForeground(comment); f.setBackground(alt_bg.darker(105))


    def _setup_rules(self):
        """Define regex rules for highlighting (excluding code blocks)."""
        rules = []
        rules.append((QRegularExpression(r"^(#{1,6})\s+(.*)"), 2, self.formats["heading"])) # Headings
        rules.append((QRegularExpression(r"^\s*([-*_])\s*\1\s*\1\s*$"), 0, self.formats["hr"])) # HR
        rules.append((QRegularExpression(r"^(>\s+)(.*)"), 2, self.formats["blockquote"])) # Blockquotes
        rules.append((QRegularExpression(r"^(\s*(?:[-+*]|\d+\.)\s)"), 1, self.formats["list_marker"])) # List Markers
        rules.append((QRegularExpression(r"(-\s+)\[([ xX])\]"), 2, self.formats["list_marker"])) # Checkbox marker

        # Emphasis (order matters: bold-italic, bold, italic)
        rules.append((QRegularExpression(r"(?<![*\\])\*\*\*([^\*\s](?:[^*]|\\\*)*?[^\*\s])\*\*\*(?![*])"), 1, self.formats["bold_italic"])) # ***text***
        rules.append((QRegularExpression(r"(?<![*\\])\*\*([^\*\s](?:[^*]|\\\*)*?[^\*\s])\*\*(?![*])"), 1, self.formats["bold"])) # **text**
        rules.append((QRegularExpression(r"(?<![*\w\\])\*([^\*\s](?:[^*]|\\\*)*?[^\*\s])\*(?![*\w])"), 1, self.formats["italic"])) # *text*
        rules.append((QRegularExpression(r"(?<![_\\])___([^_\s](?:[^_]|\\_)*?[^_\s])___(?!_)"), 1, self.formats["bold_italic"])) # ___text___
        rules.append((QRegularExpression(r"(?<![_\\])__([^_\s](?:[^_]|\\_)*?[^_\s])__(?!_)"), 1, self.formats["bold"])) # __text__
        rules.append((QRegularExpression(r"(?<![_\w\\])_([^\s_](?:[^_]|\\_)*?[^\s_])_(?![_\w])"), 1, self.formats["italic"])) # _text_

        rules.append((QRegularExpression(r"~~(.+?)~~"), 1, self.formats["strike"])) # Strikethrough
        rules.append((QRegularExpression(r"(?<!`)`([^`\n]+)`(?!`)"), 1, self.formats["code"])) # Inline Code
        rules.append((QRegularExpression(r"!\[([^\]]*)\]"), 1, self.formats["link_title"])) # Image Alt Text
        rules.append((QRegularExpression(r"\[([^\]]+)\]"), 1, self.formats["link_text"])) # Link Text
        rules.append((QRegularExpression(r"\(([^\s\)]+)\)"), 1, self.formats["link_url"])) # Link URL/Image Path
        rules.append((QRegularExpression(r"(\")([^\"]+)(\")"), 2, self.formats["link_title"])) # Link Title
        rules.append((QRegularExpression(r"\b(https?://[^\s'\")\]]+)"), 1, self.formats["link_url"])) # Autolink URL
        return rules

    def highlightBlock(self, text):
        """Highlights a single block of text."""
        prev_state = self.previousBlockState() # 0 = Normal, 1 = In Code Block
        in_code_block = (prev_state == 1)

        start_match = self.code_block_start_re.match(text)
        end_match = self.code_block_end_re.match(text)

        current_state = 0 # Default to normal state

        if in_code_block:
            if end_match.hasMatch(): # End of block
                self.setFormat(0, len(text), self.formats["code_block_fence"])
                current_state = 0
            else: # Still inside
                self.setFormat(0, len(text), self.formats["code_block_bg"])
                current_state = 1
        else: # Not in code block
            if start_match.hasMatch(): # Start of block
                self.setFormat(0, len(text), self.formats["code_block_fence"])
                current_state = 1
            else: # Apply normal rules
                current_state = 0
                for pattern, group, fmt in self.rules:
                    iterator = pattern.globalMatch(text)
                    while iterator.hasNext():
                        match = iterator.next()
                        start, length = match.capturedStart(group), match.capturedLength(group)
                        if start >= 0 and length > 0: self.setFormat(start, length, fmt)

        self.setCurrentBlockState(current_state)

    def rehighlight(self):
        # Force rehighlight of the entire document when styles change
        super().rehighlight()

