import os
import sys
import time
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QApplication
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtCore import QSizeF, QMarginsF, QUrl
from PyQt6.QtGui import QPageLayout, QPageSize, QTextDocument

# Import conditionally if needed for DOCX via other means
try:
    import pypandoc # Example using pandoc
    PANDOC_AVAILABLE = True
    # Check if pandoc executable exists
    try:
        pypandoc.get_pandoc_path()
        print("Pandoc executable found, DOCX export enabled via pypandoc.")
    except OSError:
        print("Warning: pypandoc library found, but Pandoc executable not found in PATH. DOCX export disabled.")
        PANDOC_AVAILABLE = False
except ImportError:
    PANDOC_AVAILABLE = False
    print("Warning: pypandoc library not found. DOCX export via pandoc disabled.")


from logic.formatter import markdown_to_html, get_pygments_css, PYGMENTS_AVAILABLE
from core.settings import settings_manager # For default path

class Exporter:
    def __init__(self, parent_widget=None):
        """
        Initializes the Exporter.
        :param parent_widget: Optional parent widget for dialogs.
        """
        self.parent_widget = parent_widget

    def _get_save_path(self, title, suggested_filename, filter_str):
        """Helper to get save file path using QFileDialog."""
        default_dir = settings_manager.get("default_save_path", os.path.expanduser("~"))
        os.makedirs(default_dir, exist_ok=True) # Ensure default dir exists

        suggested_path = os.path.join(default_dir, suggested_filename)

        file_path, selected_filter = QFileDialog.getSaveFileName(
            self.parent_widget,
            title,
            suggested_path,
            filter_str
        )

        # Auto-append extension based on filter if user didn't add one
        if file_path and selected_filter:
             # Extract extensions from the selected filter (e.g., "PDF Files (*.pdf)")
             import re
             ext_match = re.search(r'\(\*(\.\w+)', selected_filter)
             if ext_match:
                 default_ext = ext_match.group(1)
                 # Check if file_path already has an extension
                 _, current_ext = os.path.splitext(file_path)
                 if not current_ext and default_ext:
                      file_path += default_ext
                      print(f"Appended default extension '{default_ext}' based on filter.")

        return file_path

    def export_to_md(self, content: str, suggested_filename: str = "note.md"):
        """Exports the content as a Markdown file."""
        file_path = self._get_save_path(
             "Export as Markdown",
             suggested_filename,
             "Markdown Files (*.md);;Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                QMessageBox.information(self.parent_widget, "Export Successful", f"Note exported to:\n{file_path}")
                return file_path
            except Exception as e:
                QMessageBox.critical(self.parent_widget, "Export Error", f"Could not save Markdown file:\n{e}")
        return None

    def export_to_html(self, md_content: str, suggested_filename: str = "note.html", source_file_path: str = None):
        """Exports the content as an HTML file."""
        file_path = self._get_save_path(
             "Export as HTML",
             suggested_filename,
             "HTML Files (*.html *.htm);;All Files (*)"
        )
        if file_path:
            try:
                html_content = self._generate_full_html(md_content, source_file_path)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                QMessageBox.information(self.parent_widget, "Export Successful", f"Note exported to:\n{file_path}")
                return file_path
            except Exception as e:
                QMessageBox.critical(self.parent_widget, "Export Error", f"Could not save HTML file:\n{e}")
        return None

    def _generate_full_html(self, md_content: str, source_file_path: str = None) -> str:
        """
        Generates a complete HTML document with basic styling and code highlighting CSS.
        :param md_content: Markdown content string.
        :param source_file_path: Optional path to the original MD file for resolving relative image paths.
        """
        body_html = markdown_to_html(md_content, use_pygments=PYGMENTS_AVAILABLE)
        # Use theme-aware pygments style
        pygments_style = 'native' if settings_manager.is_dark_mode() else 'default'
        pygments_css = get_pygments_css(style=pygments_style) # Get CSS for Pygments

        # Basic CSS for structure, similar to preview but maybe simpler
        css = f"""
        body {{ font-family: sans-serif; line-height: 1.6; padding: 25px; margin: 0 auto; max-width: 850px; background-color: #fff; color: #111; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        h1, h2, h3, h4, h5, h6 {{ margin-top: 1.6em; margin-bottom: 0.6em; border-bottom: 1px solid #eee; padding-bottom: 0.3em;}}
        pre {{ border: 1px solid #ddd; padding: 12px; border-radius: 4px; overflow: auto; background-color: #f8f8f8; }}
        code {{ font-family: monospace, Consolas, 'Courier New'; }}
        /* Inline code styling */
        code:not(pre > code) {{ background-color: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; font-size: 90%; border: 1px solid #e0e0e0; }}
        table {{ border-collapse: collapse; margin: 1.2em 0; width: auto; border: 1px solid #ccc; }}
        th, td {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #f2f2f2; font-weight: bold;}}
        blockquote {{ border-left: 5px solid #ccc; padding-left: 15px; color: #555; margin-left: 0; font-style: italic; }}
        img {{ max-width: 100%; height: auto; display: block; margin: 1.2em 0; border-radius: 3px; }} /* Responsive images */
        hr {{ border: none; border-top: 2px solid #eee; margin: 2.5em 0; }}
        ul.task-list {{ padding-left: 1.5em; list-style: none; }} /* Adjust task list padding */
        li.task-list-item input[type="checkbox"] {{ margin-right: 0.6em; vertical-align: middle; }}
        /* Pygments CSS */
        {pygments_css}
        """
        # Dark mode adjustments for exported HTML file
        dark_mode_css = ""
        if settings_manager.is_dark_mode(): # Check current theme preference when exporting
            dark_mode_css = """
            /* Basic Dark Mode Styling for Exported HTML */
            body { background-color: #2d2d2d; color: #ccc; }
            a { color: #87cefa; } /* Light sky blue links */
            h1, h2, h3, h4, h5, h6 { border-bottom-color: #555; }
            pre { border-color: #555; background-color: #222; }
            code:not(pre > code) { background-color: #3a3a3a; color: #ccc; border-color: #4a4a4a; }
            table, th, td { border-color: #555; }
            th { background-color: #4a4a4a; }
            blockquote { border-left-color: #555; color: #aaa; }
            hr { border-top-color: #555; }
            """

        # Full HTML structure
        base_tag = ""
        if source_file_path:
            try:
                abs_path = os.path.abspath(source_file_path)
                base_dir_path = os.path.dirname(abs_path)
                # Ensure trailing slash for directory URL, use file:/// scheme
                base_url = QUrl.fromLocalFile(base_dir_path + os.path.sep)
                if base_url.isValid():
                    base_tag = f'<base href="{base_url.toString()}">'
                else: print(f"Warning: Could not create valid base URL from path: {base_dir_path}")
            except Exception as e: print(f"Warning: Error creating base tag for HTML export: {e}")

        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {base_tag}
    <title>Exported Note</title>
    <style>
{css}
{dark_mode_css}
    </style>
</head>
<body>
{body_html}
</body>
</html>"""
        return full_html


    def export_to_pdf(self, md_content: str, suggested_filename: str = "note.pdf", source_file_path: str = None):
        """
        Exports the content as a PDF file by rendering HTML via QTextDocument.
        :param md_content: Markdown content string.
        :param suggested_filename: Suggested name for the output PDF file.
        :param source_file_path: Optional path to the original MD file for resolving relative paths.
        """
        file_path = self._get_save_path(
             "Export as PDF",
             suggested_filename,
             "PDF Files (*.pdf);;All Files (*)"
        )
        if file_path:
            try:
                printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(file_path)

                # Set up page layout (A4 with reasonable margins)
                page_layout = QPageLayout()
                page_size = QPageSize(QPageSize.PageSizeId.A4)
                page_layout.setPageSize(page_size)
                page_layout.setOrientation(QPageLayout.Orientation.Portrait)
                margins_mm = QMarginsF(15, 15, 15, 15) # L, T, R, B
                page_layout.setMargins(margins_mm, QPageSize.Unit.Millimeter)
                printer.setPageLayout(page_layout)

                # Generate full HTML for rendering
                html_for_pdf = self._generate_full_html(md_content, source_file_path)

                temp_doc = QTextDocument()
                if source_file_path:
                    try:
                        abs_path = os.path.abspath(source_file_path)
                        base_dir_path = os.path.dirname(abs_path)
                        base_url = QUrl.fromLocalFile(base_dir_path + os.path.sep)
                        if base_url.isValid():
                             temp_doc.setBaseUrl(base_url)
                             print(f"PDF Export: Using base URL {base_url.toString()}")
                        else: print(f"Warning (PDF Export): Invalid base URL from path: {base_dir_path}")
                    except Exception as e: print(f"Warning (PDF Export): Error creating base URL: {e}")

                temp_doc.setPageSize(QSizeF(printer.pageRect(QPrinter.Unit.Point).size()))
                temp_doc.setHtml(html_for_pdf)

                print(f"Printing document to PDF: {file_path}")
                temp_doc.print_(printer) # Perform the printing
                print("PDF export finished.")
                QMessageBox.information(self.parent_widget, "Export Successful", f"Note exported to:\n{file_path}")
                return file_path

            except Exception as e:
                QMessageBox.critical(self.parent_widget, "Export Error", f"Could not save PDF file:\n{e}")
                import traceback; traceback.print_exc()
        return None


    def export_to_docx(self, md_content: str, suggested_filename: str = "note.docx"):
        """Exports the content as a DOCX file using Pandoc."""
        if not PANDOC_AVAILABLE:
            QMessageBox.warning(
                self.parent_widget, "Feature Not Available",
                "Exporting to DOCX requires 'pypandoc' and 'pandoc' installation."
            )
            return None

        file_path = self._get_save_path(
            "Export as DOCX", suggested_filename, "Word Documents (*.docx);;All Files (*)"
        )
        if file_path:
            try:
                print(f"Converting Markdown to DOCX using Pandoc: {file_path}")
                input_format = 'gfm' # GitHub Flavored Markdown often works well
                extra_args = [] # Add --reference-doc etc. if needed

                output = pypandoc.convert_text(
                    source=md_content, to='docx', format=input_format,
                    outputfile=file_path, extra_args=extra_args
                )
                if output: # Pandoc returns empty string on success with outputfile
                     print(f"Pandoc Message during DOCX conversion: {output}")
                     QMessageBox.warning(self.parent_widget, "Pandoc Message", f"Pandoc reported issues:\n{output[:200]}...")

                if os.path.exists(file_path):
                    print("DOCX export finished.")
                    QMessageBox.information(self.parent_widget, "Export Successful", f"Note exported to:\n{file_path}")
                    return file_path
                else:
                    raise RuntimeError("Pandoc command finished but output file was not created.")

            except FileNotFoundError:
                 QMessageBox.critical(self.parent_widget, "Export Error", "Pandoc command not found. Ensure Pandoc is installed and in PATH.")
            except Exception as e:
                QMessageBox.critical(self.parent_widget, "Export Error", f"Could not save DOCX file using Pandoc:\n{e}")
                import traceback; traceback.print_exc()
        return None
