"""팝업: POP-310 Test Power · POP-350 온/습도 설정 · POP-320 운영결과 조회 · POP-370 종료 · POP-380 환경 설정 · 알람.

2026-11-03: Setup 통신 탭을 실제 장비 기준으로 변경(NEX1200 COM1 / USP ×6 COM2 멀티드롭 주소), 통신 시험 창 추가.
2026-11-13: Test Power·Result 실행 이력의 다중 선택을 체크박스 열로 변경(Ctrl·Shift 삭제), Test Power 전류 기준 Min/Max,
            전압·전류 소수점 2자리, 온/습도 설정 시간 숫자 연속 입력(hhmmss), Result 그래프 JPEG 저장.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pyqtgraph as pg
from PySide6.QtCore import Qt, QDateTime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QDoubleSpinBox, QSpinBox, QRadioButton, QCheckBox,
                               QLineEdit, QDateTimeEdit, QComboBox, QTabWidget, QWidget, QButtonGroup, QFileDialog,
                               QInputDialog, QFormLayout, QListWidget, QPlainTextEdit)

from presentation.styling import theme as T
from infrastructure.persistence.datalog import ResultQuery
from domain.models.function_model import parse_hms
from presentation.widgets.widgets import Panel, button, label, confirm, info, set_state, check_item, checked_rows, setup_check_column, TimeEdit
from presentation.graphs.graphs import new_plot, GraphTools


def _table(headers, rows=0, stretch_last=True, select_rows=True, multi=False) -> QTableWidget:
    t = QTableWidget(rows, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    if select_rows:
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch if stretch_last else QHeaderView.Interactive)
    t.verticalHeader().setDefaultSectionSize(28)
    return t


def _item(text, color=None, align=Qt.AlignLeft | Qt.AlignVCenter):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    if color:
        it.setForeground(pg.mkColor(color))
    return it


# ====================================================================== 알람
class AlarmDialog(QDialog):
    _open: "AlarmDialog | None" = None

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("알람")
        self.setModal(False)
        self.resize(560, 260)
        v = QVBoxLayout(self)
        v.addWidget(label("알람 발생", bold=True, color=T.RED, size=12))
        self.lst = QListWidget()
        v.addWidget(self.lst, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(button("확인", primary=True, slot=self.close))
        v.addLayout(h)

    @classmethod
    def show_alarm(cls, parent, code, msg):
        if cls._open is None or not cls._open.isVisible():
            cls._open = AlarmDialog(parent)
        cls._open.lst.insertItem(0, f"{datetime.now():%H:%M:%S}  {code}  {msg}")
        cls._open.show()
        cls._open.raise_()


# ====================================================================== POP-310
class TestPowerDialog(QDialog):
    """2026-11-13: 채널 선택을 체크박스 열(No 다음)로 변경 — Ctrl·Shift 다중 선택 삭제.
    행 아무 곳이나 클릭해도 체크 전환, 머리글 [선택] 클릭 = 전체 선택/해제. 전류 기준 Min/Max 입력 추가."""
    COL_CHK = 1

    def __init__(self, parent, hub, cfg, preselect=None):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        self.setWindowTitle("Power Test")
        self.resize(940, 640)
        v = QVBoxLayout(self)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(button("On", primary=True, min_w=140, h=34, slot=lambda: self.power(True)))
        h.addWidget(button("Off", min_w=140, h=34, slot=lambda: self.power(False)))
        h.addStretch(1)
        v.addLayout(h)
        p = Panel("Power 목록  —  체크한 채널에 적용 · 행을 클릭해도 체크 전환 · 머리글 [선택] = 전체 선택/해제")
        pl = p.body_layout()
        self.tbl = _table(["No", "선택", "Name", "RS-485 주소", "상태", "CV (V)", "CC (A)", "전류 기준 (A)"], 6,
                          stretch_last=True)
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl.setFocusPolicy(Qt.NoFocus)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        self.tbl.setColumnWidth(0, 50)
        self.tbl.setColumnWidth(1, 56)
        for r in range(6):
            self.tbl.setItem(r, self.COL_CHK, check_item())
        setup_check_column(self.tbl, self.COL_CHK, row_click=True, on_change=self._sel_changed)
        pl.addWidget(self.tbl)
        self.lbl_sel = label("", sub=True)
        pl.addWidget(self.lbl_sel)
        v.addWidget(p, 1)
        f = QHBoxLayout()
        f.addWidget(QLabel("CV"))
        self.sp_v = QDoubleSpinBox(decimals=2, maximum=cfg.psu_v_max, singleStep=0.1, suffix=" V")
        self.sp_a = QDoubleSpinBox(decimals=2, maximum=cfg.psu_i_max, singleStep=0.1, suffix=" A")
        self.sp_v.setValue(14.0)
        self.sp_a.setValue(min(61.0, cfg.psu_i_max))
        f.addWidget(self.sp_v)
        f.addSpacing(20)
        f.addWidget(QLabel("CC"))
        f.addWidget(self.sp_a)
        f.addWidget(label(f"정격 0~{cfg.psu_v_max} V / 0~{cfg.psu_i_max} A", sub=True))
        f.addStretch(1)
        f.addWidget(button("Set Values", primary=True, min_w=140, slot=self.set_values))
        v.addLayout(f)
        # 전류 기준 Min/Max (2026-11-13)
        lp = Panel("전류 기준 Min / Max  —  출력 중 전류가 한 번이라도 벗어나면 메인 화면 카드에 알람 ([알람 전체 해제] 전까지 유지)")
        ll = lp.body_layout("h", 8, 8)
        self.lim = LimitInput(cfg)
        ll.addWidget(self.lim)
        ll.addStretch(1)
        ll.addWidget(button("기준 적용", primary=True, min_w=140, slot=self.set_limits))
        v.addWidget(lp)
        h2 = QHBoxLayout()
        h2.addStretch(1)
        h2.addWidget(button("Close", min_w=110, slot=self.accept))
        v.addLayout(h2)
        self._last = {}
        self.refresh(hub.snapshot())
        if preselect:
            pre = [preselect] if isinstance(preselect, int) else list(preselect)
            self._select(pre)
        self._sel_changed()
        hub.updated.connect(self.refresh)
        hub.info.connect(self._info)

    def _select(self, chs):
        self.tbl.blockSignals(True)
        for r in range(6):
            it = self.tbl.item(r, self.COL_CHK)
            on = (r + 1) in chs and bool(it.flags() & Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.tbl.blockSignals(False)

    def _sel_changed(self):
        chs = self.chs()
        self.lbl_sel.setText("선택: " + (", ".join(f"P{c:02d}" for c in chs) if chs else "없음"))
        if chs and getattr(self, "_lim_from", None) != chs[0]:
            d = self.hub.snapshot()["channels"][chs[0] - 1]
            self.lim.set(d.get("i_min"), d.get("i_max"))       # 첫 채널의 현재 기준을 입력칸에 보여줌
            self._lim_from = chs[0]

    def _info(self, msg):
        self.lbl_sel.setText(f"선택: {', '.join(f'P{c:02d}' for c in self.chs()) or '없음'}    ·    {msg}")

    def refresh(self, s):
        fmt = lambda x: "—" if x is None else f"{x:.2f}"
        for r, d in enumerate(s["channels"]):
            busy = bool(d["func"]) and d["status"] in ("RUN", "WAIT")
            port = "시뮬레이터" if self.cfg.simulate else f"{self.cfg.powers[r].address or r + 1}  ({self.cfg.psu_com})"
            lim = "미설정" if d.get("i_min") is None and d.get("i_max") is None else f"{fmt(d.get('i_min'))} ~ {fmt(d.get('i_max'))}"
            if d.get("i_alarm"):
                lim += "  ⚠"
            vals = {0: d["ch"], 2: d["name"], 3: port, 4: T.STATUS_TEXT.get(d["status"], d["status"]),
                    5: f"{d['cv']:.2f}", 6: f"{d['cc']:.2f}", 7: lim}
            self.tbl.blockSignals(True)
            for c, val in vals.items():
                col = T.STATUS.get(d["status"]) if c == 4 else (T.RED if c == 7 and d.get("i_alarm") else (T.DIM if busy else T.TXT))
                state = (str(val), col)
                if self._last.get((r, c)) == state:
                    continue                      # 바뀐 칸만 고친다
                self._last[(r, c)] = state
                it = self.tbl.item(r, c)
                if it is None:
                    it = _item("", align=Qt.AlignCenter if c == 0 else Qt.AlignLeft | Qt.AlignVCenter)
                    it.setFlags(Qt.ItemIsEnabled)
                    self.tbl.setItem(r, c, it)
                it.setText(str(val))
                it.setForeground(pg.mkColor(col))
            ck = self.tbl.item(r, self.COL_CHK)
            if self._last.get((r, "busy")) != busy:
                self._last[(r, "busy")] = busy
                if busy:
                    ck.setCheckState(Qt.Unchecked)
                ck.setFlags(Qt.ItemIsUserCheckable | (Qt.NoItemFlags if busy else Qt.ItemIsEnabled))
                ck.setToolTip("Function 실행 중 — Run Function 에서 정지하세요" if busy else "")
            self.tbl.blockSignals(False)

    def chs(self):
        return [r + 1 for r in checked_rows(self.tbl, self.COL_CHK)]

    def set_values(self):
        chs = self.chs()
        if not chs:
            return info(self, "Set Values", "채널을 체크하세요.")
        self.hub.call(self.hub.test_power_set, chs, self.sp_v.value(), self.sp_a.value())

    def set_limits(self):
        chs = self.chs()
        if not chs:
            return info(self, "전류 기준", "채널을 체크하세요.")
        lim = self.lim.values()
        if lim is None:
            return
        self.hub.call(self.hub.set_current_limits, chs, *lim)

    def power(self, on):
        chs = self.chs()
        if not chs:
            return info(self, "Power", "채널을 체크하세요.")
        names = ", ".join(f"P{c:02d}" for c in chs)
        if confirm(self, "Power " + ("On" if on else "Off"), f"{names} 출력을 {'켭니다' if on else '끕니다'}.", warn=on):
            self.hub.call(self.hub.test_power_on, chs, on)

    def done(self, r):
        for sig, slot in ((self.hub.updated, self.refresh), (self.hub.info, self._info)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(r)


class LimitInput(QWidget):
    """전류 기준 Min / Max 입력 (각각 체크해야 사용). Test Power · Run Function 공용 (2026-11-13)."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.cb_min, self.cb_max = QCheckBox("Min"), QCheckBox("Max")
        self.sp_min = QDoubleSpinBox(decimals=2, maximum=cfg.psu_i_max, singleStep=0.1, suffix=" A")
        self.sp_max = QDoubleSpinBox(decimals=2, maximum=cfg.psu_i_max, singleStep=0.1, suffix=" A")
        for cb, sp in ((self.cb_min, self.sp_min), (self.cb_max, self.sp_max)):
            cb.toggled.connect(sp.setEnabled)
            sp.setEnabled(False)
            sp.setMinimumWidth(110)
            h.addWidget(cb)
            h.addWidget(sp)
        h.addWidget(label("체크 해제 = 그 기준 사용 안 함", sub=True, size=9))

    def set(self, lo, hi):
        self.cb_min.setChecked(lo is not None)
        self.cb_max.setChecked(hi is not None)
        if lo is not None:
            self.sp_min.setValue(lo)
        if hi is not None:
            self.sp_max.setValue(hi)

    def values(self):
        """(min, max) — 사용 안 함은 None. 잘못되면 안내 후 None 반환."""
        lo = self.sp_min.value() if self.cb_min.isChecked() else None
        hi = self.sp_max.value() if self.cb_max.isChecked() else None
        if lo is not None and hi is not None and lo >= hi:
            info(self, "전류 기준", f"Min({lo:.2f} A)은 Max({hi:.2f} A)보다 작아야 합니다.", warn=True)
            return None
        return lo, hi


