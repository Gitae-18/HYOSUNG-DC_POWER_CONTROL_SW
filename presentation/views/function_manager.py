"""POP-340 Function 관리 — 탭① 채널 할당·실행, 탭② Function 편집 (팝업 없이 표에서 바로 입력).

2026-11-13 변경
- [구성 저장]/[구성 불러오기] → [Function Profile 내보내기]/[Function Profile 가져오기]
  Function 상세 Profile(Temperature Segment + Step)을 파일(.hfp)로 내보내 다른 PC 로 옮기고,
  다른 PC 에서 만든 Profile 을 가져와 이 PC 에서 편집할 수 있다. (같은 이름 = 이름 바꿔 가져오기 / 덮어쓰기)
- Temperature Segment · Step 표: No 다음에 체크박스 열 — 체크한 여러 행을 [복사] 후 [위에/아래에 붙여넣기], [삭제]
  (체크가 없으면 현재 행). 다른 Segment·다른 Function 에도 붙여넣기 가능 (창을 닫기 전까지 유지)
- 시간 칸: 숫자만 연속 입력 (hhmmss, 예 013000 = 1시간 30분)
- 설정 내용 그래프: 온도·습도·전압·전류를 한 그래프에 (왼쪽 축 ℃/%, 오른쪽 축 V, A) + JPEG 저장
- 채널 할당: 전류 기준 Min/Max 입력 (출력 중 벗어나면 메인 화면 카드 알람), 체크박스 머리글 = 전체 선택

2026-11-16 변경
- 붙여넣기 위치 = 체크한 행 기준 ([위에] = 체크한 첫 행 위, [아래에] = 체크한 마지막 행 아래). [복사] 하면 체크를 비워
  붙여넣을 위치를 바로 체크할 수 있게 함. 체크가 없을 때만 현재 행 기준
- 설정 내용 그래프 [⟲ 초기화]: 네 항목 모두 표시 + 확대·이동 해제 + 전체 보기
- Step 표 입력 중에 Segment 가 바뀌면, 입력값을 원래 Segment 에 먼저 반영 (다른 Segment 에 잘못 들어가던 문제 방지)
"""
from __future__ import annotations

import copy
import socket

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QWidget, QTableWidget,
                               QTableWidgetItem, QLineEdit, QDoubleSpinBox, QSpinBox, QListWidget, QListWidgetItem,
                               QRadioButton, QAbstractItemView, QHeaderView, QFileDialog, QInputDialog, QLabel,
                               QCheckBox, QButtonGroup, QApplication, QAbstractItemDelegate)

from infrastructure.system.config import APP_DIR
from presentation.graphs.graphs import new_plot, GraphTools
from presentation.styling import theme as T
from domain.models.function_model import (Function, Segment, Step, hms, parse_hms, write_profile, read_profile, unique_name,
                            PROFILE_EXT)
from presentation.widgets.widgets import (Panel, button, label, confirm, info, ask_record, check_item, checked_rows, set_all_checked,
                     setup_check_column, TimeDelegate)

# 표 열 번호 (2026-11-13: No 다음에 체크박스 열)
S_NO, S_CHK, S_TEMP, S_HUMI, S_TIME, S_RT, S_RH, S_WAIT = range(8)
P_NO, P_CHK, P_TIME, P_TEMP, P_HUMI, P_VOLT, P_CURR = range(7)


def _it(text, editable=False, color=None, center=False):
    it = QTableWidgetItem("" if text is None else str(text))
    if not editable:
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if color:
        it.setForeground(pg.mkColor(color))
    if center:
        it.setTextAlignment(Qt.AlignCenter)
    return it


def _put(t: QTableWidget, r, c, text, editable=False, color=None, center=False):
    """칸 값을 바꾼다 — 이미 있는 칸은 객체를 바꾸지 않고 글자만 고쳐서, 입력 중인 편집기가 깨지지 않게 한다."""
    it = t.item(r, c)
    if it is None or it.flags() & Qt.ItemIsUserCheckable:
        t.setItem(r, c, _it(text, editable, color, center))
        return
    it.setText("" if text is None else str(text))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsEditable if editable else Qt.NoItemFlags))
    it.setForeground(pg.mkColor(color or T.TXT))
    it.setTextAlignment(Qt.AlignCenter if center else Qt.AlignLeft | Qt.AlignVCenter)


def _put_check(t: QTableWidget, r, c, checked, flags=Qt.ItemIsEnabled | Qt.ItemIsUserCheckable):
    it = t.item(r, c)
    if it is None or not it.flags() & Qt.ItemIsUserCheckable:
        it = QTableWidgetItem()
        t.setItem(r, c, it)
    it.setFlags(flags)
    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)


def _tbl(headers, edit=False):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(28)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.setEditTriggers(QAbstractItemView.AllEditTriggers if edit else QAbstractItemView.NoEditTriggers)
    return t


def _narrow(t: QTableWidget, cols: dict[int, int]):
    hh = t.horizontalHeader()
    for c, w in cols.items():
        hh.setSectionResizeMode(c, QHeaderView.Fixed)
        t.setColumnWidth(c, w)


