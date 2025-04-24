import os
import pickle # More robust for storing Google's Credentials object
import json
import io
import time # For backup file naming
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer
from PyQt6.QtWidgets import QMessageBox, QApplication

# Import Google libraries conditionally to avoid hard dependency if not used
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    print("Warning: Google API libraries not found. Cloud sync disabled.")
    print("Install them using: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")


from core.settings import settings_manager

# If modifying these SCOPES, delete the token file.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

class NoteGdriveMapper:
    """Manages the mapping between local note IDs and Google Drive file IDs."""
    def __init__(self, map_file_path):
        self.map_file_path = map_file_path
        self.mapping = self._load_mapping() # { local_item_id: gdrive_file_id }

    def _load_mapping(self):
        if not os.path.exists(self.map_file_path): return {}
        try:
            with open(self.map_file_path, 'r', encoding='utf-8') as f:
                content = f.read(); return json.loads(content) if content else {}
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading GDrive map file {self.map_file_path}: {e}")
            backup_path = self.map_file_path + f".corrupted.{int(time.time())}"
            try:
                 if os.path.exists(self.map_file_path): os.rename(self.map_file_path, backup_path)
            except OSError: pass
            return {}

    def save_mapping(self):
        try:
            os.makedirs(os.path.dirname(self.map_file_path), exist_ok=True)
            temp_path = self.map_file_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f: json.dump(self.mapping, f, indent=2)
            os.replace(temp_path, self.map_file_path)
        except (IOError, OSError) as e: print(f"Error saving GDrive map file {self.map_file_path}: {e}")

    def get_gdrive_id(self, local_item_id): return self.mapping.get(str(local_item_id))
    def update_mapping(self, local_item_id, gdrive_file_id):
        if local_item_id and gdrive_file_id: self.mapping[str(local_item_id)] = gdrive_file_id; self.save_mapping()
        else: print(f"Warning: Invalid mapping update: local={local_item_id}, gdrive={gdrive_file_id}")
    def remove_mapping(self, local_item_id):
        local_id_str = str(local_item_id)
        if local_id_str in self.mapping: del self.mapping[local_id_str]; self.save_mapping(); print(f"Removed GDrive map: {local_id_str}")

# Initialize mapper globally
gdrive_mapper = NoteGdriveMapper(settings_manager.get("gdrive_note_map_file"))


