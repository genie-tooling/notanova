import subprocess
import os
import sys
import re
import time
from abc import ABC, abstractmethod
from PyQt6.QtCore import QObject, pyqtSignal, QThread

# Conditional imports for spellcheckers
try:
    import language_tool_python
    LANGUAGETOOL_AVAILABLE = True
except ImportError:
    LANGUAGETOOL_AVAILABLE = False
    print("Warning: language_tool_python not found. LanguageTool spellcheck disabled.")
    print("Install it using: pip install language_tool_python (requires Java)")

try:
    import hunspell
    HUNSPELL_AVAILABLE = True
except ImportError:
    HUNSPELL_AVAILABLE = False
    print("Warning: python-hunspell library not found. Hunspell spellcheck disabled.")
    print("Install it using: pip install python-hunspell (requires hunspell dev libraries)")
except Exception as e: # Catch potential linking errors too
    HUNSPELL_AVAILABLE = False
    print(f"Warning: Error importing python-hunspell: {e}. Hunspell spellcheck disabled.")

# Check for Aspell command availability later
ASPELL_AVAILABLE = True

from core.settings import settings_manager

class SpellCheckResult:
    """Simple structure to hold spellcheck results."""
    def __init__(self, word, line, start_col, end_col, suggestions, message="", rule_id=""):
        self.word = word # The incorrect word/phrase
        self.line = line # Line number (1-based)
        self.start_col = start_col # Start column (0-based)
        self.end_col = end_col # End column (0-based, exclusive)
        self.suggestions = suggestions # List of suggested corrections
        self.message = message # Description of the error (from LanguageTool etc.)
        self.rule_id = rule_id # ID of the rule triggered (from LanguageTool etc.)

    def __str__(self):
        sug_str = f" Suggestions: {self.suggestions}" if self.suggestions else ""
        msg_str = f" ({self.message})" if self.message else ""
        return f"L{self.line} C{self.start_col}-{self.end_col}: '{self.word}'{msg_str}{sug_str}"

class SpellCheckerBase(ABC):
    """Abstract base class for spell checkers."""
    def __init__(self, language='en-US'):
        # Normalize language code format (e.g., en-US)
        self.language = language.replace('_', '-') if language else 'en-US'

    @abstractmethod
    def check(self, text) -> list[SpellCheckResult]:
        """Checks the text and returns a list of SpellCheckResult objects."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the spellchecker backend is available."""
        pass

    def cleanup(self):
        """Optional cleanup method for checkers (like LanguageTool)."""
        pass

class NoSpellChecker(SpellCheckerBase):
    """Dummy spell checker when none is selected or available."""
    def check(self, text) -> list[SpellCheckResult]:
        return []
    def is_available(self) -> bool:
        return True # Always "available" as the 'none' option

class LanguageToolChecker(SpellCheckerBase):
    """Spell and grammar checker using LanguageTool."""
    def __init__(self, language='en-US'):
        super().__init__(language)
        self._tool = None
        if LANGUAGETOOL_AVAILABLE:
            try:
                self._tool = language_tool_python.LanguageTool(self.language)
                print(f"LanguageTool initialized for {self.language}")
            except ValueError as e:
                print(f"Error initializing LanguageTool: Invalid language code '{self.language}'? Details: {e}")
                self._tool = None
            except Exception as e:
                print(f"Error initializing LanguageTool for {self.language}: {e}")
                self._tool = None

    def check(self, text) -> list[SpellCheckResult]:
        if not self.is_available(): return []
        results = []
        try:
            matches = self._tool.check(text)
            line_start_offsets = [0] + [m.end() for m in re.finditer(r'\n', text)]
            for match in matches:
                 line_num, start_col = self._get_line_col(match.offset, line_start_offsets)
                 end_col = start_col + match.errorLength
                 results.append(SpellCheckResult(
                     word=text[match.offset:match.offset + match.errorLength],
                     line=line_num, start_col=start_col, end_col=end_col,
                     suggestions=match.replacements[:5], message=match.message, rule_id=match.ruleId
                 ))
        except language_tool_python.utils.LanguageToolError as lt_error:
             print(f"LanguageTool Error during check: {lt_error}")
        except Exception as e:
            print(f"Error during LanguageTool check: {e}")
        return results

    def _get_line_col(self, offset, line_start_offsets):
         line_num = 1
         col_num = offset
         for i, line_start_offset in enumerate(line_start_offsets):
              if offset >= line_start_offset:
                   line_num = i + 1
                   col_num = offset - line_start_offset
              else: break
         return line_num, col_num

    def is_available(self) -> bool:
        return self._tool is not None

    def cleanup(self):
         if self.is_available():
             try:
                 self._tool.close()
                 print("LanguageTool closed.")
                 self._tool = None
             except Exception as e: print(f"Error closing LanguageTool: {e}")

