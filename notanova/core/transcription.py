import os
import tempfile
import time
import sys
import platform
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer, QUrl, qWarning, QIODevice
from PyQt6.QtMultimedia import (QMediaRecorder, QAudioInput, QMediaFormat, QMediaDevices,
                                QAudioDevice, QMediaCaptureSession, QAudioSource, QAudioFormat) # Import QAudioFormat
# from PyQt6.QtMultimedia import QAudio # Use QtAudio namespace if needed, e.g. QtAudio.State
from PyQt6.QtWidgets import QApplication # For clipboard access if needed

# Conditional import for Whisper
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("Warning: openai-whisper not found. Transcription features disabled.")
    print("Install using: pip install openai-whisper (requires ffmpeg)")
except Exception as e:
     WHISPER_AVAILABLE = False
     print(f"Warning: Error importing openai-whisper: {e}. Transcription features disabled.")

from core.settings import settings_manager

class AudioRecorder(QObject):
    """Handles audio recording using QtMultimedia QMediaCaptureSession."""
    recordingFinished = pyqtSignal(str) # Emits the path to the recorded file
    error = pyqtSignal(str)
    statusChanged = pyqtSignal(str) # e.g., "Recording", "Idle", "Error: ..."

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture_session = None
        self._audio_input = None # QAudioInput
        self._recorder = None    # QMediaRecorder
        self._output_path = ""
        self._recording = False
        self._available = False # Default to False, set True ONLY on success

        try:
            # 1. Get the selected audio device
            selected_device = self._get_selected_audio_device()
            if selected_device.isNull():
                 raise RuntimeError("No valid audio input device found or selected.")

            # 2. Define the desired *raw* audio format from the input device
            input_format = QAudioFormat()
            input_format.setSampleRate(16000) # Preferred by Whisper
            input_format.setChannelCount(1)   # Mono
            input_format.setSampleFormat(QAudioFormat.SampleFormat.Int16) # Standard PCM 16-bit

            # 3. Check if the device supports the desired *input* format
            if not selected_device.isFormatSupported(input_format):
                qWarning(f"Requested input format (SR={input_format.sampleRate()}, Ch={input_format.channelCount()}, Fmt={input_format.sampleFormat().name}) not directly supported by '{selected_device.description()}'. Trying nearest.")
                nearest_format = selected_device.nearestFormat(input_format)
                if nearest_format.isValid():
                     input_format = nearest_format # Use the nearest supported format for input
                     print(f"Using nearest supported input format: SR={input_format.sampleRate()}, Ch={input_format.channelCount()}, Fmt={input_format.sampleFormat().name}")
                else:
                     # Proceeding without guaranteed support is risky, but let's warn.
                     qWarning(f"Could not find a supported format near the preferred one for device '{selected_device.description()}'. Recording might fail or produce unexpected results.")
                     # Keep the original preferred format, hoping the backend handles it.

            # 4. Construct QAudioInput with Device and Parent
            print(f"Constructing QAudioInput with device '{selected_device.description()}'")
            self._audio_input = QAudioInput(selected_device, self)

            # 5. Create the session
            self._capture_session = QMediaCaptureSession(self)

            # 6. Set the audio input on the session
            self._capture_session.setAudioInput(self._audio_input)

            # 7. Create the recorder
            self._recorder = QMediaRecorder(self)

            # 8. Configure the recorder's *output* format (container/codec)
            self._configure_media_format() # Use existing method

            # Set specific *encoding* parameters matching input format
            self._recorder.setAudioSampleRate(input_format.sampleRate())
            self._recorder.setAudioChannelCount(input_format.channelCount())

            # 9. Set the recorder on the session
            self._capture_session.setRecorder(self._recorder)

            # 10. Check Recorder Availability *After* Configuration
            if not self._recorder.isAvailable():
                 status_str = self._recorder.status().name if hasattr(self._recorder.status(), 'name') else str(self._recorder.status())
                 error_str = self._recorder.errorString()
                 input_error = self._audio_input.errorString() if hasattr(self._audio_input, 'errorString') else "N/A"
                 rec_err_code = self._recorder.error()
                 rec_err_name = rec_err_code.name if hasattr(rec_err_code,'name') else str(rec_err_code)
                 raise RuntimeError(f"QMediaRecorder is not available after setup. Status: {status_str}, ErrorEnum: {rec_err_name}, ErrorStr: {error_str}. Input Error: {input_error}")

            # 11. Connect signals and set available flag
            self._recorder.recorderStateChanged.connect(self._handle_state_changed)
            self._recorder.errorChanged.connect(self._handle_recorder_error) # Handles recorder errors
            if hasattr(self._audio_input, 'errorChanged'): # Handles input device errors
                self._audio_input.errorChanged.connect(self._handle_input_error)
            self._recorder.durationChanged.connect(self._handle_duration_changed)

            self._available = True
            self.statusChanged.emit("Idle")
            print("AudioRecorder initialized successfully using QAudioInput.")

        except Exception as e:
            print(f"Error initializing AudioRecorder: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(f"Failed to initialize audio recorder: {e}")
            self.statusChanged.emit("Error: Recorder Init Failed")
            # Cleanup
            if self._recorder: self._recorder.deleteLater()
            if self._audio_input: self._audio_input.deleteLater()
            if self._capture_session: self._capture_session.deleteLater()
            self._recorder=None; self._audio_input=None; self._capture_session=None
            self._available = False

    def _handle_input_error(self):
        """Handles errors reported by the QAudioInput's errorChanged signal."""
        if self._audio_input and hasattr(self._audio_input, 'errorString'):
             error_string = self._audio_input.errorString()
             if error_string: # Assuming empty string means no error
                 print(f"QAudioInput Error: {error_string}")
                 # Avoid duplicate signals if recorder already failed
                 if self._available:
                    self.error.emit(f"Audio input error: {error_string}")
                    self.statusChanged.emit(f"Error: Input {error_string}")
                 self._recording = False
                 self._cleanup_temp_file(self._output_path)
                 self._output_path = ""
                 self._available = False # Input error makes unavailable

    def _handle_recorder_error(self):
         """Handles errors reported by the QMediaRecorder's errorChanged signal."""
         if self._recorder and self._recorder.error() != QMediaRecorder.Error.NoError:
             code = self._recorder.error()
             code_name = code.name if hasattr(code, 'name') else str(code)
             string = self._recorder.errorString()
             print(f"QMediaRecorder Error {code_name}: {string}")
             if self._available: # Avoid duplicate error messages
                self.error.emit(f"Audio record error: {string}")
                self.statusChanged.emit(f"Error: Recorder {string}")
             self._recording = False
             self._cleanup_temp_file(self._output_path)
             self._output_path = ""
             self._available = False

    def _get_selected_audio_device(self) -> QAudioDevice:
        preferred_desc = settings_manager.get("audio_input_device")
        available = QMediaDevices.audioInputs()
        target_device = QAudioDevice() # Start with null device

        if not available:
            print("Error: No audio input devices found.")
            return target_device # Return null device

        # Try finding preferred device
        if preferred_desc:
             for device in available:
                 if device.description() == preferred_desc:
                     print(f"Using selected audio input: {preferred_desc}")
                     target_device = device
                     break # Found preferred
             if target_device.isNull():
                  print(f"Warning: Preferred audio device '{preferred_desc}' not found. Trying default.")

        # If preferred not found or not set, try default
        if target_device.isNull():
            default = QMediaDevices.defaultAudioInput()
            if not default.isNull():
                is_default_in_list = any(dev == default for dev in available)
                if is_default_in_list:
                     print(f"Using default audio input: {default.description()}")
                     target_device = default
                else:
                     print(f"Warning: Default audio device ({default.description()}) not in available list. Trying first available.")
            else:
                 print("Warning: No default audio input device found. Trying first available.")

        # Fallback to first available device if others failed
        if target_device.isNull() and available:
            print(f"Warning: Using first available audio input: {available[0].description()}")
            target_device = available[0]

        return target_device

    def _configure_media_format(self):
        if not self._recorder: return

        # Prefer FLAC for raw quality, MP3 though is better, Wave as fallback
        preferred_formats = [
            (QMediaFormat.FileFormat.MP3, QMediaFormat.AudioCodec.MP3),
            (QMediaFormat.FileFormat.Wave, QMediaFormat.AudioCodec.FLAC),
            (QMediaFormat.FileFormat.Wave, QMediaFormat.AudioCodec.Wave),
        ]
        mf = QMediaFormat()
        suppported_codecs = QMediaFormat().supportedAudioCodecs(mf.ConversionMode.Encode)
        format_set = False

        for container, codec in preferred_formats:
            if container in suppported_codecs:
                mf.setFileFormat(container)
                supported_codecs = self._recorder.supportedAudioCodecs(mf.fileFormat())
                if codec in supported_codecs:
                    mf.setAudioCodec(codec)
                    print(f"Recorder using output container: {mf.fileFormat().name}, Codec: {mf.audioCodec().name}")
                    format_set = True
                    break
                else:
                    # Try container with default codec if specific one fails
                    mf = QMediaFormat(); mf.setFileFormat(container)
                    # Check if recorder is usable with just container (might choose default codec)
                    temp_recorder = QMediaRecorder() # Use temporary to check availability
                    temp_recorder.setMediaFormat(mf)
                    if temp_recorder.isAvailable():
                         print(f"Recorder using output container: {mf.fileFormat().name}, Codec: Default")
                         format_set = True
                         temp_recorder.deleteLater()
                         break
                    else:
                         mf = QMediaFormat() # Reset if container with default codec is also unavailable
                         temp_recorder.deleteLater()


        if not format_set:
            print(f"Warning: Preferred formats (WAV/PCM, MP3/MP3) not supported. Recorder will use default format.")
            mf = QMediaFormat() # Use default

        self._recorder.setMediaFormat(mf)

    def is_available(self):
        return self._available and self._recorder is not None and self._recorder.isAvailable()

    def _get_temp_filename(self):
        ext=".wav"; # Default/Preferred
        if self._recorder:
            ff = self._recorder.mediaFormat().fileFormat()
            ext_map = {
                 QMediaFormat.FileFormat.Wave: ".wav",
                 QMediaFormat.FileFormat.MP3: ".mp3",
                 QMediaFormat.FileFormat.Ogg: ".ogg",
            }
            ext = ext_map.get(ff, ".audio")

        try: fd, path = tempfile.mkstemp(suffix=ext, prefix="notanova_rec_"); os.close(fd); return path
        except Exception as e: print(f"Error creating temp file: {e}"); self.error.emit("Failed temp file."); return None

    def start_recording(self):
        if not self.is_available():
            self.error.emit("Audio recorder is not available.")
            self.statusChanged.emit("Error: Recorder N/A")
            return False
        if self._recording:
            print("Already recording.")
            return True
        current_state = self._recorder.recorderState()
        if current_state != QMediaRecorder.RecorderState.StoppedState:
            state_name = current_state.name if hasattr(current_state, 'name') else str(current_state)
            print(f"Recorder not stopped (state={state_name}). Cannot start.")
            self.error.emit(f"Cannot start recording, recorder state is {state_name}")
            self.statusChanged.emit("Error: Recorder busy")
            return False

        self._output_path = self._get_temp_filename()
        if not self._output_path: return False

        self._recorder.setOutputLocation(QUrl.fromLocalFile(self._output_path))
        print(f"Starting recording to: {self._output_path}")
        self._recorder.record()
        # Check for immediate errors after calling record
        QTimer.singleShot(100, self._check_recorder_status_after_start)
        return True

    def _check_recorder_status_after_start(self):
         """Checks recorder status shortly after starting."""
         if self._recorder and self._recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
              if self._recorder.error() != QMediaRecorder.Error.NoError:
                   self._handle_recorder_error() # Trigger error handling if stopped with error
              else:
                   # Stopped immediately without error? Might indicate config issue.
                   print("Warning: Recorder stopped immediately after starting without explicit error.")
                   self.error.emit("Recording failed to start.")
                   self.statusChanged.emit("Error: Record Start Failed")
                   self._recording = False
                   self._cleanup_temp_file(self._output_path)
                   self._output_path = ""


    def stop_recording(self):
        if not self.is_available():
             print("Cannot stop: Recorder is not available.")
             return False
        if self._recorder is None:
             print("Cannot stop: Recorder object is None.")
             return False

        current_state = self._recorder.recorderState()
        if current_state != QMediaRecorder.RecorderState.RecordingState:
            state_name = current_state.name if hasattr(current_state, 'name') else str(current_state)
            print(f"Not recording (state={state_name}). Cannot stop.")
            if current_state == QMediaRecorder.RecorderState.StoppedState:
                 self.statusChanged.emit("Idle")
            return False

        print("Stopping recording...")
        self._recorder.stop()
        return True

    def is_recording(self):
        return self._recording and self._recorder is not None and self._recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState

    def _handle_state_changed(self, state: QMediaRecorder.RecorderState):
        state_name = state.name if hasattr(state, 'name') else str(state)
        print(f"Recorder state changed: {state_name}")
        if state == QMediaRecorder.RecorderState.RecordingState:
            if not self._recording:
                self._recording = True
                self.statusChanged.emit("Recording... 0.0s")
        elif state == QMediaRecorder.RecorderState.PausedState:
             self._recording = False
             self.statusChanged.emit("Paused")
        elif state == QMediaRecorder.RecorderState.StoppedState:
            was_recording = self._recording
            self._recording = False
            recorder_error = self._recorder.error() if self._recorder else QMediaRecorder.Error.ResourceError
            if was_recording and recorder_error == QMediaRecorder.Error.NoError:
                 print(f"Recording stopped signal received. File: {self._output_path}")
                 QTimer.singleShot(250, self._check_output_file_and_finish)
            else:
                 error_name = recorder_error.name if hasattr(recorder_error,'name') else str(recorder_error)
                 print(f"Stopped state reached, but was_recording={was_recording} or error={error_name}. Not processing file.")
                 self.statusChanged.emit("Idle")
                 self._cleanup_temp_file(self._output_path)
                 self._output_path = ""

    def _check_output_file_and_finish(self):
        path = self._output_path
        self._output_path = "" # Clear path immediately

        if path and os.path.exists(path):
            try:
                file_size = os.path.getsize(path)
                if file_size > 100: # Basic check for non-empty file
                    print(f"Recorded file OK: {path} ({file_size} bytes)")
                    self.recordingFinished.emit(path)
                    # Don't clean up here, let TranscriptionWorker handle it
                else:
                    errmsg = f"Recorded file is too small or empty: {os.path.basename(path)} ({file_size} bytes)"
                    print(f"Error: {errmsg}")
                    self.error.emit(errmsg)
                    self.statusChanged.emit("Error: Rec file empty")
                    self._cleanup_temp_file(path) # Clean up empty/small file
            except OSError as e:
                 errmsg = f"Error accessing recorded file '{os.path.basename(path)}': {e}"
                 print(errmsg)
                 self.error.emit(errmsg)
                 self.statusChanged.emit("Error: File Access")
                 self._cleanup_temp_file(path) # Clean up inaccessible file
        elif path:
             errmsg = f"Recording output file not found after stop: {os.path.basename(path)}"
             print(f"Error: {errmsg}")
             self.error.emit(errmsg)
             self.statusChanged.emit("Error: Rec file missing")

        # Let TranscriptionManager handle the final "Idle" status

    def _handle_duration_changed(self, duration: int):
        if self.is_recording():
            self.statusChanged.emit(f"Recording... {duration / 1000.0:.1f}s")

    def _cleanup_temp_file(self, path):
        if path and os.path.exists(path) and "notanova_rec_" in os.path.basename(path):
            try:
                os.remove(path)
                print(f"Cleaned temp audio file: {path}")
            except OSError as e:
                print(f"Error removing temp audio file {path}: {e}")

    def __del__(self):
        self._cleanup_temp_file(self._output_path)


# --- TranscriptionWorker Class ---
class TranscriptionWorker(QObject):
    """Worker to run Whisper transcription in a background thread."""
    finished = pyqtSignal(str) # Emits transcribed text
    error = pyqtSignal(str)
    progress = pyqtSignal(str) # Emits status updates

    def __init__(self, audio_path: str, model_version: str):
        super().__init__()
        self.audio_path = audio_path
        self.model_version = model_version
        self.model = None
        self._cancelled = False # Cancellation flag

    def _is_cancelled(self):
        # Check cancellation flag and potentially thread interruption request
        if self._cancelled or QThread.currentThread().isInterruptionRequested():
            print("Transcription Worker: Cancellation detected.")
            return True
        return False

    def cancel(self):
        """Sets the cancellation flag."""
        print("Transcription Worker: Received cancel request.")
        self._cancelled = True

    def run(self):
        try:
            if not WHISPER_AVAILABLE: raise RuntimeError("openai-whisper library not available.")
            if not self.audio_path or not os.path.exists(self.audio_path): raise FileNotFoundError(f"Audio file not found: {self.audio_path}")
            if self._is_cancelled(): return

            # --- Emit pre-load status ---
            self.progress.emit(f"🎤 Loading/Downloading Whisper '{self.model_version}'...")
            print(f"Loading Whisper model: {self.model_version}")
            start_load = time.time()
            try:
                device = "cpu" # Stick to CPU for GUI stability
                self.model = whisper.load_model(self.model_version, device=device)
                print(f"Whisper model loaded ({device}) in {time.time() - start_load:.2f}s.")
            except Exception as e: raise RuntimeError(f"Failed loading Whisper model '{self.model_version}': {e}")

            if self._is_cancelled(): return

            # --- Emit transcribing status ---
            self.progress.emit("🎤 Transcribing audio...")
            print(f"Starting transcription of: {self.audio_path}")
            start_transcribe = time.time()
            # TODO: Whisper doesn't have built-in cancellation during transcribe()
            result = self.model.transcribe(self.audio_path, fp16=False) # fp16=False for CPU
            txt = result.get('text', '')
            print(f"Transcription finished in {time.time() - start_transcribe:.2f}s.")

            if self._is_cancelled(): return # Check again after transcription finishes

            self.finished.emit(txt.strip())

        except Exception as e:
            if not self._cancelled: # Don't report error if cancelled
                 import traceback; traceback.print_exc()
                 self.error.emit(f"Transcription failed: {e}")
        finally:
            # Cleanup the temporary audio file *always* after transcription attempt
            if self.audio_path and os.path.exists(self.audio_path) and "notanova_rec_" in os.path.basename(self.audio_path):
                try:
                    os.remove(self.audio_path)
                    print(f"Removed temp audio file after transcription: {self.audio_path}")
                except OSError as e:
                    print(f"Error removing temp audio file {self.audio_path} after transcription: {e}")


# --- TranscriptionManager Class ---
class TranscriptionManager(QObject):
    """Manages audio recording and transcription."""
    transcriptionComplete = pyqtSignal(str)
    transcriptionError = pyqtSignal(str)
    statusUpdate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recorder = AudioRecorder(self) # Initialize recorder first
        self._thread = None
        self._worker = None
        self._is_available = False # Combined availability

        # Set availability based on recorder AND whisper init status
        self._is_available = self._recorder.is_available() and WHISPER_AVAILABLE
        if self._is_available:
             self._recorder.recordingFinished.connect(self._start_transcription)
             self._recorder.error.connect(self._handle_recorder_error)
             self._recorder.statusChanged.connect(self.statusUpdate)
             print("Transcription Manager initialized (Recorder & Whisper OK).")
             self.statusUpdate.emit("Idle") # Set initial status only if fully available
        else:
            if not self._recorder.is_available():
                print("Transcription Manager: Audio recorder initialization failed or unavailable.")
            if not WHISPER_AVAILABLE:
                print("Transcription Manager: Whisper library not found or failed to import.")
            self.statusUpdate.emit("Error: Transcription unavailable") # Explicitly set error state

    def start_recording(self) -> bool:
        if not self.is_available():
            self.transcriptionError.emit("Recorder or Whisper is unavailable.")
            self.statusUpdate.emit("Error: Rec N/A")
            return False
        if self.is_busy():
            self.transcriptionError.emit("Cannot start recording: Already recording or transcribing.")
            return False
        return self._recorder.start_recording()

    def stop_recording_and_transcribe(self) -> bool:
        if not self.is_available(): return False
        if not self._recorder.is_recording():
             print("Stop requested, but not currently recording.")
             return False
        success = self._recorder.stop_recording()
        # Transcription starts via the recordingFinished signal if stop was successful
        return success

    def cancel_current_task(self):
        """Requests cancellation of the current recording or transcription."""
        if self.is_recording():
             print("Requesting recording cancellation...")
             # Stop recording immediately, don't trigger transcription
             try: # Disconnect might fail if already disconnected
                 self._recorder.recordingFinished.disconnect(self._start_transcription)
             except TypeError: pass # Ignore if not connected
             self._recorder.stop_recording()
             self._recorder._cleanup_temp_file(self._recorder._output_path) # Clean up partial file
             self._recorder._output_path = ""
             self.statusUpdate.emit("Recording Cancelled")
             QTimer.singleShot(100, lambda: self.statusUpdate.emit("Idle"))
             # Reconnect for future use
             QTimer.singleShot(50, lambda: self._recorder.recordingFinished.connect(self._start_transcription))
        elif self.is_transcribing() and self._worker:
             print("Requesting transcription cancellation...")
             self._worker.cancel()
             # Interrupt thread might help if whisper blocks
             if self._thread and self._thread.isRunning():
                  self._thread.requestInterruption()
             self.statusUpdate.emit("Transcription Cancellation Requested...")
             # Cleanup will happen via signals, status reset in cleanup
        else:
             print("No active recording or transcription task to cancel.")


    def _start_transcription(self, audio_path: str):
        if not self.is_available():
            if not self._recorder.is_available(): self.statusUpdate.emit("Error: Rec Failed")
            else: self.statusUpdate.emit("Error: Whisper N/A")
            self._cleanup_temp_file(audio_path) # Clean up file if we can't transcribe
            return

        model_version = settings_manager.get("whisper_model_version")
        if not model_version:
            self.transcriptionError.emit("Whisper model version not configured.")
            self.statusUpdate.emit("Error: Whisper N/C")
            self._cleanup_temp_file(audio_path)
            return
        if self.is_transcribing():
            self.transcriptionError.emit("Transcription already in progress.")
            # Don't cleanup file, maybe user wants it later? Or maybe do? Let's cleanup.
            self._cleanup_temp_file(audio_path)
            return

        print(f"Starting transcription for: {audio_path}")
        # Status update for loading/downloading happens inside the worker now

        self._thread = QThread()
        self._worker = TranscriptionWorker(audio_path, model_version)
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(self._on_transcription_finished)
        self._worker.error.connect(self._on_transcription_error)
        self._worker.progress.connect(self.statusUpdate) # Pass progress through
        self._worker.finished.connect(self._cleanup_thread)
        self._worker.error.connect(self._cleanup_thread) # Cleanup on error too
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_transcription_finished(self, text: str):
        self.transcriptionComplete.emit(text)
        self.statusUpdate.emit("Transcription Complete")
        # Status reset to Idle happens in _cleanup_thread

    def _on_transcription_error(self, msg: str):
        # Check if the error is due to cancellation
        if self._worker and self._worker._cancelled:
             print("Transcription task cancelled, error signal suppressed.")
             self.statusUpdate.emit("Transcription Cancelled")
        else:
            print(f"Transcription Error: {msg}")
            self.transcriptionError.emit(msg)
            self.statusUpdate.emit(f"Error: Transcription Failed") # Keep status simple
        # Status reset to Idle happens in _cleanup_thread

    def _handle_recorder_error(self, msg: str):
        print(f"Recorder Error reported to Manager: {msg}")
        self.statusUpdate.emit(f"Error: {msg}") # Update status
        self._is_available = False # Recorder error makes transcription unavailable

    def _cleanup_thread(self):
         """Safely cleans up the worker thread and object."""
         if self._thread is None and self._worker is None: return # Already cleaned up
         print("Cleaning up transcription worker thread...")
         worker = self._worker; thread = self._thread
         self._worker = None; self._thread = None # Clear references first

         if worker is not None: worker.deleteLater()
         if thread is not None:
             if thread.isRunning():
                 thread.quit();
                 if not thread.wait(1000):
                      print("Warning: Transcription thread didn't quit gracefully. Terminating.")
                      thread.terminate(); thread.wait(500)
             thread.deleteLater()

         print("Transcription worker thread cleaned up.")
         # Reset status after cleanup is complete, unless recording restarted somehow
         QTimer.singleShot(50, lambda: self.statusUpdate.emit("Idle") if not self.is_busy() else None)

    def is_recording(self) -> bool:
        return self._recorder is not None and self._recorder.is_recording()

    def is_transcribing(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def is_busy(self) -> bool:
        return self.is_recording() or self.is_transcribing()

    def is_available(self) -> bool:
        if self._is_available and self._recorder is not None and not self._recorder.is_available():
             print("Recorder became unavailable after initialization.")
             self._is_available = False # Update state if recorder failed
        return self._is_available

    def get_available_audio_inputs(self) -> list[str]:
        return [d.description() for d in QMediaDevices.audioInputs()]

    # Helper to cleanup temp file, used by recorder and worker
    def _cleanup_temp_file(self, path):
        if path and os.path.exists(path) and "notanova_rec_" in os.path.basename(path):
            try:
                os.remove(path)
                print(f"Cleaned temp audio file: {path}")
            except OSError as e:
                print(f"Error removing temp audio file {path}: {e}")

