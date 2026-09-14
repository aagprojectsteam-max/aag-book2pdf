from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QSettings
from PySide6.QtGui import QImage, QPixmap, QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QSpinBox, QScrollArea, QFileDialog, QMessageBox,
                              QComboBox, QStackedWidget, QToolBar)

from .main_window import STYLE
from .viewer_worker import ViewerThread
from .continuous import ContinuousArea


class PageArea(QScrollArea):
    zoom_requested = Signal(int)
    page_requested = Signal(int)
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet('QScrollArea { background: #465264; border: none; border-radius: 8px; }')
        self.page_image = QLabel('פותח ובודק את הספר…')
        self.page_image.setStyleSheet('background: white; color: #18283d; padding: 0;')
        self.page_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_image.setMinimumSize(250, 180)
        self.setWidget(self.page_image)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_requested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if (delta < 0 and bar.value() == bar.maximum()) or (delta > 0 and bar.value() == bar.minimum()):
            self.page_requested.emit(1 if delta < 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class ViewerWindow(QMainWindow):
    MIN_ZOOM = .001
    MAX_ZOOM = 4.0
    page_displayed = Signal(int)
    document_opened = Signal(dict)
    closed = Signal()

    def __init__(self, source, recovery='reconstruction-preview-opaque', parent=None, *, settings=None, view_mode=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings('AAG', 'Book2PDF')
        self.view_mode = view_mode or self.settings.value('viewer/view_mode', 'SINGLE_PAGE')
        if self.view_mode not in ('SINGLE_PAGE', 'CONTINUOUS_SCROLL'):
            self.view_mode = 'SINGLE_PAGE'
        self.source = Path(source).resolve()
        self.setWindowTitle(f'{self.source.name} — AAG Book2PDF')
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(STYLE)
        self.resize(1040, 900)
        self.setMinimumSize(780, 600)
        self.page_index = 0
        self.page_count = 0
        self.zoom = 1.0
        self.fit_mode = 'page'
        self.page_dimensions = (612, 792)
        self.open_result = None
        self.validation_result = None
        self.validation_progress = None
        self.save_result = None
        self.last_displayed = None
        self.pending_close = False
        self.touch_reading_position = None
        self.last_error = ''
        self.print_worker = None
        self.print_result = None
        self.export_result = None
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 14, 18, 14)
        self.toolbar = QToolBar('כלי קריאה', self)
        self.toolbar.setObjectName('viewerToolbar')
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        self.toolbar.setStyleSheet('QToolButton { padding: 5px; } QComboBox { padding: 4px; }')
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.controls = self.toolbar
        self.toolbar.setEnabled(False)
        self.mode_selector = QComboBox()
        self.mode_selector.setAccessibleName('מצב תצוגה')
        self.mode_selector.addItem('תצוגת עמוד יחיד', 'SINGLE_PAGE')
        self.mode_selector.addItem('גלילה רציפה', 'CONTINUOUS_SCROLL')
        self.mode_selector.setCurrentIndex(self.mode_selector.findData(self.view_mode))
        self.toolbar.addWidget(self.mode_selector)
        self.toolbar.addSeparator()

        def button(text, tooltip):
            action = QAction(text, self)
            action.setToolTip(tooltip)
            self.toolbar.addAction(action)
            widget = self.toolbar.widgetForAction(action)
            widget.setAccessibleName(tooltip)
            return widget

        self.first_button = button('⇥', 'עמוד ראשון · Home')
        self.previous_button = button('›', 'העמוד הקודם · PageUp')
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setAccessibleName('מספר עמוד')
        self.page_spin.setFixedWidth(72)
        self.toolbar.addWidget(self.page_spin)
        self.page_label = QLabel('עמוד — מתוך —')
        self.toolbar.addWidget(self.page_label)
        self.next_button = button('‹', 'העמוד הבא · PageDown')
        self.last_button = button('⇤', 'עמוד אחרון · End')
        self.toolbar.addSeparator()
        self.zoom_out_button = button('−', 'הקטן · Ctrl+-')
        self.zoom_in_button = button('+', 'הגדל · Ctrl++')
        self.actual_button = button('100%', 'גודל אמיתי / 100% · Ctrl+1')
        self.zoom_label = self.actual_button
        self.fit_page_button = button('עמוד', 'התאם לעמוד · Ctrl+0')
        self.fit_width_button = button('רוחב', 'התאם לרוחב')
        self.toolbar.addSeparator()
        self.save_button = button('שמור כ-PDF', 'שמור כ-PDF · Ctrl+S')
        self.export_button = button('שמור עמודים כ-PDF', 'שמור עמודים כ-PDF')
        self.print_button = button('הדפס', 'הדפס · Ctrl+P')
        self.cancel_save_button = button('בטל שמירה', 'בטל שמירה או ייצוא')
        self.cancel_save_button.defaultAction().setVisible(False)
        self.cancel_save_button.defaultAction().triggered.connect(lambda:self.worker.cancel_output() if self.worker else None)
        self.toolbar.addSeparator()
        self.fullscreen_button = button('מסך מלא', 'מסך מלא · F11')
        self.touch_action = QAction('מגע ומחוות', self)
        self.touch_action.setCheckable(True)
        self.touch_action.setChecked(self.settings.value('viewer/touch_enabled',True,type=bool))
        self.touch_action.setToolTip('בעמוד יחיד: החלקה ימינה: העמוד הבא; שמאלה: הקודם. בעמוד רחב: גרירה להזזה.\n'
                                    'בגלילה רציפה: גרירה לגלילה, הנף לתנופה ונגיעה לעצירה. צביטה: זום. הקשה כפולה: התאמה לרוחב וחזרה לזום הקודם.')
        self.toolbar.addAction(self.touch_action)
        self.banner = QLabel('פותח ובודק את הספר… קובץ המקור נשמר ללא שינוי')
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.area = PageArea()
        try:
            prefetch = int(self.settings.value('viewer/prefetch_pages', 1))
        except (ValueError, TypeError):
            prefetch = 1
        self.continuous_area = ContinuousArea(prefetch=prefetch)
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self.area)
        self.page_stack.addWidget(self.continuous_area)
        layout.addWidget(self.page_stack, 1)
        self.validation_label = QLabel('בודק מבנה ומכין עמוד לקריאה…')
        layout.addWidget(self.validation_label)
        self.status = QLabel('פותח את העמוד הראשון…')
        layout.addWidget(self.status)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(100)
        self.resize_timer.timeout.connect(self.apply_fit)
        self.area.resized.connect(lambda: self.resize_timer.start() if self.fit_mode else None)
        self.area.zoom_requested.connect(self.change_zoom)
        self.area.page_requested.connect(lambda delta: self.go_to(self.page_index + delta))
        self.continuous_area.zoom_requested.connect(self.change_zoom)
        self.continuous_area.current_changed.connect(self.update_page_controls)
        self.continuous_area.page_ready.connect(self.on_continuous_ready)
        self.continuous_area.resized.connect(lambda: self.resize_timer.start() if self.fit_mode == 'page' else None)
        self.continuous_area.resized.connect(self.restore_continuous_touch_position)
        self.mode_selector.currentIndexChanged.connect(lambda: self.set_view_mode(self.mode_selector.currentData()))
        self.fit_page_button.setToolTip('בגלילה רציפה: גודל העמוד הנוכחי קובע את הזום לכל העמודים')
        self.first_button.defaultAction().triggered.connect(lambda: self.go_to(0))
        self.last_button.defaultAction().triggered.connect(lambda: self.go_to(self.page_count - 1))
        self.previous_button.defaultAction().triggered.connect(lambda: self.go_to(self.page_index - 1))
        self.next_button.defaultAction().triggered.connect(lambda: self.go_to(self.page_index + 1))
        self.page_spin.valueChanged.connect(lambda value: self.go_to(value - 1))
        self.zoom_in_button.defaultAction().triggered.connect(lambda: self.change_zoom(1))
        self.zoom_out_button.defaultAction().triggered.connect(lambda: self.change_zoom(-1))
        self.fit_page_button.defaultAction().triggered.connect(lambda: self.set_fit('page'))
        self.fit_width_button.defaultAction().triggered.connect(lambda: self.set_fit('width'))
        self.actual_button.defaultAction().triggered.connect(lambda: self.set_zoom(1.0))
        self.fullscreen_button.defaultAction().triggered.connect(self.toggle_fullscreen)
        self.save_button.defaultAction().triggered.connect(self.choose_save)
        self.print_button.defaultAction().triggered.connect(self.choose_print)
        self.export_button.defaultAction().triggered.connect(self.choose_export)
        self.shortcuts = []
        for key, callback in [('PgDown',lambda: self.go_to(self.page_index+1)), ('PgUp',lambda: self.go_to(self.page_index-1)),
                              ('Left',lambda: self.go_to(self.page_index+1)), ('Right',lambda: self.go_to(self.page_index-1)),
                              ('Home',lambda: self.go_to(0)), ('End',lambda: self.go_to(self.page_count-1)),
                              ('Ctrl++',lambda: self.change_zoom(1)), ('Ctrl+=',lambda: self.change_zoom(1)),
                              ('Ctrl+-',lambda: self.change_zoom(-1)), ('Ctrl+0',lambda: self.set_fit('page')),
                              ('Ctrl+1',lambda: self.set_zoom(1)), ('Ctrl+S',self.choose_save),
                              ('Ctrl+P',self.choose_print),
                              ('F11',self.toggle_fullscreen), ('Escape',self.exit_fullscreen)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        from .touch import TouchController
        self.touch = TouchController(self)
        self.touch.set_enabled(self.touch_action.isChecked())
        self.touch_action.toggled.connect(self.set_touch_enabled)
        self.worker = ViewerThread(self.source, recovery, self)
        self.continuous_area.render_requested.connect(self.request_visible)
        self.worker.loaded.connect(self.on_loaded)
        self.worker.validation_updated.connect(self.on_validation_updated)
        self.worker.rendered.connect(self.on_rendered)
        self.worker.saved.connect(self.on_saved)
        self.worker.exported.connect(self.on_exported)
        self.worker.failed.connect(self.on_error)
        self.worker.analysis_ready.connect(self.on_analysis)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    @Slot(dict)
    def on_loaded(self, result):
        self.touch.cancel()
        self.open_result = result
        self.document_model=result.get('recovered_document',{})
        self.page_count = len(self.document_model['pages']) if self.document_model else result['page_count']
        self.page_spin.setRange(1, self.page_count)
        self.continuous_area.set_document(self.document_model)
        self.mode_selector.setEnabled(True)
        self.controls.setEnabled(True)
        self.save_button.setEnabled(True)
        self.print_button.setEnabled(True)
        self.export_button.setEnabled(True)
        repaired = result['status'] == 'PASS_REPAIRED'
        self.banner.setText('נפתח עם שחזור מבנה (PASS_REPAIRED) — תוכן המקור נשמר; ערכי גרפיקה חסרים הושלמו ומתועדים.' if repaired
                            else ('PDF נפתח ואומת — תוכנו נשמר ללא שינוי' if self.source.suffix.lower()=='.pdf'
                                  else 'נפתח ואומת (PASS_EXACT) — תוכן המקור נשמר במדויק'))
        if result['status'] == 'PASS_DECODED':
            self.banner.setText('נפתח לאחר פענוח מכולה (PASS_DECODED) — כל העמודים אומתו; תוכן ה־PDF נשמר')
            if result['recovery_class'] == 'DECODED_ORIGINAL_CONTENT':
                self.banner.setText('הספר פוענח ואומת — כל הסריקות נשמרו באיכות המקור')
                if result.get('preservation',{}).get('opaque_entries'):
                    self.banner.setText('כל הסריקות פוענחו ואומתו. קיימת רשומה נוספת ללא תמונה; פרטיה נשמרו בדוח, ולא נוצר עבורה עמוד ריק.')
                if result.get('fidelity')=='PRINT_RENDERING':
                    self.banner.setText('עותק שהופק בהדפסה ל־PDF מתוך ספר שפוענח')
        if result['recovery_class'] == 'RECONSTRUCTED_PREVIEW':
            self.banner.setText('תצוגה משוחזרת — חלק קטן מנתוני התצוגה המקוריים חסר, ולכן ייתכן הבדל חזותי בעמודים מסוימים. '
                                + ('הנחת מסכה אטומה' if result['preview_policy']=='opaque' else 'הנחת מסכה שקופה')
                                + ' · עמודים מושפעים: ' + ', '.join(map(str,result['affected_pages'])))
            self.banner.setStyleSheet('background: #fff1c2; color: #573e00; padding: 8px;')
        level=self.document_model.get('recovery_level')
        if level in ('PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY'):
            self.banner.setText('שוחזרו עמודים או תמונות מקוריים — ייתכן שהפריסה המקורית לא נשמרה. ההנחות מתועדות בדוח.')
        elif result['recovery_class']=='STRUCTURAL_PDF_RECOVERY':
            self.banner.setText('מבנה המסמך שוחזר ואומת — תוכן העמודים והזרמים המקוריים נשמרו.')
        if result.get('warning') and 'Font diagnostics retained:' in result['warning']:
            self.banner.setText(self.banner.text() + ' · קיימות אזהרות גופנים במקור; הפרטים נשמרו בדוח.')
        self.banner.setToolTip(result['warning'])
        if result['status'] == 'INTERACTIVE_READY':
            self.banner.setText('הספר זמין לקריאה — המבנה והעמוד הראשון נבדקו; אימות הספר המלא טרם הסתיים.')
            self.validation_label.setText('אימות הספר ממשיך ברקע')
        if self.document_model.get('source_family')=='TURBOSUN':
            self.banner.setText('אוסף TurboSun — סדר הכרכים והעמודים לפי האינדקס; הסריקות מפוענחות לפי הצורך')
            extras=self.document_model.get('metadata',{}).get('supplemental_pages',[])
            if extras:
                from PySide6.QtWidgets import QMenu
                menu=QMenu('עמודים נוספים ללא תווית קטלוג',self)
                for page in extras:
                    action=menu.addAction(f"{page['container_path']} · עמוד {page['logical_page_number']}")
                    action.triggered.connect(lambda checked=False,index=page['page_index']:self.go_to(index))
                self.menuBar().addMenu(menu)
        self.document_opened.emit(result)
        if self.view_mode == 'CONTINUOUS_SCROLL':
            self.page_stack.setCurrentWidget(self.continuous_area)
            self.continuous_area.activate(self.zoom, self.fit_mode, 0)
        self.go_to(0)
        self.apply_fit()

    @Slot(dict)
    def on_validation_updated(self, event):
        if self.pending_close:return
        if event['kind']=='progress':
            self.validation_progress=event
            phase={'decode':'פענוח סריקות','structure':'בדיקת מבנה','render_validation':'בדיקת עמודים'}.get(event['phase'],'בדיקה')
            count=f" — {event['done']} מתוך {event['total']}" if event.get('total') else ''
            self.validation_label.setText('אימות הספר ממשיך ברקע · '+phase+count)
            return
        result=event['result']
        self.validation_result=result
        self.open_result=result
        if result['status'] in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW'):
            model=result.get('recovered_document',{})
            if model and self.document_model:
                for page,geometry in zip(model['pages'],self.document_model['pages']):
                    page.update(width=geometry['width'],height=geometry['height'])
                self.document_model.update(model)
            self.validation_label.setText('אימות הספר הושלם')
            self.banner.setText('הספר אומת — '+result['status']+(' · קיימות הנחות או אזהרות; הפרטים בדוח' if result.get('warning') or result.get('assumptions') else ''))
        else:
            if self.document_model:self.document_model['validation']={'status':'FAIL','mode':'full_document','error':result.get('error','')}
            self.validation_label.setText('אימות הספר נכשל — ניתן לנסות לייצא עמודים תקינים בנפרד')
            self.banner.setText('נמצאה בעיה באימות המלא. שמירה מלאה תחייב בדיקה מוצלחת; פרטי השגיאה בדוח.')
            self.on_analysis(result)
        self.validation_label.setToolTip(result.get('error') or result.get('warning',''))
        self.banner.setToolTip(result.get('error') or result.get('warning',''))
        if self.worker and self.worker.cache_path:
            import json
            (self.worker.cache_path/'validation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

    def update_page_controls(self, page):
        self.page_index = max(0, min(self.page_count - 1, page))
        if self.document_model:
            model_page = self.document_model['pages'][self.page_index]
            self.page_dimensions = model_page['width'], model_page['height']
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.page_index + 1)
        self.page_spin.blockSignals(False)
        self.page_label.setText(f'עמוד {self.page_index + 1} מתוך {self.page_count}')
        self.previous_button.setEnabled(self.page_index > 0)
        self.first_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index < self.page_count - 1)
        self.last_button.setEnabled(self.page_index < self.page_count - 1)
        if self.view_mode == 'CONTINUOUS_SCROLL':
            if self.continuous_area.scales:
                self.zoom = self.continuous_area.scales[self.page_index]
                self.zoom_label.defaultAction().setText(f'{self.zoom * 100:.0f}%')
            self.last_displayed = self.page_index if self.page_index in self.continuous_area.cache else None

    def go_to(self, page):
        if not self.page_count or not self.worker:
            return
        self.touch.cancel()
        self.update_page_controls(page)
        if self.view_mode == 'CONTINUOUS_SCROLL':
            self.continuous_area.go_to(self.page_index)
            return
        self.area.verticalScrollBar().setValue(0)
        self.request_page()

    def set_view_mode(self, mode):
        if mode not in ('SINGLE_PAGE', 'CONTINUOUS_SCROLL') or mode == self.view_mode:
            return
        self.touch.cancel()
        self.view_mode = mode
        self.settings.setValue('viewer/view_mode', mode)
        self.settings.sync()
        self.mode_selector.blockSignals(True)
        self.mode_selector.setCurrentIndex(self.mode_selector.findData(mode))
        self.mode_selector.blockSignals(False)
        if not self.page_count:
            return
        page = self.page_index
        if mode == 'CONTINUOUS_SCROLL':
            self.area.page_image.clear()
            self.page_stack.setCurrentWidget(self.continuous_area)
            self.continuous_area.activate(self.zoom, self.fit_mode, page)
            self.continuous_area.setFocus()
        else:
            self.continuous_area.deactivate()
            self.page_stack.setCurrentWidget(self.area)
            self.area.setFocus()
        self.go_to(page)
        self.apply_fit()

    def request_visible(self, requests, generation):
        if self.worker:
            self.worker.request_visible(requests, generation)

    def on_continuous_ready(self, page):
        if self.view_mode != 'CONTINUOUS_SCROLL' or page != self.page_index:
            return
        self.last_displayed = page
        self.status.setText(f'עמוד {page + 1} מתוך {self.page_count}  ·  קובץ המקור לא שונה')
        self.page_displayed.emit(page)

    def request_page(self):
        if self.worker and self.page_count:
            if self.touch_reading_position:
                position,point,_,_=self.touch_reading_position
                self.touch_reading_position=(position,point,self.page_index,self.zoom)
            self.last_displayed = None
            self.status.setText('מציג עמוד…')
            self.zoom_label.defaultAction().setText(f'{self.zoom * 100:.0f}%')
            if self.view_mode == 'CONTINUOUS_SCROLL':
                self.continuous_area.configure(self.zoom, self.fit_mode)
                return
            self.worker.request_render(self.page_index, self.zoom, self.devicePixelRatioF())

    def set_zoom(self, zoom):
        if not self.touch.applying:self.touch.cancel()
        self.fit_mode = None
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        self.request_page()

    def change_zoom(self, direction):
        self.set_zoom(self.zoom * (1.2 if direction > 0 else 1/1.2))

    def set_fit(self, mode):
        if not self.touch.applying:self.touch.cancel()
        self.fit_mode = mode
        self.apply_fit()

    def set_touch_enabled(self, enabled):
        self.touch.set_enabled(enabled)
        self.settings.setValue('viewer/touch_enabled',enabled)
        self.settings.sync()

    def reading_position(self, point):
        if self.view_mode=='CONTINUOUS_SCROLL':return self.continuous_area.reading_position(point)
        image=self.area.page_image
        return (self.page_index,max(0,min(1,(point.x()-image.x())/max(1,image.width()))),
                max(0,min(1,(point.y()-image.y())/max(1,image.height()))))

    def restore_touch_position(self, position, point):
        self.touch_reading_position=(position,point,self.page_index,self.zoom)
        if self.view_mode=='CONTINUOUS_SCROLL':
            self.continuous_area.restore_reading_position(position,point)
            self.restore_continuous_touch_position()

    def restore_continuous_touch_position(self):
        token=self.touch_reading_position
        if token is None or self.view_mode!='CONTINUOUS_SCROLL':return
        position,point,_,zoom=token
        def restore():
            if (self.touch_reading_position is token and self.view_mode=='CONTINUOUS_SCROLL'
                    and abs(self.zoom-zoom)<.000001):
                self.continuous_area.restore_reading_position(position,point)
        # Run after ContinuousArea's deferred resize layout, including when
        # pinch zoom adds/removes the horizontal scrollbar.
        QTimer.singleShot(0,restore)
        def expire():
            if self.touch_reading_position is token:self.touch_reading_position=None
        QTimer.singleShot(200,expire)

    def apply_touch_zoom(self, zoom, position, point):
        zoom=max(self.MIN_ZOOM,min(self.MAX_ZOOM,zoom))
        if abs(zoom-self.zoom)>max(.000001,self.zoom*.001) or self.fit_mode is not None:
            self.set_zoom(zoom)
            self.restore_touch_position(position,point)

    def apply_touch_view(self, state, position, point):
        zoom,fit=state
        if fit:self.set_fit(fit)
        else:self.set_zoom(zoom)
        self.restore_touch_position(position,point)

    def restore_single_touch_position(self):
        token=self.touch_reading_position
        if token is None:return
        position,point,page,zoom=token
        def restore():
            if (self.touch_reading_position is token and self.view_mode=='SINGLE_PAGE'
                    and self.page_index==page and abs(self.zoom-zoom)<.000001):
                self.area.horizontalScrollBar().setValue(round(position[1]*self.area.page_image.width()-point.x()))
                self.area.verticalScrollBar().setValue(round(position[2]*self.area.page_image.height()-point.y()))
        restore();QTimer.singleShot(0,restore)
        # Allow scrollbar/fit resize events to settle. New gestures, navigation,
        # panning or wheel input invalidate this token immediately.
        def expire():
            if self.touch_reading_position is token:self.touch_reading_position=None
        QTimer.singleShot(200,expire)

    def apply_fit(self):
        if not self.fit_mode or not self.page_count:
            return
        width, height = self.page_dimensions
        area = self.continuous_area if self.view_mode == 'CONTINUOUS_SCROLL' else self.area
        zoom = max(50, area.viewport().width() - 24) / width
        if self.fit_mode == 'page':
            zoom = min(zoom, max(50, area.viewport().height() - 24) / height)
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        if (abs(self.zoom - zoom) > max(.000001, zoom*.002)
                or (self.view_mode == 'CONTINUOUS_SCROLL' and self.continuous_area.fit_mode != self.fit_mode)):
            self.zoom = zoom
            self.request_page()

    @Slot(dict)
    def on_rendered(self, data):
        if 'generation' in data:
            if self.view_mode == 'CONTINUOUS_SCROLL':
                self.continuous_area.accept_render(data)
            return
        if self.view_mode != 'SINGLE_PAGE':
            return
        if data['page'] != self.page_index or abs(data['zoom'] - self.zoom) > max(.000001,self.zoom*.002):
            return
        self.page_dimensions = (data['page_width'], data['page_height'])
        image = QImage(data['pixels'], data['width'], data['height'], data['stride'], QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(data['ratio'])
        self.area.page_image.setPixmap(pixmap)
        self.area.page_image.resize(pixmap.deviceIndependentSize().toSize())
        self.restore_single_touch_position()
        self.last_displayed = data['page']
        self.status.setText(f'עמוד {data["page"] + 1} מתוך {self.page_count}  ·  קובץ המקור לא שונה')
        self.page_displayed.emit(data['page'])
        self.apply_fit()

    def choose_save(self):
        if not self.open_result or not self.save_button.isEnabled():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'שמור כ-PDF', str(self.source.with_suffix('.pdf')), 'PDF (*.pdf)',
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            target = Path(path)
            if target.suffix.lower() != '.pdf':
                target = target.with_name(target.name + '.pdf')
            overwrite = self.confirm_overwrite(target)
            if overwrite is not None:
                self.save_to(target, overwrite=overwrite)

    def confirm_overwrite(self, target):
        if not target.exists():
            return False
        if QMessageBox.question(self,'דריסת PDF קיים','הקובץ כבר קיים. האם להחליף אותו?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            return True
        return None

    def save_to(self, path, overwrite=False):
        if self.worker and self.open_result:
            self.save_button.setEnabled(False)
            self.cancel_save_button.defaultAction().setVisible(True)
            self.status.setText('שומר ובודק PDF…')
            self.worker.request_save(Path(path).absolute(), overwrite)

    def choose_pages(self, title):
        from .page_dialog import PageSelectionDialog
        if not self.page_count:
            return None
        dialog = PageSelectionDialog(self.page_count,self.page_index+1,title,self)
        return dialog.pages if dialog.exec() else None

    def choose_export(self):
        pages = self.choose_pages('שמור עמודים כ-PDF')
        if not pages:
            return
        path,_ = QFileDialog.getSaveFileName(self,'שמור עמודים כ-PDF',str(self.source.with_name(self.source.stem+'-pages.pdf')),'PDF (*.pdf)',
                                           options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            target = Path(path)
            if target.suffix.lower() != '.pdf':
                target = target.with_name(target.name+'.pdf')
            overwrite = self.confirm_overwrite(target)
            if overwrite is not None:
                self.export_to(target,pages,overwrite=overwrite)

    def export_to(self,path,pages,overwrite=False):
        from ..page_ranges import parse_pages, normalize_pages
        try:
            selected = parse_pages(pages,self.page_count) if isinstance(pages,str) else normalize_pages(pages,self.page_count)
        except ValueError as exc:
            self.on_error(str(exc))
            return
        if self.worker:
            self.export_button.setEnabled(False)
            self.status.setText('מייצא ובודק עמודים…')
            self.cancel_save_button.defaultAction().setVisible(True)
            self.worker.request_export(Path(path).absolute(),selected,overwrite)

    @Slot(dict)
    def on_exported(self,result):
        self.cancel_save_button.defaultAction().setVisible(False)
        self.export_result = result
        self.export_button.setEnabled(True)
        if result['status'] in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW'):
            self.status.setText(f'נשמרו {result["page_count"]} עמודים כ־PDF' +
                (' — תצוגה משוחזרת; ההנחות מתועדות בקובץ' if result['status']=='RECONSTRUCTED_PREVIEW' else ' באיכות המקור'))
        elif result['status']=='CANCELLED':
            self.status.setText('השמירה בוטלה')
        else:
            self.on_error(result['error'])

    def choose_print(self):
        from PySide6.QtPrintSupport import QPrinter,QPrintDialog,QAbstractPrintDialog
        pages = self.choose_pages('הדפס — בחירת עמודים')
        if not pages:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.source.stem)
        dialog = QPrintDialog(printer,self)
        dialog.setWindowTitle('הדפס')
        # The preceding range dialog supports disjoint ranges; disable the native
        # second range selector so it cannot silently override the selected pages.
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange,False)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage,False)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintSelection,False)
        if not dialog.exec():
            return
        overwrite = False
        if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
            output = Path(printer.outputFileName())
            if output.exists():
                overwrite = QMessageBox.question(self,'דריסת PDF קיים','הקובץ כבר קיים. האם להחליף אותו?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
                if not overwrite:
                    return
        self.start_print(printer,pages,overwrite)

    def start_print(self,printer,pages,overwrite=False):
        from ..page_ranges import normalize_pages
        from .printing import PrintThread
        if not self.worker or self.print_worker:
            return
        pages = normalize_pages(pages,self.page_count)
        self.print_button.setEnabled(False)
        self.print_worker = PrintThread(self.worker.cache_path/'view.pdf',printer,pages,
                                       original_source=self.source,overwrite=overwrite,parent=self,
                                       viewing_source=self.source,recovery=self.worker.recovery,expected_hash=self.open_result['source_hash'])
        self.print_worker.progress.connect(lambda done,total: self.status.setText(f'מדפיס: {done} / {total}'))
        self.print_worker.completed.connect(self.on_printed)
        self.print_worker.failed.connect(self.on_error)
        self.print_worker.finished.connect(self.on_print_finished)
        self.print_worker.start()

    @Slot(dict)
    def on_printed(self,result):
        self.print_result = result
        self.status.setText('ההדפסה הושלמה' if result['status']=='PASS' else 'העמודים נשלחו למדפסת')

    @Slot()
    def on_print_finished(self):
        self.print_worker.wait()
        self.print_worker.deleteLater()
        self.print_worker = None
        self.print_button.setEnabled(True)
        if self.pending_close:
            self.close()

    @Slot(dict)
    def on_saved(self, result):
        self.cancel_save_button.defaultAction().setVisible(False)
        self.save_result = result
        self.save_button.setEnabled(True)
        if result['status'] in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW','SKIPPED_EXISTING_VALID'):
            self.status.setText('ה־PDF נשמר ואומת בהצלחה')
        elif result['status']=='CANCELLED':
            self.status.setText('השמירה בוטלה')
        else:
            self.on_error(result['error'])

    @Slot(str)
    def on_error(self, error):
        if 'TURBOSUN_' in error:
            self.last_error=error
            self.banner.setText('לא ניתן לפתוח את אוסף TurboSun: '+error)
            self.status.setText('יש לבדוק שהקטלוג, האינדקס וכל קובצי המקור נמצאים יחד.')
            if not self.open_result:self.area.page_image.setText('אוסף TurboSun אינו שלם או אינו נתמך.\n'+error)
            return
        self.last_error = error
        if error.startswith(('DEPENDENCY_MISSING:', 'RUNTIME_LOAD_FAILED:')):
            self.banner.setText('לא ניתן לפתוח: חסר רכיב תוכנה או שרכיב לא נטען. יש להריץ בדיקת סביבה.')
            self.banner.setToolTip(error)
            self.status.setText('שגיאת התקנה — אינה מעידה על פורמט הספר')
            self.save_button.setEnabled(False)
            return
        self.banner.setText('לא ניתן לפתוח או לשמור את הספר בבטחה. קובץ המקור לא שונה.')
        self.banner.setToolTip(error)
        self.status.setText('שגיאה — מבנה לא נתמך או בדיקה שנכשלה')
        if not self.open_result:
            self.area.page_image.setText('לא ניתן לפתוח את הספר בבטחה.\nמבנה הקובץ אינו נתמך או שהוא פגום.')
        self.save_button.setEnabled(bool(self.open_result and self.worker))

    def on_analysis(self, result):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        self.analysis_result = result
        path = result.get('analysis_package')
        if path:
            self.analysis_button = QPushButton('פתח דוח ניתוח')
            self.centralWidget().layout().addWidget(self.analysis_button)
            self.analysis_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        from ..report import STATUS_LABELS
        self.banner.setText('לא ניתן לפתוח בבטחה — '+STATUS_LABELS.get(result['status'],'לא נמצא מסלול שחזור בטוח'))
        self.banner.setToolTip(result.get('error',''))

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()

    @Slot()
    def on_finished(self):
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        self.save_button.setEnabled(False)
        if self.pending_close:
            self.close()

    def closeEvent(self, event):
        self.touch.cancel()
        if self.print_worker and self.print_worker.isRunning():
            self.pending_close = True
            self.print_worker.cancelled.set()
            self.status.setText('עוצר הדפסה וסוגר בבטחה…')
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self.pending_close = True
            self.worker.stop()
            self.status.setText('סוגר בבטחה ומנקה נתונים זמניים…')
            event.ignore()
        else:
            self.continuous_area.deactivate()
            self.area.page_image.clear()
            event.accept()
            self.closed.emit()