# ====================================================================== POP-350
class ChamberDialog(QDialog):
    def __init__(self, parent, hub, cfg, snap):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        c = (snap or {}).get("chamber") or {}
        self.running = c.get("running", False)
        self.setWindowTitle("온/습도 설정")
        self.resize(760, 520)
        v = QVBoxLayout(self)
        h = QHBoxLayout()
        h.addWidget(label("운전 방식", sub=True))
        self.rb_fix, self.rb_prog = QRadioButton("정치"), QRadioButton("프로그램")
        (self.rb_prog if c.get("mode") == "program" else self.rb_fix).setChecked(True)
        h.addWidget(self.rb_fix)
        h.addWidget(self.rb_prog)
        h.addStretch(1)
        v.addLayout(h)
        if self.running:
            self.rb_fix.setEnabled(False)
            self.rb_prog.setEnabled(False)
            v.addWidget(label("운전 중에는 운전 방식을 바꿀 수 없습니다.", sub=True, size=9))
        # 정치
        self.p_fix = Panel("정치 운전")
        g = self.p_fix.body_layout("g", 12, 8)
        (tl, th), (hl, hh) = cfg.nex_sp_t, cfg.nex_sp_h
        self.sp_t = QDoubleSpinBox(decimals=1, minimum=tl, maximum=th, singleStep=0.5, suffix=" ℃")
        self.sp_h = QDoubleSpinBox(decimals=1, minimum=hl, maximum=hh, singleStep=1, suffix=" %")
        self.sp_t.setValue(c.get("sp_t", 25.0))
        self.sp_h.setValue(c.get("sp_h", 50.0))
        self.no_h = QCheckBox("습도 제어 안 함")
        self.no_h.toggled.connect(self.sp_h.setDisabled)
        g.addWidget(QLabel("온도 SP"), 0, 0)
        g.addWidget(self.sp_t, 0, 1)
        g.addWidget(label(f"차단 {tl}~{th} · 경고 {cfg.chamber_t[0]}~{cfg.chamber_t[1]}", sub=True, size=9), 0, 2)
        g.addWidget(QLabel("습도 SP"), 1, 0)
        g.addWidget(self.sp_h, 1, 1)
        g.addWidget(self.no_h, 1, 2)
        self.timed = QCheckBox("시간설정 운전")
        self.dev = QDoubleSpinBox(decimals=1, minimum=0.0, maximum=200.0, singleStep=0.5, suffix=" ℃")
        self.dev.setValue(1.0)
        self.run_time = TimeEdit("10:00:00")           # 2026-11-13: 숫자만 연속 입력 (hhmmss)
        self.run_time.setPlaceholderText("hhmmss (최대 9999:59:59)")
        g.addWidget(self.timed, 2, 0)
        tr = QHBoxLayout()
        tr.addWidget(label("편차", sub=True))
        tr.addWidget(self.dev)
        tr.addWidget(label("시간", sub=True))
        tr.addWidget(self.run_time)
        g.addLayout(tr, 2, 1, 1, 2)
        for w in (self.sp_t, self.sp_h):
            w.valueChanged.connect(self.check_warn)
        self.run_time.textChanged.connect(self.check_warn)
        v.addWidget(self.p_fix)
        # 프로그램
        self.p_prog = Panel("프로그램 운전 (NEX1200 내부 패턴)")
        pg_ = self.p_prog.body_layout("h", 12, 8)
        pg_.addWidget(QLabel("패턴 번호"))
        self.pattern = QSpinBox(minimum=1, maximum=500)
        self.pattern.setValue(max(1, c.get("pattern", 1)))
        pg_.addWidget(self.pattern)
        pg_.addWidget(label("1~500 · 실험명·세그먼트 수는 NEX1200 에서 읽음", sub=True, size=9))
        pg_.addStretch(1)
        v.addWidget(self.p_prog)
        self.msg = label("", color=T.YEL)
        v.addWidget(self.msg)
        v.addStretch(1)
        b = QHBoxLayout()
        b.addStretch(1)
        b.addWidget(button("적용", min_w=110, slot=lambda: self.apply(False)))
        b.addWidget(button("적용 후 실행", primary=True, min_w=130, slot=lambda: self.apply(True)))
        b.addWidget(button("취소", min_w=100, slot=self.reject))
        v.addLayout(b)
        self.rb_fix.toggled.connect(self.sync)
        self.sync()
        self.check_warn()

    def sync(self):
        fix = self.rb_fix.isChecked()
        self.p_fix.setEnabled(fix)
        self.p_prog.setEnabled(not fix)

    def check_warn(self):
        w = []
        t, h = self.sp_t.value(), self.sp_h.value()
        ct, chh = self.cfg.chamber_t, self.cfg.chamber_h
        bad_t = not ct[0] <= t <= ct[1]
        bad_h = not self.no_h.isChecked() and not chh[0] <= h <= chh[1]
        set_state(self.sp_t, "warn", bad_t)
        set_state(self.sp_h, "warn", bad_h)
        if bad_t:
            w.append(f"온도 {t} ℃ 는 챔버 사양({ct[0]}~{ct[1]} ℃) 밖입니다.")
        if bad_h:
            w.append(f"습도 {h} % 는 챔버 사양({chh[0]}~{chh[1]} %) 밖입니다.")
        try:
            parse_hms(self.run_time.text())
            set_state(self.run_time, "invalid", False)
        except ValueError:
            set_state(self.run_time, "invalid", True)
            w.append("시간 형식이 올바르지 않습니다 (숫자 hhmmss, 분·초 59 이하).")
        self.msg.setText("\n".join(w))
        return w

    def apply(self, run_after):
        warns = self.check_warn()
        if self.rb_fix.isChecked():
            try:
                secs = parse_hms(self.run_time.text()) or 0
            except ValueError:
                return info(self, "입력 오류", "시간 형식이 올바르지 않습니다 (숫자 hhmmss, 분·초 59 이하).", warn=True)
            if secs > 9999 * 3600 + 59 * 60 + 59:
                return info(self, "입력 오류", "운전 시간은 최대 9999:59:59 입니다.", warn=True)
            if warns and not confirm(self, "챔버 사양 경고", "\n".join(warns) + "\n\n그래도 진행할까요?", ok="진행", warn=True):
                return
            humi = None if self.no_h.isChecked() else self.sp_h.value()
            self.hub.call(self.hub.chamber_apply, "fix", self.sp_t.value(), humi, 1, self.timed.isChecked(),
                          self.dev.value(), secs, run_after and not self.running)
        else:
            self.hub.call(self.hub.chamber_apply, "program", 0, None, self.pattern.value(), run_after=run_after and not self.running)
        self.accept()


