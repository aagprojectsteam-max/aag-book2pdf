from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QRadioButton, QLineEdit,
                              QLabel, QDialogButtonBox)
from ..page_ranges import parse_pages, PageRangeError


class PageSelectionDialog(QDialog):
    def __init__(self, total, current, title='בחירת עמודים', parent=None):
        super().__init__(parent)
        self.total = total
        self.current = current
        self.pages = []
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.all_button = QRadioButton('כל העמודים')
        self.current_button = QRadioButton(f'העמוד הנוכחי ({current})')
        self.range_button = QRadioButton('טווח עמודים')
        for radio in (self.all_button,self.current_button,self.range_button):
            layout.addWidget(radio)
        self.all_button.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(QLabel('טווח:'))
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText('1-5,8,10-15')
        self.range_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row.addWidget(self.range_edit)
        layout.addLayout(row)
        self.range_edit.setEnabled(False)
        self.range_button.toggled.connect(self.range_edit.setEnabled)
        self.error_label = QLabel('העמודים ימוינו בסדר עולה, ללא כפילויות.')
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('המשך')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('ביטול')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            if self.all_button.isChecked():
                self.pages = list(range(1,self.total+1))
            elif self.current_button.isChecked():
                self.pages = [self.current]
            else:
                self.pages = parse_pages(self.range_edit.text(),self.total)
        except PageRangeError as exc:
            self.error_label.setText(str(exc))
            self.error_label.setStyleSheet('color: #a42e25;')
            return
        super().accept()
