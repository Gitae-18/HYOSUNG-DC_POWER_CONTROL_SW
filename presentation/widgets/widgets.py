"""공통 위젯 — 디자인 샘플 스타일(패널 제목줄, 카드, 검정 툴바, 칩 버튼).

2026-11-02 변경: 메뉴를 아이콘+글자 바로가기로, Power 카드에 채널 색 표시, 챔버 값 상자 축소 + 아이콘, 편차 막대 추가.
2026-11-13 변경
- Panel: 제목줄 오른쪽 버튼(최소화/최대화) 추가 — add_title_button()
- 표 체크박스 열 공통 함수(check_item / checked_rows / setup_check_column) — Ctrl·Shift 다중 선택 대신 체크박스
- TimeDelegate: 표의 시간 칸을 숫자만 연속 입력(hhmmss)
- PowerCard: 전압·전류 소수점 2자리, 전류 기준 Min/Max 표시, 기준 이탈 알람(빨간 카드, 해제 전까지 유지)
2026-11-16 변경
- PowerCard 에 전류 편차 막대(CurrentBar): 측정 전류 − 설정 전류, 초록 구간 = 전류 기준 Min~Max, 안정/이상 표시
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal, QSize, QRectF, QRegularExpression
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QMessageBox,
                               QGridLayout, QSizePolicy, QToolButton, QTableWidget, QTableWidgetItem,
                               QStyledItemDelegate, QLineEdit)

from presentation.styling import theme as T
from presentation.styling import resources as R


class Panel(QFrame):
    """회색 제목줄 + 본문 패널. 제목줄 오른쪽에 버튼(최소화·최대화 등)을 붙일 수 있다."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QFrame(objectName="panelHead")
        self._hl = QHBoxLayout(self.head)
        self._hl.setContentsMargins(0, 0, 4, 0)
        self._hl.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("panelTitle")
        self._hl.addWidget(self.title, 1)
        lay.addWidget(self.head)
        self.body = QWidget()
        lay.addWidget(self.body, 1)

    def add_title_button(self, text: str, tip: str, slot) -> QToolButton:
        b = QToolButton(objectName="panelBtn")
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        self._hl.addWidget(b)
        return b

    def body_layout(self, kind="v", margins=8, spacing=6):
        lay = QVBoxLayout(self.body) if kind == "v" else (QHBoxLayout(self.body) if kind == "h" else QGridLayout(self.body))
        lay.setContentsMargins(margins, margins, margins, margins)
        lay.setSpacing(spacing)
        return lay


# ---------------------------------------------------------------- 표 체크박스 열 (2026-11-13)
def check_item(checked=False, enabled=True) -> QTableWidgetItem:
    it = QTableWidgetItem()
    it.setFlags(Qt.ItemIsUserCheckable | (Qt.ItemIsEnabled if enabled else Qt.NoItemFlags))
    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    return it


def checked_rows(tbl: QTableWidget, col: int) -> list[int]:
    out = []
    for r in range(tbl.rowCount()):
        it = tbl.item(r, col)
        if it is not None and it.checkState() == Qt.Checked and it.flags() & Qt.ItemIsEnabled:
            out.append(r)
    return out


def set_all_checked(tbl: QTableWidget, col: int, on: bool | None = None):
    """on=None 이면 모두 체크돼 있을 때 해제, 아니면 모두 체크 (사용 가능한 행만)."""
    rows = [r for r in range(tbl.rowCount()) if tbl.item(r, col) is not None and tbl.item(r, col).flags() & Qt.ItemIsEnabled]
    if on is None:
        on = not all(tbl.item(r, col).checkState() == Qt.Checked for r in rows) if rows else True
    for r in rows:
        tbl.item(r, col).setCheckState(Qt.Checked if on else Qt.Unchecked)


def setup_check_column(tbl: QTableWidget, col: int, row_click=False, on_change=None):
    """체크박스 열: 머리글 클릭 = 전체 선택/해제, row_click=True 면 행의 다른 칸을 눌러도 체크 전환.

    Ctrl·Shift 키 없이 마우스만으로 여러 행을 고른다 (2026-11-13 요구사항).
    """
    hi = tbl.horizontalHeaderItem(col)
    if hi is not None:
        hi.setToolTip("머리글 클릭 = 전체 선택 / 해제")
    tbl.horizontalHeader().sectionClicked.connect(
        lambda c: (set_all_checked(tbl, col), on_change and on_change()) if c == col else None)
    if row_click:
        def _click(r, c):
            it = tbl.item(r, col)
            if c != col and it is not None and it.flags() & Qt.ItemIsEnabled:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        tbl.cellClicked.connect(_click)
    if on_change:
        tbl.itemChanged.connect(lambda it: on_change() if it.column() == col else None)