class HunspellChecker(SpellCheckerBase):
    """Spell checker using Hunspell."""
    def __init__(self, language='en_US'):
        super().__init__(language)
        self._hobj = None
        if HUNSPELL_AVAILABLE:
            hunspell_lang_code = language.replace('-', '_')
            paths = self._get_hunspell_search_paths()
            found_path = False
            for p in paths:
                dic_path = os.path.join(p, f'{hunspell_lang_code}.dic')
                aff_path = os.path.join(p, f'{hunspell_lang_code}.aff')
                if os.path.exists(dic_path) and os.path.exists(aff_path):
                    try:
                        self._hobj = hunspell.HunSpell(dic_path, aff_path)
                        print(f"Hunspell initialized with: {dic_path}")
                        found_path = True
                        break
                    except Exception as e: print(f"Warning: Error initializing Hunspell with {dic_path}: {e}")
            if not found_path: print(f"Warning: Hunspell dictionaries for '{hunspell_lang_code}' not found.")

    def _get_hunspell_search_paths(self):
        paths = []
        if sys.platform == "darwin": paths.extend(['/usr/share/hunspell', '/usr/local/share/hunspell', '/opt/homebrew/share/hunspell', os.path.expanduser('~/Library/Spelling')])
        elif sys.platform == "win32":
             program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
             program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
             paths.extend([
                 os.path.join(program_files, "Hunspell", "share", "hunspell"),
                 os.path.join(program_files_x86, "Hunspell", "share", "hunspell"),
             ])
        else: paths.extend(['/usr/share/hunspell', '/usr/local/share/hunspell', '/usr/share/myspell/dicts'])
        try: # Check relative to executable
            exe_dir = os.path.dirname(sys.executable)
            paths.append(os.path.join(exe_dir, 'share', 'hunspell'))
            paths.append(os.path.join(os.path.dirname(exe_dir), 'share', 'hunspell'))
        except Exception: pass
        return paths

    def check(self, text) -> list[SpellCheckResult]:
        if not self.is_available(): return []
        results = []
        lines = text.splitlines()
        for line_idx, line_text in enumerate(lines):
            for match in re.finditer(r"\b[\w'-]+\b", line_text, re.UNICODE):
                 word = match.group(0)
                 start_col = match.start()
                 end_col = match.end()
                 if word.isdigit() or len(word) < 2 or (re.search(r'\d', word) and not word.isalnum()): continue
                 try:
                     if not self._hobj.spell(word):
                         suggestions = [s for s in self._hobj.suggest(word) if s != word]
                         results.append(SpellCheckResult(word, line_idx + 1, start_col, end_col, suggestions[:5]))
                 except Exception as e: print(f"Error checking word '{word}' with Hunspell: {e}")
        return results

    def is_available(self) -> bool: return self._hobj is not None

