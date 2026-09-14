"""Virtual vertical page layout. Only viewport/prefetch pages own QPixmaps."""
from bisect import bisect_left, bisect_right
import math

from PySide6.QtCore import Qt, QRectF, Signal, QTimer, QEvent, QPointF, QSizeF
from PySide6.QtGui import QPainter, QColor, QImage, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea


class ContinuousArea(QAbstractScrollArea):
    render_requested = Signal(list, int)
    current_changed = Signal(int)
    page_ready = Signal(int)
    zoom_requested = Signal(int)
    resized = Signal()
    GAP = 18
    MARGIN = 12
    MAX_RASTER_BYTES = 64*1024*1024

    def __init__(self, parent=None, prefetch=1):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setAccessibleName('גלילה רציפה של עמודי הספר')
        self.prefetch=max(0,min(3,int(prefetch)))
        self.model={};self.pages=[];self.tops=[];self.bottoms=[];self.rects=[];self.scales=[]
        self.cache={};self.generation=0;self.active=False;self.current_page=0
        self.zoom=1.0;self.fit_mode='page';self.total_height=0;self.scroll_unit=1.0
        self.max_width=0;self._laying_out=False;self._last_desired=None;self.position_revision=0
        self.renders_received=0;self.renders_discarded=0;self.peak_raster_bytes=0
        self.refresh_timer=QTimer(self);self.refresh_timer.setSingleShot(True);self.refresh_timer.setInterval(15)
        self.refresh_timer.timeout.connect(self.refresh_visible)
        self.kinetic_scrolling=False
        from .kinetic import KineticScroll
        self.kinetic=KineticScroll(self)
        self.verticalScrollBar().sliderPressed.connect(self.kinetic.stop)
        self.horizontalScrollBar().sliderPressed.connect(self.kinetic.stop)

    def set_document(self,model):
        self.kinetic.stop()
        self.model=model;self.pages=model.get('pages',[])
        for page in self.pages:
            if not all(math.isfinite(page.get(k,0)) and page.get(k,0)>0 for k in ('width','height')):
                raise ValueError('Missing validated page layout dimensions')
        self.relayout(anchor=(0,.5))

    def activate(self,zoom,fit_mode,page):
        self.active=True;self.zoom=zoom;self.fit_mode=fit_mode;self.current_page=page
        self.relayout(anchor=(page,.5));self.go_to(page)

    def deactivate(self):
        self.kinetic.stop()
        self.active=False;self.generation+=1;self.cache.clear();self._last_desired=None
        self.refresh_timer.stop();self.render_requested.emit([],self.generation);self.viewport().update()

    def scroll_top(self):return self.verticalScrollBar().value()*self.scroll_unit

    def anchor(self,viewport_height=None):
        if not self.pages or not self.tops:return (0,.5)
        height=self.viewport().height() if viewport_height is None else viewport_height
        center=self.scroll_top()+height/2
        index=max(0,min(len(self.pages)-1,bisect_right(self.tops,center)-1))
        if center>self.bottoms[index] and index+1<len(self.pages):
            if center-self.bottoms[index]>self.tops[index+1]-center:index+=1
        return index,max(0.0,min(1.0,(center-self.tops[index])/max(1,self.bottoms[index]-self.tops[index])))

    def restore_anchor(self,anchor):
        if not self.pages:return
        index,fraction=anchor;index=max(0,min(len(self.pages)-1,index))
        top=self.tops[index]+fraction*(self.bottoms[index]-self.tops[index])-self.viewport().height()/2
        self.verticalScrollBar().setValue(round(top/self.scroll_unit))

    def reading_position(self, point):
        """Page and normalized coordinates under a viewport point (not pixels)."""
        y=self.scroll_top()+point.y()
        index=max(0,min(len(self.pages)-1,bisect_right(self.tops,y)-1))
        if y>self.bottoms[index] and index+1<len(self.pages):
            if y-self.bottoms[index]>self.tops[index+1]-y:index+=1
        width,height=self.rects[index]
        left=(max(self.max_width,self.viewport().width())-width)/2-self.horizontalScrollBar().value()
        return index,max(0,min(1,(point.x()-left)/width)),max(0,min(1,(y-self.tops[index])/height))

    def restore_reading_position(self, position, point):
        index,x,y=position;width,height=self.rects[index]
        left=(max(self.max_width,self.viewport().width())-width)/2
        self.horizontalScrollBar().setValue(round(left+x*width-point.x()))
        self.verticalScrollBar().setValue(round((self.tops[index]+y*height-point.y())/self.scroll_unit))
        self.refresh_visible()

    def configure(self,zoom,fit_mode):
        self.kinetic.stop()
        anchor=self.anchor();self.zoom=zoom;self.fit_mode=fit_mode;self.relayout(anchor)

    def relayout(self,anchor=None):
        if self._laying_out or not self.pages:return
        self._laying_out=True
        try:
            horizontal_center=(self.horizontalScrollBar().value()+self.viewport().width()/2)/max(1,self.max_width)
            anchor=anchor or self.anchor();self.generation+=1;self.cache.clear();self._last_desired=None
            available=max(1,self.viewport().width()-2*self.MARGIN)
            first_scale=available/self.pages[0]['width'] if self.fit_mode=='width' else self.zoom
            first_height=max(1,round(self.pages[0]['height']*first_scale))
            # Head/tail room lets Home/End center even very small pages.
            self.tops=[];self.bottoms=[];self.rects=[];self.scales=[]
            top=float(max(self.MARGIN,(self.viewport().height()-first_height)/2))
            widths=[]
            for page in self.pages:
                scale=available/page['width'] if self.fit_mode=='width' else self.zoom
                width=max(1,round(page['width']*scale));height=max(1,round(page['height']*scale))
                self.tops.append(top);self.bottoms.append(top+height);self.scales.append(scale)
                self.rects.append((width,height));widths.append(width);top+=height+self.GAP
            self.max_width=max(widths)+2*self.MARGIN
            self.total_height=top-self.GAP+max(self.MARGIN,(self.viewport().height()-self.rects[-1][1])/2)
            maximum=max(0,self.total_height-self.viewport().height())
            self.scroll_unit=max(1.0,maximum/2_000_000_000)
            self.verticalScrollBar().setRange(0,round(maximum/self.scroll_unit))
            self.verticalScrollBar().setPageStep(max(1,round(self.viewport().height()/self.scroll_unit)))
            self.verticalScrollBar().setSingleStep(max(1,round(40/self.scroll_unit)))
            self.horizontalScrollBar().setRange(0,max(0,self.max_width-self.viewport().width()))
            self.horizontalScrollBar().setPageStep(self.viewport().width())
            self.horizontalScrollBar().setValue(round(horizontal_center*self.max_width-self.viewport().width()/2))
            self.restore_anchor(anchor)
        finally:self._laying_out=False
        self.refresh_visible();self.viewport().update()

    def visible_pages(self):
        if not self.pages or not self.tops:return []
        top=self.scroll_top();bottom=top+self.viewport().height()
        first=min(len(self.pages)-1,bisect_right(self.bottoms,top))
        last=max(first+1,bisect_left(self.tops,bottom))
        return list(range(first,min(len(self.pages),last)))

    def wanted_pages(self):
        visible=self.visible_pages()
        if not visible:return []
        prefetch=0 if self.kinetic_scrolling else self.prefetch
        return list(range(max(0,visible[0]-prefetch),min(len(self.pages),visible[-1]+prefetch+1)))

    def go_to(self,page):
        if not self.pages:return
        self.kinetic.stop()
        page=max(0,min(len(self.pages)-1,page))
        fraction=min(.5,self.viewport().height()/(2*max(1,self.rects[page][1])))
        self.restore_anchor((page,fraction));self.refresh_visible()

    def raster_bytes(self):return sum(p.width()*p.height()*p.depth()//8 for p in self.cache.values())

    def refresh_visible(self):
        if not self.active or not self.pages:return
        wanted=set(self.wanted_pages());visible=set(self.visible_pages())
        for page in list(self.cache):
            if page not in wanted:del self.cache[page]
        current=self.anchor()[0]
        if current!=self.current_page:
            self.current_page=current;self.current_changed.emit(current)
        missing=sorted(wanted-set(self.cache),key=lambda p:(p not in visible,abs(p-current),p))
        requests=[(p,self.scales[p],self.devicePixelRatioF()) for p in missing]
        identity=(self.generation,tuple(requests))
        if identity!=self._last_desired:
            self._last_desired=identity;self.render_requested.emit(requests,self.generation)
        self.viewport().update()
        if current in self.cache:self.page_ready.emit(current)

    def accept_render(self,data):
        page=data['page']
        if not self.active or data.get('generation')!=self.generation or page not in self.wanted_pages():
            self.renders_discarded+=1;return False
        image=QImage(data['pixels'],data['width'],data['height'],data['stride'],QImage.Format.Format_RGB888)
        pixmap=QPixmap.fromImage(image);pixmap.setDevicePixelRatio(data['ratio'])
        needed=pixmap.width()*pixmap.height()*pixmap.depth()//8
        visible=set(self.visible_pages())
        # The renderer caps each page at 2048px. Release far prefetch frames first.
        for key in sorted(self.cache,key=lambda p:(p in visible,-abs(p-self.current_page))):
            if self.raster_bytes()+needed<=self.MAX_RASTER_BYTES:break
            del self.cache[key]
        if self.raster_bytes()+needed>self.MAX_RASTER_BYTES:
            self.renders_discarded+=1;return False
        self.cache[page]=pixmap;self.renders_received+=1
        self.peak_raster_bytes=max(self.peak_raster_bytes,self.raster_bytes())
        self.viewport().update()
        if page==self.current_page:self.page_ready.emit(page)
        return True

    def stats(self):
        return {'total_pages':len(self.pages),'placeholder_count':len(self.rects),
                'visible_pages':self.visible_pages(),'prefetch_pages':self.prefetch,
                'allowed_pages':self.wanted_pages(),'rendered_pages':sorted(self.cache),
                'raster_bytes':self.raster_bytes(),'peak_raster_bytes':self.peak_raster_bytes,
                'generation':self.generation,'renders_received':self.renders_received,
                'renders_discarded':self.renders_discarded,'kinetic_scrolling':self.kinetic_scrolling,
                'scrollbar_maximum':self.verticalScrollBar().maximum(),'logical_height':self.total_height}

    def paintEvent(self,event):
        painter=QPainter(self.viewport());painter.fillRect(self.viewport().rect(),QColor('#465264'))
        if not self.pages:return
        left=self.horizontalScrollBar().value();top=self.scroll_top();canvas=max(self.max_width,self.viewport().width())
        for index in self.visible_pages():
            width,height=self.rects[index];rect=QRectF((canvas-width)/2-left,self.tops[index]-top,width,height)
            painter.fillRect(rect,QColor('white'))
            pixmap=self.cache.get(index)
            if pixmap is not None:
                painter.drawPixmap(rect,pixmap,QRectF(pixmap.rect()))
            else:
                painter.setPen(QColor('#526071'));painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,f'עמוד {index+1}')

    def scrollContentsBy(self,dx,dy):
        if self._laying_out:return
        self.position_revision+=1
        # Expensive pages are released immediately; rendering requests coalesce.
        if self.active:
            wanted=set(self.wanted_pages())
            for page in list(self.cache):
                if page not in wanted:del self.cache[page]
        self.viewport().update()
        if not self.refresh_timer.isActive():self.refresh_timer.start()

    def viewportEvent(self,event):
        if event.type()==QEvent.Type.ScrollPrepare:
            if not self.active or not self.pages:
                event.ignore();return True
            event.setViewportSize(QSizeF(self.viewport().size()))
            event.setContentPosRange(QRectF(0,0,self.horizontalScrollBar().maximum(),
                                           self.verticalScrollBar().maximum()*self.scroll_unit))
            event.setContentPos(QPointF(self.horizontalScrollBar().value(),self.scroll_top()))
            event.accept();return True
        if event.type()==QEvent.Type.Scroll:
            if self.active:
                position=event.contentPos()
                self.horizontalScrollBar().setValue(round(position.x()))
                self.verticalScrollBar().setValue(round(position.y()/self.scroll_unit))
                if event.scrollState()==event.ScrollState.ScrollFinished:self.refresh_visible()
            event.accept();return True
        return super().viewportEvent(event)

    def resizeEvent(self,event):
        if hasattr(self,'kinetic'):self.kinetic.stop()
        # Qt has already resized the viewport when this handler runs. Use the
        # old height, including when zoom makes the horizontal bar appear.
        height=event.oldSize().height()
        anchor=self.anchor(height if height>0 else None);super().resizeEvent(event)
        generation=self.generation
        position_revision=self.position_revision
        if self.pages:
            # A later mode/zoom layout supersedes this resize. Restoring its
            # old anchor afterwards could move short recovered-image pages.
            def finish_resize():
                if self.generation==generation:
                    self.relayout(anchor if self.position_revision==position_revision else self.anchor())
            QTimer.singleShot(0,finish_resize)
        self.resized.emit()

    def wheelEvent(self,event):
        self.kinetic.stop()
        delta=event.pixelDelta().y() or event.angleDelta().y()
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            if delta:self.zoom_requested.emit(1 if delta>0 else -1)
            event.accept();return
        if not event.pixelDelta().isNull():
            self.verticalScrollBar().setValue(round((self.scroll_top()-event.pixelDelta().y())/self.scroll_unit))
            event.accept();return
        super().wheelEvent(event)