class CloudSyncWorker(QObject):
    """Worker object for performing cloud operations in a separate thread."""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    success = pyqtSignal(object) # Can carry results like file list or content
    authentication_required = pyqtSignal()

    def __init__(self, operation, *args, **kwargs):
        super().__init__(); self.operation = operation; self.args = args; self.kwargs = kwargs
        self._service = None; self._credentials = None

    def _get_credentials(self):
        """Gets valid user credentials from storage or initiates flow."""
        creds = None; token_path = settings_manager.get("google_credentials_path"); secret_path = settings_manager.get("google_client_secret_path")
        if not secret_path or not os.path.exists(secret_path): raise ValueError("Client Secret not configured/found.")
        if os.path.exists(token_path):
            try:
                with open(token_path, 'rb') as token: creds = pickle.load(token)
                print(f"Loaded credentials: {token_path}")
            except Exception as e: print(f"Error loading token: {e}. Re-auth needed."); creds = None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("Credentials expired, refreshing...");
                try:
                    creds.refresh(Request()); print("Credentials refreshed.")
                    os.makedirs(os.path.dirname(token_path), exist_ok=True)
                    with open(token_path, 'wb') as token: pickle.dump(creds, token)
                except Exception as e: print(f"Error refreshing token: {e}. Re-auth needed."); self.authentication_required.emit(); return None
            else: print("No valid credentials. Authentication required."); self.authentication_required.emit(); return None
        else: print("Using valid existing credentials.")
        return creds

    def _get_service(self):
        if not GOOGLE_LIBS_AVAILABLE: raise RuntimeError("Google libraries not installed.")
        if self._service is None:
            self._credentials = self._get_credentials()
            if self._credentials is None: return None # Auth needed
            try: self._service = build('drive', 'v3', credentials=self._credentials, cache_discovery=False); print("Drive service built.")
            except HttpError as err: raise ConnectionError(f"Build service error: {err}")
            except Exception as e: raise ConnectionError(f"Unexpected build error: {e}")
        return self._service

    def run(self):
        """Execute the requested cloud operation."""
        try:
            service = self._get_service()
            if service is None: self.finished.emit(); return # Auth signal already sent

            if self.operation == 'list_files': self._list_files(service)
            elif self.operation == 'upload_file': local_id = self.kwargs.get('local_item_id'); self._upload_file(service, *self.args, local_item_id=local_id)
            elif self.operation == 'download_file': self._download_file(service, *self.args)
            else: raise ValueError(f"Unknown operation: {self.operation}")
        except (ValueError, ConnectionError, FileNotFoundError) as e: self.error.emit(str(e))
        except HttpError as err:
            reason = f"API Error ({err.resp.status})"; msg = err.reason
            try: content=json.loads(err.content.decode()); msg=content.get('error',{}).get('message',err.reason)
            except: pass; reason = f"API Error ({err.resp.status}): {msg}"
            if err.resp.status in [401, 403]:
                token_path = settings_manager.get("google_credentials_path")
                if token_path and os.path.exists(token_path):
                    try: os.remove(token_path); print("Removed invalid token file.")
                    except OSError as e: print(f"Warn: Error removing token file {token_path}: {e}")
                self.authentication_required.emit(); self.error.emit(f"Auth error. Re-authenticate. Details: {reason}")
            else: self.error.emit(reason)
        except Exception as e: import traceback; traceback.print_exc(); self.error.emit(f"Unexpected cloud error: {e}")
        finally: self.finished.emit()

    def _list_files(self, service):
        query = "(mimeType='text/markdown' or mimeType='text/plain') and 'me' in owners and trashed=false"
        results = service.files().list(pageSize=50, q=query, orderBy='modifiedByMeTime desc', fields="files(id,name,modifiedTime,mimeType)").execute()
        self.success.emit(results.get('files', []))

    def _upload_file(self, service, local_path, gdrive_file_id=None, mime_type='text/markdown', local_item_id=None):
        if not os.path.exists(local_path): raise FileNotFoundError(f"Local file not found: {local_path}")
        fname = os.path.basename(local_path); meta = {'name': fname}
        if not mime_type: mime_type = 'text/markdown' if fname.lower().endswith(".md") else 'text/plain'
        media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True); file = None
        try:
            if gdrive_file_id: print(f"Updating GDrive {gdrive_file_id}"); file = service.files().update(fileId=gdrive_file_id, media_body=media, fields='id,name').execute()
            else: print(f"Creating GDrive {meta['name']}"); meta['mimeType']=mime_type; file = service.files().create(body=meta, media_body=media, fields='id,name').execute()
        except HttpError as err:
             if err.resp.status == 404 and gdrive_file_id:
                 print(f"GDrive 404 on update {gdrive_file_id}, creating new."); meta['mimeType']=mime_type
                 if local_item_id: gdrive_mapper.remove_mapping(local_item_id)
                 file = service.files().create(body=meta, media_body=media, fields='id,name').execute()
             else: raise
        if file: self.success.emit({"gdrive_file": file, "local_item_id": local_item_id})
        else: raise RuntimeError("Upload failed: no file object.")

    def _download_file(self, service, file_id, local_path):
        print(f"Downloading GDrive {file_id} to {local_path}"); request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO(); downloader = MediaIoBaseDownload(fh, request, chunksize=1024*1024); done = False
        while not done: status, done = downloader.next_chunk(num_retries=3); # print(f"DL {int(status.progress()*100)}%")
        os.makedirs(os.path.dirname(local_path), exist_ok=True);
        with open(local_path, 'wb') as f: f.write(fh.getvalue())
        print(f"Downloaded to: {local_path}"); self.success.emit({"local_path": local_path, "gdrive_id": file_id})

