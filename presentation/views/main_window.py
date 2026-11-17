"""SCR-300 DC Power & 온/습도 메인 화면 (스토리보드 V1.3.0 20p).

A 상단 메뉴 · B Power 상태 카드 ×6 · C 챔버(NEX1200) · D 실시간 모니터링 · E 상태바

2026-11-02 변경
- 메뉴: Test Power · Run Function · Result · Setup · Exit (아이콘 + 글자 바로가기), 결과 조회는 Result 로 이동
- 그래프: Power 번호별 색(P1 파랑, P2 노랑 …), 전압 실선 / 전류 파선, 항목 체크박스 색 제거
- 그래프 저장 삭제 — 모든 데이터는 DB(시계열)에 자동 저장, Result 에서 조회·CSV 내보내기
- 챔버: 값 상자 축소 + 아이콘, 편차 막대 표시

2026-11-04 변경
- Power 선택: 클릭 = 그 채널만, Shift(또는 Ctrl)+클릭 = 여러 채널 추가/해제. 선택한 채널이 Test Power 창에 그대로 넘어감
- 그래프: 공통 조작(graphs.py) — 드래그 ↘ 확대 / ↖ 축소, 버튼 [＋ － ↶ 전체 이동]
- 챔버: 버튼·값 크게, 편차 표시 작게(빈 공간 제거)

2026-11-13 변경
- 패널 3개(Power Supply 상태 · 챔버 · 실시간 모니터링) 제목줄에 [— 최소화] [□ 최대화] — 최소화한 패널은 위쪽 줄의 버튼으로 복원
- POWER 선택: 클릭할 때마다 선택 ↔ 해제 (Shift·Ctrl 불필요), 카드 클릭도 같음
- 그래프: [⟲ 초기화] [JPEG 저장], 시간 이동 [◀ 1시간] [◀ 10분] [현재] [10분 ▶] [1시간 ▶] (기본 = 현재 시각)
- P06 색 변경(분홍), 전압·전류 소수점 2자리
- 전류 기준 Min/Max 이탈 알람: 카드에 표시, [알람 전체 해제] 로 해제

2026-11-16 변경
- 그래프 표시 범위 조절: [−1시간] [−10분] [+10분] [+1시간] — 보이는 시간 폭을 늘리고 줄임(항상 현재 시각까지). 기간 이동 기능은 삭제
- 그래프 조작 버튼을 한 줄로 (POWER · 항목 · 조작 · 표시 범위)
- 기본 높이: Power Supply 상태·챔버 50 % / 실시간 모니터링 50 %
"""
from __future__ import annotations

import html
import time
from collections import deque
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QStatusBar,
                               QCheckBox, QFrame)

from presentation.styling import resources as R
from presentation.styling import theme as T
from presentation.graphs.graphs import new_plot, GraphTools
from presentation.widgets.widgets import Panel, BlackToolbar, PowerCard, ValueBox, DeviationBar, button, label, confirm

MAXPTS = 24 * 3600