class FunctionManager(QDialog):
    def __init__(self, parent, hub, cfg):
        super().__init__(parent)
        self.hub, self.cfg = hub, cfg
        self.store = hub.functions
        self.clip_seg: list[Segment] = []          # 복사한 Segment (창을 닫기 전까지 유지)
        self.clip_step: list[Step] = []
        self.seg_chk: set[int] = set()             # 체크한 Segment / Step (id) — 표를 다시 그려도 유지
        self.step_chk: set[int] = set()
        self.setWindowTitle("Function 관리")
        self.resize(1760, 960)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_run(), "① 채널 할당 · 실행")
        self.tabs.addTab(self._tab_edit(), "② Function 편집")
        self.tabs.currentChanged.connect(self.on_tab)
        h = QHBoxLayout()
        b1 = button("Function Profile 내보내기", slot=self.profile_export)
        b1.setToolTip("Function 상세 Profile(Temperature Segment + Step)을 파일로 저장 — 다른 PC 로 옮길 때")
        b2 = button("Function Profile 가져오기", slot=self.profile_import)
        b2.setToolTip("다른 PC 에서 내보낸 Profile 파일을 이 PC 의 Function 목록에 추가 — 가져온 뒤 편집 가능")
        h.addWidget(b1)
        h.addWidget(b2)
        h.addStretch(1)
        h.addWidget(button("닫기", min_w=100, slot=self.close))
        v.addLayout(h)
        self.reload_lists()
        self.refresh(hub.snapshot())
        hub.updated.connect(self.refresh)

    # ================================================================== 탭①
    def _tab_run(self):
        from presentation.views.dialogs import LimitInput
        w = QWidget()
        h = QHBoxLayout(w)
        left = Panel("채널 할당 현황")
        ll = left.body_layout()
        self.t_ch = _tbl(["선택", "CH", "Function", "OCP (A)", "Cycle", "전류 기준 (A)", "상태", "진행"])
        self.t_ch.setRowCount(6)
        self.t_ch.setSelectionMode(QAbstractItemView.NoSelection)
        self.t_ch.setFocusPolicy(Qt.NoFocus)
        _narrow(self.t_ch, {0: 56, 1: 56})
        for r in range(6):
            self.t_ch.setItem(r, 0, check_item())
        setup_check_column(self.t_ch, 0, row_click=True)
        ll.addWidget(self.t_ch, 1)
        b = QHBoxLayout()
        b.addWidget(button("All 선택", slot=self.check_all))
        b.addWidget(button("실행", primary=True, min_w=100, slot=self.do_start))
        b.addWidget(button("정지", min_w=100, slot=self.do_stop))
        b.addWidget(button("할당 해제", slot=self.do_unassign))
        b.addStretch(1)
        ll.addLayout(b)
        ll.addWidget(label("체크한 채널에 실행·정지·해제가 적용됩니다 (행 클릭 = 체크 전환). 실행 중인 채널은 해제할 수 없습니다.",
                           sub=True, size=9))
        h.addWidget(left, 3)
        right = Panel("Function 목록")
        rl = right.body_layout()
        self.search = QLineEdit(placeholderText="검색")
        self.search.textChanged.connect(self.fill_flist)
        rl.addWidget(self.search)
        self.t_func = _tbl(["Name", "Step", "총 시간"])
        self.t_func.setObjectName("functionSelectTable")
        self.t_func.verticalHeader().setDefaultSectionSize(36)
        self.t_func.setStyleSheet(f"""
            QTableWidget#functionSelectTable {{
                background: {T.ROW}; border: 2px solid {T.BRD}; outline: none;
            }}
            QTableWidget#functionSelectTable::item {{
                padding: 5px 8px; border-bottom: 1px solid {T.BRD};
            }}
            QTableWidget#functionSelectTable::item:hover:!selected {{
                background: #2B3745; color: #FFFFFF;
            }}
            QTableWidget#functionSelectTable::item:selected {{
                background: #33475C; color: {T.TXT}; font-weight: bold;
                border-top: 1px solid {T.BLU}; border-bottom: 1px solid {T.BLU};
            }}
        """)
        self.t_func.itemSelectionChanged.connect(self._show_selected_function)
        self.t_func.cellDoubleClicked.connect(self.edit_from_list)
        rl.addWidget(self.t_func, 1)
        self.lbl_selected_func = QLabel()
        self.lbl_selected_func.setMinimumHeight(38)
        rl.addWidget(self.lbl_selected_func)
        self._show_selected_function()
        rl.addWidget(label("할당 조건", sub=True))
        g = QGridLayout()
        self.ocp = QDoubleSpinBox(decimals=2, maximum=self.cfg.psu_i_max, suffix=" A")
        self.ocp.setValue(self.cfg.psu_i_max)
        self.cycles = QSpinBox(minimum=1, maximum=9999)
        self.cycles.setValue(1)
        self.logs = QDoubleSpinBox(decimals=1, minimum=1.0, maximum=1.0)
        self.logs.setValue(1.0)
        self.logs.setSuffix(" s  (DB 1초 고정)")
        self.logs.setEnabled(False)          # 2026-11-04: 데이터 수집 주기 1초 고정 (DB 요구사항)
        self.lim = LimitInput(self.cfg)      # 2026-11-13: 전류 기준 Min/Max
        for r, (lab, wd) in enumerate([("OCP", self.ocp), ("Total Cycle", self.cycles), ("Logging Time", self.logs),
                                       ("전류 기준", self.lim)]):
            g.addWidget(QLabel(lab), r, 0)
            g.addWidget(wd, r, 1)
        rl.addLayout(g)
        rl.addWidget(label("전류 기준: 출력 중 전류가 한 번이라도 Min~Max 를 벗어나면 메인 화면 카드에 알람 "
                           "([알람 전체 해제] 전까지 유지)", sub=True, size=9))
        bb = QHBoxLayout()
        bb.addWidget(button("체크한 채널에 할당", primary=True, h=34, slot=self.do_assign), 2)
        lb = button("전류 기준만 적용", h=34, slot=self.do_limits)
        lb.setToolTip("Function 할당은 그대로 두고 체크한 채널의 전류 기준만 바꿈 (실행 중 채널도 가능)")
        bb.addWidget(lb, 1)
        rl.addLayout(bb)
        h.addWidget(right, 2)
        return w

    def refresh(self, s):
        fmt = lambda x: "—" if x is None else f"{x:.2f}"
        for r, d in enumerate(s["channels"]):
            lim = "미설정" if d.get("i_min") is None and d.get("i_max") is None else f"{fmt(d.get('i_min'))} ~ {fmt(d.get('i_max'))}"
            if d.get("i_alarm"):
                lim += "  ⚠ 이탈"
            vals = [f"P{d['ch']}", d["func"] or "—", f"{d['ocp']:.2f}" if d["func"] else "—",
                    d["cycles"] or "—", lim, T.STATUS_TEXT.get(d["status"]), d["progress"]]
            for c, val in enumerate(vals, 1):
                it = self.t_ch.item(r, c)
                col = T.STATUS.get(d["status"]) if c == 6 else (T.RED if c == 5 and d.get("i_alarm") else T.TXT)
                if it is not None and it.text() == str(val) and it.data(Qt.UserRole) == col:
                    continue
                it = _it(val, color=col)
                it.setData(Qt.UserRole, col)
                it.setFlags(Qt.ItemIsEnabled)
                self.t_ch.setItem(r, c, it)

    def checked(self):
        return [r + 1 for r in checked_rows(self.t_ch, 0)]

    def check_all(self):
        set_all_checked(self.t_ch, 0)

    def selected_func(self):
        r = self.t_func.currentRow()
        item = self.t_func.item(r, 0) if r >= 0 else None
        return item.text() if item is not None else None

    def _show_selected_function(self):
        """채널에 할당될 Function을 목록 밖에서도 명확하게 표시한다."""
        fn = self.selected_func()
        if fn:
            self.lbl_selected_func.setText(f"선택됨  ·  할당할 Function:  {fn}")
            self.lbl_selected_func.setStyleSheet(
                f"background:{T.CARD}; color:{T.TXT}; border:1px solid {T.BLU}; "
                "border-radius:3px; padding:5px 10px; font-size:10pt; font-weight:bold;"
            )
        else:
            self.lbl_selected_func.setText("할당할 Function을 목록에서 선택하세요.")
            self.lbl_selected_func.setStyleSheet(
                f"background:{T.INP}; color:{T.SUB}; border:1px dashed {T.BRD}; "
                "border-radius:3px; padding:5px 10px;"
            )

    def do_assign(self):
        chs, fn = self.checked(), self.selected_func()
        if not chs or not fn:
            return info(self, "할당", "채널을 체크하고 Function 을 선택하세요.")
        lim = self.lim.values()
        if lim is None:
            return
        f = self.store.load(fn)
        maxc = max((st.curr for s in f.segments for st in s.steps), default=0)
        if self.ocp.value() < maxc and not confirm(self, "OCP 확인", f"OCP {self.ocp.value()} A 가 Step 최대 전류 {maxc} A 보다 작습니다. 계속할까요?", warn=True):
            return
        self.hub.call(self.hub.assign, chs, fn, self.ocp.value(), self.cycles.value(), self.logs.value(), i_limits=lim)

    def do_limits(self):
        chs = self.checked()
        if not chs:
            return info(self, "전류 기준", "채널을 체크하세요.")
        lim = self.lim.values()
        if lim is not None:
            self.hub.call(self.hub.set_current_limits, chs, *lim)

    def do_unassign(self):
        if self.checked():
            self.hub.call(self.hub.unassign, self.checked())

    def do_start(self):
        chs = self.checked()
        if not chs:
            return info(self, "실행", "채널을 체크하세요.")
        s = self.hub.snapshot()
        ready = [c for c in chs if s["channels"][c - 1]["func"] and s["channels"][c - 1]["status"] not in ("RUN", "WAIT", "ERR")]
        if not ready:
            return info(self, "실행", "실행할 수 있는 채널이 없습니다 (Function 미할당 또는 실행 중).")
        lines = "\n".join(f"P{c:02d} · {s['channels'][c - 1]['func']}" for c in ready)
        record = ask_record(self, "Function 실행 및 저장",
                            f"다음 채널의 Function 을 실행합니다.\n\n{lines}\n\n챔버는 먼저 시작한 Function 이 제어합니다.")
        if record is not None:
            self.hub.call(self.hub.start_func, ready, record=record)

    def do_stop(self):
        chs = self.checked()
        if chs and confirm(self, "정지", f"P{chs} Function 을 정지하고 출력을 끕니다.", warn=True):
            self.hub.call(self.hub.stop_func, chs)

    # ================================================================== Function Profile 내보내기 / 가져오기
    def profile_export(self):
        names = self.store.names()
        if not names:
            return info(self, "Function Profile 내보내기", "내보낼 Function 이 없습니다.")
        if self.dirty and not confirm(self, "Function Profile 내보내기",
                                      f"'{self.orig_name}' 에 저장하지 않은 변경 사항이 있습니다.\n"
                                      "내보내기는 저장된 내용으로 합니다. 계속할까요?", ok="계속"):
            return
        pre = {self.orig_name} if self.tabs.currentIndex() == 1 and self.orig_name else ({self.selected_func()} - {None})
        d = ProfileExportDialog(self, names, pre)
        if d.exec() != QDialog.Accepted:
            return
        chosen = d.chosen()
        folder = APP_DIR / "Profile"
        folder.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        base = chosen[0] if len(chosen) == 1 else f"FunctionProfile_{len(chosen)}개_{datetime.now():%Y%m%d_%H%M}"
        path, _ = QFileDialog.getSaveFileName(self, "Function Profile 내보내기", str(folder / f"{base}{PROFILE_EXT}"),
                                              f"Function Profile (*{PROFILE_EXT});;JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith((PROFILE_EXT, ".json")):
            path += PROFILE_EXT
        try:
            write_profile(path, [self.store.load(n) for n in chosen], source=socket.gethostname())
        except Exception as e:  # noqa
            return info(self, "Function Profile 내보내기", f"저장하지 못했습니다.\n{e}", warn=True)
        self.hub.log.write("USER", f"Function Profile 내보내기 {chosen} → {path}")
        info(self, "Function Profile 내보내기", f"{len(chosen)}개 Function 을 내보냈습니다.\n\n{path}\n\n"
                                               "이 파일을 다른 PC 로 옮긴 뒤 [Function Profile 가져오기] 로 불러오세요.")

    def profile_import(self):
        if self.dirty and not confirm(self, "Function Profile 가져오기",
                                      f"'{self.orig_name}' 에 저장하지 않은 변경 사항이 있습니다.\n버리고 계속할까요?",
                                      ok="버리고 계속"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Function Profile 가져오기", str(APP_DIR / "Profile"),
                                              f"Function Profile (*{PROFILE_EXT} *.json);;모든 파일 (*)")
        if not path:
            return
        try:
            funcs, meta, bad = read_profile(path)
        except Exception as e:  # noqa
            return info(self, "Function Profile 가져오기", f"파일을 읽지 못했습니다.\n{e}", warn=True)
        if not funcs:
            return info(self, "Function Profile 가져오기", "가져올 Function 이 없습니다.\n" + "\n".join(bad), warn=True)
        running = {d["func"] for d in self.hub.snapshot()["channels"] if d["func"] and d["status"] in ("RUN", "WAIT")}
        d = ProfileImportDialog(self, funcs, meta, bad, self.store, self.cfg, running)
        if d.exec() != QDialog.Accepted:
            return
        saved = []
        for f, name, overwrite in d.result_items():
            f = copy.deepcopy(f)
            f.name = name
            self.store.save(f)
            saved.append(name + (" (덮어씀)" if overwrite else ""))
        if not saved:
            return
        self.hub.log.write("USER", f"Function Profile 가져오기 {path} → {saved}")
        self.clear_dirty()
        first = saved[0].replace(" (덮어씀)", "")
        self.reload_lists(select=first)
        self.tabs.setCurrentIndex(1)
        info(self, "Function Profile 가져오기", f"{len(saved)}개 Function 을 가져왔습니다.\n\n· " + "\n· ".join(saved) +
             "\n\n② Function 편집 탭에서 바로 편집할 수 있습니다.")

    # ================================================================== 탭②
    def _tab_edit(self):
        w = QWidget()
        h = QHBoxLayout(w)
        # 목록
        lp = Panel("Function 목록")
        ll = lp.body_layout()
        bb = QHBoxLayout()
        bb.addWidget(button("+ 새로", slot=self.f_new))
        bb.addWidget(button("복사", slot=self.f_copy))
        bb.addWidget(button("삭제", slot=self.f_delete))
        ll.addLayout(bb)
        self.lst = QListWidget()
        self.lst.currentTextChanged.connect(self.select_function)
        ll.addWidget(self.lst, 1)
        lp.setFixedWidth(270)
        h.addWidget(lp)
        # 편집
        c = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(label("Function 이름", sub=True))
        self.name = QLineEdit()
        self.name.textEdited.connect(self.mark_dirty)
        top.addWidget(self.name, 1)
        top.addSpacing(20)
        top.addWidget(label("시작값", sub=True))
        self.rb_pv, self.rb_man = QRadioButton("현재 측정값"), QRadioButton("직접")
        self.st_t = QDoubleSpinBox(decimals=1, minimum=-100, maximum=200, suffix=" ℃")
        self.st_h = QDoubleSpinBox(decimals=1, minimum=0, maximum=100, suffix=" %")
        for wd in (self.rb_pv, self.rb_man, self.st_t, self.st_h):
            top.addWidget(wd)
        for wd in (self.rb_pv, self.rb_man):
            wd.toggled.connect(self.from_header)
        for wd in (self.st_t, self.st_h):
            wd.valueChanged.connect(self.from_header)
        c.addLayout(top)
        # ---- Temperature Segment
        sp = Panel("Temperature Segment  (챔버 목표)")
        sl = sp.body_layout()
        sl.addLayout(self._row_tools("seg"))
        self.t_seg = _tbl(["No", "선택", "목표 온도 ℃", "목표 습도 %", "시간 (hhmmss)", "온도 변화율 ℃/min",
                           "습도 변화율 %/min", "Wait"], edit=True)
        _narrow(self.t_seg, {S_NO: 44, S_CHK: 48, S_WAIT: 56})
        self.t_seg.setItemDelegateForColumn(S_TIME, TimeDelegate(self.t_seg))   # 표마다 따로 (공유하면 Qt 경고)
        self.t_seg.itemChanged.connect(self.seg_changed)
        self.t_seg.currentCellChanged.connect(lambda r, *_: self.show_steps(r))
        self.t_seg.horizontalHeader().sectionClicked.connect(lambda col: self._check_all("seg") if col == S_CHK else None)
        sl.addWidget(self.t_seg)
        c.addWidget(sp, 4)
        # ---- Step
        stp = Panel("Step  (전원 출력 단계)")
        self.step_panel = stp
        stl = stp.body_layout()
        sb2 = self._row_tools("step")
        self.lbl_stepsum = label("", sub=True, size=9)
        sb2.insertWidget(0, self.lbl_stepsum)
        stl.addLayout(sb2)
        self.t_step = _tbl(["No", "선택", "시간 (hhmmss)", "온도 ℃", "습도 %", "전압 V", "전류 A"], edit=True)
        _narrow(self.t_step, {P_NO: 44, P_CHK: 48})
        self.t_step.setItemDelegateForColumn(P_TIME, TimeDelegate(self.t_step))
        self.t_step.itemChanged.connect(self.step_changed)
        self.t_step.horizontalHeader().sectionClicked.connect(lambda col: self._check_all("step") if col == P_CHK else None)
        stl.addWidget(self.t_step)
        c.addWidget(stp, 4)
        hp = Panel("입력 안내")
        hl = hp.body_layout(margins=8, spacing=2)
        for r in ["셀을 누르고 바로 입력 · Enter = 확정 · Tab = 다음 칸 · Esc = 입력 취소",
                  "시간: 숫자만 연속 입력 hhmmss (013000 = 1시간 30분, 500 = 5분, 1000000 = 100시간) — 콜론·커서 이동 불필요",
                  "복사할 행 체크 → [복사](체크가 비워짐) → 붙여넣을 위치의 행 체크 → [위에/아래에 붙여넣기]",
                  "Step 조건: 시간·온도·습도 중 입력한 조건이 먼저 충족되면 다음 Step ('—' 또는 빈칸 = 사용 안 함)",
                  "범위를 벗어난 값은 저장할 때 오류(저장 불가) 또는 경고(확인 후 저장)로 안내"]:
            hl.addWidget(label("· " + r, size=9))
        c.addWidget(hp, 1)
        tl = QHBoxLayout()
        self.lbl_total = label("", sub=True)
        self.lbl_clip = label("", color=T.BLU, size=9)
        tl.addWidget(self.lbl_total)
        tl.addStretch(1)
        tl.addWidget(self.lbl_clip)
        c.addLayout(tl)
        h.addLayout(c, 3)
        # ---- 그래프: 온도·습도·전압·전류 한 그래프 (2026-11-13)
        rp = Panel("설정 내용 그래프  (온도 · 습도 · 전압 · 전류)")
        rl = rp.body_layout()
        gb = QHBoxLayout()
        self.g_cb = {}
        for k, name, col in [("T", "온도 ℃", T.TEMP_COLOR), ("H", "습도 %", T.HUMI_COLOR),
                             ("V", "전압 V", T.SERIES["V"]), ("A", "전류 A", T.SERIES["A"])]:
            cb = QCheckBox(name)
            cb.setChecked(True)
            cb.setStyleSheet(f"color:{col};")
            cb.toggled.connect(lambda *_: self.draw())
            self.g_cb[k] = cb
            gb.addWidget(cb)
        gb.addStretch(1)
        rl.addLayout(gb)
        self._build_graph()
        gb2 = QHBoxLayout()
        self.gtools = GraphTools(self.g_vb, on_fit=self.g_fit, hint=False, save_widget=self.gplot,
                                 save_name="Function설정그래프", on_reset=self.g_reset)
        gb2.addWidget(self.gtools)
        gb2.addStretch(1)
        rl.addLayout(gb2)
        rl.addWidget(self.gplot, 1)
        bb2 = QHBoxLayout()
        bb2.addWidget(button("저장", primary=True, min_w=110, slot=self.f_save))
        bb2.addWidget(button("되돌리기", min_w=110, slot=self.f_revert))
        self.lbl_dirty = QLabel()
        bb2.addWidget(self.lbl_dirty)
        bb2.addStretch(1)
        rl.addLayout(bb2)
        h.addWidget(rp, 2)
        self.f: Function | None = None
        self.orig_name = None
        self.dirty = False
        self._busy = False
        return w

    def _row_tools(self, kind):
        """[+ 추가][삽입][삭제][▲][▼] | [복사][위에 붙여넣기][아래에 붙여넣기]"""
        op = self.seg_op if kind == "seg" else self.step_op
        b = QHBoxLayout()
        if kind == "seg":
            b.addWidget(label("목표·시간만 입력 — 변화율은 자동 계산 (파란 글자, 읽기 전용)", sub=True, size=9))
        b.addStretch(1)
        for t, o, tip in [("+ 추가", "add", "맨 아래에 추가"), ("삽입", "ins", "현재 행 위에 삽입"),
                          ("삭제", "del", "체크한 행 삭제 (체크가 없으면 현재 행)"), ("▲", "up", "현재 행 위로"),
                          ("▼", "down", "현재 행 아래로")]:
            bt = button(t, chip=True, slot=lambda _=False, oo=o: op(oo))
            bt.setToolTip(tip)
            b.addWidget(bt)
        b.addSpacing(10)
        for t, o, tip in [("복사", "copy", "체크한 행 복사 (체크가 없으면 현재 행)"),
                          ("↑ 위에 붙여넣기", "paste_above", "복사한 행을 체크한 행 위에 넣기 (여러 행 체크 시 첫 행 위)"),
                          ("↓ 아래에 붙여넣기", "paste_below", "복사한 행을 체크한 행 아래에 넣기 (여러 행 체크 시 마지막 행 아래)")]:
            bt = button(t, chip=True, slot=lambda _=False, oo=o: op(oo))
            bt.setToolTip(tip)
            b.addWidget(bt)
        return b

    def _build_graph(self):
        """축 3개: 왼쪽 = 온도 ℃ / 습도 %, 오른쪽 1 = 전압 V, 오른쪽 2 = 전류 A (메인 화면과 같은 방식)."""
        self.gplot = new_plot()
        self.gplot.setLabel("bottom", "경과 (h)")
        pi = self.gplot.getPlotItem()
        self.g_vb = pi.vb
        pi.showAxis("right")
        self.g_ax_th, self.g_ax_v = pi.getAxis("left"), pi.getAxis("right")
        self.g_ax_a = pg.AxisItem("right")
        pi.layout.addItem(self.g_ax_a, 2, 3)
        self.g_vbv, self.g_vba = pg.ViewBox(), pg.ViewBox()
        for vb, ax in ((self.g_vbv, self.g_ax_v), (self.g_vba, self.g_ax_a)):
            pi.scene().addItem(vb)
            ax.linkToView(vb)
            vb.setXLink(self.g_vb)
            vb.setMouseEnabled(x=False, y=False)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        for ax, text in ((self.g_ax_th, "온도 ℃ / 습도 %"), (self.g_ax_v, "전압 V"), (self.g_ax_a, "전류 A")):
            ax.setLabel(text, color=T.SUB)
            ax.enableAutoSIPrefix(False)
            ax.setTextPen(T.SUB)
            ax.setPen(T.BRD)

        def sync():
            for vb in (self.g_vbv, self.g_vba):
                vb.setGeometry(self.g_vb.sceneBoundingRect())
                vb.linkedViewChanged(self.g_vb, vb.XAxis)
        self.g_vb.sigResized.connect(sync)
        sync()
        self.g_legend = pi.addLegend(offset=(10, 10), colCount=2, labelTextColor=T.TXT)
        self.g_items: list[tuple[pg.GraphicsObject, pg.ViewBox]] = []

    # ---------------------------------------------------------- lists
    def reload_lists(self, select=None):
        names = self.store.names()
        self.fill_flist()
        self.lst.blockSignals(True)
        self.lst.clear()
        self.lst.addItems(names)
        self.lst.blockSignals(False)
        if names:
            target = select if select in names else names[0]
            self.lst.setCurrentRow(names.index(target))
            self.select_function(target, force=True)

    def fill_flist(self, *_):
        selected = self.selected_func() if hasattr(self, "t_func") else None
        q = self.search.text().strip().lower()
        rows = []
        for n in self.store.names():
            if q and q not in n.lower():
                continue
            f = self.store.load(n)
            rows.append((n, f.step_count, hms(f.total_s)))
        self.t_func.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, val in enumerate(vals):
                self.t_func.setItem(r, c, _it(val))
        if selected:
            for r in range(self.t_func.rowCount()):
                if self.t_func.item(r, 0).text() == selected:
                    self.t_func.selectRow(r)
                    break
        self._show_selected_function()

    def edit_from_list(self, r, _c):
        name = self.t_func.item(r, 0).text()
        self.tabs.setCurrentIndex(1)
        items = self.lst.findItems(name, Qt.MatchExactly)
        if items:
            self.lst.setCurrentItem(items[0])

    def on_tab(self, i):
        if i == 0:
            self.fill_flist()

    # ---------------------------------------------------------- editor
    def mark_dirty(self, *_):
        self.dirty = True
        self.lbl_dirty.setText(f"<span style='color:{T.YEL}'>●</span> <span style='color:{T.SUB}'>변경 사항 있음</span>")

    def clear_dirty(self):
        self.dirty = False
        self.lbl_dirty.setText("")

    def select_function(self, name, force=False):
        if not name or (self.f and name == self.orig_name and not force):
            return
        if self.dirty and not force:
            if not confirm(self, "변경 사항", "저장하지 않은 변경 사항이 있습니다. 버리고 이동할까요?", ok="버리고 이동"):
                self.lst.blockSignals(True)
                items = self.lst.findItems(self.orig_name, Qt.MatchExactly)
                if items:
                    self.lst.setCurrentItem(items[0])
                self.lst.blockSignals(False)
                return
        self.f = self.store.load(name)
        self.orig_name = name
        self.seg_chk.clear()
        self.step_chk.clear()
        self.load_editor()

    def load_editor(self):
        f = self.f
        self._busy = True
        self.name.setText(f.name)
        (self.rb_man if f.start_mode == "manual" else self.rb_pv).setChecked(True)
        self.st_t.setValue(f.start_temp)
        self.st_h.setValue(f.start_humi)
        self._busy = False
        self.fill_segments()
        self.clear_dirty()

    def from_header(self, *_):
        if self._busy or not self.f:
            return
        self.f.start_mode = "manual" if self.rb_man.isChecked() else "pv"
        self.f.start_temp, self.f.start_humi = self.st_t.value(), self.st_h.value()
        self.st_t.setEnabled(self.rb_man.isChecked())
        self.st_h.setEnabled(self.rb_man.isChecked())
        self.fill_segments(keep_row=True)
        self.mark_dirty()

    def fill_segments(self, keep_row=False, row=None):
        if row is None:
            row = self.t_seg.currentRow() if keep_row else 0
        f = self.f
        self._busy = True
        rates = f.rates()
        self.t_seg.setRowCount(len(f.segments))
        for r, (s, (rt, rh)) in enumerate(zip(f.segments, rates)):
            t = self.t_seg
            _put(t, r, S_NO, r + 1, center=True)
            _put_check(t, r, S_CHK, id(s) in self.seg_chk)
            _put(t, r, S_TEMP, f"{s.temp:.1f}", True)
            _put(t, r, S_HUMI, "—" if s.humi is None else f"{s.humi:.1f}", True)
            _put(t, r, S_TIME, hms(s.time_s), True)
            _put(t, r, S_RT, f"{rt:+.3f}", color=T.BLU)
            _put(t, r, S_RH, "—" if rh is None else f"{rh:+.3f}", color=T.BLU)
            _put_check(t, r, S_WAIT, s.wait, Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        self._busy = False
        if f.segments:
            row = max(0, min(row, len(f.segments) - 1))
            if self.t_seg.currentRow() != row:          # 같은 행이면 커서(편집 중인 칸)를 옮기지 않음
                self.t_seg.blockSignals(True)
                self.t_seg.setCurrentCell(row, max(self.t_seg.currentColumn(), S_TEMP))
                self.t_seg.blockSignals(False)
            self.show_steps(row)
        self.update_total()
        self.draw()

    def seg_changed(self, it: QTableWidgetItem):
        if self._busy or not self.f:
            return
        r, c = it.row(), it.column()
        s = self.f.segments[r]
        if c == S_CHK:                                   # 체크 = 선택만 (변경 아님)
            (self.seg_chk.add if it.checkState() == Qt.Checked else self.seg_chk.discard)(id(s))
            return
        txt = it.text().strip()
        try:
            if c == S_TEMP:
                s.temp = float(txt)
            elif c == S_HUMI:
                s.humi = None if txt in ("", "—", "-") else float(txt)
            elif c == S_TIME:
                s.time_s = parse_hms(txt) or 0
            elif c == S_WAIT:
                s.wait = it.checkState() == Qt.Checked
        except ValueError as e:
            info(self, "입력 오류", f"'{txt}' 는 올바른 값이 아닙니다.\n{e if c == S_TIME else ''}", warn=True)
        self.fill_segments(keep_row=True)
        self.mark_dirty()

    # ---- 체크한 행 (없으면 현재 행)
    def _target_rows(self, kind) -> list[int]:
        tbl, chk = (self.t_seg, S_CHK) if kind == "seg" else (self.t_step, P_CHK)
        rows = checked_rows(tbl, chk)
        if not rows and tbl.currentRow() >= 0:
            rows = [tbl.currentRow()]
        return rows

    def _check_all(self, kind):
        if not self.f:
            return
        objs = self.f.segments if kind == "seg" else (self.cur_seg().steps if self.cur_seg() else [])
        chk = self.seg_chk if kind == "seg" else self.step_chk
        ids = {id(o) for o in objs}
        if ids and ids <= chk:
            chk -= ids
        else:
            chk |= ids
        if kind == "seg":
            self.fill_segments(keep_row=True)
        else:
            self.show_steps(self.t_seg.currentRow())

    def seg_op(self, op):
        if not self.f:
            return
        segs = self.f.segments
        r = self.t_seg.currentRow()
        if op == "add":
            last = segs[-1] if segs else Segment()
            segs.append(Segment(last.temp, last.humi, 3600, False, [Step(3600, None, None, 0.0, 0.0)]))
            r = len(segs) - 1
        elif op == "ins" and r >= 0:
            segs.insert(r, Segment(segs[r].temp, segs[r].humi, 3600, False, [Step(3600, None, None, 0.0, 0.0)]))
        elif op == "del":
            rows = self._target_rows("seg")
            if not rows:
                return
            if len(segs) - len(rows) < 1:
                return info(self, "삭제", "Segment 는 1개 이상 있어야 합니다.", warn=True)
            if len(rows) > 1 and not confirm(self, "삭제", f"체크한 Segment {len(rows)}개 (No {', '.join(str(i + 1) for i in rows)})를 삭제합니다.",
                                             ok="삭제", warn=True):
                return
            for i in sorted(rows, reverse=True):
                self.seg_chk.discard(id(segs[i]))
                segs.pop(i)
            r = min(rows[0], len(segs) - 1)
        elif op == "up" and r > 0:
            segs[r - 1], segs[r] = segs[r], segs[r - 1]
            r -= 1
        elif op == "down" and 0 <= r < len(segs) - 1:
            segs[r + 1], segs[r] = segs[r], segs[r + 1]
            r += 1
        elif op == "copy":
            rows = self._target_rows("seg")
            if not rows:
                return info(self, "복사", "복사할 Segment 를 체크하거나 행을 누르세요.")
            self.clip_seg = [copy.deepcopy(segs[i]) for i in rows]
            self.seg_chk.clear()                      # 체크를 비워 붙여넣을 위치를 바로 체크할 수 있게
            self._refresh_checks("seg")
            self._show_clip()
            return
        elif op in ("paste_above", "paste_below"):
            if not self.clip_seg:
                return info(self, "붙여넣기", "먼저 Segment 를 [복사] 하세요.")
            pos = self._paste_pos("seg", op, r, len(segs))
            segs[pos:pos] = [copy.deepcopy(s) for s in self.clip_seg]
            self.seg_chk.clear()
            r = pos
        else:
            return
        self.fill_segments(row=max(r, 0))
        self.mark_dirty()

    def show_steps(self, r):
        if not self.f or r is None or r < 0 or r >= len(self.f.segments):
            return
        seg = self.f.segments[r]
        if getattr(self, "_steps_of", None) is not seg:
            self._commit_open_editor(self.t_step)    # 다른 Segment 로 바뀌기 전에 입력 중인 칸을 원래 Segment 에 반영
        self._steps_of = seg                          # Step 표가 보여 주는 Segment (step_changed 가 이 Segment 에 씀)
        self._busy = True
        self.step_panel.title.setText(f"Segment {r + 1} · Step  (전원 출력 단계)")
        self.t_step.setRowCount(len(seg.steps))
        for i, st in enumerate(seg.steps):
            vals = {P_NO: i + 1, P_TIME: hms(st.time_s), P_TEMP: "—" if st.temp is None else f"{st.temp:.1f}",
                    P_HUMI: "—" if st.humi is None else f"{st.humi:.1f}", P_VOLT: f"{st.volt:.2f}", P_CURR: f"{st.curr:.2f}"}
            for c, val in vals.items():
                _put(self.t_step, i, c, val, editable=c != P_NO, center=c == P_NO)
            _put_check(self.t_step, i, P_CHK, id(st) in self.step_chk)
        self._busy = False
        tsum = sum(st.time_s or 0 for st in seg.steps)
        only_time = all(st.time_s is not None and st.temp is None and st.humi is None for st in seg.steps)
        d = tsum - seg.time_s
        if only_time and d:
            msg = f"<span style='color:{T.YEL}'>Step 합계 {hms(tsum)} / Segment {hms(seg.time_s)} ({hms(abs(d))} {'부족' if d < 0 else '초과'})</span>"
        else:
            msg = f"Step 합계 {hms(tsum)} / Segment {hms(seg.time_s)}"
        self.lbl_stepsum.setText(msg)
        self.draw()

    def _commit_open_editor(self, tbl):
        """표에 열려 있는 입력칸이 있으면 지금 확정하고 닫는다 (2026-11-16)."""
        if tbl.state() != QAbstractItemView.EditingState:
            return
        fw = QApplication.focusWidget()
        if fw is not None and tbl.viewport().isAncestorOf(fw):
            tbl.commitData(fw)
            tbl.closeEditor(fw, QAbstractItemDelegate.NoHint)

    def cur_seg(self):
        r = self.t_seg.currentRow()
        return self.f.segments[r] if self.f and 0 <= r < len(self.f.segments) else None

    def step_changed(self, it):
        if self._busy:
            return
        seg = getattr(self, "_steps_of", None) or self.cur_seg()
        if not seg or not 0 <= it.row() < len(seg.steps):
            return
        st = seg.steps[it.row()]
        c = it.column()
        if c == P_CHK:
            (self.step_chk.add if it.checkState() == Qt.Checked else self.step_chk.discard)(id(st))
            return
        txt = it.text().strip()
        num = lambda: None if txt in ("", "—", "-") else float(txt)
        try:
            if c == P_TIME:
                st.time_s = parse_hms(txt)
            elif c == P_TEMP:
                st.temp = num()
            elif c == P_HUMI:
                st.humi = num()
            elif c == P_VOLT:
                st.volt = float(txt or 0)
            elif c == P_CURR:
                st.curr = float(txt or 0)
        except ValueError as e:
            info(self, "입력 오류", f"'{txt}' 는 올바른 값이 아닙니다.\n{e if c == P_TIME else ''}", warn=True)
        self.show_steps(self.t_seg.currentRow())
        self.update_total()
        self.mark_dirty()

    def step_op(self, op):
        seg = self.cur_seg()
        if not seg:
            return
        st = seg.steps
        r = self.t_step.currentRow()
        if op == "add":
            st.append(Step(600, None, None, st[-1].volt if st else 0.0, st[-1].curr if st else 0.0))
            r = len(st) - 1
        elif op == "ins" and r >= 0:
            st.insert(r, Step(600, None, None, st[r].volt, st[r].curr))
        elif op == "del":
            rows = self._target_rows("step")
            if not rows:
                return
            if len(st) - len(rows) < 1:
                return info(self, "삭제", "Step 은 1개 이상 있어야 합니다.", warn=True)
            if len(rows) > 1 and not confirm(self, "삭제", f"체크한 Step {len(rows)}개 (No {', '.join(str(i + 1) for i in rows)})를 삭제합니다.",
                                             ok="삭제", warn=True):
                return
            for i in sorted(rows, reverse=True):
                self.step_chk.discard(id(st[i]))
                st.pop(i)
            r = min(rows[0], len(st) - 1)
        elif op == "up" and r > 0:
            st[r - 1], st[r] = st[r], st[r - 1]
            r -= 1
        elif op == "down" and 0 <= r < len(st) - 1:
            st[r + 1], st[r] = st[r], st[r + 1]
            r += 1
        elif op == "copy":
            rows = self._target_rows("step")
            if not rows:
                return info(self, "복사", "복사할 Step 을 체크하거나 행을 누르세요.")
            self.clip_step = [copy.deepcopy(st[i]) for i in rows]
            self.step_chk.clear()                      # 체크를 비워 붙여넣을 위치를 바로 체크할 수 있게
            self._refresh_checks("step")
            self._show_clip()
            return
        elif op in ("paste_above", "paste_below"):
            if not self.clip_step:
                return info(self, "붙여넣기", "먼저 Step 을 [복사] 하세요.")
            pos = self._paste_pos("step", op, r, len(st))
            st[pos:pos] = [copy.deepcopy(x) for x in self.clip_step]
            self.step_chk.clear()
            r = pos
        else:
            return
        self.show_steps(self.t_seg.currentRow())
        self.t_step.setCurrentCell(max(r, 0), P_TIME)
        self.update_total()
        self.mark_dirty()

    def _paste_pos(self, kind, op, r, n):
        """붙여넣을 위치 (2026-11-16): 체크한 행 기준 — 위에 = 체크한 첫 행 앞, 아래에 = 체크한 마지막 행 뒤.
        체크가 없으면 현재 행, 현재 행도 없으면 맨 끝."""
        tbl, col = (self.t_seg, S_CHK) if kind == "seg" else (self.t_step, P_CHK)
        rows = checked_rows(tbl, col)
        if rows:
            return rows[0] if op == "paste_above" else rows[-1] + 1
        if r < 0:
            return n
        return r + 1 if op == "paste_below" else r

    def _refresh_checks(self, kind):
        tbl, col = (self.t_seg, S_CHK) if kind == "seg" else (self.t_step, P_CHK)
        tbl.blockSignals(True)
        for i in range(tbl.rowCount()):
            it = tbl.item(i, col)
            if it is not None:
                it.setCheckState(Qt.Unchecked)
        tbl.blockSignals(False)

    def _show_clip(self):
        parts = []
        if self.clip_seg:
            parts.append(f"Segment {len(self.clip_seg)}개")
        if self.clip_step:
            parts.append(f"Step {len(self.clip_step)}개")
        self.lbl_clip.setText("복사됨: " + " · ".join(parts) + "  → 붙여넣을 위치의 행을 체크하고 [위에/아래에 붙여넣기]")

    def update_total(self):
        f = self.f
        if f:
            self.lbl_total.setText(f"Function 총 {hms(f.total_s)} · Segment {len(f.segments)} · Step {f.step_count}")

    # ---------------------------------------------------------- 그래프
    def draw(self):
        p = self.gplot
        for item, vb in self.g_items:
            vb.removeItem(item)
        self.g_items.clear()
        self.g_legend.clear()
        f = self.f
        if not f:
            return
        sel = self.t_seg.currentRow()
        t = 0.0
        x0 = f.start_temp if f.start_mode == "manual" else (self.hub.cr.pv_t if self.hub.cr else f.start_temp)
        h0 = f.start_humi if f.start_mode == "manual" else (self.hub.cr.pv_h if self.hub.cr else f.start_humi)
        xs, ts, hs = [0.0], [x0], [h0]
        vx, vv, va = [], [], []
        for i, s in enumerate(f.segments):
            t1 = t + s.time_s / 3600
            if i == sel:
                region = pg.LinearRegionItem((t, t1), movable=False, brush=pg.mkBrush(31, 111, 178, 60), pen=pg.mkPen(None))
                self.g_vb.addItem(region)
                self.g_items.append((region, self.g_vb))
            xs.append(t1)
            ts.append(s.temp)
            hs.append(s.humi if s.humi is not None else hs[-1])
            tt = t
            for st in s.steps:
                dur = (st.time_s or 0) / 3600 or s.time_s / 3600 / max(1, len(s.steps))
                vx += [tt, min(t1, tt + dur)]
                vv += [st.volt, st.volt]
                va += [st.curr, st.curr]
                tt = min(t1, tt + dur)
            t = t1
        self._g_total = t
        on = {k: cb.isChecked() for k, cb in self.g_cb.items()}
        series = [("T", "온도 ℃", xs, ts, T.TEMP_COLOR, Qt.SolidLine, self.g_vb),
                  ("H", "습도 %", xs, hs, T.HUMI_COLOR, Qt.DotLine, self.g_vb),
                  ("V", "전압 V", vx, vv, T.SERIES["V"], Qt.SolidLine, self.g_vbv),
                  ("A", "전류 A", vx, va, T.SERIES["A"], Qt.DashLine, self.g_vba)]
        for k, name, x, y, col, style, vb in series:
            if not on[k] or not x:
                continue
            item = pg.PlotDataItem(x, y, pen=pg.mkPen(col, width=2.2, style=style), name=name)
            vb.addItem(item)
            self.g_items.append((item, vb))
            self.g_legend.addItem(item, name)
        self.g_ax_th.setStyle(showValues=on["T"] or on["H"])
        self.g_ax_v.setVisible(on["V"])
        self.g_ax_a.setVisible(on["A"])
        p.update()
        self.g_fit()

    def g_reset(self):
        for cb in self.g_cb.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self.draw()
        self.gtools.reset_view()

    def g_fit(self):
        self.g_vb.history.clear()
        total = getattr(self, "_g_total", 1.0) or 1.0
        self.g_vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.g_vb.setXRange(0, total, padding=0.02)
        for vb in (self.g_vbv, self.g_vba):
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)

    # ---------------------------------------------------------- file ops
    def f_new(self):
        name, ok = QInputDialog.getText(self, "새 Function", "Function 이름")
        if ok and name.strip():
            if self.store.exists(name):
                return info(self, "새 Function", "같은 이름이 있습니다.", warn=True)
            self.store.save(Function(name.strip()))
            self.clear_dirty()
            self.reload_lists(select=name.strip())

    def f_copy(self):
        if not self.f:
            return
        name, ok = QInputDialog.getText(self, "복사", "새 이름", text=self.f.name + " (복사)")
        if ok and name.strip() and not self.store.exists(name):
            f = copy.deepcopy(self.f)
            f.name = name.strip()
            self.store.save(f)
            self.clear_dirty()
            self.reload_lists(select=f.name)

    def f_delete(self):
        if not self.f:
            return
        used = [d["ch"] for d in self.hub.snapshot()["channels"] if d["func"] == self.orig_name]
        if used:
            return info(self, "삭제", f"P{used} 에 할당 중이라 삭제할 수 없습니다.", warn=True)
        if confirm(self, "삭제", f"'{self.orig_name}' 을(를) 삭제합니다.", ok="삭제", warn=True):
            self.store.delete(self.orig_name)
            self.f = None
            self.clear_dirty()
            self.reload_lists()

    def f_save(self):
        if not self.f:
            return
        self.f.name = self.name.text().strip()
        if self.f.name != self.orig_name and self.store.exists(self.f.name):
            return info(self, "저장", "같은 이름의 Function 이 있습니다.", warn=True)
        err, warn = self.f.validate(self.cfg)
        if err:
            return info(self, "저장 불가", "\n".join(err), warn=True)
        if warn and not confirm(self, "확인 후 저장", "\n".join(warn) + "\n\n이대로 저장할까요?", ok="저장", warn=True):
            return
        self.store.save(self.f, self.orig_name)
        self.hub.log.write("USER", f"Function 저장 '{self.f.name}'")
        self.clear_dirty()
        self.reload_lists(select=self.f.name)

    def f_revert(self):
        if self.orig_name:
            self.clear_dirty()
            self.select_function(self.orig_name, force=True)

    def closeEvent(self, e):
        if self.dirty and not confirm(self, "닫기", "저장하지 않은 변경 사항이 있습니다. 저장하지 않고 닫을까요?", ok="닫기"):
            e.ignore()
            return
        try:
            self.hub.updated.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        e.accept()

    def reject(self):
        self.close()


# ====================================================================== Function Profile 창 (2026-11-13)
class ProfileExportDialog(QDialog):
    """내보낼 Function 을 체크박스로 고른다."""

    def __init__(self, parent, names: list[str], preselect: set[str]):
        super().__init__(parent)
        self.setWindowTitle("Function Profile 내보내기")
        self.resize(520, 560)
        v = QVBoxLayout(self)
        v.addWidget(label("내보낼 Function 을 체크하세요. Temperature Segment 와 Step 이 모두 한 파일(.hfp)에 저장됩니다.",
                          sub=True))
        self.lst = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if n in preselect else Qt.Unchecked)
            self.lst.addItem(it)
        v.addWidget(self.lst, 1)
        self.lbl = label("", sub=True)
        v.addWidget(self.lbl)
        self.lst.itemChanged.connect(lambda *_: self._count())
        h = QHBoxLayout()
        h.addWidget(button("전체 선택 / 해제", slot=self.toggle_all))
        h.addStretch(1)
        h.addWidget(button("내보내기", primary=True, min_w=110, slot=self._ok))
        h.addWidget(button("취소", min_w=90, slot=self.reject))
        v.addLayout(h)
        self._count()

    def _items(self):
        return [self.lst.item(i) for i in range(self.lst.count())]

    def chosen(self):
        return [it.text() for it in self._items() if it.checkState() == Qt.Checked]

    def toggle_all(self):
        on = len(self.chosen()) < self.lst.count()
        for it in self._items():
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _count(self):
        self.lbl.setText(f"선택 {len(self.chosen())} / {self.lst.count()}개")

    def _ok(self):
        if not self.chosen():
            return info(self, "Function Profile 내보내기", "Function 을 1개 이상 체크하세요.")
        self.accept()