class GoogleDriveSync(QObject):
    """Main class to manage Google Drive interactions."""
    authenticationComplete = pyqtSignal(bool); syncError = pyqtSignal(str); listFilesComplete = pyqtSignal(list)
    uploadComplete = pyqtSignal(str, str); downloadComplete = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent); self.parent_widget = parent; self._thread = None; self._worker = None
        self._auth_timer = QTimer(self); self._auth_timer.setSingleShot(True); self._auth_timer.timeout.connect(self.initiate_authentication_flow)

    def _start_worker(self, operation, *args, **kwargs):
        if not GOOGLE_LIBS_AVAILABLE: self.syncError.emit("Google Cloud libraries not installed."); return False
        if self._thread is not None and self._thread.isRunning(): self.syncError.emit("Cloud operation already in progress."); return False
        self._thread = QThread(); self._worker = CloudSyncWorker(operation, *args, **kwargs)
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(self._on_worker_finished); self._worker.error.connect(self._on_worker_error)
        self._worker.success.connect(self._on_worker_success); self._worker.authentication_required.connect(self.request_authentication)
        self._thread.started.connect(self._worker.run); self._thread.finished.connect(self._thread.deleteLater)
        self._worker.finished.connect(self._worker.deleteLater); self._thread.start(); return True

    def _on_worker_finished(self): print("Cloud worker finished."); self._thread = None; self._worker = None
    def _on_worker_error(self, msg): print(f"Cloud worker error: {msg}"); self.syncError.emit(msg)
    def _on_worker_success(self, result):
        if self._worker is None: return; op = self._worker.operation; print(f"Cloud worker success: {op}")
        if op == 'list_files': self.listFilesComplete.emit(result)
        elif op == 'upload_file':
            gfile=result.get('gdrive_file'); lid=result.get('local_item_id')
            if gfile and lid and (gid := gfile.get('id')): gdrive_mapper.update_mapping(lid, gid); self.uploadComplete.emit(lid, gid)
            else: self.syncError.emit("Upload success but mapping info missing.")
        elif op == 'download_file':
            lpath=result.get("local_path"); gid=result.get("gdrive_id")
            if lpath and gid: self.downloadComplete.emit(gid, lpath)
            else: self.syncError.emit("Download success but result data missing.")

    def request_authentication(self):
        if not self._auth_timer.isActive(): print("Auth required. Scheduling flow..."); self._auth_timer.start(100)

    def initiate_authentication_flow(self):
        if not GOOGLE_LIBS_AVAILABLE: self.syncError.emit("Google libs not installed."); self.authenticationComplete.emit(False); return
        secret_path = settings_manager.get("google_client_secret_path"); token_path = settings_manager.get("google_credentials_path")
        if not secret_path or not os.path.exists(secret_path):
            QMessageBox.warning(self.parent_widget, "Config Error", "Google Client Secrets missing."); self.authenticationComplete.emit(False); return
        try:
            if os.path.exists(token_path): os.remove(token_path)
            print("Starting local server for auth flow..."); flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
            creds = flow.run_local_server(port=0) # Blocks, opens browser
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, 'wb') as token: pickle.dump(creds, token)
            print(f"Auth success. Token saved: {token_path}"); self.authenticationComplete.emit(True)
        except Exception as e: QMessageBox.critical(self.parent_widget, "Auth Error", f"Auth failed:\n{e}"); import traceback; traceback.print_exc(); self.authenticationComplete.emit(False)

    def list_files(self): 
        print("Requesting file list...")
        if not self.has_token(): 
            self.request_authentication(); return self._start_worker('list_files')
    def upload_file(self, lpath, lid): 
        print(f"Req upload: {lpath} (LID:{lid})"); 
        if not self.has_token(): self.request_authentication(); return self._start_worker('upload_file', lpath, gdrive_file_id=gdrive_mapper.get_gdrive_id(lid), local_item_id=lid)
    def download_file(self, gid, lpath): 
        print(f"Req download: {gid} -> {lpath}"); 
        if not self.has_token(): self.request_authentication(); return self._start_worker('download_file', gid, lpath)

    def is_configured(self) -> bool:
        """Check if essential Google Drive settings are present."""
        secret_path = settings_manager.get("google_client_secret_path")
        # *** CORRECTED RETURN TYPE *** Ensure boolean
        return bool(GOOGLE_LIBS_AVAILABLE and secret_path and os.path.exists(secret_path))

    def has_token(self) -> bool:
        """Check if a token file exists (doesn't guarantee validity)."""
        token_path = settings_manager.get("google_credentials_path")
        # *** CORRECTED RETURN TYPE *** Ensure boolean
        return bool(GOOGLE_LIBS_AVAILABLE and token_path and os.path.exists(token_path))
