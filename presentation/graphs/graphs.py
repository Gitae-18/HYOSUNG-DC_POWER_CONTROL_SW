"""그래프 공통 조작 — 모든 그래프(메인 실시간, Result, Function 설정)에 같은 방법으로 적용.

마우스
  왼쪽 버튼 드래그  ↘ (왼쪽 위 → 오른쪽 아래) : 드래그한 영역으로 확대 (파란 상자)
  왼쪽 버튼 드래그  ↖ (오른쪽 아래 → 왼쪽 위) : 축소 = 직전 확대 이전 화면으로 (기록이 없으면 2배 축소) (빨간 상자)
  휠                                         : 확대 / 축소
  [이동] 버튼을 켜면 왼쪽 드래그가 화면 이동(Pan)으로 바뀜
버튼 (GraphTools)
  [＋ 확대] [－ 축소] [↶ 이전] [⤢ 전체] [✥ 이동] [⟲ 초기화] [JPEG 저장]

2026-11-13: [⟲ 초기화](on_reset 을 준 그래프), [JPEG 저장](save_widget 을 준 그래프) 추가.
2026-11-16: [⟲ 초기화] 를 모든 그래프에 — on_reset 이 없으면 처음 보기로 되돌림(확대·이동 해제, 전체 보기).

x_only=True (메인 실시간 그래프): 시간축만 확대·축소, 세로축은 항상 자동 맞춤.
"""
from __future__ import annotations

import pyqtgraph as pg
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtWidgets import QHBoxLayout, QWidget, QLabel, QFileDialog, QMessageBox

from presentation.styling import theme as T


class ZoomViewBox(pg.ViewBox):
    sigUserZoom = Signal()          # 사용자가 확대·축소·이동함 (실시간 따라가기 해제용)

    def __init__(self, x_only: bool = False, **kw):
        super().__init__(**kw)
        self.x_only = x_only
        self.history: list[list[list[float]]] = []
        self.pan_mode = False
        self.setMouseMode(self.RectMode)
        if x_only:
            self.setMouseEnabled(x=True, y=False)

    # ---- 기록
    def push(self):
        self.history.append(self.viewRange())
        del self.history[:-30]

    def back(self):
        if self.history:
            (x0, x1), (y0, y1) = self.history.pop()
            self.setXRange(x0, x1, padding=0)
            if not self.x_only:
                self.setYRange(y0, y1, padding=0)
        else:
            self.zoom(2.0, record=False)
        self.sigUserZoom.emit()

    def zoom(self, f: float, record: bool = True):
        """f < 1 확대, f > 1 축소 (화면 가운데 기준)."""
        if record:
            self.push()
        (x0, x1), (y0, y1) = self.viewRange()
        cx, wx = (x0 + x1) / 2, (x1 - x0) * f / 2
        self.setXRange(cx - wx, cx + wx, padding=0)
        if not self.x_only:
            cy, wy = (y0 + y1) / 2, (y1 - y0) * f / 2
            self.setYRange(cy - wy, cy + wy, padding=0)
        self.sigUserZoom.emit()

    def set_pan(self, on: bool):
        self.pan_mode = on
        self.setMouseMode(self.PanMode if on else self.RectMode)

    # ---- 마우스
    def wheelEvent(self, ev, axis=None):
        self.push()
        super().wheelEvent(ev, axis)
        self.sigUserZoom.emit()

    def mouseDragEvent(self, ev, axis=None):
        if self.pan_mode or ev.button() != Qt.LeftButton or axis is not None:
            super().mouseDragEvent(ev, axis)
            if self.pan_mode and ev.isFinish():
                self.sigUserZoom.emit()
            return
        ev.accept()
        p0, p1 = ev.buttonDownPos(), ev.pos()
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        zoom_in = dx > 0 if abs(dx) >= abs(dy) or self.x_only else dy > 0
        if not ev.isFinish():
            self.rbScaleBox.setPen(pg.mkPen(T.BLU if zoom_in else T.RED, width=1.5))
            self.rbScaleBox.setBrush(pg.mkBrush((78, 161, 243, 45) if zoom_in else (229, 83, 75, 45)))
            if self.x_only:      # 시간축만: 세로는 전체 높이로 표시
                r = self.boundingRect()
                self.updateScaleBox(pg.Point(p0.x(), r.top()), pg.Point(p1.x(), r.bottom()))
            else:
                self.updateScaleBox(p0, p1)
            return
        self.rbScaleBox.hide()
        if abs(dx) < 6 and (self.x_only or abs(dy) < 6):
            return                                      # 클릭 수준의 짧은 드래그는 무시
        if zoom_in:
            rect = self.childGroup.mapRectFromParent(QRectF(p0, p1).normalized())
            self.push()
            self.setXRange(rect.left(), rect.right(), padding=0)
            if not self.x_only:
                self.setYRange(rect.top(), rect.bottom(), padding=0)
        else:
            self.back()
            return
        self.sigUserZoom.emit()


