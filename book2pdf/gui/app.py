import multiprocessing
import sys
from pathlib import Path


def main(argv=None):
    multiprocessing.freeze_support()
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont, QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
    except (ModuleNotFoundError, ImportError) as exc:
        from ..platforms import setup_error
        message = ('חסרה ספריית הממשק PySide6.' if isinstance(exc,ModuleNotFoundError) else 'לא ניתן לטעון את ספריית הממשק או את התלויות שלה.') + ' יש להריץ בדיקת סביבה או את המתקין.'
        setup_error(message)
        return 2
    app = QApplication([sys.argv[0]])
    app.setApplicationName('AAG Book2PDF')
    app.setOrganizationName('AAG')
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setFont(QFont('Segoe UI' if sys.platform == 'win32' else 'Noto Sans Hebrew', 10))
    app.setStyle('Fusion')
    from ..platforms import resource_path
    icon = resource_path('aag-book2pdf.svg')
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    try:
        import pikepdf
        import pymupdf
        from .main_window import MainWindow
        paths = argv if argv is not None else sys.argv[1:]
        from ..turbosun.source import database_files
        if paths and all(((Path(path).is_dir() and bool(database_files(Path(path)))) or (Path(path).is_file() and Path(path).suffix.lower() in ('.book', '.pdf', '.tif', '.tiff'))) for path in paths):
            from .viewer_window import ViewerWindow
            # Open one viewer at a time by default; several arguments remain queued in
            # the converter to avoid spawning unbounded viewers on a large selection.
            if len(paths) == 1:
                window = ViewerWindow(paths[0])
            else:
                window = MainWindow(paths)
        else:
            window = MainWindow(paths)
    except ImportError as exc:
        message = 'חסרה ספרייה נדרשת.' if isinstance(exc,ModuleNotFoundError) else 'ספרייה נדרשת או תלות מערכת לא נטענה.'
        QMessageBox.critical(None, 'AAG Book2PDF', message + ' יש להריץ בדיקת סביבה.\n' + str(exc))
        return 2
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