class MainWindow(QMainWindow):
    def __init__(self, hub, cfg):
        super().__init__()
        self.hub, self.cfg = hub, cfg
        self.snap = None
        self.sel = {1}                          # 그래프 대상 채널 (카드 클릭·POWER 칩으로 선택)
        self.items = {"V": True, "A": True, "T": True, "H": True}
        self.range_s = cfg.graph_default_min * 60
        self.follow = True
        self.buf = {i: deque(maxlen=MAXPTS) for i in range(1, 7)}
        self.cbuf = deque(maxlen=MAXPTS)
        self.alarm_text = ""
        self.setWindowTitle("DC Power & 온/습도 제어 프로그램")
        self.setWindowIcon(R.logo_icon())
        self.resize(1920, 1080)
        self.setMinimumSize(1280, 800)
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # ---------------- A
        self.toolbar = BlackToolbar([("Test Power", "test_power", self.open_test_power),
                                     ("Run Function", "run_function", self.open_function),
                                     ("Result", "result", self.open_result),
                                     ("Setup", "setup", self.open_settings),
                                     ("Exit", "exit", self.close)])
        v.addWidget(self.toolbar)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(8, 8, 8, 8)
        bv.setSpacing(8)
        v.addWidget(body, 1)
        # 최소화된 패널 복원 줄 (2026-11-13)
        self.minbar = QWidget()
        mb = QHBoxLayout(self.minbar)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(6)
        mb.addWidget(label("숨긴 패널", sub=True, size=9))
        self.min_btns = {}
        for k, t in [("pb", "Power Supply 상태"), ("pc", "챔버 (NEX1200)"), ("pd", "실시간 모니터링")]:
            b = button(f"▣ {t}", chip=True, slot=lambda _=False, kk=k: self.restore_panel(kk))
            b.setToolTip("눌러서 다시 표시")
            self.min_btns[k] = b
            mb.addWidget(b)
        self.btn_clear2 = button("알람 전체 해제", chip=True, slot=self.clear_alarms)
        mb.addWidget(self.btn_clear2)
        mb.addStretch(1)
        bv.addWidget(self.minbar)
        self.top_w = QWidget()
        top = QHBoxLayout(self.top_w)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        bv.addWidget(self.top_w, 1)          # 2026-11-16: 위 50 % / 아래 50 %
        # ---------------- B
        pb = Panel("Power Supply 상태 (P01 ~ P06)")
        g = pb.body_layout("g", 8, 6)
        self.cards = {}
        for i in range(1, 7):
            c = PowerCard(i)
            c.clicked.connect(self.select_ch)
            c.double_clicked.connect(lambda ch: self.open_test_power(ch))
            g.addWidget(c, (i - 1) // 3, (i - 1) % 3)
            self.cards[i] = c
        top.addWidget(pb, 2)
        self.btn_clear = pb.add_title_button("알람 전체 해제", "전류 기준 이탈·OCP 알람을 모두 해제", self.clear_alarms)
        # ---------------- C
        pc = Panel("챔버 (NEX1200)")
        cl = pc.body_layout("v", 10, 8)
        row = QHBoxLayout()
        row.addWidget(label("운전 상태", sub=True))
        self.lbl_cstate = QLabel()
        row.addWidget(self.lbl_cstate, 1)
        cl.addLayout(row)
        gv = QGridLayout()
        gv.setSpacing(6)
        self.vb = {
            "pv_t": ValueBox("현재 온도", "thermo", T.TEMP_COLOR, "℃"),
            "sp_t": ValueBox("설정 온도", "target", T.SUB, "℃"),
            "pv_h": ValueBox("현재 습도", "drop", T.BLU, "%"),
            "sp_h": ValueBox("설정 습도", "target", T.SUB, "%"),
        }
        for n, k in enumerate(["pv_t", "sp_t", "pv_h", "sp_h"]):
            gv.addWidget(self.vb[k], n // 2, n % 2)
        cl.addLayout(gv, 1)
        # 편차 (현재 − 설정): 막대 길이·색으로 바로 판단
        dev = QFrame(objectName="card")
        dl_ = QGridLayout(dev)
        dl_.setContentsMargins(10, 4, 10, 4)
        dl_.setHorizontalSpacing(8)
        dl_.setVerticalSpacing(0)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(R.pixmap("delta", T.SUB, 14))
        head.addWidget(ic)
        head.addWidget(label("편차 (현재 − 설정)", sub=True, size=9))
        head.addStretch(1)
        self.lbl_tol = label(f"녹색 구간 = 허용 ±{self.cfg.wait_tol_t:g} ℃ / ±{self.cfg.wait_tol_h:g} %", color=T.DIM, size=8.5)
        head.addWidget(self.lbl_tol)
        dl_.addLayout(head, 0, 0, 1, 3)
        self.dev_t = DeviationBar("℃", self.cfg.wait_tol_t, max(5.0, self.cfg.wait_tol_t * 5))
        self.dev_h = DeviationBar("%", self.cfg.wait_tol_h, max(15.0, self.cfg.wait_tol_h * 5))
        for r, (nm, icn, col, bar) in enumerate([("온도", "thermo", T.TEMP_COLOR, self.dev_t),
                                                 ("습도", "drop", T.BLU, self.dev_h)], 1):
            i2 = QLabel()
            i2.setPixmap(R.pixmap(icn, col, 20))
            dl_.addWidget(i2, r, 0)
            dl_.addWidget(label(nm, bold=True, size=11), r, 1)
            dl_.addWidget(bar, r, 2)
        dl_.setColumnStretch(2, 1)
        cl.addWidget(dev)
        br = QHBoxLayout()
        self.btn_run = button("실행", primary=True, h=52, slot=self.chamber_run)
        self.btn_run.setIcon(R.icon("play", "#FFFFFF", 16))
        self.btn_stop = button("정지", h=52, slot=self.chamber_stop)
        self.btn_stop.setIcon(R.icon("stop", T.RED, 16))
        b_set = button("온/습도 설정", h=52, slot=self.open_chamber)
        b_set.setIcon(R.icon("sliders", T.TXT, 16))
        for b_ in (self.btn_run, self.btn_stop, b_set):
            b_.setIconSize(QSize(20, 20))
            b_.setStyleSheet("font-size:12.5pt; font-weight:bold;")
        br.addWidget(self.btn_run)
        br.addWidget(self.btn_stop)
        br.addWidget(b_set)
        cl.addLayout(br)
        self.lbl_prog = label("", sub=True, size=9)
        cl.addWidget(self.lbl_prog)
        top.addWidget(pc, 1)
        # ---------------- D
        pd = Panel("실시간 모니터링")
        dl = pd.body_layout("v", 8, 6)
        tb = QHBoxLayout()
        tb.setSpacing(4)
        tb.addWidget(label("POWER", sub=True, size=9))
        self.ch_btns = {}
        for i in list(range(1, 7)) + ["All"]:
            b = button(str(i), chip=True, checkable=True, min_w=40 if i == "All" else 32)
            if i != "All":
                col = T.CH_COLORS[i]
                # 선택하면 그래프 선 색으로 채움, 선택 전에도 아래 색 띠로 채널 색을 보여줌
                b.setStyleSheet(f"QPushButton {{ border-bottom: 3px solid {col}; }}"
                                f"QPushButton:checked {{ background: {col}; color: #10161D; font-weight: bold; }}")
            b.clicked.connect(lambda _=False, k=i: self.on_ch_chip(k))
            self.ch_btns[i] = b
            tb.addWidget(b)
        tb.addSpacing(14)
        self.item_cb = {}
        for k, name in [("V", "Voltage"), ("A", "Current"), ("T", "Temp"), ("H", "Humidity")]:
            cb = QCheckBox(name)
            cb.setChecked(self.items[k])
            cb.toggled.connect(lambda on, kk=k: self.set_item(kk, on))
            self.item_cb[k] = cb
            tb.addWidget(cb)
        # 2026-11-16: 한 줄 — POWER · 항목 · [그래프 조작] · 표시 범위
        for b in self.ch_btns.values():
            b.setToolTip("클릭 = 그래프에 표시 / 다시 클릭 = 해제 · 전압 실선, 전류 파선")
        tb.addSpacing(12)
        self.gtools_slot = tb.count()            # GraphTools 는 그래프를 만든 뒤 이 자리에 넣는다
        tb.addSpacing(12)
        tb.addWidget(label("표시 범위", sub=True, size=9))
        self.span_btns = {}
        for t, sec, tip in [("−1시간", -3600, "보이는 시간 폭을 1시간 줄임 (최소 10분)"),
                            ("−10분", -600, "보이는 시간 폭을 10분 줄임 (최소 10분)")]:
            b = button(t, chip=True, slot=lambda _=False, ss=sec: self.adjust_range(ss))
            b.setToolTip(tip)
            self.span_btns[sec] = b
            tb.addWidget(b)
        self.lbl_range = label("", bold=True, size=10)
        self.lbl_range.setAlignment(Qt.AlignCenter)
        self.lbl_range.setMinimumWidth(78)
        self.lbl_range.setToolTip("지금 그래프에 보이는 시간 폭 (현재 시각까지)")
        tb.addWidget(self.lbl_range)
        for t, sec, tip in [("+10분", 600, "보이는 시간 폭을 10분 늘림"), ("+1시간", 3600, "보이는 시간 폭을 1시간 늘림")]:
            b = button(t, chip=True, slot=lambda _=False, ss=sec: self.adjust_range(ss))
            b.setToolTip(tip)
            self.span_btns[sec] = b
            tb.addWidget(b)
        self.btn_all = button("전체 기간", chip=True, checkable=True, slot=lambda: self.set_range(0 if self.btn_all.isChecked() else self.cfg.graph_default_min * 60))
        self.btn_all.setToolTip("프로그램을 켠 뒤(또는 초기화 뒤) 모든 데이터 표시")
        tb.addWidget(self.btn_all)
        tb.addSpacing(8)
        self.lbl_span = label("", sub=True, size=9)
        tb.addWidget(self.lbl_span)
        tb.addStretch(1)
        dl.addLayout(tb)
        pg.setConfigOptions(antialias=True)
        # 축 3개: 왼쪽 = 전압(V), 오른쪽 1 = 전류(A), 오른쪽 2 = 온도(℃)·습도(%)
        # 값의 크기가 달라도(전류 0.5 A, 온도 25 ℃) 각자 축에서 잘 보이도록 ViewBox 를 나눠 겹쳐 그린다.
        self.plot = new_plot(x_only=True, date_axis=True)
        pi = self.plot.getPlotItem()
        self.vb_v = pi.vb
        # 줌·이동은 시간축만(ZoomViewBox x_only), 세로축은 항상 자동 맞춤
        self.gtools = GraphTools(self.vb_v, on_fit=self.fit, on_reset=self.reset_graph, save_widget=self.plot,
                                 save_name="실시간그래프", hint=False,
                                 on_saved=lambda p: self.statusBar().showMessage(f"그래프 저장: {p}", 8000))
        tb.insertWidget(self.gtools_slot, self.gtools)
        self.update_range_label()
        pi.showAxis("right")
        self.ax_v, self.ax_a = pi.getAxis("left"), pi.getAxis("right")
        self.ax_th = pg.AxisItem("right")
        pi.layout.addItem(self.ax_th, 2, 3)
        self.vb_a, self.vb_th = pg.ViewBox(), pg.ViewBox()
        for vb, ax in ((self.vb_a, self.ax_a), (self.vb_th, self.ax_th)):
            pi.scene().addItem(vb)
            ax.linkToView(vb)
            vb.setXLink(self.vb_v)
            vb.setMouseEnabled(x=False, y=False)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        for ax, text, col in ((self.ax_v, "전압 (V)", T.SUB), (self.ax_a, "전류 (A)", T.SUB),
                              (self.ax_th, "온도 ℃ / 습도 %", T.SUB)):
            ax.setLabel(text, color=col)
            ax.enableAutoSIPrefix(False)      # 0.5 A 가 "500 (x0.001)" 로 바뀌지 않게
            ax.setTextPen(col)
            ax.setPen(T.BRD)
        self.plot.getAxis("bottom").setTextPen(T.SUB)
        self.plot.getAxis("bottom").setPen(T.SUB)
        self.vb_v.sigResized.connect(self._sync_views)
        self._sync_views()
        # 범례를 ViewBox 안에 두면 데이터 선과 겹쳐 보이므로 그래프 위의 전용 영역에 둔다.
        # 최대 14개(P1~P6 전압/전류 + 온도/습도)를 7열, 최대 2줄로 표시한다.
        # pyqtgraph LegendItem은 14개 항목을 2줄로 재배치할 때 일부 항목이 이전
        # 위치에 남는 문제가 있어, 그래프 Scene과 무관한 일반 Qt 위젯으로 구성한다.
        self.legend_view = QWidget(objectName="realtimeLegend")
        self.legend_view.setStyleSheet(f"QWidget#realtimeLegend {{ background: {T.PNL}; }}")
        legend_outer = QHBoxLayout(self.legend_view)
        legend_outer.setContentsMargins(8, 0, 0, 0)
        legend_outer.setSpacing(0)
        self.legend_content = QWidget()
        self.legend_layout = QGridLayout(self.legend_content)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setHorizontalSpacing(14)
        self.legend_layout.setVerticalSpacing(0)
        legend_outer.addWidget(self.legend_content, 0, Qt.AlignLeft | Qt.AlignTop)
        legend_outer.addStretch(1)
        self._legend_widgets = []
        self._legend_names = ()
        self.legend_view.setVisible(False)
        self.vb_v.sigUserZoom.connect(lambda: setattr(self, "follow", False))
        self.vb_v.sigXRangeChanged.connect(lambda *_: self.update_span())
        self.curves = {}
        dl.addWidget(self.legend_view)
        dl.addWidget(self.plot, 1)
        bv.addWidget(pd, 1)
        # 패널 최소화 / 최대화 (2026-11-13)
        self.panels = {"pb": pb, "pc": pc, "pd": pd}
        self.pmin = {k: False for k in self.panels}
        self.pmax = None
        self.max_btns = {}
        for k, p in self.panels.items():
            p.add_title_button("—", "최소화 (숨기기)", lambda _=False, kk=k: self.minimize_panel(kk))
            self.max_btns[k] = p.add_title_button("□", "최대화", lambda _=False, kk=k: self.maximize_panel(kk))
        self.apply_panels()
        # ---------------- E
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb_nex, self.sb_pow, self.sb_dir, self.sb_alarm = QLabel(), QLabel(), QLabel(), QLabel()
        for w in (self.sb_nex, self.sb_pow, self.sb_dir, self.sb_alarm):
            sb.addWidget(w)
        self.sb_clock = QLabel()
        sb.addPermanentWidget(self.sb_clock)
        self.sync_chips()
        hub.updated.connect(self.on_update)
        hub.alarm.connect(self.on_alarm)
        hub.info.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.clock = QTimer(self, interval=1000, timeout=self.tick_clock)
        self.clock.start()
        self.tick_clock()

    # ================================================================ update
    def tick_clock(self):
        self.sb_clock.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.toolbar.right.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def on_update(self, s: dict):
        self.snap = s
        ts = s["time"].timestamp()
        for d in s["channels"]:
            self.cards[d["ch"]].update_view(d, d["ch"] in self.sel)
            if d["status"] != "ERR":
                self.buf[d["ch"]].append((ts, d["v"], d["a"]))
        c = s["chamber"]
        if c:
            self.cbuf.append((ts, c["pv_t"], c["pv_h"]))
            for k in ("pv_t", "sp_t", "pv_h", "sp_h"):
                self.vb[k].set(c[k])
            self.dev_t.set(c["pv_t"] - c["sp_t"], c["running"])
            self.dev_h.set(c["pv_h"] - c["sp_h"], c["running"])
            if c["error"]:
                st, col = f"에러 · {c['error']}", T.RED
            elif not c["running"]:
                st, col = "정지", T.GRY
            else:
                st, col = ("정치 운전" if c["mode"] == "fix" else f"프로그램 운전 (패턴 {c['pattern']} · 세그 {c['segment']})"), T.GRN
            self.lbl_cstate.setText(f"<span style='color:{col}'>● <b>{st}</b></span>")
            self.btn_run.setEnabled(s["chamber_ok"] and not c["running"] and not c["error"])
            self.btn_stop.setEnabled(s["chamber_ok"] and c["running"])
        if not s["chamber_ok"]:
            self.lbl_cstate.setText(f"<span style='color:{T.RED}'>● <b>통신 이상</b></span>")
            for k in self.vb:
                self.vb[k].set(None)
            self.dev_t.set(None, False)
            self.dev_h.set(None, False)
            self.btn_run.setEnabled(False)
            self.btn_stop.setEnabled(False)
        ok = s["chamber_ok"]
        self.sb_nex.setText(f"<span style='color:{T.GRN if ok else T.RED}'>●</span> NEX1200 {'정상' if ok else '통신 이상'}")
        bad = [f"P{d['ch']:02d}" for d in s["channels"] if d["status"] == "ERR"]
        self.sb_pow.setText(f"<span style='color:{T.RED if bad else T.GRN}'>●</span> " +
                            (", ".join(bad) + " 통신 이상" if bad else "P01~P06 정상"))
        if s.get("simulate"):
            self.sb_dir.setText(f"<span style='color:{T.YEL}'>시뮬레이터</span> · 저장 {s['data_dir']}")
        else:
            pm = s.get("poll_ms", 0)
            self.sb_dir.setText(f"<span style='color:{T.YEL if pm > 900 else T.SUB}'>통신 주기 {pm:.0f} ms</span> · 저장 {s['data_dir']}")
        self.sb_alarm.setVisible(bool(self.alarm_text))
        self.update_alarm_buttons()
        self.sb_alarm.setText(f"<span style='color:{T.RED}'>▲ {html.escape(self.alarm_text)}</span>")
        self.redraw()

    def on_alarm(self, code, msg):
        self.alarm_text = f"{code} {msg}"
        from presentation.views.dialogs import AlarmDialog
        AlarmDialog.show_alarm(self, code, msg)

    # ================================================================ graph (D)
    def select_ch(self, ch):
        """클릭할 때마다 그 채널 선택 ↔ 해제 (2026-11-13: Shift·Ctrl 없이 토글, 모두 해제도 가능)."""
        if ch in self.sel:
            self.sel.discard(ch)
        else:
            self.sel.add(ch)
        self.sync_chips()
        self.redraw()

    toggle_graph_ch = select_ch          # 이전 이름 호환

    def on_ch_chip(self, k):
        if k == "All":
            self.sel = set() if len(self.sel) == 6 else set(range(1, 7))
            self.sync_chips()
            self.redraw()
        else:
            self.select_ch(k)

    def sync_chips(self):
        for k, b in self.ch_btns.items():
            b.setChecked(len(self.sel) == 6 if k == "All" else k in self.sel)
        if self.snap:
            for d in self.snap["channels"]:
                self.cards[d["ch"]].update_view(d, d["ch"] in self.sel)

    def set_item(self, k, on):
        self.items[k] = on
        self.redraw()

    MIN_RANGE_S, MAX_RANGE_S = 600, 24 * 3600

    def set_range(self, s):
        self.range_s = s
        self.btn_all.setChecked(s == 0)
        self.update_range_label()
        self.gtools.fit() if hasattr(self, "gtools") else self.fit()

    def adjust_range(self, delta):
        """표시 범위(시간 폭) ± — 10분 보기에서 +10분 = 20분, 다시 +10분 = 30분. 항상 현재 시각까지 보여준다."""
        base = self.range_s
        if base == 0:                                    # 전체 기간 보기에서는 지금 보이는 폭을 10분 단위로 올림
            (x0, x1), _ = self.vb_v.viewRange()
            base = max(self.MIN_RANGE_S, int(-(-(x1 - x0) // 600)) * 600)
        new = min(self.MAX_RANGE_S, max(self.MIN_RANGE_S, base + delta))
        if new == base and self.range_s:
            self.statusBar().showMessage("표시 범위는 10분 ~ 24시간입니다.", 5000)
        self.set_range(new)

    @staticmethod
    def fmt_range(sec):
        if not sec:
            return "전체"
        h, m = divmod(int(sec) // 60, 60)
        return f"{h}시간 {m}분" if h and m else (f"{h}시간" if h else f"{m}분")

    def update_range_label(self):
        self.lbl_range.setText(self.fmt_range(self.range_s))
        for sec, b in self.span_btns.items():
            b.setEnabled(not (self.range_s and ((sec < 0 and self.range_s <= self.MIN_RANGE_S) or
                                                (sec > 0 and self.range_s >= self.MAX_RANGE_S))))

    def fit(self):
        self.follow = True
        for vb in (self.vb_v, self.vb_a, self.vb_th):
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.redraw()

    def _sync_views(self):
        for vb in (self.vb_a, self.vb_th):
            vb.setGeometry(self.vb_v.sceneBoundingRect())
            vb.linkedViewChanged(self.vb_v, vb.XAxis)

    def _series(self):
        """(이름, 색, 선 모양, xs, ys, 축) 목록.

        선택한 DC Power(카드 클릭 또는 POWER 1~6/All)마다 체크한 전압·전류를 그린다.
        Power 는 번호별 색(P1 파랑, P2 노랑, P3 초록, P4 주황, P5 보라, P6 분홍)으로 구분하고,
        같은 Power 의 전압은 실선, 전류는 파선이다. 온도·습도는 챔버 값(빨강·회백색)을 함께 그린다.
        """
        out = []
        for i in sorted(self.sel):
            b = self.buf[i]
            if not b:
                continue
            xs = [p[0] for p in b]
            col = T.CH_COLORS[i]
            if self.items["V"]:
                out.append((f"P{i} 전압", col, Qt.SolidLine, xs, [p[1] for p in b], "V"))
            if self.items["A"]:
                out.append((f"P{i} 전류", col, Qt.DashLine, xs, [p[2] for p in b], "A"))
        if self.cbuf:
            xs = [p[0] for p in self.cbuf]
            if self.items["T"]:
                out.append(("온도", T.TEMP_COLOR, Qt.SolidLine, xs, [p[1] for p in self.cbuf], "TH"))
            if self.items["H"]:
                out.append(("습도", T.HUMI_COLOR, Qt.DotLine, xs, [p[2] for p in self.cbuf], "TH"))
        return out

    def _rebuild_realtime_legend(self, series):
        """그래프 Scene 밖에서 현재 시리즈의 좌측 밀착형 범례를 재구성한다."""
        names = tuple(s[0] for s in series)
        if names == self._legend_names:
            return
        for widget in self._legend_widgets:
            self.legend_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._legend_widgets.clear()
        line_style = {Qt.SolidLine: "solid", Qt.DashLine: "dashed", Qt.DotLine: "dotted"}
        for index, (name, color, style, *_rest) in enumerate(series):
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            swatch = QFrame()
            swatch.setFixedSize(24, 10)
            swatch.setStyleSheet(
                f"border: none; border-top: 2px {line_style.get(style, 'solid')} {color}; margin-top: 4px;"
            )
            text = QLabel(name)
            text.setStyleSheet(f"color: {T.TXT}; font-size: 9pt;")
            row.addWidget(swatch)
            row.addWidget(text)
            self.legend_layout.addWidget(entry, index // 7, index % 7, Qt.AlignLeft)
            self._legend_widgets.append(entry)
        self._legend_names = names

    def redraw(self):
        series = self._series()
        names = {s[0] for s in series}
        boxes = {"V": self.vb_v, "A": self.vb_a, "TH": self.vb_th}
        for n in list(self.curves):
            if n not in names:
                item, vb = self.curves.pop(n)
                vb.removeItem(item)
        for n, col, style, xs, ys, ax in series:
            if n not in self.curves:
                item = pg.PlotDataItem(pen=pg.mkPen(col, width=2.0, style=style), name=n)
                boxes[ax].addItem(item)
                self.curves[n] = (item, boxes[ax])
            self.curves[n][0].setData(xs, ys)
        self._rebuild_realtime_legend(series)
        # 범례 항목 수에 맞춰 전용 공간만 확보한다. 데이터가 그려지는 ViewBox와는
        # 완전히 분리되어 있어 선이 범례 글자/샘플을 침범하지 않는다.
        legend_rows = (len(series) + 6) // 7
        self.legend_view.setVisible(bool(legend_rows))
        self.legend_view.setFixedHeight(26 * legend_rows if legend_rows else 0)
        used = {s[5] for s in series}
        self.ax_v.setStyle(showValues="V" in used)
        self.ax_a.setVisible("A" in used)
        self.ax_th.setVisible("TH" in used)
        if self.follow:
            now = time.time()
            if self.range_s:
                start = now - self.range_s           # 2026-11-16: 선택한 시간 폭 그대로 (데이터가 짧아도)
            else:
                start = min([s[3][0] for s in series], default=now - 60)
            self.vb_v.setXRange(start, max(now, start + 10), padding=0.01)
        self.update_span()

    # ---- 표시 구간 · 초기화
    def update_span(self):
        (x0, x1), _ = self.vb_v.viewRange()
        try:
            a, b = datetime.fromtimestamp(x0), datetime.fromtimestamp(x1)
        except (OverflowError, OSError, ValueError):
            return
        fmt = "%H:%M:%S" if a.date() == b.date() == datetime.now().date() else "%m-%d %H:%M"
        mode = f"<span style='color:{T.GRN}'>● 실시간</span>" if self.follow else f"<span style='color:{T.YEL}'>■ 고정 ([⤢ 전체] = 실시간)</span>"
        self.lbl_span.setText(f"{a:{fmt}} ~ {b:{fmt}}  {mode}")

    def reset_graph(self):
        if not confirm(self, "그래프 초기화", "화면의 그래프를 지우고 지금부터 다시 그립니다.\n"
                                         "표시 범위·확대도 처음 상태(현재 시각)로 돌아갑니다.\n\n"
                                         "저장된 데이터(DB)는 지워지지 않으며 Result 에서 조회할 수 있습니다."):
            return
        for b in self.buf.values():
            b.clear()
        self.cbuf.clear()
        for item, vb in self.curves.values():
            vb.removeItem(item)
        self.curves.clear()
        self.gtools.btn_pan.setChecked(False)
        self.vb_v.set_pan(False)
        self.set_range(self.cfg.graph_default_min * 60)
        self.hub.log.write("USER", "실시간 그래프 초기화")

    # ================================================================ 패널 최소화 / 최대화 (2026-11-13)
    def minimize_panel(self, k):
        self.pmin[k] = True
        if self.pmax == k:
            self.pmax = None
        self.apply_panels()

    def maximize_panel(self, k):
        self.pmax = None if self.pmax == k else k
        if self.pmax:
            self.pmin[k] = False
        self.apply_panels()

    def restore_panel(self, k):
        self.pmin[k] = False
        self.pmax = None
        self.apply_panels()

    def apply_panels(self):
        hidden, shown = [], {}
        for k, p in self.panels.items():
            vis = (self.pmax == k) if self.pmax else not self.pmin[k]
            shown[k] = vis
            p.setVisible(vis)
            if not vis:
                hidden.append(k)
            self.max_btns[k].setText("❐" if self.pmax == k else "□")
            self.max_btns[k].setToolTip("이전 크기로" if self.pmax == k else "최대화")
            self.min_btns[k].setVisible(not vis)
        self.top_w.setVisible(shown["pb"] or shown["pc"])
        self.minbar.setVisible(bool(hidden))
        self.update_alarm_buttons()

    # ================================================================ 알람 전체 해제 (2026-11-13)
    def _alarm_list(self):
        out = []
        for d in (self.snap or {}).get("channels", []):
            if d.get("i_alarm"):
                out.append(f"P{d['ch']:02d} 전류 기준 이탈 · {d['i_alarm']}")
            if d["status"] == "OCP":
                out.append(f"P{d['ch']:02d} OCP 차단")
        return out

    def update_alarm_buttons(self):
        n = len(self._alarm_list())
        show2 = hasattr(self, "panels") and (self.panels["pb"].isHidden() or self.top_w.isHidden())
        if (n, show2) == getattr(self, "_alarm_btn_state", None):
            return
        self._alarm_btn_state = (n, show2)
        txt = f"⚠ 알람 전체 해제 ({n})" if n else "알람 전체 해제"
        css = f"background:{T.RED}; color:#FFFFFF; font-weight:bold;" if n else ""
        for b in (self.btn_clear, self.btn_clear2):
            b.setText(txt)
            b.setStyleSheet(css)
        self.btn_clear2.setVisible(show2)

    def clear_alarms(self):
        lst = self._alarm_list()
        body = ("\n".join("· " + a for a in lst) + "\n\n") if lst else "현재 카드에 표시된 알람이 없습니다.\n"
        if not confirm(self, "알람 전체 해제", body + "모든 알람 표시를 해제합니다.", ok="전체 해제"):
            return
        self.alarm_text = ""
        self.sb_alarm.setVisible(False)
        self.hub.call(self.hub.clear_alarms)

    # ================================================================ C actions
    def chamber_run(self):
        c = self.snap and self.snap["chamber"]
        if not c:
            return
        mode = "정치" if c["mode"] == "fix" else f"프로그램 (패턴 {c['pattern']})"
        if confirm(self, "챔버 실행", f"챔버 운전을 시작합니다.\n\n운전 방식: {mode}\n설정 온도: {c['sp_t']:.1f} ℃\n설정 습도: {c['sp_h']:.1f} %"):
            self.hub.call(self.hub.chamber_run)

    def chamber_stop(self):
        running = [d for d in self.snap["channels"] if d["status"] in ("RUN", "WAIT") and d["func"]] if self.snap else []
        stop_power = False
        if running:
            if not confirm(self, "챔버 정지", "Function 실행 중인 채널이 있습니다.\nPower 출력도 함께 정지할까요?",
                           ok="챔버·Power 모두 정지", cancel="취소", warn=True):
                return
            stop_power = True
        elif not confirm(self, "챔버 정지", "챔버 운전을 정지합니다."):
            return
        self.hub.call(self.hub.chamber_stop, stop_power)

    # ================================================================ dialogs
    def open_test_power(self, ch=None):
        from presentation.views.dialogs import TestPowerDialog
        # 카드 더블클릭 = 그 채널, 메뉴 = 메인 화면에서 선택한 채널들을 미리 선택
        pre = [ch] if isinstance(ch, int) and not isinstance(ch, bool) else sorted(self.sel)
        TestPowerDialog(self, self.hub, self.cfg, preselect=pre).exec()

    def open_chamber(self):
        from presentation.views.dialogs import ChamberDialog
        ChamberDialog(self, self.hub, self.cfg, self.snap).exec()

    def open_result(self):
        from presentation.views.dialogs import ResultDialog
        ResultDialog(self, self.hub, self.cfg).exec()

    def open_function(self):
        from presentation.views.function_manager import FunctionManager
        FunctionManager(self, self.hub, self.cfg).exec()

    def open_settings(self):
        from presentation.views.dialogs import SettingsDialog
        SettingsDialog.open_with_password(self, self.hub, self.cfg)

    def closeEvent(self, e):
        from presentation.views.dialogs import ExitDialog
        if getattr(self, "_closed", False):
            return e.accept()
        r = ExitDialog.ask(self, self.snap)
        if r is None:
            e.ignore()
            return
        self._closed = True
        self.hub.shutdown(stop_devices=r)
        e.accept()