class AspellChecker(SpellCheckerBase):
    """Spell checker using the aspell command-line tool."""
    def __init__(self, language='en-US'):
        super().__init__(language)
        self._aspell_path = self._find_aspell()

    def _find_aspell(self):
        import shutil
        cmd = shutil.which("aspell")
        if cmd:
            try:
                result = subprocess.run([cmd, 'version'], capture_output=True, check=True, text=True, timeout=2)
                print(f"Found aspell executable: {cmd}. Version: {result.stdout.strip()}")
                return cmd
            except Exception as e: print(f"Warning: Found 'aspell' but failed verification: {e}")
        print("Warning: 'aspell' command not found in PATH.")
        return None

    def is_available(self) -> bool: return self._aspell_path is not None

    def check(self, text) -> list[SpellCheckResult]:
        if not self.is_available(): return []
        results = []
        try:
            aspell_lang_code = self.language.replace('-', '_')
            process = subprocess.Popen(
                [self._aspell_path, '-a', '--lang', aspell_lang_code, '--encoding=utf-8'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore'
            )
            aspell_input = "!\n^" + text.replace("\n", "\n^") # Terse mode, line context
            stdout, stderr = process.communicate(input=aspell_input, timeout=30)
            if stderr and "Assuming encoding" not in stderr: print(f"Aspell stderr: {stderr.strip()}")

            lines = text.splitlines() # Original text lines
            unique_errors = {}
            for line_output in stdout.splitlines():
                if line_output.startswith('&') or line_output.startswith('#'):
                    parts = line_output.split(':')
                    details = parts[0].split()
                    if len(details) < 4: continue # Expect type, word, line, col
                    word, line_num_str, col_num_str = details[1], details[2], details[3]
                    try: line_num, col_num = int(line_num_str), int(col_num_str)
                    except ValueError: continue
                    suggestions = [s.strip() for s in parts[1].split(',')] if line_output.startswith('&') and len(parts) > 1 else []

                    # Find word on the specific line (less reliable than offset but better than nothing)
                    if 0 < line_num <= len(lines):
                         target_line_text = lines[line_num - 1]
                         # Search near the reported column first, then anywhere on line
                         found_pos = -1
                         search_window = 10 # Search around reported column
                         search_start = max(0, col_num - search_window)
                         for match in re.finditer(r'\b' + re.escape(word) + r'\b', target_line_text[search_start:], re.UNICODE):
                             start_col = match.start() + search_start
                             # Prioritize match closest to reported column?
                             found_pos = start_col
                             break # Use first match near column for now
                         # If not found near column, search whole line
                         if found_pos == -1:
                              match = re.search(r'\b' + re.escape(word) + r'\b', target_line_text, re.UNICODE)
                              if match: found_pos = match.start()

                         if found_pos != -1:
                              start_col = found_pos
                              end_col = start_col + len(word)
                              key = (line_num, start_col, word)
                              if key not in unique_errors:
                                  unique_errors[key] = SpellCheckResult(word, line_num, start_col, end_col, suggestions[:5])
                         # else: print(f"Warning: Could not locate '{word}' on line {line_num} from aspell.")
                    # else: print(f"Warning: Aspell reported invalid line {line_num}.")
            results = list(unique_errors.values())
        except FileNotFoundError: print("Error: Aspell not found during check."); self._aspell_path = None
        except subprocess.TimeoutExpired: print("Error: Aspell timed out.")
        except Exception as e: print(f"Error running Aspell: {e}")
        return results

class SpellCheckWorker(QObject):
    """Worker to run spellcheck in a background thread."""
    finished = pyqtSignal(list) # Emits list of SpellCheckResult
    error = pyqtSignal(str)

    def __init__(self, checker: SpellCheckerBase, text: str):
        super().__init__()
        self.checker = checker
        self.text = text

    def run(self):
        try:
            if not self.checker or not self.checker.is_available():
                print(f"Spell checker '{type(self.checker).__name__}' unavailable.")
                self.finished.emit([])
                return
            start_time = time.time()
            print(f"Running spell check ({type(self.checker).__name__})...")
            results = self.checker.check(self.text)
            duration = time.time() - start_time
            print(f"Spell check finished ({duration:.2f}s), found {len(results)} issues.")
            self.finished.emit(results)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.error.emit(f"Spellcheck worker failed: {e}")

class SpellCheckManager(QObject):
    """Manages spell checking operations."""
    checkComplete = pyqtSignal(list)
    checkError = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checker = None
        self._thread = None
        self._worker = None
        self._current_engine_type = ""
        self._current_language = ""
        self.load_checker() # Initialize based on settings
        settings_manager.settingsChanged.connect(self._handle_settings_change)

    def _handle_settings_change(self, key):
        if key in ["spellcheck_engine", "language"]:
            print(f"Reloading spell checker due to setting change: {key}")
            self.load_checker()

    def load_checker(self):
        """Loads the spell checker based on current settings."""
        engine = settings_manager.get("spellcheck_engine")
        language = settings_manager.get("language")
        if engine == self._current_engine_type and language == self._current_language and self._checker and self._checker.is_available():
            return # No change needed

        if self._checker and hasattr(self._checker, 'cleanup'): self._checker.cleanup()

        print(f"Loading spell checker: {engine}, Language: {language}")
        if engine == "languagetool": self._checker = LanguageToolChecker(language)
        elif engine == "hunspell": self._checker = HunspellChecker(language)
        elif engine == "aspell": self._checker = AspellChecker(language)
        else: self._checker = NoSpellChecker(language)

        if not self._checker.is_available() and not isinstance(self._checker, NoSpellChecker):
            print(f"Warning: Selected spellchecker '{engine}' failed to initialize or is unavailable. Falling back to 'none'.")
            self._checker = NoSpellChecker(language) # Fallback if chosen engine fails
            engine = "none" # Update engine type to reflect fallback

        self._current_engine_type = engine
        self._current_language = language
        print(f"Current checker type: {type(self._checker).__name__}")

    def check_text_async(self, text: str):
        """Starts a background thread to check the text."""
        if self._thread is not None and self._thread.isRunning():
            print("Spellcheck already in progress, skipping.")
            return False
        if not self.is_checker_active():
             print(f"Spellcheck engine '{self._current_engine_type}' not active.")
             self.checkComplete.emit([])
             return False

        self._thread = QThread()
        self._worker = SpellCheckWorker(self._checker, text)
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.error.connect(self._on_check_error)
        self._worker.finished.connect(self._cleanup_thread)
        self._worker.error.connect(self._cleanup_thread)
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()
        return True

    def _on_check_finished(self, results): self.checkComplete.emit(results)
    def _on_check_error(self, error_msg): self.checkError.emit(error_msg)

    def _cleanup_thread(self):
         if self._thread is not None:
             if self._thread.isRunning(): self._thread.quit(); self._thread.wait(1000)
             if self._worker: self._worker.deleteLater()
             self._thread.deleteLater()
             self._thread = None; self._worker = None

    def cleanup(self):
         if self._checker and hasattr(self._checker, 'cleanup'): self._checker.cleanup()

    def is_checker_active(self):
        return not isinstance(self._checker, NoSpellChecker) and self._checker.is_available()
