import os
import sys
import gc
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer # <-- Added QTimer
from core.settings import settings_manager

# Conditional import for llama.cpp
try:
    from llama_cpp import Llama
    LLAMA_CPP_AVAILABLE = True
except ImportError:
    LLAMA_CPP_AVAILABLE = False
    print("Warning: llama-cpp-python not found. Local LLM features disabled.")
except Exception as e:
    LLAMA_CPP_AVAILABLE = False
    print(f"Warning: Error importing llama-cpp-python: {e}. Local LLM features disabled.")

# --- Base Worker with Cancellation ---
class LLMBaseWorker(QObject):
    finished = pyqtSignal(str) # Emits result text
    error = pyqtSignal(str)    # Emits error message
    progress = pyqtSignal(str) # Emits status updates

    def __init__(self, model_path: str):
        super().__init__()
        self.model_path = model_path
        self.llm = None
        self._cancelled = False # Cancellation flag

    def _is_cancelled(self):
        # Check cancellation flag and potentially thread interruption request
        if self._cancelled or QThread.currentThread().isInterruptionRequested():
            print(f"{self.__class__.__name__}: Cancellation detected.")
            self._cleanup()
            return True
        return False

    def cancel(self):
        """Sets the cancellation flag."""
        print(f"{self.__class__.__name__}: Received cancel request.")
        self._cancelled = True
        # Note: Actual stopping depends on checks within run()

    def _cleanup(self):
         """Release LLM resources."""
         if hasattr(self, 'llm') and self.llm:
             # Explicitly delete and collect garbage to free GPU memory if used
             del self.llm
             self.llm = None
             gc.collect()
             print(f"{self.__class__.__name__}: LLM resources released.")

    def _load_llm(self):
        """Loads the LLM model, checking for cancellation."""
        if not LLAMA_CPP_AVAILABLE: raise RuntimeError("llama-cpp-python library not available.")
        if not self.model_path or not os.path.exists(self.model_path): raise FileNotFoundError(f"LLM model not found: {self.model_path}")

        self.progress.emit(f"🧠 Loading LLM: {os.path.basename(self.model_path)}...")
        if self._is_cancelled(): return False

        n_gpu_layers = -1 if sys.platform == "darwin" else 0 # Simple platform check
        try:
            self.llm = Llama(model_path=self.model_path, n_ctx=4096, n_gpu_layers=n_gpu_layers, verbose=False)
            print("LLM model loaded.")
            return True
        except Exception as e:
             raise RuntimeError(f"Failed to load LLM model: {e}")


# --- Worker for Grammar/Style Fixing ---
class LLMFixWorker(LLMBaseWorker):
    def __init__(self, text: str, model_path: str):
        super().__init__(model_path)
        self.text = text

    def run(self):
        try:
            if not self._load_llm(): return # Load model, checks cancellation
            if self._is_cancelled(): return

            self.progress.emit("🧠 Generating corrected text...")

            prompt = f"""[INST] You are an expert technical editor specializing in Markdown. Correct grammar, spelling, clarity, and style in the following text. Preserve ALL original Markdown formatting (headings, lists, bold, italic, code, links, images, tables, etc.) exactly. Output ONLY the fully corrected Markdown text without explanations.

[Original Markdown Text]
{self.text}
[/Original Markdown Text]

[Corrected Markdown Text]
[/INST]
"""
            max_tokens = int(len(self.text)*1.5)+1024
            # TODO: Implement streaming or check cancellation during generation if possible
            output = self.llm(prompt, max_tokens=max_tokens, stop=["[INST]","[Original","[Corrected","\n\nUser:"], temperature=0.6, top_p=0.9, echo=False)

            if self._is_cancelled(): return

            corrected_text = output['choices'][0].get('text', '').strip() if output and 'choices' in output and output['choices'] else ""
            prefixes = ["[Corrected Markdown Text]", "Corrected Markdown Text:", "```markdown", "```"]
            for p in prefixes:
                if corrected_text.startswith(p): corrected_text = corrected_text[len(p):].lstrip(); break
            if corrected_text.endswith("```"): corrected_text = corrected_text[:-3].rstrip()
            if not corrected_text: print("Warning: LLM fix returned empty."); corrected_text = self.text

            self.finished.emit(corrected_text)
        except Exception as e:
            if not self._cancelled: # Don't report error if cancelled
                 import traceback; traceback.print_exc(); self.error.emit(f"LLM fix failed: {e}")
        finally:
            self._cleanup()


