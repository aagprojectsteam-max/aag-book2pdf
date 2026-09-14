"""Drive real Qt dialogs and start a real conversion, with screenshot evidence."""
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit
from book2pdf.gui.main_window import MainWindow


def main():
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('AAG Book2PDF')
    app.setStyle('Fusion')
    app.setFont(QFont('Noto Sans Hebrew', 10))
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    window = MainWindow(settings=QSettings(str(output / 'acceptance.ini'), QSettings.Format.IniFormat))
    window.show()
    evidence = {'platform': app.platformName(), 'rtl': window.layoutDirection() == Qt.LayoutDirection.RightToLeft,
                'file_picker': False, 'folder_picker': False, 'output_picker': False, 'progress_events': 0, 'ui_ticks': 0}
    errors = []

    def select_dialog(path, label):
        print('Selecting', label, str(path), flush=True)
        dialog = app.activeModalWidget()
        if not isinstance(dialog, QFileDialog):
            errors.append(f'{label}: no QFileDialog')
            print(errors[-1], flush=True)
            return
        dialog.setDirectory(str(path.parent))
        dialog.selectFile(str(path))
        edit = dialog.findChild(QLineEdit, 'fileNameEdit')
        if edit:
            edit.setText(str(path))
        evidence[label] = True
        QTimer.singleShot(80, dialog.accept)

    def start():
        print('Starting dialog tests', flush=True)
        QTimer.singleShot(150, lambda: select_dialog(source, 'file_picker'))
        QTest.mouseClick(window.single_button, Qt.MouseButton.LeftButton)
        print('File dialog returned', window.inputs.count(), flush=True)
        QTimer.singleShot(150, lambda: select_dialog(source.parent, 'folder_picker'))
        QTest.mouseClick(window.folder_button, Qt.MouseButton.LeftButton)
        print('Folder dialog returned', flush=True)
        # The folder picker is tested, but don't queue the user's entire home.
        window.clear_inputs()
        window.add_paths([str(source)])
        QTimer.singleShot(150, lambda: select_dialog(output, 'output_picker'))
        QTest.mouseClick(window.output_button, Qt.MouseButton.LeftButton)
        print('Output dialog returned', window.output_edit.text(), flush=True)
        window.strict.setChecked(True)
        window.jobs.setCurrentIndex(0)
        window.grab().save(str(output / 'gui-ready.png'))
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        window.worker.event.connect(lambda event: evidence.__setitem__('progress_events', evidence['progress_events'] + 1))

    begin = time.monotonic()
    def poll():
        evidence['ui_ticks'] += 1
        if time.monotonic() - begin > 60:
            errors.append('GUI test timed out')
            window.cancel()
            for widget in app.topLevelWidgets():
                if isinstance(widget, QFileDialog):
                    widget.reject()
        if window.last_summary and window.worker is None:
            evidence['summary'] = window.last_summary
            evidence['output_exists'] = (output / (source.stem + '.pdf')).exists()
            window.grab().save(str(output / 'gui-result.png'))
            if hasattr(window, 'summary_dialog'):
                window.summary_dialog.close()
            window.close()
            evidence['closed_cleanly'] = not window.isVisible()
            evidence['errors'] = errors
            (output / 'gui-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
            app.quit()
        elif errors and window.worker is None:
            app.exit(2)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(40)
    QTimer.singleShot(250, start)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