class ProfileImportDialog(QDialog):
    """가져올 Function 확인 — 체크박스 · 같은 이름 처리(이름 바꾸기/덮어쓰기) · 가져올 이름 수정."""
    C_CHK, C_SRC, C_SEG, C_STEP, C_TIME, C_STATE, C_NAME = range(7)

    def __init__(self, parent, funcs, meta, bad, store, cfg, running: set[str]):
        super().__init__(parent)
        self.funcs, self.store, self.running = funcs, store, running
        self.setWindowTitle("Function Profile 가져오기")
        self.resize(1080, 560)
        v = QVBoxLayout(self)
        src = meta.get("source") or "-"
        when = meta.get("exported_at") or "-"
        v.addWidget(label(f"파일: 내보낸 PC {src} · 내보낸 시각 {when} · Function {len(funcs)}개", sub=True))
        mode = QHBoxLayout()
        mode.addWidget(label("같은 이름이 이미 있으면", sub=True))
        self.rb_rename, self.rb_over = QRadioButton("이름 바꿔 가져오기 (예: 이름 (2))"), QRadioButton("덮어쓰기")
        self.rb_rename.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_rename)
        grp.addButton(self.rb_over)
        mode.addWidget(self.rb_rename)
        mode.addWidget(self.rb_over)
        mode.addStretch(1)
        v.addLayout(mode)
        self.tbl = _tbl(["선택", "Profile 의 이름", "Segment", "Step", "총 시간", "이 PC 확인", "가져올 이름 (수정 가능)"])
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl.setEditTriggers(QAbstractItemView.AllEditTriggers)
        _narrow(self.tbl, {self.C_CHK: 56, self.C_SEG: 80, self.C_STEP: 64, self.C_TIME: 100})
        self.tbl.setRowCount(len(funcs))
        self.errs = []
        for r, f in enumerate(funcs):
            err, warn = f.validate(cfg)
            self.errs.append(err)
            self.tbl.setItem(r, self.C_CHK, check_item(not err, enabled=not err))
            for c, val in ((self.C_SRC, f.name), (self.C_SEG, len(f.segments)), (self.C_STEP, f.step_count),
                           (self.C_TIME, hms(f.total_s))):
                self.tbl.setItem(r, c, _it(val, center=c != self.C_SRC))
            st = _it("")
            if err:
                st.setText("가져올 수 없음 — " + err[0])
                st.setForeground(pg.mkColor(T.RED))
                st.setToolTip("\n".join(err))
            elif warn:
                st.setToolTip("\n".join(warn))
            self.tbl.setItem(r, self.C_STATE, st)
            self.tbl.setItem(r, self.C_NAME, _it(f.name, editable=not err))
        setup_check_column(self.tbl, self.C_CHK)
        v.addWidget(self.tbl, 1)
        if bad:
            v.addWidget(label("읽지 못한 항목: " + " / ".join(bad), color=T.YEL, size=9))
        v.addWidget(label("가져온 Function 은 ② Function 편집 탭에서 바로 편집·저장할 수 있습니다. "
                          "실행 중인 채널이 쓰는 Function 은 덮어쓸 수 없습니다.", sub=True, size=9))
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(button("가져오기", primary=True, min_w=110, slot=self._ok))
        h.addWidget(button("취소", min_w=90, slot=self.reject))
        v.addLayout(h)
        self.rb_rename.toggled.connect(lambda *_: self._names())
        self.tbl.itemChanged.connect(self._edited)
        self._names()

    def _names(self):
        """같은 이름 처리 방식에 따라 '가져올 이름'·'이 PC 확인' 칸을 다시 채운다."""
        self.tbl.blockSignals(True)
        taken = set()
        for r, f in enumerate(self.funcs):
            if self.errs[r]:
                continue
            name = f.name
            if self.rb_rename.isChecked():
                name = unique_name(self.store, f.name, taken)
            taken.add(name)
            self.tbl.item(r, self.C_NAME).setText(name)
        self.tbl.blockSignals(False)
        self._states()

    def _edited(self, it):
        if it.column() == self.C_NAME:
            self._states()

    def _states(self):
        self.tbl.blockSignals(True)
        for r, f in enumerate(self.funcs):
            if self.errs[r]:
                continue
            name = self.tbl.item(r, self.C_NAME).text().strip()
            st = self.tbl.item(r, self.C_STATE)
            if name in self.running and self.store.exists(name):
                txt, col = "실행 중인 Function — 덮어쓸 수 없음 (이름을 바꾸세요)", T.RED
            elif self.store.exists(name):
                txt, col = "같은 이름 있음 → 덮어씀", T.YEL
            elif name != f.name:
                txt, col = "새로 추가 (이름 바꿈)", T.BLU
            else:
                txt, col = "새로 추가", T.GRN
            if st.toolTip() and col != T.RED:
                txt += "  · 경고 있음(툴팁)"
            st.setText(txt)
            st.setForeground(pg.mkColor(col))
        self.tbl.blockSignals(False)

    def result_items(self):
        out = []
        for r in checked_rows(self.tbl, self.C_CHK):
            name = self.tbl.item(r, self.C_NAME).text().strip()
            out.append((self.funcs[r], name, self.store.exists(name)))
        return out

    def _ok(self):
        import re
        rows = checked_rows(self.tbl, self.C_CHK)
        if not rows:
            return info(self, "Function Profile 가져오기", "가져올 Function 을 체크하세요.")
        names, problems = [], []
        for r in rows:
            name = self.tbl.item(r, self.C_NAME).text().strip()
            if not name or re.search(r'[\\/:*?"<>|]', name):
                problems.append(f"{r + 1}행: 이름이 비었거나 \\ / : * ? \" < > | 가 있습니다")
            elif name in names:
                problems.append(f"{r + 1}행: '{name}' 이(가) 다른 행과 같습니다")
            elif name in self.running and self.store.exists(name):
                problems.append(f"{r + 1}행: '{name}' 은(는) 실행 중이라 덮어쓸 수 없습니다")
            names.append(name)
        if problems:
            return info(self, "Function Profile 가져오기", "\n".join(problems), warn=True)
        over = [n for n in names if self.store.exists(n)]
        if over and not confirm(self, "덮어쓰기", "이 PC 에 있는 다음 Function 을 덮어씁니다.\n\n· " + "\n· ".join(over),
                                ok="덮어쓰고 가져오기", warn=True):
            return
        self.accept()
