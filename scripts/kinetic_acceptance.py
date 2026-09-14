"""Native QScroller acceptance, called from the real-book viewer acceptance run."""
import json
import time


def exercise_kinetic(window, app, destination):
    from PySide6.QtCore import QPoint,QPointF,Qt
    from PySide6.QtWidgets import QScroller
    from PySide6.QtTest import QTest
    from touch_input import Fingers
    from continuous_acceptance import rss_tree
    w=window;area=w.continuous_area;scroller=area.kinetic.scroller
    checks={};traces={};scheduling_before=w.worker.scheduling_stats()
    def wait(condition,seconds=30):
        deadline=time.monotonic()+seconds
        while True:
            app.processEvents()
            assert not w.last_error,w.last_error
            if condition():return
            if time.monotonic()>deadline:raise TimeoutError('Kinetic acceptance deadline')
            time.sleep(.005)
    def pause(seconds):
        until=time.monotonic()+seconds;wait(lambda:time.monotonic()>=until)
    def ready():return w.last_displayed==w.page_index and set(area.wanted_pages())<=set(area.cache)
    def reset():
        w.set_view_mode('CONTINUOUS_SCROLL');w.set_fit('width')
        area.go_to(w.page_count//4);wait(ready);pause(.25)
    reset();finger=Fingers(area.viewport())
    finger.press(400,650);pause(.08);finger.move(400,620);pause(.1)
    before=area.scroll_top();finger.move(400,570);pause(.3)
    assert abs(area.scroll_top()-before-50)<=2
    finger.release(400,570);position=area.scroll_top();pause(.15)
    assert scroller.state()==QScroller.State.Inactive and area.scroll_top()==position
    checks['TOUCH_CONTINUOUS_DRAG']='PASS'
    def fling(label,distance,duration):
        reset();start=area.scroll_top();start_page=w.page_index
        maximum=area.verticalScrollBar().maximum();generation=area.generation
        finger.flick((400,650),(420,650-distance),duration_ms=duration)
        release=area.scroll_top();begin=time.monotonic();samples=[]
        while True:
            app.processEvents();stats=area.stats()
            assert stats['scrollbar_maximum']==maximum and stats['generation']==generation
            assert set(stats['rendered_pages'])<=set(stats['allowed_pages'])
            assert stats['raster_bytes']<=area.MAX_RASTER_BYTES
            row={'seconds':time.monotonic()-begin,'position':area.scroll_top(),
                 'velocity':scroller.velocity().y(),'state':scroller.state().name,
                 'page':w.page_index,'center_page':area.anchor()[0],
                 'stats':stats,'scheduling':w.worker.scheduling_stats(),'rss':rss_tree()}
            assert row['scheduling']['queued']<=len(area.visible_pages())+2
            samples.append(row)
            if scroller.state()==QScroller.State.Inactive:break
            assert time.monotonic()-begin<10
            pause(.025)
        assert all(a['position']<=b['position'] for a,b in zip(samples,samples[1:]))
        assert all(b['velocity']<=a['velocity']+.01 for a,b in zip(samples,samples[1:]))
        assert w.page_index==area.anchor()[0]
        traces[label]={'start_position':start,'release_position':release,'start_page':start_page,
                       'end_page':w.page_index,'travel_after_release':area.scroll_top()-release,'samples':samples}
        (destination/'kinetic-traces.json').write_text(json.dumps(traces,indent=2),encoding='utf-8')
        return traces[label]
    short=fling('short',90,350)
    medium=fling('medium',300,220)
    physical_speed=fling('physical_speed',300,100)
    strong=fling('strong',550,100)
    assert short['travel_after_release']<medium['travel_after_release']<strong['travel_after_release']
    assert strong['end_page']-strong['start_page']>=3
    # Include a shorter flick at the speeds observed on the actual screen,
    # instead of proving multi-page motion only with a huge synthetic swipe.
    assert physical_speed['travel_after_release']>area.viewport().height()*3
    assert physical_speed['end_page']-physical_speed['start_page']>=3
    assert strong['travel_after_release']>area.viewport().height()*3
    assert len({s['page'] for s in strong['samples']})>=3
    assert strong['samples'][0]['velocity']>strong['samples'][len(strong['samples'])//2]['velocity']>0
    checks.update(TOUCH_FLING_SCROLL='PASS',TOUCH_FLING_MULTI_PAGE='PASS',TOUCH_FLING_DECELERATION='PASS',
                  CURRENT_PAGE_DURING_FLING='PASS',LAZY_RENDERING_DURING_FLING='PASS',SCROLLBAR_GEOMETRY='PASS')
    reset();finger.flick((400,650),(420,100));pause(.1)
    assert scroller.state()==QScroller.State.Scrolling
    finger.press(400,200);position=area.scroll_top();pause(.15)
    assert scroller.state()==QScroller.State.Pressed and area.scroll_top()==position
    for step in range(1,9):
        pause(.016);finger.move(400,200+step*50)
    finger.release(400,600);position=area.scroll_top();pause(.15)
    assert area.scroll_top()<position and scroller.velocity().y()<0
    checks['TOUCH_TOUCH_TO_STOP']=checks['TOUCH_REVERSE_FLING']='PASS'
    for operation in ('pinch','double_tap'):
        reset();finger.flick((400,650),(420,100));pause(.1)
        assert scroller.state()==QScroller.State.Scrolling
        zoom=w.zoom
        if operation=='pinch':finger.pinch((420,350),80,110)
        else:finger.double_tap((420,350))
        assert scroller.state()==QScroller.State.Inactive
        wait(ready);pause(.3);position=area.scroll_top();pause(.1)
        assert area.scroll_top()==position
        if operation=='pinch':assert w.zoom>zoom
        else:assert w.zoom==1 and w.fit_mode is None
        checks['PINCH_CANCELS_FLING' if operation=='pinch' else 'DOUBLE_TAP_CANCELS_FLING']='PASS'
    reset();position=area.scroll_top();viewport=area.viewport()
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=QPoint(400,650))
    QTest.mouseMove(viewport,QPoint(400,100));QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=QPoint(400,100))
    pause(.15);assert area.scroll_top()==position and scroller.state()==QScroller.State.Inactive
    checks['MOUSE_DRAG_NO_FLING']='PASS'
    # An additional burst of page anchors proves old pending requests are
    # replaced, including on machines that render faster than the fling.
    before_burst=w.worker.scheduling_stats()
    for index in range(0,w.page_count,max(1,w.page_count//30)):
        area.go_to(index)
        assert w.worker.scheduling_stats()['queued']<=len(area.visible_pages())+2
    wait(ready)
    after=w.worker.scheduling_stats()
    assert after['queued_cancelled']>before_burst['queued_cancelled']
    checks['STALE_RENDER_CANCELLATION']='PASS'
    rows=[s for trace in traces.values() for s in trace['samples']]
    measurements={'scope':'native fling traces, short/medium/physical_speed/strong; RSS includes live GUI/worker processes',
                  'MAX_RENDERED_PAGE_IMAGES':max(len(s['stats']['rendered_pages']) for s in rows),
                  'RASTER_CACHE_MIB':max(s['stats']['raster_bytes'] for s in rows)/1024**2,
                  'TOTAL_RSS':max(s['rss']['total_bytes'] for s in rows if s['rss']) if any(s['rss'] for s in rows) else None,
                  'MAX_RENDER_QUEUE':max(s['scheduling']['queued'] for s in rows),
                  'STALE_RENDER_REQUESTS_CANCELLED':after['queued_cancelled']-scheduling_before['queued_cancelled'],
                  'INFLIGHT_RESULTS_DISCARDED':after['inflight_results_discarded']-scheduling_before['inflight_results_discarded'],
                  'cancellation_before_anchor_burst':before_burst['queued_cancelled']-scheduling_before['queued_cancelled'],
                  'cancellation_in_anchor_burst':after['queued_cancelled']-before_burst['queued_cancelled']}
    result={'checks':checks,'measurements':measurements,'traces':traces,
            'PHYSICAL_TOUCH_TEST':'NOT_PROVEN','input':'Qt QTest touchscreen events'}
    (destination/'kinetic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'checks':checks,'measurements':measurements}