def new_plot(x_only=False, date_axis=False, **kw) -> pg.PlotWidget:
    """ZoomViewBox 를 쓰는 검정 배경 그래프."""
    axis = {"bottom": pg.DateAxisItem()} if date_axis else {}
    p = pg.PlotWidget(viewBox=ZoomViewBox(x_only=x_only), axisItems=axis, **kw)
    p.setBackground("#000000")
    p.showGrid(x=True, y=True, alpha=0.25)
    return p


def default_image_dir() -> Path:
    from infrastructure.system.config import APP_DIR
    return APP_DIR / "Image"


def save_jpeg(widget: QWidget, parent: QWidget, name: str = "Graph", folder: Path | None = None) -> str | None:
    """그래프(위젯) 화면 그대로를 JPEG 로 저장. 저장한 경로를 돌려준다 (취소 = None)."""
    folder = Path(folder or default_image_dir())
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    default = folder / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    path, _ = QFileDialog.getSaveFileName(parent, "그래프 JPEG 저장", str(default), "JPEG (*.jpg *.jpeg)")
    if not path:
        return None
    if not path.lower().endswith((".jpg", ".jpeg")):
        path += ".jpg"
    ok = widget.grab().save(path, "JPG", 95)
    if not ok:
        QMessageBox.warning(parent, "JPEG 저장", f"저장하지 못했습니다.\n{path}")
        return None
    return path


class GraphTools(QWidget):
    """그래프 조작 버튼 묶음. on_fit 를 주면 [전체] 는 그 함수를 부른다 (메인 실시간 그래프의 따라가기 등).

    on_reset   : [⟲ 초기화] 버튼 동작 (주지 않으면 reset_view = 확대·이동 해제 후 전체 보기)
    save_widget: [JPEG 저장] 으로 찍을 위젯 (보통 PlotWidget 또는 그래프를 감싼 영역)
    """

    def __init__(self, vb: ZoomViewBox, on_fit=None, hint=True, parent=None, on_reset=None, save_widget=None,
                 save_name="Graph", on_saved=None):
        super().__init__(parent)
        from presentation.widgets.widgets import button
        self.vb, self.on_fit = vb, on_fit
        self.save_widget, self.save_name, self.on_saved = save_widget, save_name, on_saved
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        for txt, tip, fn in [("＋", "확대", lambda: vb.zoom(0.5)), ("－", "축소", lambda: vb.zoom(2.0)),
                             ("↶", "이전 화면 (드래그 ↖ 와 같음)", vb.back), ("⤢ 전체", "전체 보기", self.fit)]:
            b = button(txt, chip=True, min_w=30, slot=fn)
            b.setToolTip(tip)
            h.addWidget(b)
        self.btn_pan = button("✥ 이동", chip=True, checkable=True, slot=lambda: vb.set_pan(self.btn_pan.isChecked()))
        self.btn_pan.setToolTip("켜면 왼쪽 드래그로 화면 이동 · 끄면 드래그로 확대/축소")
        h.addWidget(self.btn_pan)
        self.btn_reset = self.btn_save = None
        self.btn_reset = button("⟲ 초기화", chip=True, slot=on_reset or self.reset_view)
        self.btn_reset.setToolTip("그래프 초기화 — 확대·이동을 풀고 처음 보기로")
        h.addWidget(self.btn_reset)
        if save_widget is not None:
            self.btn_save = button("JPEG 저장", chip=True, slot=self.save)
            self.btn_save.setToolTip("지금 보이는 그래프를 JPEG 이미지로 저장")
            h.addWidget(self.btn_save)
        if hint:
            lab = QLabel("드래그 ↘ 확대 · ↖ 축소")
            lab.setStyleSheet(f"color:{T.DIM}; font-size:8.5pt; padding-left:4px;")
            h.addWidget(lab)

    def fit(self):
        self.vb.history.clear()
        if self.on_fit:
            self.on_fit()
        else:
            self.vb.enableAutoRange()
            self.vb.autoRange()

    def reset_view(self):
        """확대·이동 해제 + [✥ 이동] 끄기 + 전체 보기."""
        self.btn_pan.setChecked(False)
        self.vb.set_pan(False)
        self.fit()

    def save(self):
        path = save_jpeg(self.save_widget, self.window(), self.save_name)
        if path and self.on_saved:
            self.on_saved(path)
        return path
