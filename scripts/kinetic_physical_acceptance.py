"""Observe actual touchscreen input. This script never synthesizes input events.

Close each viewer to continue to the next book. Hardware evidence remains
AWAITING_USER_CONFIRMATION; an observer cannot establish subjective UX success.
"""
import argparse
from collections import Counter
import json
import multiprocessing
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    from PySide6.QtCore import QObject,QEvent,QTimer,QSettings,Qt
    from PySide6.QtGui import QInputDevice,QEventPoint
    from PySide6.QtWidgets import QApplication,QLabel
    from book2pdf.gui.viewer_window import ViewerWindow
    from book2pdf.state import sha256
    import book2pdf
    app=QApplication(['AAG Book2PDF physical touchscreen acceptance'])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft);app.setQuitOnLastWindowClosed(False)
    result={'version':book2pdf.__version__,'module':str(Path(book2pdf.__file__).resolve()),
            'platform':app.platformName(),'input_injection':'NONE',
            'status':'AWAITING_USER_CONFIRMATION','books':[],
            'devices':[{'name':d.name(),'type':d.type().name} for d in QInputDevice.devices()]}
    current={};index=0
    def persist():
        # Keep live reporting cheap; write dense traces only between books.
        summary={**result,'books':[{**{k:v for k,v in row.items() if k not in ('touch','motion')},
                                  'touch_records':len(row['touch']),'motion_records':len(row['motion'])}
                                 for row in result['books']]}
        (args.evidence/'evidence.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    def sample():
        w=current.get('window');row=current.get('row')
        if not w or not row or not w.page_count:return
        area=w.continuous_area;s=area.kinetic.scroller
        stats=area.stats();elapsed=time.monotonic()-current['started']
        item={'seconds':elapsed,'state':s.state().name,'position':area.scroll_top(),
              'velocity':s.velocity().y(),'page':w.page_index,'zoom':w.zoom,'fit':w.fit_mode,
              'mode':w.view_mode,'raster_bytes':stats['raster_bytes'],
              'rendered_pages':stats['rendered_pages'],'wanted_pages':stats['allowed_pages']}
        if w.worker:item['scheduling']=w.worker.scheduling_stats()
        if len(row['motion'])<24000:row['motion'].append(item)
        else:row['motion_truncated']=True
        row['page_count']=w.page_count
        states=row.setdefault('observed_scroller_states',{})
        states[item['state']]=states.get(item['state'],0)+1
        row['max_raster_bytes']=max(row.get('max_raster_bytes',0),stats['raster_bytes'])
        row['max_rendered_pages']=max(row.get('max_rendered_pages',0),len(stats['rendered_pages']))
        if row['motion'] and len(row['motion'])%20==0:persist()
    class Observer(QObject):
        def eventFilter(self,watched,event):
            w=current.get('window');row=current.get('row')
            if not w or not row:return False
            if event.type() in (QEvent.Type.TouchBegin,QEvent.Type.TouchUpdate,QEvent.Type.TouchEnd,QEvent.Type.TouchCancel):
                if watched in w.touch.surfaces:
                    device=event.pointingDevice()
                    points=event.points();entry={'seconds':time.monotonic()-current['started'],
                        'type':event.type().name,'device':device.name(),'device_type':device.type().name,
                        'spontaneous':event.spontaneous(),'point_count':len(points),
                        'timestamp_ms':event.timestamp(),
                        'positions':[[p.position().x(),p.position().y()] for p in points],
                        'active_points':sum(p.state()!=QEventPoint.State.Released for p in points),
                        'scroller_before':w.continuous_area.kinetic.scroller.state().name,
                        'point_states':[p.state().name for p in points]}
                    if len(row['touch'])<24000:row['touch'].append(entry)
                    else:row['touch_truncated']=True
                    if device.type()==QInputDevice.DeviceType.TouchScreen:
                        row['touchscreen_events']=row.get('touchscreen_events',0)+1
                        if event.spontaneous():row['spontaneous_touchscreen_events']=row.get('spontaneous_touchscreen_events',0)+1
            return False
    observer=Observer();app.installEventFilter(observer)
    timer=QTimer();timer.setInterval(50);timer.timeout.connect(sample);timer.start()
    def open_next():
        nonlocal index
        if index==len(args.sources):
            result['all_windows_closed']=True;persist();app.quit();return
        source=args.sources[index];index+=1
        row={'source':str(source.resolve()),'sha256_before':sha256(source),'touch':[],'motion':[]}
        result['books'].append(row)
        settings=QSettings(str(args.evidence/f'{source.stem}-settings.ini'),QSettings.Format.IniFormat)
        w=ViewerWindow(source,settings=settings,view_mode='CONTINUOUS_SCROLL')
        current.update(window=w,row=row,started=time.monotonic())
        # Install last, so observation precedes the controller's consuming filter.
        app.removeEventFilter(observer);app.installEventFilter(observer)
        w.setWindowTitle(f'בדיקת תנופה משופרת 1.4.4 — {source.name} — ספר {index}/{len(args.sources)}')
        hint=QLabel('בדיקת תנופה משופרת: הנף מהר והרם את האצבע — המתן כמה שניות ותן לעמודים להמשיך לנוע.\n'
                    'נגיעה נוספת עוצרת מיד. בדוק גם גרירה איטית, הנף נוסף ונגיעה לעצירה.\n'
                    'בדוק גם הנף בכיוון ההפוך, צביטה בזמן גלילה והקשה כפולה. לספר הבא: סגור את החלון.')
        hint.setWordWrap(True);hint.setStyleSheet('background: #fff3cc; color: #18283d; padding: 7px;')
        w.centralWidget().layout().insertWidget(0,hint)
        w.document_opened.connect(lambda _:w.set_fit('width'))
        def closed():
            row['sha256_after']=sha256(source);row['source_unchanged']=row['sha256_after']==row['sha256_before']
            row['window_closed']=True
            row['observed_scroller_states']=dict(Counter(m['state'] for m in row['motion']))
            row['observed_max_points']=max((e['active_points'] for e in row['touch']),default=0)
            row['last_error']=w.last_error
            row['trace_file']=source.stem+'-physical.json'
            (args.evidence/row['trace_file']).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
            persist();current.clear();w.deleteLater();QTimer.singleShot(300,open_next)
        w.closed.connect(closed)
        w.show();w.raise_();w.activateWindow();persist()
    QTimer.singleShot(0,open_next)
    app.exec();timer.stop();persist()


if __name__=='__main__':
    multiprocessing.freeze_support();main()