# --- Worker for Generic AI Instructions ---
class LLMInstructWorker(LLMBaseWorker):
    def __init__(self, selected_text: str, instruction: str, model_path: str):
        super().__init__(model_path)
        self.selected_text = selected_text
        self.instruction = instruction

    def run(self):
        try:
            if not self._load_llm(): return # Load model, checks cancellation
            if self._is_cancelled(): return

            self.progress.emit(f"🧠 Applying instruction: {self.instruction[:30]}...")

            prompt = f"""[INST] Apply the following instruction to the provided Markdown text. Preserve Markdown formatting where appropriate unless the instruction specifically modifies it (e.g., 'convert to list'). Output ONLY the modified text.

[Instruction]
{self.instruction}
[/Instruction]

[Original Text]
{self.selected_text}
[/Original Text]

[Modified Text]
[/INST]
"""
            max_tokens=int(len(self.selected_text)*2)+1024
            # TODO: Implement streaming or check cancellation during generation if possible
            output = self.llm(prompt, max_tokens=max_tokens, stop=["[INST]","[Instruction]","[Original Text]","[Modified Text]","\n\nUser:"], temperature=0.7, top_p=0.9, echo=False)

            if self._is_cancelled(): return

            result_text = output['choices'][0].get('text', '').strip() if output and 'choices' in output and output['choices'] else ""
            prefixes = ["[Modified Text]", "Modified Text:", "```markdown", "```"]
            for p in prefixes:
                if result_text.startswith(p): result_text = result_text[len(p):].lstrip(); break
            if result_text.endswith("```"): result_text = result_text[:-3].rstrip()
            if not result_text: print("Warning: LLM instruction returned empty."); result_text = self.selected_text # Fallback

            self.finished.emit(result_text)
        except Exception as e:
            if not self._cancelled: # Don't report error if cancelled
                 import traceback; traceback.print_exc(); self.error.emit(f"LLM instruction failed: {e}")
        finally:
            self._cleanup()


# --- LLM Manager ---
class LLMManager(QObject):
    fixComplete = pyqtSignal(str); fixError = pyqtSignal(str)
    instructionComplete = pyqtSignal(str); instructionError = pyqtSignal(str)
    statusUpdate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None # Holds the active worker instance

    def _start_worker(self, worker_class, *args):
        if self._thread is not None and self._thread.isRunning():
            return False, "LLM operation already in progress."
        if not self.is_available():
             return False, "LLM not available or configured."
        model_path = settings_manager.get("llm_model_path")

        self._thread = QThread()
        self._worker = worker_class(*args, model_path=model_path) # Create worker instance
        self._worker.moveToThread(self._thread)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(self.statusUpdate)
        self._worker.finished.connect(self._on_finished)
        # Connect cleanup signals
        self._worker.finished.connect(self._cleanup_thread)
        self._worker.error.connect(self._cleanup_thread) # Cleanup on error too
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._cleanup_thread) # Cleanup if thread finishes unexpectedly
        self.statusUpdate.emit("Starting LLM...")
        self._thread.start()
        return True, ""

    def fix_text_async(self, text: str):
        success, msg = self._start_worker(LLMFixWorker, text)
        if not success: self.fixError.emit(msg)
        return success

    def instruct_ai_async(self, selected_text: str, instruction: str):
        success, msg = self._start_worker(LLMInstructWorker, selected_text, instruction)
        if not success: self.instructionError.emit(msg)
        return success

    def cancel_current_task(self):
        """Requests cancellation of the currently running LLM task."""
        if self._worker and hasattr(self._worker, 'cancel'):
            print("Requesting LLM task cancellation...")
            self._worker.cancel()
            # Optionally request thread interruption for faster exit in some cases
            if self._thread and self._thread.isRunning():
                 self._thread.requestInterruption()
            self.statusUpdate.emit("🧠 Cancellation Requested...")
        else:
             print("No active LLM task to cancel.")

    def _on_finished(self, result_text):
        if isinstance(self._worker, LLMFixWorker): self.fixComplete.emit(result_text)
        elif isinstance(self._worker, LLMInstructWorker): self.instructionComplete.emit(result_text)
        # Don't emit Idle status here, let cleanup handle final status

    def _on_error(self, error_msg):
        # Check if the error is due to cancellation before emitting
        if self._worker and self._worker._cancelled:
             print("LLM task cancelled, error signal suppressed.")
             self.statusUpdate.emit("🧠 LLM Task Cancelled")
        else:
            if isinstance(self._worker, LLMFixWorker): self.fixError.emit(error_msg)
            elif isinstance(self._worker, LLMInstructWorker): self.instructionError.emit(error_msg)
            self.statusUpdate.emit(f"🧠 LLM Error") # Keep status simple on error

    def _cleanup_thread(self):
         """Safely cleans up the worker thread and object."""
         if self._thread is None and self._worker is None: return # Already cleaned up

         print("Cleaning up LLM worker thread...")
         worker = self._worker; thread = self._thread
         self._worker = None; self._thread = None # Clear references first

         if worker is not None:
             # Ensure worker cleanup runs if needed (e.g., release model explicitly)
             if hasattr(worker, '_cleanup') and callable(worker._cleanup):
                  try: worker._cleanup()
                  except Exception as e: print(f"Error during worker cleanup: {e}")
             worker.deleteLater()

         if thread is not None:
             if thread.isRunning():
                 thread.quit();
                 if not thread.wait(1000): # Wait up to 1s
                      print("Warning: LLM thread didn't quit gracefully. Terminating.")
                      thread.terminate(); thread.wait(500)
             thread.deleteLater()

         print("LLM worker thread cleaned up.")
         # Reset status after cleanup is complete
         QTimer.singleShot(0, lambda: self.statusUpdate.emit("🧠 LLM Idle"))


    def is_available(self) -> bool:
        model_path = settings_manager.get("llm_model_path")
        return bool(LLAMA_CPP_AVAILABLE and model_path and os.path.exists(model_path))