# ====================================================================== POP-320
class ResultDialog(QDialog):
    PAGE = 100

    def __init__(self, parent, hub, cfg):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        self.q = ResultQuery(hub.data.root, hub.data)
        self.cols, self.rows, self.page = [], [], 0
        self.setWindowTitle("Result · 운영결과 조회")
        self.resize(1500, 900)
        h = QHBoxLayout(self)
        # ---------------- 조건
        left = Panel("조회 조건")
        ll = left.body_layout("v", 10, 8)
        self.mode_tabs = QTabWidget()
        # 기간 조회
        w1 = QWidget()
        f1 = QVBoxLayout(w1)
        now = QDateTime.currentDateTime()
        self.t0 = QDateTimeEdit(now.addSecs(-3600), calendarPopup=True, displayFormat="yyyy-MM-dd HH:mm")
        self.t1 = QDateTimeEdit(now, calendarPopup=True, displayFormat="yyyy-MM-dd HH:mm")
        f1.addWidget(label("조회 기간", sub=True))
        f1.addWidget(self.t0)
        f1.addWidget(label("~", sub=True, align=Qt.AlignCenter))
        f1.addWidget(self.t1)
        qb = QHBoxLayout()
        for txt, fn in [("오늘", self.q_today), ("어제", self.q_yesterday), ("최근 7일", self.q_week)]:
            qb.addWidget(button(txt, chip=True, slot=fn))
        f1.addLayout(qb)
        f1.addStretch(1)
        self.mode_tabs.addTab(w1, "기간 조회")
        # 실행 이력 조회 — DC Power 별 · Function 별 (질의 5, 2026-11-04 DB)
        w2 = QWidget()
        f2 = QVBoxLayout(w2)
        fr = QGridLayout()
        fr.addWidget(label("DC Power", sub=True), 0, 0)
        self.cb_power = QComboBox()
        self.cb_power.addItem("전체", 0)
        for i, p in enumerate(cfg.powers, 1):
            self.cb_power.addItem(f"P{i:02d} {p.name}", i)
        fr.addWidget(self.cb_power, 0, 1)
        fr.addWidget(label("Function", sub=True), 1, 0)
        self.cb_func = QComboBox()
        fr.addWidget(self.cb_func, 1, 1)
        fr.setColumnStretch(1, 1)
        f2.addLayout(fr)
        f2.addWidget(label("실행 이력 — 체크한 이력 조회 · 행 클릭 = 체크", sub=True))
        self.tbl_runs = _table(["선택", "시작", "종료", "Power", "Function", "상태"], stretch_last=False)
        self.tbl_runs.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_runs.setFocusPolicy(Qt.NoFocus)
        self.tbl_runs.horizontalHeader().setStretchLastSection(True)
        setup_check_column(self.tbl_runs, 0, row_click=True)
        f2.addWidget(self.tbl_runs, 1)
        self.mode_tabs.addTab(w2, "실행 이력 조회")
        ll.addWidget(self.mode_tabs, 1)
        ll.addWidget(label("추출 간격", sub=True))
        ib = QHBoxLayout()
        self.ig = QButtonGroup(self)
        for s, t in [(1, "1초"), (10, "10초"), (60, "1분"), (300, "5분")]:
            b = button(t, chip=True, checkable=True)
            b.setProperty("sec", s)
            b.setChecked(s == 10)
            self.ig.addButton(b)
            ib.addWidget(b)
        ll.addLayout(ib)
        ll.addWidget(label("대상", sub=True))
        tg = QGridLayout()
        self.cb_ch = {}
        for i in range(1, 7):
            cb = QCheckBox(f"P{i}")
            cb.setChecked(i <= 2)
            self.cb_ch[i] = cb
            tg.addWidget(cb, (i - 1) // 3, (i - 1) % 3)
        self.cb_chamber = QCheckBox("챔버")
        self.cb_chamber.setChecked(True)
        tg.addWidget(self.cb_chamber, 2, 0)
        ll.addLayout(tg)
        ll.addWidget(label("데이터 항목", sub=True))
        ig = QGridLayout()
        self.cb_item = {}
        for n, (k, on) in enumerate([("전압", True), ("전류", True), ("온도", True), ("습도", True), ("STEP/CYCLE", False), ("상태", False)]):
            cb = QCheckBox(k)
            cb.setChecked(on)
            self.cb_item[k] = cb
            ig.addWidget(cb, n // 2, n % 2)
        ll.addLayout(ig)
        ll.addWidget(button("조회", primary=True, h=36, slot=self.run_query))
        ll.addWidget(button("조건 초기화", chip=True, slot=self.reset))
        left.setFixedWidth(430)
        h.addWidget(left)
        # ---------------- 결과
        right = QVBoxLayout()
        pg_panel = Panel("추이 그래프")
        gl = pg_panel.body_layout()
        self.plot = new_plot(date_axis=True)
        self.plot.addLegend(offset=(10, 10))
        gt = QHBoxLayout()
        self.gtools = GraphTools(self.plot.getViewBox(), save_widget=self.plot, save_name="결과조회그래프")
        gt.addWidget(self.gtools)
        gt.addStretch(1)
        gl.addLayout(gt)
        gl.addWidget(self.plot, 1)
        right.addWidget(pg_panel, 5)
        pt = Panel("조회 결과")
        tl = pt.body_layout()
        self.tbl = _table([], stretch_last=True)
        tl.addWidget(self.tbl, 1)
        pr = QHBoxLayout()
        self.lbl_page = label("", sub=True)
        pr.addWidget(self.lbl_page)
        pr.addWidget(button("◀", chip=True, slot=lambda: self.goto(self.page - 1)))
        pr.addWidget(button("▶", chip=True, slot=lambda: self.goto(self.page + 1)))
        pr.addStretch(1)
        pr.addWidget(button("CSV 내보내기", slot=self.export))
        pr.addWidget(button("닫기", slot=self.accept))
        tl.addLayout(pr)
        right.addWidget(pt, 5)
        h.addLayout(right, 1)
        self._all_runs = self.q.runs()
        self.cb_func.addItem("전체")
        self.cb_func.addItems(sorted({r["function"] for r in self._all_runs}))
        self.cb_power.currentIndexChanged.connect(lambda *_: self.load_runs())
        self.cb_func.currentIndexChanged.connect(lambda *_: self.load_runs())
        self.load_runs()
        files = self.q.files()
        if files:
            span = f"{files[0].stem[-8:]} ~ {files[-1].stem[-8:]}"
            self.lbl_page.setText(f"DB 일별 파일 {len(files)}개 ({span})")

    # ---- quick ranges
    def _set(self, a: datetime, b: datetime):
        self.t0.setDateTime(QDateTime(a))
        self.t1.setDateTime(QDateTime(b))

    def q_today(self):
        n = datetime.now()
        self._set(n.replace(hour=0, minute=0, second=0, microsecond=0), n)

    def q_yesterday(self):
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._set(d - timedelta(days=1), d - timedelta(seconds=1))

    def q_week(self):
        n = datetime.now()
        self._set(n - timedelta(days=7), n)

    def reset(self):
        self.q_today()
        for i, cb in self.cb_ch.items():
            cb.setChecked(i <= 2)

    def load_runs(self, *_):
        ch = self.cb_power.currentData()
        fn = self.cb_func.currentText()
        runs = [r for r in self._all_runs if (not ch or r["ch"] == ch) and (fn in ("", "전체") or r["function"] == fn)]
        runs.sort(key=lambda r: r["start"], reverse=True)
        self._runs = runs
        self.tbl_runs.setRowCount(len(runs))
        for i, r in enumerate(runs):
            vals = [r["start"][5:16], (r["end"] or "")[5:16], f"P{r['ch']:02d}", r["function"], r["status"]]
            self.tbl_runs.setItem(i, 0, check_item())
            for c, v in enumerate(vals, 1):
                it = _item(v, T.GRN if c == 5 and r["status"] == "실행 중" else None)
                it.setFlags(Qt.ItemIsEnabled)
                self.tbl_runs.setItem(i, c, it)
        self.tbl_runs.resizeColumnsToContents()

    # ---- query
    def run_query(self):
        interval = self.ig.checkedButton().property("sec")
        items = [k for k, cb in self.cb_item.items() if cb.isChecked()]
        if self.mode_tabs.currentIndex() == 0:
            t0, t1 = self.t0.dateTime().toPython(), self.t1.dateTime().toPython()
            if t1 <= t0:
                return info(self, "조회", "종료 시각은 시작 시각보다 뒤여야 합니다.", warn=True)
            if interval == 1 and t1 - t0 > timedelta(days=1):
                if not confirm(self, "조회", "1초 간격으로 1일이 넘는 기간을 조회하면 오래 걸릴 수 있습니다. 계속할까요?"):
                    return
            chs = [i for i, cb in self.cb_ch.items() if cb.isChecked()]
            cols, rows = self.q.query(t0, t1, interval, chs, self.cb_chamber.isChecked(), items)
        else:
            sel = checked_rows(self.tbl_runs, 0)
            if not sel:
                return info(self, "조회", "실행 이력을 체크하세요.")
            runs = [self._runs[i] for i in sel]
            t0 = min(datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S") for r in runs)
            # 아직 실행 중인 이력은 지금 시각까지
            t1 = max(datetime.strptime(r["end"], "%Y-%m-%d %H:%M:%S") if r["end"] else datetime.now() for r in runs)
            cols, rows = self.q.query(t0, t1, interval, [r["ch"] for r in runs], self.cb_chamber.isChecked(), items, runs=runs)
        self.cols, self.rows = cols, rows
        if not rows:
            self.tbl.setRowCount(0)
            self.plot.clear()
            self.lbl_page.setText("조회 결과가 없습니다.")
            return
        self.draw()
        self.goto(0)

    def draw(self):
        """메인 화면과 같은 규칙: Power 번호별 색, 전압 실선 / 전류 파선, 온도 빨강 · 습도 회백색."""
        self.plot.clear()
        xs = [r[0].timestamp() for r in self.rows]
        for ci, c in enumerate(self.cols[1:], 1):
            ys = [r[ci] for r in self.rows]
            if not any(isinstance(y, (int, float)) for y in ys):
                continue
            if c.startswith("P"):
                col = T.CH_COLORS.get(int(c[1]), T.TXT)
                style = Qt.DashLine if c.endswith(" A") else Qt.SolidLine
            else:
                col, style = (T.TEMP_COLOR, Qt.SolidLine) if c.startswith("온도") else (T.HUMI_COLOR, Qt.DotLine)
            pts = [(x, y) for x, y in zip(xs, ys) if isinstance(y, (int, float))]
            self.plot.plot([p[0] for p in pts], [p[1] for p in pts], name=c, pen=pg.mkPen(col, width=1.8, style=style))
        self.gtools.fit()

    def goto(self, p):
        n = max(1, (len(self.rows) + self.PAGE - 1) // self.PAGE)
        self.page = max(0, min(p, n - 1))
        chunk = self.rows[self.page * self.PAGE:(self.page + 1) * self.PAGE]
        self.tbl.setColumnCount(len(self.cols))
        self.tbl.setHorizontalHeaderLabels(self.cols)
        self.tbl.setRowCount(len(chunk))
        for i, r in enumerate(chunk):
            self.tbl.setItem(i, 0, _item(r[0].strftime("%m-%d %H:%M:%S")))
            for c, v in enumerate(r[1:], 1):
                if isinstance(v, float):
                    col = self.cols[c]
                    v = f"{v:.2f}" if col.endswith((" V", " A")) else f"{v:.1f}"     # 2026-11-13: 전압·전류 2자리
                self.tbl.setItem(i, c, _item(v))
        self.lbl_page.setText(f"총 {len(self.rows):,}행 · {self.page + 1} / {n} 페이지")

    def export(self):
        if not self.rows:
            return info(self, "CSV 내보내기", "먼저 조회하세요.")
        iv = self.ig.checkedButton().text()
        name = f"결과조회_{self.rows[0][0]:%Y%m%d-%H%M}_{self.rows[-1][0]:%Y%m%d-%H%M}_{iv}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV 내보내기", name, "CSV (*.csv)")
        if path:
            self.q.export_csv(path, self.cols, self.rows)
            self.hub.log.write("USER", f"결과 조회 CSV 내보내기 {path}")


# ====================================================================== POP-370
class ExitDialog(QDialog):
    @staticmethod
    def ask(parent, snap):
        """None = 취소, True = 장비 정지 후 종료, False = 프로그램만 종료."""
        running = []
        if snap:
            for d in snap["channels"]:
                if d["status"] in ("RUN", "WAIT"):
                    running.append(f"P{d['ch']:02d} · {d['func'] or 'Test Power'} ({d['cycle']})")
            c = snap["chamber"]
            if c and c["running"]:
                running.append(f"챔버 · {'정치' if c['mode'] == 'fix' else '프로그램'} 운전 중 ({c['sp_t']:.1f} ℃ / {c['sp_h']:.1f} %)")
        if not running:
            return False if confirm(parent, "프로그램 종료", "종료하시겠습니까?", ok="종료") else None
        d = ExitDialog(parent, running)
        return d.result_value if d.exec() else None

    def __init__(self, parent, running):
        super().__init__(parent)
        self.setWindowTitle("프로그램 종료")
        self.resize(620, 360)
        v = QVBoxLayout(self)
        v.addWidget(label("▲  운전 중인 장비가 있습니다.", bold=True, color=T.YEL, size=12))
        lst = QListWidget()
        lst.addItems(["● " + r for r in running])
        v.addWidget(lst, 1)
        self.rb_keep = QRadioButton("장비는 계속 운전하고 프로그램만 종료  (Function 단계 진행·데이터 저장은 중단)")
        self.rb_stop = QRadioButton("모든 Power 출력 OFF, 챔버 정지 후 종료")
        self.rb_keep.setChecked(True)   # 미결 13: 기본값은 발주처 정책으로 결정
        v.addWidget(self.rb_keep)
        v.addWidget(self.rb_stop)
        h = QHBoxLayout()
        h.addStretch(1)
        b = button("종료", min_w=110, slot=self._ok)
        b.setStyleSheet(f"color:{T.RED};")
        h.addWidget(b)
        h.addWidget(button("취소", min_w=110, slot=self.reject))
        v.addLayout(h)
        self.result_value = False

    def _ok(self):
        self.result_value = self.rb_stop.isChecked()
        self.accept()


# ====================================================================== POP-380
class SettingsDialog(QDialog):
    @staticmethod
    def open_with_password(parent, hub, cfg):
        pw, ok = QInputDialog.getText(parent, "Setup", "관리자 암호", QLineEdit.Password)
        if not ok:
            return
        if pw != cfg.admin_password:
            return info(parent, "환경 설정", "암호가 올바르지 않습니다.", warn=True)
        SettingsDialog(parent, hub, cfg).exec()

    def __init__(self, parent, hub, cfg):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        self.setWindowTitle("Setup · 환경 설정")
        self.resize(820, 600)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs, 1)
        running = any(c.run for c in hub.ch.values())
        # 통신
        w = QWidget()
        wl = QVBoxLayout(w)
        pn = Panel("NEX1200 온습도 컨트롤러  —  RS-485 · MODBUS RTU")
        pl = pn.body_layout("h")
        self.nex_com = QLineEdit(cfg.nex_com)
        self.nex_addr = QSpinBox(minimum=0, maximum=255, value=cfg.nex_address)
        self.nex_baud = QComboBox()
        self.nex_baud.addItems(["9600", "19200", "38400"])
        self.nex_baud.setCurrentText(str(cfg.nex_baud))
        self.nex_par = QComboBox()
        self.nex_par.addItems(["N", "E", "O"])
        self.nex_par.setCurrentText(cfg.nex_parity)
        self.nex_stop = QComboBox()
        self.nex_stop.addItems(["1", "2"])
        self.nex_stop.setCurrentText(str(cfg.nex_stopbits))
        for lab, wd in [("COM", self.nex_com), ("주소", self.nex_addr), ("속도", self.nex_baud), ("패리티", self.nex_par),
                        ("정지비트", self.nex_stop)]:
            pl.addWidget(label(lab, sub=True))
            pl.addWidget(wd)
        pl.addStretch(1)
        wl.addWidget(pn)
        pp = Panel("USP DC Power ×6  —  RS-485 멀티드롭 · SCPI (CADDR 주소)")
        ppl = pp.body_layout()
        row = QHBoxLayout()
        self.psu_com = QLineEdit(cfg.psu_com)
        self.psu_baud = QComboBox()
        self.psu_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.psu_baud.setCurrentText(str(cfg.psu_baud))
        self.psu_par = QComboBox()
        self.psu_par.addItems(["N", "E", "O"])
        self.psu_par.setCurrentText(cfg.psu_parity)
        self.psu_term = QComboBox()
        self.psu_term.addItems(["LF (\\n)", "CR+LF (\\r\\n)", "CR (\\r)"])
        self.psu_term.setCurrentIndex({"\n": 0, "\r\n": 1, "\r": 2}.get(cfg.psu_terminator, 0))
        for lab, wd in [("COM", self.psu_com), ("속도", self.psu_baud), ("패리티", self.psu_par), ("끝 문자", self.psu_term)]:
            row.addWidget(label(lab, sub=True))
            row.addWidget(wd)
        row.addStretch(1)
        ppl.addLayout(row)
        self.tbl = _table(["No", "이름", "RS-485 주소", "사용"], 6)
        self.tbl.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        for i, p in enumerate(cfg.powers):
            self.tbl.setItem(i, 0, _item(i + 1))
            self.tbl.item(i, 0).setFlags(Qt.ItemIsEnabled)
            self.tbl.setItem(i, 1, _item(p.name))
            self.tbl.setItem(i, 2, _item(p.address or i + 1))
            self.tbl.setItem(i, 3, _item("사용" if p.enabled else "미사용"))
        ppl.addWidget(self.tbl)
        ppl.addWidget(label("이름·주소·사용은 더블클릭해서 고칩니다. 주소는 각 USP 장비 메뉴의 RS-485 주소와 같아야 합니다. "
                            "'미사용' 채널은 통신하지 않습니다.", sub=True, size=9))
        wl.addWidget(pp, 1)
        self.sim = QCheckBox("시뮬레이터로 동작 (장비 없이 시험) — 실제 장비 연결 시 해제")
        self.sim.setChecked(cfg.simulate)
        wl.addWidget(self.sim)
        self.trace = QCheckBox("통신 기록 남기기 (Log\\COMM_날짜.log · 현장 시험 때만 켜 두세요)")
        self.trace.setChecked(cfg.comm_trace)
        wl.addWidget(self.trace)
        if running:
            w.setEnabled(False)
            wl.addWidget(label("Function 실행 중에는 통신 설정을 바꿀 수 없습니다.", color=T.YEL))
        tabs.addTab(w, "통신")
        # 저장
        w2 = QWidget()
        f2 = QFormLayout(w2)
        self.data_dir = QLineEdit(cfg.data_dir)
        self.disk = QDoubleSpinBox(decimals=1, minimum=0.1, maximum=100, value=cfg.disk_warn_gb, suffix=" GB")
        f2.addRow("데이터 폴더", self.data_dir)
        f2.addRow("DB", label("DuckDB 일별 파일  ·  [데이터 폴더]\\DB\\연도\\월\\HyosungData_YYYYMMDD.duckdb", sub=True))
        f2.addRow("저장 주기", label("1초 (고정) — DC Power 출력 중 채널, 챔버 운전 중 또는 Power 출력 중일 때 자동 저장", sub=True))
        f2.addRow("디스크 경고 기준", self.disk)
        tabs.addTab(w2, "저장")
        # 그래프
        w3 = QWidget()
        f3 = QFormLayout(w3)
        self.g_min = QComboBox()
        self.g_min.addItems(["10", "30", "60"])
        self.g_min.setCurrentText(str(cfg.graph_default_min))
        f3.addRow("기본 표시 시간 (분)", self.g_min)
        tabs.addTab(w3, "그래프")
        # 관리
        w4 = QWidget()
        f4 = QFormLayout(w4)
        self.v_max = QDoubleSpinBox(decimals=2, maximum=1000, value=cfg.psu_v_max, suffix=" V")
        self.i_max = QDoubleSpinBox(decimals=2, maximum=1000, value=cfg.psu_i_max, suffix=" A")
        self.rate = QDoubleSpinBox(decimals=2, maximum=50, value=cfg.chamber_rate_max, suffix=" ℃/min")
        self.policy = QComboBox()
        self.policy.addItems(["A안: Segment 시간 우선", "B안: Step 시간 우선"])
        self.policy.setCurrentIndex(0 if cfg.step_policy == "segment" else 1)
        self.wait_lim = QSpinBox(minimum=1, maximum=1440, value=cfg.wait_limit_min, suffix=" 분")
        self.pw = QLineEdit(cfg.admin_password)
        self.pw.setEchoMode(QLineEdit.Password)
        f4.addRow("DC Power 정격 전압", self.v_max)
        f4.addRow("DC Power 정격 전류", self.i_max)
        f4.addRow("챔버 최대 변화율 (경고)", self.rate)
        f4.addRow("Step/Segment 불일치 처리", self.policy)
        f4.addRow("Wait 대기 한도 (AL-04)", self.wait_lim)
        f4.addRow("관리자 암호", self.pw)
        tabs.addTab(w4, "관리")
        h = QHBoxLayout()
        h.addWidget(button("통신 시험", slot=self.test))
        h.addStretch(1)
        h.addWidget(button("저장", primary=True, min_w=100, slot=self.save))
        h.addWidget(button("취소", min_w=100, slot=self.reject))
        v.addLayout(h)

    def test(self):
        CommTestDialog(self, self.hub, self.cfg).exec()

    def save(self):
        c = self.cfg
        addrs = []
        for i in range(len(c.powers)):
            try:
                a = int(self.tbl.item(i, 2).text())
            except ValueError:
                return info(self, "Setup", f"P{i + 1:02d} RS-485 주소가 숫자가 아닙니다.", warn=True)
            if self.tbl.item(i, 3).text() != "미사용":
                addrs.append(a)
        if len(addrs) != len(set(addrs)):
            return info(self, "Setup", "사용하는 Power 의 RS-485 주소가 겹칩니다. 멀티드롭에서는 주소가 모두 달라야 합니다.", warn=True)
        c.nex_com, c.nex_address = self.nex_com.text().strip(), self.nex_addr.value()
        c.nex_baud, c.nex_parity = int(self.nex_baud.currentText()), self.nex_par.currentText()
        c.nex_stopbits = int(self.nex_stop.currentText())
        c.psu_com, c.psu_baud = self.psu_com.text().strip(), int(self.psu_baud.currentText())
        c.psu_parity = self.psu_par.currentText()
        c.psu_terminator = ["\n", "\r\n", "\r"][self.psu_term.currentIndex()]
        for i, p in enumerate(c.powers):
            p.name = self.tbl.item(i, 1).text()
            p.address = int(self.tbl.item(i, 2).text())
            p.enabled = self.tbl.item(i, 3).text() != "미사용"
            self.hub.ch[i + 1].name = p.name
        c.simulate = self.sim.isChecked()
        c.comm_trace = self.trace.isChecked()
        c.data_dir, c.disk_warn_gb = self.data_dir.text(), self.disk.value()
        c.graph_default_min = int(self.g_min.currentText())
        c.psu_v_max, c.psu_i_max, c.chamber_rate_max = self.v_max.value(), self.i_max.value(), self.rate.value()
        c.step_policy = "segment" if self.policy.currentIndex() == 0 else "step"
        c.wait_limit_min, c.admin_password = self.wait_lim.value(), self.pw.text()
        c.save()
        self.hub.log.write("USER", "환경 설정 저장")
        info(self, "환경 설정", "저장했습니다.\n통신·데이터 폴더는 프로그램을 다시 시작하면 적용됩니다.")
        self.accept()


# ====================================================================== 통신 시험 (Setup › 통신 시험)
class CommTestDialog(QDialog):
    """현장 통신 시험용 — 장비를 하나 골라 식별/읽기/직접 명령을 보내고 결과를 본다.

    명령은 hub 작업 스레드에서 실행되므로(포트 충돌 없음) 프로그램이 동작 중이어도 쓸 수 있다.
    """

    def __init__(self, parent, hub, cfg):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        self.setWindowTitle("통신 시험")
        self.resize(940, 600)
        v = QVBoxLayout(self)
        sim = getattr(hub, "bus", None) is None
        mode = ("시뮬레이터로 실행 중 — 실제 장비와 통신하지 않습니다 (Setup 에서 시뮬레이터 해제 후 다시 시작)" if sim else
                f"NEX1200 {cfg.nex_com} (주소 {cfg.nex_address}, {cfg.nex_baud} bps)   ·   USP ×6 {cfg.psu_com} ({cfg.psu_baud} bps)")
        v.addWidget(label(mode, color=T.YEL if sim else T.SUB))
        row = QHBoxLayout()
        self.target = QComboBox()
        self.target.addItem("NEX1200 챔버", "NEX")
        for i, p in enumerate(cfg.powers, 1):
            self.target.addItem(f"P{i:02d} {p.name} (주소 {p.address or i})", f"P{i}")
        self.target.currentIndexChanged.connect(self._hint)
        row.addWidget(label("대상", sub=True))
        row.addWidget(self.target)
        self.quick = {}
        for txt, cmd in [("식별", "identify"), ("현재값 읽기", "read"), ("SP 범위", "limits"), ("알람 해제", "clear")]:
            b = button(txt, slot=lambda _=False, c=cmd: self.send(c))
            row.addWidget(b)
            self.quick[cmd] = b
        row.addStretch(1)
        v.addLayout(row)
        r2 = QHBoxLayout()
        r2.addWidget(label("직접 명령", sub=True))
        self.cmd = QLineEdit()
        self.cmd.returnPressed.connect(lambda: self.send(self.cmd.text().strip()))
        r2.addWidget(self.cmd, 1)
        r2.addWidget(button("보내기", primary=True, slot=lambda: self.send(self.cmd.text().strip())))
        v.addLayout(r2)
        self.hint = label("", sub=True, size=9)
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self.out = QPlainTextEdit(readOnly=True)
        self.out.setStyleSheet(f"QPlainTextEdit {{ font-family: Consolas, 'D2Coding', monospace; font-size: 9.5pt; color: {T.TXT}; }}")
        v.addWidget(self.out, 1)
        h = QHBoxLayout()
        h.addWidget(button("지우기", slot=self.out.clear))
        h.addStretch(1)
        h.addWidget(button("닫기", min_w=100, slot=self.accept))
        v.addLayout(h)
        hub.comm_result.connect(self.show_result)
        self._hint()

    def _hint(self):
        nex = self.target.currentData() == "NEX"
        self.quick["limits"].setVisible(nex)
        self.quick["clear"].setVisible(not nex)
        if nex:
            self.cmd.setPlaceholderText("R 7001 19   (D7001 부터 19개 읽기)   ·   W 1300 500   (D1300 에 500 쓰기)")
            self.hint.setText("NEX1200: D-Register 번호 그대로 입력 (MODBUS 주소 변환은 자동). 값은 장비 내부 정수입니다 "
                              "(소수 1자리 설정이면 50.0 ℃ = 500).")
        else:
            self.cmd.setPlaceholderText("*IDN?   ·   MEAS:VOLT?   ·   OUTP?   ·   ASWRS?   ·   VOLT 12")
            self.hint.setText("USP: SCPI 명령 그대로 입력. CADDR 은 자동으로 보냅니다. '?' 가 있는 명령만 응답을 기다립니다.")

    def send(self, cmd):
        if not cmd:
            return
        tgt = self.target.currentData()
        if tgt != "NEX" and cmd.split()[0].upper().startswith("OUTP") and "?" not in cmd and not confirm(
                self, "통신 시험", "출력 명령을 보냅니다. 장비에 부하가 연결되어 있으면 실제로 전원이 나갑니다. 계속할까요?",
                warn=True):
            return
        if tgt == "NEX" and cmd.upper().startswith("W") and not confirm(
                self, "통신 시험", "NEX1200 레지스터에 값을 씁니다. 운전 명령(D1303) 등은 챔버가 실제로 동작합니다. 계속할까요?",
                warn=True):
            return
        self.out.appendPlainText(f"→ {self.target.currentText()} : {cmd}")
        self.hub.call(self.hub.comm_exec, tgt, cmd)

    def show_result(self, text):
        self.out.appendPlainText(text)

    def done(self, r):
        try:
            self.hub.comm_result.disconnect(self.show_result)
        except (RuntimeError, TypeError):
            pass
        super().done(r)