class TimeEdit(QLineEdit):
    """시간 입력칸 — 누르면 숫자만(hhmmss) 전체 선택되어 바로 연속 입력, 입력을 마치면 HH:MM:SS 로 표시."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:]{0,10}"), self))
        self.setPlaceholderText("hhmmss")
        self.setToolTip("숫자만 연속 입력: hhmmss (예: 013000 = 1시간 30분, 1000000 = 100시간)")
        self.editingFinished.connect(self._format)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.setText(self.text().replace(":", ""))
        self.selectAll()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.selectAll)       # 마우스 클릭으로 들어와도 전체 선택

    def _format(self):
        from domain.models.function_model import parse_hms, hms
        try:
            sec = parse_hms(self.text())
        except ValueError:
            return
        if sec is not None:
            self.blockSignals(True)
            self.setText(hms(sec))
            self.blockSignals(False)


class TimeDelegate(QStyledItemDelegate):
    """시간 칸 편집기 — 숫자만 연속 입력 (hhmmss). 콜론 없이 "013000" = 01:30:00.

    편집을 시작하면 기존 값이 숫자만(예: 013000) 전체 선택된 상태로 열리므로, 바로 6자리를 치면 바뀐다.
    """

    def createEditor(self, parent, option, index):
        e = QLineEdit(parent)
        e.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:]{0,10}|—|-"), e))
        e.setPlaceholderText("hhmmss")
        e.setToolTip("숫자만 연속 입력: hhmmss (예: 013000 = 1시간 30분, 500 = 5분) · 빈칸 = 사용 안 함")
        return e

    def setEditorData(self, editor, index):
        txt = str(index.data() or "")
        editor.setText("" if txt in ("—", "-") else txt.replace(":", ""))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip())


def label(text="", sub=False, size=None, bold=False, color=None, align=None) -> QLabel:
    l = QLabel(text)
    css = []
    if sub:
        css.append(f"color:{T.SUB};")
    if color:
        css.append(f"color:{color};")
    if size:
        css.append(f"font-size:{size}pt;")
    if bold:
        css.append("font-weight:bold;")
    if css:
        l.setStyleSheet("".join(css))
    if align:
        l.setAlignment(align)
    return l


def button(text, primary=False, chip=False, checkable=False, min_w=None, h=None, slot=None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if chip:
        b.setProperty("chip", True)
    b.setCheckable(checkable)
    if min_w:
        b.setMinimumWidth(min_w)
    if h:
        b.setFixedHeight(h)
    if slot:
        b.clicked.connect(slot)
    return b


def set_state(w: QWidget, prop: str, val):
    w.setProperty(prop, val)
    w.style().unpolish(w)
    w.style().polish(w)


class BlackToolbar(QFrame):
    """메인 메뉴 — 검정 줄 + 아이콘·글자 바로가기 (디자인 샘플의 노란 글자 유지).

    items: [(글자, 아이콘 이름, 동작), ...]  · 아이콘 이름은 resources._SVG 참고
    """

    def __init__(self, items: list[tuple[str, str, callable]], parent=None):
        super().__init__(parent)
        self.setObjectName("toolbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)
        self.buttons = {}
        for i, (text, icon_name, slot) in enumerate(items):
            if i:
                s = QFrame()
                s.setObjectName("vsep")
                s.setFixedSize(1, 40)
                lay.addWidget(s)
            b = QToolButton()
            b.setObjectName("shortcut")
            b.setText(text)
            b.setIcon(R.icon(icon_name, T.YEL, 28))
            b.setIconSize(QSize(28, 28))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setMinimumWidth(96)
            b.setFixedHeight(62)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(text)
            b.clicked.connect(slot)
            lay.addWidget(b)
            self.buttons[text] = b
        lay.addStretch(1)
        self.right = QLabel()
        self.right.setStyleSheet(f"color:{T.SUB}; font-size:11pt;")
        lay.addWidget(self.right)


class PowerCard(QFrame):
    """B 영역 Power 상태 카드 — 채널명·상태 / 전압·전류 큰 숫자 / OCP·STEP·CYC·경과."""
    clicked = Signal(int)
    double_clicked = Signal(int)

    def __init__(self, ch: int, parent=None):
        super().__init__(parent)
        self.ch = ch
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        g = QGridLayout(self)
        g.setContentsMargins(12, 8, 12, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)
        self.lbl_name = QLabel()
        self.lbl_status = QLabel()
        self.lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        g.addWidget(self.lbl_name, 0, 0, 1, 2)
        g.addWidget(self.lbl_status, 0, 2, 1, 2)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BRD};")
        g.addWidget(line, 1, 0, 1, 4)
        g.addWidget(label("전압 (V)", sub=True, size=9), 2, 0, 1, 2)
        g.addWidget(label("전류 (A)", sub=True, size=9), 2, 2, 1, 2)
        self.lbl_v, self.lbl_a = QLabel("0.00"), QLabel("0.00")
        for l in (self.lbl_v, self.lbl_a):
            l.setObjectName("big")
        g.addWidget(self.lbl_v, 3, 0, 1, 2)
        g.addWidget(self.lbl_a, 3, 2, 1, 2)
        line2 = QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet(f"background:{T.BRD};")
        g.addWidget(line2, 4, 0, 1, 4)
        self.lbl_ocp, self.lbl_step, self.lbl_cyc = QLabel(), QLabel(), QLabel()
        self.lbl_el = QLabel()
        for i, l in enumerate((self.lbl_ocp, self.lbl_step, self.lbl_cyc)):
            g.addWidget(l, 5, i if i < 2 else 2, 1, 1 if i < 2 else 2)
        # 2026-11-13: 전류 기준 Min/Max + 기준 이탈 알람 (알람 전체 해제 전까지 유지)
        self.lbl_lim = QLabel()
        self.lbl_ialarm = QLabel()
        self.lbl_ialarm.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 2026-11-16: 전류 편차 막대 (설정 전류 기준, 허용 범위 = 전류 기준 Min~Max)
        self.lbl_dev = label("전류 편차", sub=True, size=9)
        self.bar_i = CurrentBar()
        g.addWidget(self.lbl_dev, 6, 0, 1, 1)
        g.addWidget(self.bar_i, 6, 1, 1, 3)
        g.addWidget(self.lbl_lim, 7, 0, 1, 2)
        g.addWidget(self.lbl_ialarm, 7, 2, 1, 2)
        g.addWidget(self.lbl_el, 8, 0, 1, 4)

    @staticmethod
    def _kv(k, v, color=T.TXT):
        return f"<span style='color:{T.SUB}'>{k}</span> <span style='color:{color}'>{v}</span>"

    def update_view(self, d: dict, selected: bool):
        err = d["status"] == "ERR"
        col = T.CH_COLORS[self.ch]
        # ■ = 그래프에서 이 Power 를 그리는 색
        self.lbl_name.setText(f"<span style='color:{col}'>■</span> <b>P{d['ch']:02d}</b>&nbsp;&nbsp;"
                              f"<span style='color:{T.SUB}'>{d['name']}</span>")
        self.lbl_status.setText(T.status_html(d["status"]))
        # 2026-11-13: 전압·전류 소수점 2자리
        self.lbl_v.setText("---" if err else f"{d['v']:.2f}")
        self.lbl_a.setText("---" if err else f"{d['a']:.2f}")
        ocp = d["ocp"]
        warn = ocp > 0 and d["a"] >= 0.8 * ocp
        self.lbl_ocp.setText(self._kv("OCP", "---" if err else f"{ocp:.2f}", T.YEL if warn else T.TXT))
        self.lbl_step.setText(self._kv("STEP", d["step"]))
        self.lbl_cyc.setText(self._kv("CYC", d["cycle"]))
        lo, hi = d.get("i_min"), d.get("i_max")
        ial = d.get("i_alarm") or ""
        if lo is None and hi is None:
            self.lbl_lim.setText(self._kv("전류 기준", "미설정", T.DIM))
        else:
            rng = f"{'—' if lo is None else f'{lo:.2f}'} ~ {'—' if hi is None else f'{hi:.2f}'} A"
            self.lbl_lim.setText(self._kv("전류 기준", rng, T.RED if ial else T.TXT))
        self.lbl_ialarm.setText(f"<span style='color:{T.RED}'><b>⚠ 기준 이탈</b></span>" if ial else "")
        self.bar_i.set_current(None if err else d["a"], d["cc"], lo, hi, bool(d.get("on")) and not err)
        self.bar_i.setToolTip(f"측정 {d['a']:.2f} A − 설정 {d['cc']:.2f} A = 편차 {d['a'] - d['cc']:+.2f} A\n"
                              "초록 구간 = 전류 기준 Min ~ Max (허용 범위)")
        self.lbl_ialarm.setToolTip(ial)
        self.setToolTip(f"전류 기준 이탈: {ial}\n메인 화면 [알람 전체 해제] 로 해제" if ial else "")
        el = "COM 응답 없음 · 재연결 중" if err else d["elapsed"]
        fn = f"   <span style='color:{T.SUB}'>{d['func']}</span>" if d["func"] else ""
        if ial:
            fn += f"   <span style='color:{T.RED}'>{html.escape(ial)}</span>"
        self.lbl_el.setText(self._kv("경과", el) + fn)
        set_state(self, "err", err)
        sel = selected and not err
        key = (sel, bool(ial))
        if key != getattr(self, "_sel", None):
            self._sel = key
            # 전류 기준 이탈 = 빨간 테두리·배경 (선택 표시보다 우선), 선택(그래프 표시) 중이면 테두리를 채널 색으로
            if ial:
                self.setStyleSheet(f"QFrame#card {{ border: 3px solid {T.RED}; background: #3A2226; }}")
            elif sel:
                self.setStyleSheet(f"QFrame#card {{ border: 2px solid {col}; }}")
            else:
                self.setStyleSheet("")

    def mousePressEvent(self, e):
        self.clicked.emit(self.ch)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit(self.ch)


class ValueBox(QFrame):
    """C 영역 값 상자 — 아이콘 + 제목 + 값 (높이를 줄여 편차 표시 공간 확보)."""

    def __init__(self, caption: str, icon_name: str | None = None, color: str = T.SUB, unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(5)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(R.pixmap(icon_name, color, 18))
            top.addWidget(ic)
        top.addWidget(label(caption, sub=True, size=10))
        top.addStretch(1)
        lay.addLayout(top)
        row = QHBoxLayout()
        self.val = QLabel("--.-")
        self.val.setObjectName("huge")          # 2026-11-04: 값 크게
        row.addWidget(self.val)
        row.addStretch(1)
        if unit:
            u = label(unit, sub=True, size=11)
            row.addWidget(u, 0, Qt.AlignBottom)
        lay.addLayout(row)
        lay.addStretch(1)

    def set(self, v):
        self.val.setText("---" if v is None else f"{v:.1f}")


class DeviationBar(QWidget):
    """편차(현재 − 설정) 막대 — 가운데 0 기준, 막대 길이·색으로 차이를 한눈에.

    |편차| <= tol      : 초록 '안정'
    |편차| <= 3 × tol  : 노랑 '접근 중'
    그 이상            : 빨강 '편차 큼'   (scale = 막대 끝 값)
    """

    decimals = 1
    text_w = 175

    def __init__(self, unit: str, tol: float, scale: float, parent=None):
        super().__init__(parent)
        self.unit, self.tol, self.scale = unit, tol, scale
        self.dev: float | None = None
        self.active = False
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, dev: float | None, active: bool):
        self.dev, self.active = dev, active
        self.update()

    def state(self):
        if self.dev is None:
            return T.DIM, "---"
        a = abs(self.dev)
        if not self.active:
            return T.GRY, "정지"
        if a <= self.tol:
            return T.GRN, "안정"
        if a <= 3 * self.tol:
            return T.YEL, "접근 중"
        return T.RED, "편차 큼"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text_w = self.text_w
        bw = max(80, w - text_w - 8)
        ty = h / 2 - 10
        track = QRectF(0, ty, bw, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.INP))
        p.drawRoundedRect(track, 4, 4)
        cx = bw / 2
        # 허용 범위(안정 구간) 표시
        band = self._band()
        if band is not None:
            bl = cx + max(-1.0, min(1.0, band[0] / self.scale)) * bw / 2
            br = cx + max(-1.0, min(1.0, band[1] / self.scale)) * bw / 2
            p.setBrush(QColor(76, 195, 138, 50))
            p.drawRect(QRectF(bl, track.top(), max(2.0, br - bl), track.height()))
        col, txt = self.state()
        if self.dev is not None:
            frac = max(-1.0, min(1.0, self.dev / self.scale))
            x2 = cx + frac * bw / 2
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(min(cx, x2), track.top() + 2, max(3.0, abs(x2 - cx)), track.height() - 4), 2, 2)
            p.setPen(QPen(QColor(col), 3))
            p.drawLine(int(x2), int(track.top() - 4), int(x2), int(track.bottom() + 4))
        p.setPen(QPen(QColor(T.SUB), 1))
        p.drawLine(int(cx), int(track.top() - 3), int(cx), int(track.bottom() + 3))
        # 눈금 글자: -scale · 0 · +scale
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        p.setPen(QColor(T.DIM))
        ly = track.bottom() + 3
        tk = self._tick(self.scale)
        p.drawText(QRectF(0, ly, 60, 14), Qt.AlignLeft | Qt.AlignTop, f"-{tk}")
        p.drawText(QRectF(cx - 30, ly, 60, 14), Qt.AlignHCenter | Qt.AlignTop, "0")
        p.drawText(QRectF(bw - 60, ly, 60, 14), Qt.AlignRight | Qt.AlignTop, f"+{tk}")
        # 값 · 판정
        f.setPointSizeF(12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(col))
        val = "---" if self.dev is None else f"{self.dev:+.{self.decimals}f} {self.unit}"
        p.drawText(QRectF(bw + 8, 0, 95, h), Qt.AlignVCenter | Qt.AlignRight, val)
        f.setPointSizeF(10)
        p.setFont(f)
        p.drawText(QRectF(bw + 110, 0, text_w - 102, h), Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.end()

    @staticmethod
    def _tick(v):
        return f"{v:.0f}" if v >= 10 else f"{v:g}"

    def _band(self):
        """안정 구간 (편차 기준 왼쪽, 오른쪽)."""
        return (-self.tol, self.tol)


class CurrentBar(DeviationBar):
    """Power 카드 전류 편차 막대 (2026-11-16) — 챔버 편차 표시와 같은 모양.

    편차 = 측정 전류 − 설정 전류(CC). 가운데 0 = 설정값, 초록 구간 = 전류 기준 Min ~ Max (허용 범위).
    측정 전류가 Min ~ Max 안 = 초록 '안정', 밖 = 빨강 '이상', 기준 없음 = 회색 '기준 없음', 출력 OFF = '정지'.
    """
    decimals = 2
    text_w = 158

    def __init__(self, parent=None):
        super().__init__("A", 0.0, 1.0, parent)
        self.setMinimumHeight(30)
        self.meas = self.setv = None
        self.lo = self.hi = None

    def set_current(self, meas, setv, lo, hi, active):
        self.meas, self.setv, self.lo, self.hi = meas, setv, lo, hi
        dev = None if meas is None or setv is None else meas - setv
        # 막대 끝 = 기준 구간이 들어가도록 (구간이 없으면 설정값의 20 %, 최소 1 A)
        ends = [abs(x - setv) for x in (lo, hi) if x is not None] if setv is not None else []
        self.scale = max(1.0, (max(ends) * 1.25) if ends else (abs(setv or 0) * 0.2))
        self.set(dev, active)

    def _band(self):
        if self.setv is None or (self.lo is None and self.hi is None):
            return None
        lo = (self.lo - self.setv) if self.lo is not None else -self.scale * 2
        hi = (self.hi - self.setv) if self.hi is not None else self.scale * 2
        return (lo, hi)

    def state(self):
        if self.dev is None:
            return T.DIM, "---"
        if not self.active:
            return T.GRY, "정지"
        if self.lo is None and self.hi is None:
            return T.SUB, "기준 없음"
        ok = (self.lo is None or self.meas >= self.lo) and (self.hi is None or self.meas <= self.hi)
        return (T.GRN, "안정") if ok else (T.RED, "이상")


def confirm(parent, title, text, ok="확인", cancel="취소", warn=False) -> bool:
    m = QMessageBox(parent)
    m.setWindowTitle(title)
    m.setText(text)
    m.setIcon(QMessageBox.Warning if warn else QMessageBox.Question)
    yes = m.addButton(ok, QMessageBox.AcceptRole)
    m.addButton(cancel, QMessageBox.RejectRole)
    m.exec()
    return m.clickedButton() is yes


def info(parent, title, text, warn=False):
    m = QMessageBox(parent)
    m.setWindowTitle(title)
    m.setText(text)
    m.setIcon(QMessageBox.Warning if warn else QMessageBox.Information)
    m.addButton("확인", QMessageBox.AcceptRole)
    m.exec()
