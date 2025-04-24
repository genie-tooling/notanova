# notanova/ui/notebook_tree.py
import os
import json
import uuid
import sys
import time # For backup file naming
from PyQt6.QtWidgets import (QTreeView, QMenu, QMessageBox, QInputDialog,
                            QSizePolicy, QAbstractItemView, QLineEdit, QStyle, QApplication)
from PyQt6.QtGui import (QStandardItemModel, QStandardItem, QIcon, QAction,
                        QDragEnterEvent, QDropEvent, QMouseEvent, QPalette, QPixmap,
                        QPainter, QColor, QFileSystemModel, QStandardItemModel)
from PyQt6.QtCore import (Qt, pyqtSignal, QModelIndex, QMimeData, QIODevice,
                         QDataStream, QByteArray, QUrl, QTimer, QDir, QPoint)

from core.settings import settings_manager
# *** ADDED PANDOC_AVAILABLE to import ***
from logic.exporter import Exporter, PANDOC_AVAILABLE
from ui.toolbar import load_icon # Use consistent icon loading

# MIME type for dragging tree items internally
NOTEBOOK_ITEM_MIME_TYPE = "application/vnd.notanova.notebookitem"

# Custom item roles
NOTE_FILE_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole + 2 # 'notebook' or 'note'
ITEM_ID_ROLE = Qt.ItemDataRole.UserRole + 3 # Unique ID for persistence
GDRIVE_ID_ROLE = Qt.ItemDataRole.UserRole + 4 # Google Drive File ID

class NotebookItem(QStandardItem):
    """Custom item for the notebook tree."""
    def __init__(self, text="", item_type="note", file_path=None, item_id=None, gdrive_id=None):
        super().__init__()
        self._item_type = item_type
        # Ensure item_id is always a string
        self._item_id = str(uuid.uuid4()) if item_id is None else str(item_id)

        self.setData(self._item_id, ITEM_ID_ROLE)
        self.setData(item_type, ITEM_TYPE_ROLE)
        self.setData(file_path, NOTE_FILE_PATH_ROLE)
        self.setData(gdrive_id, GDRIVE_ID_ROLE)
        self.setText(text)
        self.setEditable(True)
        self.update_icon()

        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled
        if item_type == "notebook":
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        self.setFlags(flags)

    def update_icon(self):
        icon_name = "folder" if self.data(ITEM_TYPE_ROLE) == "notebook" else "text-markdown"
        fallback = "folder-symbolic" if icon_name == "folder" else "text-x-generic"
        self.setIcon(load_icon(icon_name, fallback))

    def type(self) -> int:
        # Ensure custom items can handle drops if needed (especially notebooks)
        return QStandardItem.UserType + 1

    def clone(self) -> 'NotebookItem':
        # Ensure clone creates a new item with the same data
        new_item = NotebookItem(
            self.text(),
            self.data(ITEM_TYPE_ROLE),
            self.data(NOTE_FILE_PATH_ROLE),
            self.data(ITEM_ID_ROLE), # Clone should keep the same ID? Or generate new? Keep for now.
            self.data(GDRIVE_ID_ROLE)
        )
        new_item.setFlags(self.flags())
        new_item.setEnabled(self.isEnabled())
        # Clone children recursively if needed? Not necessary for simple drag/drop data transfer.
        return new_item

    # Override data() for specific roles
    def data(self, role: int = Qt.ItemDataRole.DisplayRole):
        if role == ITEM_ID_ROLE:
            return self._item_id
        if role == ITEM_TYPE_ROLE:
            return self._item_type
        if role == Qt.ItemDataRole.ToolTipRole:
             path = self.data(NOTE_FILE_PATH_ROLE)
             gid = self.data(GDRIVE_ID_ROLE)
             tt = f"{self.text()} ({self._item_type})"
             if path: tt += f"\nPath: {path}"
             if gid: tt += f"\nGDrive: {gid}"
             tt += f"\nID: {self._item_id}"
             return tt
        # Fallback to base class implementation for other roles
        return super().data(role)

    # Override setData() to handle custom roles and update icon
    def setData(self, value, role: int):
         if role == ITEM_ID_ROLE:
             self._item_id = str(value) # Ensure ID is stored as string
             # No need to call super for this custom role if not stored by base
         elif role == ITEM_TYPE_ROLE:
             # Allow setting type initially, but maybe prevent changes later?
             if value != self._item_type:
                 # print(f"Warning: Attempt to change item type for ID {self._item_id} ignored.")
                 # return # Or allow if needed, ensure icon updates
                 self._item_type = value
                 self.update_icon()
             # No need to call super for this custom role
         elif role == Qt.ItemDataRole.EditRole: # Handle text changes through EditRole
             super().setData(value, role)
             self.setText(str(value)) # Ensure display text is updated
         else:
             # Let the base class handle standard roles (DisplayRole, CheckStateRole, etc.)
             super().setData(value, role)

         # Update icon if type potentially changed (e.g., via direct setData)
         if role == ITEM_TYPE_ROLE:
             self.update_icon()


