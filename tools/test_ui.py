"""화면 기능 시험 (2026-11-13 작성, 2026-11-16 표시 범위·붙여넣기 위치·전류 편차·초기화 반영) — 시뮬레이터 + 화면 없이(offscreen) 실행.

python tools/test_ui.py          → [OK]/[FAIL] 목록, 화면 캡처는 임시 폴더의 shots/ 에 저장
POWER 토글, 패널 최소화/최대화, 시간 이동, 그래프 초기화·JPEG, 전류 기준 알람·해제, Test Power 체크박스,
시간 숫자 입력(hhmmss), Segment/Step 복사·붙여넣기, 통합 그래프, Function Profile 내보내기·가져오기, Result 체크 조회.
확인창·파일 대화상자는 자동으로 '확인'·임시 경로로 대신한다.
"""
import os, sys, time, tempfile, json
HOME = tempfile.mkdtemp(); os.environ["HYOSUNG_HMI_HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, H)
D = HOME
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication(sys.argv); app.setFont(QFont("Malgun Gothic" if sys.platform == "win32" else "Noto Sans CJK KR", 10))
from presentation.styling import resources as R, theme; R.apply_app_identity(app, "t"); app.setStyleSheet(theme.QSS.replace("'Malgun Gothic', ", ""))
from presentation.widgets import widgets
widgets_confirm_log = []
def _confirm(parent, title, text, **k): widgets_confirm_log.append(title); return True
def _info(parent, title, text, warn=False): widgets_confirm_log.append(("info", title, text[:80]))
widgets.confirm = _confirm; widgets.info = _info
from presentation.views import main_window, dialogs, function_manager
from presentation.widgets.widgets import checked_rows
for m in (main_window, dialogs, function_manager): m.confirm = _confirm; m.info = _info
SAVE = {}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (SAVE["save"], ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (SAVE["open"], ""))
from infrastructure.system.config import Config; from application.services.hub import DeviceHub; from presentation.views.main_window import MainWindow
cfg = Config.load(); cfg.simulate = True; cfg.i_check_delay_s = 2
hub = DeviceHub(cfg); win = MainWindow(hub, cfg); win.resize(1920, 1080); hub.start(); win.show()
OUT = os.environ.get("TEST_UI_SHOTS") or os.path.join(D, "shots"); os.makedirs(OUT, exist_ok=True)
def pump(s):
    t = time.time()
    while time.time() - t < s: app.processEvents(); time.sleep(0.03)
RES = []
def ok(c, m):
    RES.append(bool(c)); print(("[OK]  " if c else "[FAIL]") + " " + m)
hub.call(hub.chamber_apply, "fix", 40.0, 60.0, run_after=True)
pump(6)
# ---- 8 chip toggle
win.on_ch_chip(2); s1 = set(win.sel); win.on_ch_chip(2); s2 = set(win.sel); win.on_ch_chip(3)
ok(2 in s1 and 2 not in s2 and 3 in win.sel, f"POWER 토글 {s1} -> {s2} -> {win.sel}")
win.on_ch_chip("All"); a1 = set(win.sel); win.on_ch_chip("All"); ok(len(a1) == 6 and not win.sel, "All 토글")
win.on_ch_chip(1); win.on_ch_chip(2); win.on_ch_chip(3); win.on_ch_chip(6)
# ---- 14 current limits: P1 min 100 A → alarm
hub.call(hub.set_current_limits, [1], 100.0, 200.0); hub.call(hub.set_current_limits, [2], 0.0, 50.0)
pump(5)
c1 = win.snap["channels"][0]
ok(bool(c1["i_alarm"]), f"P1 전류 기준 이탈 알람: {c1['i_alarm']}")
ok(not win.snap["channels"][1]["i_alarm"], "P2 범위 안 → 알람 없음")
ok(win.cards[1].bar_i.state()[1] == "이상", f"P1 전류 편차 막대 '이상' (측정 < Min) — {win.cards[1].bar_i.state()[1]}")
ok(win.cards[2].bar_i.state()[1] == "안정", f"P2 전류 편차 막대 '안정' — {win.cards[2].bar_i.state()[1]}")
ok("알람 전체 해제 (" in win.btn_clear.text(), f"해제 버튼 표시: {win.btn_clear.text()}")
ok(win.cards[1].lbl_v.text().count(".") == 1 and len(win.cards[1].lbl_v.text().split(".")[1]) == 2, f"카드 소수 2자리 {win.cards[1].lbl_v.text()} / {win.cards[1].lbl_a.text()}")
win.grab().save(f"{OUT}/01_main_alarm.png")
# ---- 7 panel min / max
win.minimize_panel("pc"); pump(0.3); win.grab().save(f"{OUT}/02_main_chamber_min.png")
ok(not win.panels["pc"].isVisible() and win.minbar.isVisible(), "챔버 최소화 → 복원 줄")
win.maximize_panel("pd"); pump(0.3); win.grab().save(f"{OUT}/03_main_graph_max.png")
ok(win.panels["pd"].isVisible() and not win.top_w.isVisible(), "실시간 모니터링 최대화")
win.maximize_panel("pd"); win.restore_panel("pc"); pump(0.3)
ok(all(p.isVisible() for p in win.panels.values()) and not win.minbar.isVisible(), "모두 복원")
# ---- 11 표시 범위 (2026-11-16): 10분 → +10분 = 20분 → +10분 = 30분 → −10분 = 20분 → −10분 = 10분
span = lambda: win.vb_v.viewRange()[0][1] - win.vb_v.viewRange()[0][0]
win.set_range(600); pump(0.2); s10 = span()
win.adjust_range(600); pump(0.2); s20 = span()
win.adjust_range(600); pump(0.2); s30 = span()
ok(win.range_s == 1800 and abs(s20 - s10 - 600) < 30 and abs(s30 - s20 - 600) < 30,
   f"+10분 두 번: 10분 → 20분 → 30분 (폭 {s10/60:.0f}/{s20/60:.0f}/{s30/60:.0f}분, 표시 '{win.lbl_range.text()}')")
win.adjust_range(-600); win.adjust_range(-600); pump(0.2)
ok(win.range_s == 600 and abs(span() - s10) < 30, f"−10분 두 번: 30분 → 10분 ({win.lbl_range.text()})")
win.adjust_range(-600); ok(win.range_s == 600 and not win.span_btns[-600].isEnabled(), "최소 10분, [−10분] 비활성")
win.adjust_range(3600); ok(win.range_s == 4200 and win.lbl_range.text() == "1시간 10분", f"+1시간 → {win.lbl_range.text()}")
win.adjust_range(-3600); ok(win.range_s == 600, "−1시간")
ok(win.follow and abs(win.vb_v.viewRange()[0][1] - time.time()) < 15, "범위 조절 후에도 현재 시각까지 표시")
win.set_range(1800); pump(0.2)
gt_row = win.gtools.parentWidget()
ok(all(w.parentWidget() is gt_row for w in (win.ch_btns[1], win.lbl_range, win.span_btns[600]))
   and abs(win.ch_btns[1].y() - win.span_btns[600].y()) < 12, "그래프 조작 버튼 한 줄")
top_h, pd_h = win.top_w.height(), win.panels["pd"].height()
ok(abs(top_h - pd_h) < 40, f"기본 높이 위 {top_h} / 아래 {pd_h} (50 : 50)")
ok(win.legend_view.isVisible() and win.legend_view.parentWidget() is not win.plot,
   "실시간 그래프 범례가 데이터 영역 밖의 전용 영역에 표시")
ok(win.legend_view.height() == 26 * ((len(win._series()) + 6) // 7),
   f"범례 항목 수에 따라 높이 확보 ({len(win._series())}개 / {win.legend_view.height()}px)")
ok(win.legend_content.x() <= 10 and win.legend_content.sizeHint().width() < win.legend_view.width() * 0.75,
   f"범례 항목 좌측 밀착 정렬 (x={win.legend_content.x()}, 폭={win.legend_content.sizeHint().width()}px)")
# P6만 선택한 상태에서 ALL 전환 시 기존 P6 범례가 남아 중복되던 회귀 검사
win.sel = {6}; win.redraw(); win.on_ch_chip("All"); pump(0.1)
all_legend_names = list(win._legend_names)
expected_legend_names = [f"P{i} {kind}" for i in range(1, 7) for kind in ("전압", "전류")] + ["온도", "습도"]
ok(all_legend_names == expected_legend_names and len(all_legend_names) == len(set(all_legend_names)),
   f"P6 개별 → ALL 범례 중복 없이 순서대로 표시 ({len(all_legend_names)}개)")
ok(len(win._legend_widgets) == 14 and
   len({(win.legend_layout.getItemPosition(i)[0], win.legend_layout.getItemPosition(i)[1])
        for i in range(win.legend_layout.count())}) == 14,
   "ALL 범례 14개가 서로 다른 Grid 칸에 배치")
win.grab().save(f"{OUT}/04a_main_graph_all_legend.png")
win.sel = {1, 2, 3, 6}; win.sync_chips(); win.redraw()
# ---- 3 전류 편차 막대
c4 = win.cards[1].bar_i
ok(c4.state()[1] in ("안정", "이상", "기준 없음"), f"P01 전류 편차 막대: {c4.state()[1]} (편차 {c4.dev:+.2f} A)")
win.grab().save(f"{OUT}/04_main_graph.png")
# ---- 10 JPEG
SAVE["save"] = os.path.join(HOME, "g.jpg"); p = win.gtools.save()
ok(p and os.path.getsize(p) > 10000, f"JPEG 저장 {p} {os.path.getsize(p) if p else 0} bytes")
# ---- 9 reset
n_before = len(win.buf[1]); win.reset_graph(); ok(len(win.buf[1]) == 0 and n_before > 0 and win.follow, f"그래프 초기화 buf {n_before} → 0")
pump(2.2); ok(len(win.buf[1]) >= 1, "초기화 후 다시 그림")
# clear alarms
win.clear_alarms(); pump(1.5)
ok(not win.snap["channels"][0]["i_alarm"], "알람 전체 해제 → 카드 알람 사라짐")
win.grab().save(f"{OUT}/05_main_cleared.png")
pump(3)
ok(bool(win.snap["channels"][0]["i_alarm"]), "여전히 벗어나면 다시 알람 (유예 후)")
hub.call(hub.set_current_limits, [1], 0.1, 60.0); win.clear_alarms(); pump(4)
ok(not win.snap["channels"][0]["i_alarm"], "기준 고친 뒤 해제 → 유지")
# ---- 1 Test Power
d = dialogs.TestPowerDialog(win, hub, cfg, preselect=sorted(win.sel)); d.resize(940, 640); d.show(); pump(1.3)
ok(d.chs() == sorted(win.sel), f"Test Power 미리 체크 {d.chs()}")
ok(d.tbl.selectionMode() == d.tbl.SelectionMode.NoSelection, "Ctrl/Shift 선택 없음 (NoSelection)")
d.tbl.horizontalHeader().sectionClicked.emit(1); pump(0.1); ok(d.chs() == [1, 2, 3, 4, 5, 6], "머리글 = 전체 체크")
d.tbl.horizontalHeader().sectionClicked.emit(1); ok(d.chs() == [], "머리글 다시 = 전체 해제")
d.tbl.cellClicked.emit(3, 2); d.tbl.cellClicked.emit(4, 4); ok(d.chs() == [4, 5], f"행 클릭 체크 {d.chs()}")
d.tbl.cellClicked.emit(3, 5); ok(d.chs() == [5], "행 다시 클릭 = 해제")
pump(1.2); ok(d.chs() == [5], "1초 갱신 후에도 체크 유지")
d.lim.set(1.0, 20.0); d.set_limits(); pump(1.2)
ok(hub.ch[5].i_min == 1.0 and hub.ch[5].i_max == 20.0, "Test Power 기준 적용 P05")
ok(cfg.i_limits.get("5") == [1.0, 20.0], "기준 config 저장")
d.tbl.cellClicked.emit(0, 2)
d.grab().save(f"{OUT}/06_testpower.png"); d.hide()
# ---- Function manager
fm = function_manager.FunctionManager(win, hub, cfg); fm.resize(1760, 960); fm.show(); pump(0.3)
fm.t_ch.cellClicked.emit(3, 2); fm.t_func.selectRow(0); fm.lim.set(0.5, 35.0)
fm.do_assign(); pump(1.3)
ok(hub.ch[4].assign and hub.ch[4].i_max == 35.0, f"할당 + 전류 기준 P04 ({hub.ch[4].assign and hub.ch[4].assign.func})")
fm.grab().save(f"{OUT}/07_function_assign.png")
fm.tabs.setCurrentIndex(1); pump(0.3)
names = fm.store.names(); fm.lst.setCurrentRow(names.index("DENSO HTHE")); pump(0.2)
# 3 time entry: type digits into time cell
def click_type(t, r, c, txt):
    rect = t.visualRect(t.model().index(r, c)); QTest.mouseClick(t.viewport(), Qt.LeftButton, pos=rect.center()); pump(0.05)
    fw = app.focusWidget(); sel = fw.selectedText(); QTest.keyClicks(fw, txt); QTest.keyClick(fw, Qt.Key_Return); pump(0.1); return sel
sel0 = click_type(fm.t_seg, 0, function_manager.S_TIME, "013000")
print("   편집기 열림 상태(전체 선택된 숫자):", sel0)
ok(fm.f.segments[0].time_s == 5400, f"시간 숫자 연속 입력 013000 → {fm.f.segments[0].time_s}s ({fm.t_seg.item(0, function_manager.S_TIME).text()})")
click_type(fm.t_step, 0, function_manager.P_TIME, "2500")
ok(fm.f.segments[0].steps[0].time_s == 1500, f"Step 시간 2500 → {fm.f.segments[0].steps[0].time_s}s")
# 5 copy/paste (2026-11-16): 1·2행 체크 → 복사(체크 비워짐) → 4행 체크 → 아래에 붙여넣기 → 5·6행
S_CHK = function_manager.S_CHK
while len(fm.f.segments) < 5:
    fm.seg_op("add")
fm.f.segments[0].temp, fm.f.segments[1].temp = 31.0, 32.0; fm.fill_segments()
nseg = len(fm.f.segments); t0, t1 = fm.f.segments[0].temp, fm.f.segments[1].temp
fm.t_seg.item(0, S_CHK).setCheckState(Qt.Checked); fm.t_seg.item(1, S_CHK).setCheckState(Qt.Checked)
fm.seg_op("copy")
ok(not checked_rows(fm.t_seg, S_CHK), "복사 후 체크 비워짐")
fm.t_seg.setCurrentCell(0, 2)                                   # 현재 행은 일부러 1행 (체크한 행이 기준이어야 함)
fm.t_seg.item(3, S_CHK).setCheckState(Qt.Checked); fm.seg_op("paste_below"); pump(0.2)
ok(len(fm.f.segments) == nseg + 2 and fm.f.segments[4].temp == t0 and fm.f.segments[5].temp == t1 and fm.f.segments[4] is not fm.f.segments[0],
   f"체크한 4행 아래(5·6행)에 붙여넣기 — 현재 행(1행)과 무관 ({nseg} → {len(fm.f.segments)})")
fm.t_seg.item(2, S_CHK).setCheckState(Qt.Checked); fm.t_seg.item(4, S_CHK).setCheckState(Qt.Checked)
fm.seg_op("paste_above"); pump(0.1)
ok(len(fm.f.segments) == nseg + 4 and fm.f.segments[2].temp == t0 and fm.f.segments[3].temp == t1, "여러 행 체크 시 [위에] = 체크한 첫 행(3행) 위")
fm.t_seg.setCurrentCell(1, 2); fm.seg_op("paste_below"); ok(len(fm.f.segments) == nseg + 6 and fm.f.segments[2].temp == t0, "체크가 없으면 현재 행 기준")
# steps
rr = next(i for i, sg in enumerate(fm.f.segments) if len(sg.steps) >= 3)
fm.t_seg.setCurrentCell(rr, 2); pump(0.1)
ns = len(fm.cur_seg().steps); v0 = fm.cur_seg().steps[0].volt
fm.t_step.item(0, function_manager.P_CHK).setCheckState(Qt.Checked); fm.t_step.item(2, function_manager.P_CHK).setCheckState(Qt.Checked)
fm.step_op("copy"); fm.t_step.item(ns - 1, function_manager.P_CHK).setCheckState(Qt.Checked); fm.step_op("paste_below"); pump(0.1)
ok(len(fm.cur_seg().steps) == ns + 2 and fm.cur_seg().steps[ns].volt == v0, f"Step: 체크한 마지막 행 아래에 붙여넣기 {ns} → {len(fm.cur_seg().steps)}")
fm.t_seg.setCurrentCell(4, 2); pump(0.1); n4 = len(fm.cur_seg().steps); fm.t_step.item(0, function_manager.P_CHK).setCheckState(Qt.Checked); fm.step_op("paste_above")
ok(len(fm.cur_seg().steps) == n4 + 2, "다른 Segment 에 Step 붙여넣기")
fm.t_seg.item(0, function_manager.S_CHK).setCheckState(Qt.Checked); fm.t_seg.item(1, function_manager.S_CHK).setCheckState(Qt.Checked)
nb = len(fm.f.segments); fm.seg_op("del"); ok(len(fm.f.segments) == nb - 2, "체크한 Segment 2개 삭제")
# Step 입력 중 Segment 변경 → 입력값은 원래 Segment 로
ra = next(i for i, sg in enumerate(fm.f.segments) if len(sg.steps) >= 2)
rb = next(i for i, sg in enumerate(fm.f.segments) if i != ra)
fm.t_seg.setCurrentCell(ra, 2); pump(0.1)
seg_a, seg_b = fm.f.segments[ra], fm.f.segments[rb]; b_before = [x.time_s for x in seg_b.steps]
rect_ = fm.t_step.visualRect(fm.t_step.model().index(1, function_manager.P_TIME))
QTest.mouseClick(fm.t_step.viewport(), Qt.LeftButton, pos=rect_.center()); pump(0.05)
QTest.keyClicks(app.focusWidget(), "000700")
fm.t_seg.setCurrentCell(rb, 2); pump(0.1)
ok(seg_a.steps[1].time_s == 420 and [x.time_s for x in seg_b.steps] == b_before,
   "Step 입력 중 Segment 를 바꿔도 값은 원래 Segment 에 반영")
# 6 combined graph + 초기화
fm.g_cb["V"].setChecked(False); fm.g_vb.zoom(0.5); fm.gtools.btn_reset.click(); pump(0.1)
ok(all(cb.isChecked() for cb in fm.g_cb.values()) and not fm.g_vb.history, "Function 그래프 [⟲ 초기화] — 항목 모두 표시·확대 해제")
names_in_legend = [it[1].text for it in fm.g_legend.items]
ok(set(["온도 ℃", "습도 %", "전압 V", "전류 A"]) <= set(names_in_legend), f"한 그래프 4항목 {names_in_legend}")
fm.t_seg.setCurrentCell(1, 2); pump(0.3)
fm.grab().save(f"{OUT}/08_function_edit.png")
fm.f_save(); pump(0.2)
# 4 profile export / import
SAVE["save"] = os.path.join(HOME, "prof.hfp")
orig_exec = function_manager.ProfileExportDialog.exec
def exp_exec(self):
    for i in range(self.lst.count()): self.lst.item(i).setCheckState(Qt.Checked)
    self.grab().save(f"{OUT}/09_profile_export.png"); return 1
function_manager.ProfileExportDialog.exec = exp_exec
fm.profile_export()
pdata = json.load(open(SAVE["save"], encoding="utf-8"))
ok(pdata["format"] == "HyosungChamber.FunctionProfile" and pdata["count"] == len(fm.store.names()), f"내보내기 {pdata['count']}개 → .hfp")
ok(pdata["functions"][[f["name"] for f in pdata["functions"]].index("DENSO HTHE")]["segments"][0]["time_s"] == 5400, "Segment·Step 상세 포함")
# simulate another PC: delete one function, import
fm.store.delete("Derating Test")
SAVE["open"] = SAVE["save"]
captured = {}
def imp_exec(self):
    captured["names"] = [self.tbl.item(r, self.C_NAME).text() for r in range(self.tbl.rowCount())]
    self.grab().save(f"{OUT}/10_profile_import.png"); return 1
function_manager.ProfileImportDialog.exec = imp_exec
fm.profile_import(); pump(0.2)
ok("Derating Test" in fm.store.names() and "DENSO HTHE (2)" in fm.store.names(), f"가져오기: 없는 것 추가, 같은 이름 → (2): {captured['names']}")
ok(fm.tabs.currentIndex() == 1 and fm.orig_name == captured["names"][0], f"가져온 Function 바로 편집 ({fm.orig_name})")
# overwrite mode
def imp_over(self):
    self.rb_over.setChecked(True); self.tbl.horizontalHeader().sectionClicked.emit(0)
    self.tbl.item(0, 0).setCheckState(Qt.Checked); return 1
function_manager.ProfileImportDialog.exec = imp_over
before = set(fm.store.names()); fm.profile_import(); ok(set(fm.store.names()) == before, "덮어쓰기 모드 — 새 이름 안 생김")
fm.dirty = False; fm.close()
# ---- 2 Result checkbox
pump(1)
rd = dialogs.ResultDialog(win, hub, cfg); rd.resize(1500, 900); rd.show(); pump(0.3)
rd.mode_tabs.setCurrentIndex(1); pump(0.2)
ok(rd.tbl_runs.rowCount() > 0, f"실행 이력 {rd.tbl_runs.rowCount()}건")
rd.tbl_runs.cellClicked.emit(0, 2); rd.tbl_runs.cellClicked.emit(1, 3) if rd.tbl_runs.rowCount() > 1 else None
ok(len(checked_rows(rd.tbl_runs, 0)) == min(2, rd.tbl_runs.rowCount()), "실행 이력 체크 다중 선택")
rd.run_query(); pump(0.3); ok(len(rd.rows) > 0, f"체크한 이력 조회 {len(rd.rows)}행")
vb_ = rd.plot.getViewBox(); r0 = vb_.viewRange()[0]; vb_.zoom(0.5); rd.gtools.btn_reset.click(); pump(0.1)
ok(rd.gtools.btn_reset is not None and abs(vb_.viewRange()[0][1] - r0[1]) < (r0[1] - r0[0]) * 0.2 and not vb_.history, "Result 그래프 [⟲ 초기화]")
rd.grab().save(f"{OUT}/11_result.png"); rd.hide()
cd = dialogs.ChamberDialog(win, hub, cfg, win.snap); cd.show(); cd.activateWindow(); pump(0.1)
QTest.mouseClick(cd.run_time, Qt.LeftButton); t_ = time.time()
while not cd.run_time.hasFocus() and time.time() - t_ < 2: pump(0.05)
pump(0.1)
QTest.keyClicks(cd.run_time, "023000"); cd.run_time.clearFocus(); cd.run_time.editingFinished.emit(); pump(0.1)
ok(cd.run_time.text() == "02:30:00", f"챔버 시간 숫자 입력 → {cd.run_time.text()}"); cd.hide()
print(f"\n결과: {sum(RES)} 통과 / {len(RES) - sum(RES)} 실패   (화면 캡처: {OUT})")
hub.shutdown(True); os._exit(0 if all(RES) else 1)