class NotebookTree(QTreeView):
    """Tree view for managing notebooks and notes."""
    noteOpened = pyqtSignal(str, str) # file_path (can be None), item_id
    noteCreated = pyqtSignal(str) # item_id (of the new note)
    itemRenamed = pyqtSignal(str, str) # item_id, new_name
    itemDeleted = pyqtSignal(str)     # item_id (of deleted item)
    structureChanged = pyqtSignal()  # Emitted after drag/drop or deletion/creation
    renameEditingStarted = pyqtSignal(str, str) # item_id, old_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QStandardItemModel(self)
        self.setModel(self.model)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.doubleClicked.connect(self.on_item_double_clicked)

        # Drag and Drop settings
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove) # Move items within the tree
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

        self.model.itemChanged.connect(self.on_item_changed)

        # Add Rename action (F2 shortcut)
        self.rename_action = QAction("Rename", self)
        self.rename_action.setShortcut(Qt.Key.Key_F2)
        self.rename_action.triggered.connect(self.rename_selected_item)
        self.addAction(self.rename_action) # Add action to the widget itself

        self.load_notebook_structure()

    def edit(self, index, trigger=QAbstractItemView.EditTrigger.EditKeyPressed, event=None):
        """Overrides edit to emit signal before editing starts."""
        if index.isValid() and trigger != QAbstractItemView.EditTrigger.NoEditTriggers:
            item = self.model.itemFromIndex(index)
            if isinstance(item, NotebookItem) and item.isEditable():
                item_id = item.data(ITEM_ID_ROLE)
                old_name = item.text()
                self.renameEditingStarted.emit(item_id, old_name) # Emit *before* editing starts
                # Proceed with the default edit behavior
                return super().edit(index, trigger, event)
        return False # Editing not started

    def on_item_double_clicked(self, index: QModelIndex):
        """Handle double-click: open notes, expand/collapse notebooks."""
        if index.isValid():
            item = self.model.itemFromIndex(index)
            if isinstance(item, NotebookItem):
                type_ = item.data(ITEM_TYPE_ROLE)
                if type_ == "note":
                    # Emit signal to open the note in the editor
                    self.noteOpened.emit(item.data(NOTE_FILE_PATH_ROLE), item.data(ITEM_ID_ROLE))
                elif type_ == "notebook":
                    # Toggle expansion state
                    self.setExpanded(index, not self.isExpanded(index))

    def show_context_menu(self, point: QPoint):
        index = self.indexAt(point)
        menu = QMenu(self)
        main_window = self.window() # Assumes tree is child of MainWindow

        if index.isValid():
            item = self.model.itemFromIndex(index)
            if isinstance(item, NotebookItem):
                item_type = item.data(ITEM_TYPE_ROLE)
                item_id = item.data(ITEM_ID_ROLE)

                if item_type == "notebook":
                    menu.addAction(load_icon("document-new"), "New Note Here", lambda: self.create_new_note(item))
                    menu.addAction(load_icon("folder-new"), "New Sub-Notebook Here", lambda: self.create_new_notebook(item))
                    menu.addSeparator()
                    menu.addAction(load_icon("edit-rename"), "Rename Notebook", self.rename_selected_item)
                    menu.addAction(load_icon("edit-delete"), "Delete Notebook", self.delete_selected_item)
                elif item_type == "note":
                    menu.addAction(load_icon("document-open"), "Open Note", lambda idx=index: self.on_item_double_clicked(idx))
                    menu.addSeparator()
                    menu.addAction(load_icon("edit-rename"), "Rename Note", self.rename_selected_item)
                    menu.addAction(load_icon("edit-delete"), "Delete Note", self.delete_selected_item)
                    menu.addSeparator()

                    # --- Export Submenu ---
                    exp_menu = menu.addMenu(load_icon("document-export"), "Export Note As...")
                    fpath = item.data(NOTE_FILE_PATH_ROLE)
                    content = None
                    can_exp = False

                    # Try getting content from open editor first
                    if hasattr(main_window, 'get_editor_content_by_id'):
                         content = main_window.get_editor_content_by_id(item_id)
                         if content is not None:
                             can_exp = True

                    # If not open or content retrieval failed, try reading from file
                    if not can_exp and fpath and os.path.exists(fpath):
                        try:
                            with open(fpath, 'r', encoding='utf-8') as f:
                                content = f.read()
                            can_exp = True
                        except Exception as e:
                            print(f"Context Menu Export: Error reading file {fpath}: {e}")
                            exp_menu.addAction("(Read Error)").setEnabled(False)

                    # Handle cases where content couldn't be obtained
                    if not can_exp and content is None:
                         exp_menu.addAction("(Note not open or file unreadable)").setEnabled(False)

                    # Add export actions if content is available
                    if can_exp and content is not None:
                        exporter = Exporter(self) # Create exporter instance
                        sname = item.text() # Suggested name from item text
                        spath = fpath # Source path for relative resources

                        exp_menu.addAction(".md", lambda checked=False, c=content, fn=sname: exporter.export_to_md(c, fn + ".md"))
                        exp_menu.addAction(".html", lambda checked=False, c=content, fn=sname, sp=spath: exporter.export_to_html(c, fn + ".html", sp))
                        exp_menu.addAction(".pdf", lambda checked=False, c=content, fn=sname, sp=spath: exporter.export_to_pdf(c, fn + ".pdf", sp))
                        # *** CORRECTED LINE BELOW ***
                        docx_a = exp_menu.addAction(".docx", lambda checked=False, c=content, fn=sname: exporter.export_to_docx(c, fn + ".docx"))
                        docx_a.setEnabled(PANDOC_AVAILABLE) # Use imported constant
        else:
            # Clicked empty area - allow creating top-level notebook
            menu.addAction(load_icon("folder-new"), "New Notebook", lambda: self.create_new_notebook(self.model.invisibleRootItem()))

        menu.exec(self.mapToGlobal(point))

    def find_item_by_id(self, item_id: str) -> NotebookItem | None:
        """Finds an item in the model by its unique ID using BFS."""
        if not item_id: return None
        root_item = self.model.invisibleRootItem()
        queue = [root_item.child(r) for r in range(root_item.rowCount())]
        while queue:
            item = queue.pop(0)
            # Check if item is valid and matches ID
            if item and isinstance(item, NotebookItem) and item.data(ITEM_ID_ROLE) == str(item_id): # Ensure comparison with string ID
                return item
            # Add children to queue
            if item and item.hasChildren():
                queue.extend([item.child(r) for r in range(item.rowCount())])
        return None # Not found

    def find_item_by_path(self, file_path: str) -> NotebookItem | None:
        """Finds a note item in the model by its file path (case-insensitive)."""
        if not file_path: return None
        try:
             # Normalize path for reliable comparison
             norm_path = os.path.normpath(os.path.abspath(file_path)).lower()
        except Exception as e:
             print(f"Error normalizing path '{file_path}': {e}")
             return None

        root = self.model.invisibleRootItem()
        queue = [root.child(r) for r in range(root.rowCount())]
        while queue:
            item = queue.pop(0)
            if item and isinstance(item, NotebookItem):
                path_data = item.data(NOTE_FILE_PATH_ROLE)
                # Check if it's a note and path_data exists
                if item.data(ITEM_TYPE_ROLE) == "note" and path_data:
                    try:
                         item_norm_path = os.path.normpath(os.path.abspath(path_data)).lower()
                         if item_norm_path == norm_path:
                             return item
                    except Exception as e:
                         # Ignore items with invalid paths stored
                         print(f"Warning: Skipping item with invalid path '{path_data}': {e}")
                         pass
                # Add children to queue
                if item.hasChildren():
                    queue.extend([item.child(r) for r in range(item.rowCount())])
        return None

    def find_item_by_gdrive_id(self, gdrive_id: str) -> NotebookItem | None:
        """Finds a note item in the model by its Google Drive ID."""
        if not gdrive_id: return None
        root = self.model.invisibleRootItem()
        queue = [root.child(r) for r in range(root.rowCount())]
        while queue:
            item = queue.pop(0)
            if item and isinstance(item, NotebookItem):
                 # Check type and GDrive ID
                 if item.data(ITEM_TYPE_ROLE) == "note" and item.data(GDRIVE_ID_ROLE) == gdrive_id:
                     return item
                 # Add children
                 if item.hasChildren():
                     queue.extend([item.child(r) for r in range(item.rowCount())])
        return None

    def rename_selected_item(self):
        """Initiates renaming for the currently selected item."""
        idx = self.currentIndex()
        if idx.isValid():
            self.edit(idx) # Trigger the overridden edit method

    def delete_selected_item(self):
        """Deletes the selected item (and children), attempts to trash files."""
        idx = self.currentIndex()
        if not idx.isValid(): return
        item = self.model.itemFromIndex(idx)
        if not isinstance(item, NotebookItem): return

        item_id = item.data(ITEM_ID_ROLE)
        item_type = item.data(ITEM_TYPE_ROLE)
        item_name = item.text()

        # Collect data for all items to be deleted (the item and its descendants)
        items_to_delete_data = []
        queue = [item]
        processed_ids = set()
        while queue:
             current_item = queue.pop(0)
             if not current_item or not isinstance(current_item, NotebookItem): continue
             current_id = current_item.data(ITEM_ID_ROLE)
             if current_id in processed_ids: continue # Avoid cycles if tree structure is weird
             processed_ids.add(current_id)

             item_data = {
                 "id": current_id,
                 "path": current_item.data(NOTE_FILE_PATH_ROLE) if current_item.data(ITEM_TYPE_ROLE) == 'note' else None,
                 "name": current_item.text(),
                 "item": current_item # Keep reference for removal
             }
             items_to_delete_data.append(item_data)
             if current_item.hasChildren():
                 queue.extend([current_item.child(r) for r in range(current_item.rowCount())])

        # --- Confirmation Dialog ---
        num_children = len(items_to_delete_data) - 1
        msg = f"Are you sure you want to delete '{item_name}'?"
        if item_type == "notebook" and num_children > 0:
            msg += f"\n\nThis will also delete {num_children} note(s) or sub-notebook(s) inside."

        # Check for associated files to be trashed
        files_to_trash = [d["path"] for d in items_to_delete_data if d["path"] and os.path.exists(d["path"])]
        if files_to_trash:
            msg += "\n\nThe following associated file(s) will be moved to the system trash:"
            # Show only first few files for brevity
            max_files_to_show = 3
            for i, fpath in enumerate(files_to_trash[:max_files_to_show]):
                msg += f"\n- {os.path.basename(fpath)}"
            if len(files_to_trash) > max_files_to_show:
                msg += f"\n- ... and {len(files_to_trash) - max_files_to_show} more."

        self.window().raise_(); self.window().activateWindow() # Bring window to front
        reply = QMessageBox.question(self, "Confirm Deletion", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return # User cancelled

        # --- Trash Files ---
        trash_success = True
        try:
            import send2trash
            for fpath in files_to_trash:
                try:
                    print(f"Trashing file: {fpath}")
                    send2trash.send2trash(fpath)
                except Exception as e:
                    print(f"Error moving file to trash: {fpath} - {e}")
                    proceed = QMessageBox.critical(self, "Trash Error",
                                                   f"Could not move '{os.path.basename(fpath)}' to trash.\nError: {e}\n\nContinue deleting the item from the notebook?",
                                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                                   QMessageBox.StandardButton.No)
                    if proceed == QMessageBox.StandardButton.No:
                        trash_success = False; break # Abort deletion
                    # Else: User chose to continue despite trash error
        except ImportError:
            print("Warning: 'send2trash' library not found. Files associated with deleted notes will NOT be moved to trash.")
            # Optionally ask user if they want to proceed without trashing?
            # reply_no_trash = QMessageBox.warning(self, "Trash Not Available",
            #                                     "'send2trash' library not found. Files cannot be moved to trash.\n\nDelete item(s) from notebook anyway?",
            #                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            #                                     QMessageBox.StandardButton.Cancel)
            # if reply_no_trash != QMessageBox.StandardButton.Yes:
            #     trash_success = False
            pass # For now, just proceed without trashing if library missing

        if not trash_success:
            print("Deletion cancelled due to trash error or user choice.")
            return

        # --- Delete from Model and Emit Signals ---
        # Remove the top-level item being deleted (removes descendants automatically)
        parent = item.parent() or self.model.invisibleRootItem()
        parent.removeRow(item.row())
        print(f"Deleted '{item_name}' (ID: {item_id}) and its children from the tree.")

        # Emit signals for each deleted item ID
        from core.cloud_sync import gdrive_mapper # Import here to avoid circularity
        for data in items_to_delete_data:
            deleted_id = data.get("id")
            if deleted_id:
                self.itemDeleted.emit(deleted_id)
                gdrive_mapper.remove_mapping(deleted_id) # Remove GDrive mapping

        self.structureChanged.emit() # Signal structure change


    def on_item_changed(self, item: QStandardItem):
        """Handle item rename confirmation from model (when editing finishes)."""
        # Check if it's our custom item and the change is in the text column (0)
        if isinstance(item, NotebookItem) and item.column() == 0:
            item_id = item.data(ITEM_ID_ROLE)
            new_name = item.text()

            # Basic validation (e.g., prevent empty names)
            if not new_name.strip():
                 print("Warning: Item name cannot be empty. Reverting is complex here, handle upstream.")
                 # Ideally, prevent empty text in the editor delegate, or revert here if possible.
                 # For now, just log it. The rename signal will still fire.
                 # TODO: Find a way to get the old name reliably here or prevent empty edit acceptance.

            print(f"Item model changed (rename finished): ID={item_id}, New Text='{new_name}'")
            # Emit signal for MainWindow to handle potential file renaming etc.
            self.itemRenamed.emit(item_id, new_name)

            # Ensure tree remains sorted after rename
            parent = item.parent() or self.model.invisibleRootItem()
            parent.sortChildren(0, Qt.SortOrder.AscendingOrder)


    def _find_unique_name(self, parent_item, base_name):
         """Generates a unique name within the parent item."""
         current_names = {parent_item.child(r).text() for r in range(parent_item.rowCount()) if parent_item.child(r)}
         name = base_name
         count = 1
         while name in current_names:
             name = f"{base_name} ({count})"
             count += 1
         return name

    def create_new_notebook(self, parent_item=None, name="New Notebook", item_id=None):
        """Creates a new notebook item."""
        parent = parent_item or self.model.invisibleRootItem()
        # Ensure parent is actually a notebook or the root
        if parent != self.model.invisibleRootItem() and parent.data(ITEM_TYPE_ROLE) != "notebook":
             print("Warning: Attempting to create notebook under a note. Creating under parent notebook instead.")
             parent = parent.parent() or self.model.invisibleRootItem()

        actual_name = self._find_unique_name(parent, name)
        nb_item = NotebookItem(actual_name, "notebook", item_id=item_id) # Use provided or generate new ID

        parent.appendRow(nb_item)
        parent.sortChildren(0, Qt.SortOrder.AscendingOrder) # Keep sorted
        idx = nb_item.index()

        # Expand parent if it wasn't the root
        if parent != self.model.invisibleRootItem():
            self.expand(parent.index())

        # Select the new item, but DON'T start editing automatically
        self.setCurrentIndex(idx)
        # QTimer.singleShot(50, lambda i=idx: self.edit(i)) # REMOVED: Don't auto-edit

        self.structureChanged.emit()
        print(f"Created Notebook: {actual_name} (ID: {nb_item.data(ITEM_ID_ROLE)})")
        return nb_item

    def create_new_note(self, parent_item=None, name="Untitled Note"):
        """Creates a new note item."""
        if parent_item is None:
             parent_item = self.model.invisibleRootItem()
        # Ensure parent is a notebook or the root
        elif parent_item.data(ITEM_TYPE_ROLE) != "notebook":
             parent_item = parent_item.parent() or self.model.invisibleRootItem()

        actual_name = self._find_unique_name(parent_item, name)
        # Notes start with no file path and a new unique ID
        note_item = NotebookItem(actual_name, "note", file_path=None)
        new_id = note_item.data(ITEM_ID_ROLE) # Get the generated ID

        parent_item.appendRow(note_item)
        parent_item.sortChildren(0, Qt.SortOrder.AscendingOrder)
        idx = note_item.index()

        # Expand parent if it wasn't the root
        if parent_item != self.model.invisibleRootItem():
            self.expand(parent_item.index())

        # Select the new item, but DON'T start editing automatically
        self.setCurrentIndex(idx)
        # QTimer.singleShot(50, lambda i=idx: self.edit(i)) # REMOVED: Don't auto-edit

        # Emit signal *after* item is added
        self.noteCreated.emit(new_id)
        self.structureChanged.emit()
        print(f"Created Note Item: {actual_name} (ID: {new_id})")
        return note_item

    def get_parent_for_new_item(self) -> QStandardItem:
        """Determines the best parent item for creating a new note/notebook."""
        idx = self.currentIndex()
        parent_item = self.model.invisibleRootItem() # Default to root
        if idx.isValid():
            item = self.model.itemFromIndex(idx)
            if isinstance(item, NotebookItem):
                if item.data(ITEM_TYPE_ROLE) == "notebook":
                     parent_item = item # Create inside selected notebook
                elif item.parent(): # Create alongside selected note (in its parent notebook)
                     parent_item = item.parent()
        return parent_item


    def update_note_metadata(self, item_id: str, file_path: str = None, gdrive_id: str = None, name: str = None):
        """Updates metadata for a note item found by ID."""
        item = self.find_item_by_id(item_id)
        if item and item.data(ITEM_TYPE_ROLE) == "note":
            updated = False
            if file_path is not None and item.data(NOTE_FILE_PATH_ROLE) != file_path:
                item.setData(file_path, NOTE_FILE_PATH_ROLE)
                updated = True
            if gdrive_id is not None and item.data(GDRIVE_ID_ROLE) != gdrive_id:
                item.setData(gdrive_id, GDRIVE_ID_ROLE)
                updated = True
            if name is not None and item.text() != name:
                item.setText(name) # setText handles DisplayRole and EditRole implicitly
                updated = True

            if updated:
                # Re-sort parent if name changed
                if name is not None:
                    parent = item.parent() or self.model.invisibleRootItem()
                    parent.sortChildren(0, Qt.SortOrder.AscendingOrder)
                # Optionally emit a signal indicating metadata change?
                # self.metadataChanged.emit(item_id)
        elif not item:
             print(f"Warning: Cannot update metadata. Item with ID {item_id} not found.")


    def create_or_update_note_item(self, parent_item, item_id, name, file_path=None, gdrive_id=None):
         """Creates a note item if ID doesn't exist, otherwise updates metadata."""
         item = self.find_item_by_id(item_id)
         if item:
             # Item exists, update its metadata
             self.update_note_metadata(item_id, file_path=file_path, gdrive_id=gdrive_id, name=name)
             # Move item if parent is different? Complex. Assume it stays for now.
             return item
         else:
             # Item doesn't exist, create a new one
             # Ensure parent is valid
             if parent_item is None or (parent_item != self.model.invisibleRootItem() and parent_item.data(ITEM_TYPE_ROLE) != 'notebook'):
                 parent_item = self.model.invisibleRootItem()

             actual_name = self._find_unique_name(parent_item, name)
             new_item = NotebookItem(actual_name, "note", file_path, item_id, gdrive_id)
             parent_item.appendRow(new_item)
             parent_item.sortChildren(0, Qt.SortOrder.AscendingOrder)
             self.structureChanged.emit()
             print(f"Created note item via create_or_update: {actual_name} (ID: {item_id})")
             return new_item

    # --- Drag and Drop Implementation ---

    def mimeTypes(self) -> list[str]:
        """Specifies the MIME type for internal drag operations."""
        return [NOTEBOOK_ITEM_MIME_TYPE]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        """Encodes the dragged item's ID into QMimeData."""
        if not indexes: return QMimeData()
        # Only allow dragging single items for now
        if len(indexes) > 1: return QMimeData()

        idx = indexes[0]
        item = self.model.itemFromIndex(idx)
        if not idx.isValid() or not isinstance(item, NotebookItem):
            return QMimeData()

        item_id = item.data(ITEM_ID_ROLE)
        if not item_id: return QMimeData() # Should not happen

        mime_data = QMimeData()
        encoded_data = QByteArray()
        stream = QDataStream(encoded_data, QIODevice.OpenModeFlag.WriteOnly)
        stream.writeQString(item_id) # Encode the unique ID
        mime_data.setData(NOTEBOOK_ITEM_MIME_TYPE, encoded_data)
        # print(f"Dragging item ID: {item_id}") # Debug
        return mime_data

    def canDropMimeData(self, data: QMimeData, action: Qt.DropAction, row: int, col: int, parent: QModelIndex) -> bool:
        """Checks if the dragged data can be dropped at the target location."""
        if not data.hasFormat(NOTEBOOK_ITEM_MIME_TYPE):
            return False # Only accept our internal type

        # Determine the target item (notebook or root)
        target_item = self.model.itemFromIndex(parent) if parent.isValid() else self.model.invisibleRootItem()

        # Allow dropping only onto notebooks or the root item's area
        if not target_item or (parent.isValid() and target_item.data(ITEM_TYPE_ROLE) != 'notebook'):
            return False

        # Decode the source item ID
        encoded_data = data.data(NOTEBOOK_ITEM_MIME_TYPE)
        stream = QDataStream(encoded_data, QIODevice.OpenModeFlag.ReadOnly)
        if stream.atEnd(): return False
        source_item_id = stream.readQString()
        source_item = self.find_item_by_id(source_item_id)

        if not source_item: return False # Source item not found?

        # Prevent dropping an item onto itself or one of its own children
        temp_parent = target_item
        while temp_parent != self.model.invisibleRootItem() and temp_parent is not None:
            if temp_parent == source_item:
                return False # Cannot drop onto self or descendant
            temp_parent = temp_parent.parent()

        # Allow dropping 'on' a notebook (becomes last child) or 'between' items
        return True

    def supportedDropActions(self) -> Qt.DropAction:
        """Specifies that only Move actions are supported."""
        return Qt.DropAction.MoveAction

    def dropEvent(self, event: QDropEvent):
        """Handles the dropping of an item."""
        if not event.mimeData().hasFormat(NOTEBOOK_ITEM_MIME_TYPE):
            event.ignore(); return

        # Decode source item ID
        encoded_data = event.mimeData().data(NOTEBOOK_ITEM_MIME_TYPE)
        stream = QDataStream(encoded_data, QIODevice.OpenModeFlag.ReadOnly)
        if stream.atEnd(): event.ignore(); return
        source_item_id = stream.readQString()
        source_item = self.find_item_by_id(source_item_id)

        if not source_item: event.ignore(); return

        # Get source item details before detaching
        source_parent = source_item.parent() or self.model.invisibleRootItem()
        source_row = source_item.row()

        # Determine target parent and row based on drop position
        drop_pos = event.position().toPoint()
        target_idx = self.indexAt(drop_pos)
        target_item = self.model.itemFromIndex(target_idx) if target_idx.isValid() else None

        target_parent = None
        target_row = -1

        drop_indicator = self.dropIndicatorPosition()

        if drop_indicator == QAbstractItemView.DropIndicatorPosition.OnItem:
            # Dropped directly onto a notebook item
            if target_item and target_item.data(ITEM_TYPE_ROLE) == 'notebook':
                target_parent = target_item
                target_row = target_parent.rowCount() # Append to end
            else: event.ignore(); return # Ignore drop onto notes
        elif drop_indicator in (QAbstractItemView.DropIndicatorPosition.AboveItem, QAbstractItemView.DropIndicatorPosition.BelowItem):
            # Dropped between items
            target_parent = target_item.parent() if target_item and target_item.parent() else self.model.invisibleRootItem()
            target_row = target_idx.row()
            if drop_indicator == QAbstractItemView.DropIndicatorPosition.BelowItem:
                target_row += 1
        elif drop_indicator == QAbstractItemView.DropIndicatorPosition.OnViewport:
            # Dropped onto empty area (treat as root)
            target_parent = self.model.invisibleRootItem()
            target_row = target_parent.rowCount()
        else:
            event.ignore(); return # Should not happen

        if target_parent is None: event.ignore(); return

        # --- Perform the move ---
        # Check if move is within the same parent (just reordering)
        is_reorder = (source_parent == target_parent)

        # Take the row from the source parent (returns a list containing the item)
        # Note: takeRow modifies the source parent immediately
        moved_item_list = source_parent.takeRow(source_row)
        if not moved_item_list:
            print("Error: Failed to take source row during drop.")
            event.ignore()
            return

        moved_item = moved_item_list[0]

        # Adjust target row if moving downwards within the same parent
        if is_reorder and source_row < target_row:
            target_row -= 1

        # Insert the item at the target location
        target_parent.insertRow(target_row, moved_item)

        # Ensure parent is expanded and item is selected
        if target_parent != self.model.invisibleRootItem():
             self.expand(target_parent.index())
        self.setCurrentIndex(moved_item.index()) # Select the moved item

        target_parent.sortChildren(0, Qt.SortOrder.AscendingOrder) # Keep target sorted
        if source_parent != target_parent:
            source_parent.sortChildren(0, Qt.SortOrder.AscendingOrder) # Keep source sorted too

        event.acceptProposedAction()
        self.structureChanged.emit() # Signal that structure has changed
        print(f"Moved item '{moved_item.text()}' to '{target_parent.text()}' at row {target_row}")


    # --- Persistence ---

    def get_notebook_structure(self, parent_item=None) -> list:
        """Recursively gets the structure of the notebook tree."""
        if parent_item is None:
            parent_item = self.model.invisibleRootItem()

        structure = []
        for row in range(parent_item.rowCount()):
            item = parent_item.child(row)
            if isinstance(item, NotebookItem):
                item_data = {
                    "id": item.data(ITEM_ID_ROLE),
                    "name": item.text(),
                    "type": item.data(ITEM_TYPE_ROLE),
                    "path": item.data(NOTE_FILE_PATH_ROLE), # Store path for notes
                    "gdrive_id": item.data(GDRIVE_ID_ROLE), # Store GDrive ID
                    "children": []
                }
                if item.hasChildren():
                    item_data["children"] = self.get_notebook_structure(item)
                structure.append(item_data)
        return structure

    def load_notebook_structure(self):
        """Loads the notebook structure from the JSON file."""
        fpath = settings_manager.get("notebook_data_file")
        if not os.path.exists(fpath):
            print("Notebook structure file not found. Starting with empty tree.")
            # Optionally create a default root or note here
            return

        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.model.clear() # Clear existing model before loading
            self.model.setHorizontalHeaderLabels(['Notebooks']) # Set header label (optional)
            self._populate_from_structure(self.model.invisibleRootItem(), data)
            print(f"Notebook structure loaded from: {fpath}")
            # Expand top-level items by default after loading
            self.expandAll() # Expand all items to ensure visibility
            self.viewport().update() # Force viewport update
            self.doItemsLayout() # Force layout recalculation

        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading notebook structure from {fpath}: {e}")
            # Backup corrupted file
            backup_path = fpath + f".backup.{int(time.time())}"
            try:
                 if os.path.exists(fpath): os.rename(fpath, backup_path)
                 print(f"Backed up corrupted structure file to: {backup_path}")
            except OSError: pass
            self.model.clear() # Start fresh if load fails
            self.model.setHorizontalHeaderLabels(['Notebooks']) # Set header label
        except Exception as e:
             print(f"Unexpected error loading structure: {e}")
             import traceback; traceback.print_exc()
             self.model.clear()
             self.model.setHorizontalHeaderLabels(['Notebooks']) # Set header label

    def _populate_from_structure(self, parent, data_list):
        """Recursively populates the model from the loaded data structure."""
        for data in data_list:
             # Create item using stored data
             item = NotebookItem(
                 text=data.get("name", "?"),
                 item_type=data.get("type", "note"),
                 file_path=data.get("path"),
                 item_id=data.get("id"), # Use stored ID
                 gdrive_id=data.get("gdrive_id")
             )
             parent.appendRow(item)
             # Recursively populate children
             if data.get("children"):
                 self._populate_from_structure(item, data["children"])
        # Sort children after populating a level
        parent.sortChildren(0, Qt.SortOrder.AscendingOrder)


    def save_notebook_structure(self):
        """Saves the current notebook structure to the JSON file."""
        data = self.get_notebook_structure()
        fpath = settings_manager.get("notebook_data_file")
        tpath = fpath + ".tmp" # Save to temp file first

        try:
            os.makedirs(os.path.dirname(fpath), exist_ok=True) # Ensure directory exists
            with open(tpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2) # Pretty print JSON
            os.replace(tpath, fpath) # Atomic replace if possible
            # print(f"Notebook structure saved: {fpath}") # Reduce noise, only log on change?
        except Exception as e:
            print(f"Error saving notebook structure to {fpath}: {e}")
            # Optionally show error to user
            # QMessageBox.warning(self, "Save Error", f"Could not save notebook structure:\n{e}")
