"""Function 실행 규칙(매크로 러너) 시험 — 시뮬레이터, 장비 없이.

    python tools/test_runner.py

A안(Segment 시간 우선)·B안(Step 시간 우선)의 Step 전환, OCP(AL-03), Wait 한도(AL-04)를 확인한다.
"""
import os, sys, tempfile
from datetime import datetime, timedelta
os.environ["HYOSUNG_HMI_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from infrastructure.system.config import Config
from application.services.hub import DeviceHub
from domain.models.function_model import Function, Segment, Step
from infrastructure.persistence.datalog import ResultQuery

def mk(policy):
    cfg = Config.load(); cfg.simulate = True; cfg.step_policy = policy
    cfg.data_dir = os.path.join(os.environ["HYOSUNG_HMI_HOME"], "Data_" + policy)
    hub = DeviceHub(cfg); hub.alarms = []
    hub.alarm.connect(lambda c, m: hub.alarms.append(c))
    return hub, cfg

# Segment 60s, steps 20+30 = 50s (short) ; Segment2 60s steps 40+40=80 (long)
f = Function("T1", "manual", 25, 50, [
    Segment(30.0, 50.0, 60, False, [Step(20, None, None, 10, 5), Step(30, None, None, 11, 5)]),
    Segment(30.0, 50.0, 60, False, [Step(40, None, None, 12, 5), Step(40, None, None, 13, 5)])])
for policy in ("segment", "step"):
    hub, cfg = mk(policy)
    err, warn = f.validate(cfg); print(policy, "validate err", err, "warn", warn)
    hub.functions.save(f)
    hub.assign([1], "T1", 61.0, 1, 1.0)
    hub.start_func([1])
    c = hub.ch[1]
    trace = []
    for s in range(1, 200):
        hub._tick(1.0)
        if c.run:
            trace.append((s, c.run.seg_i + 1, c.run.step_i + 1, round(c.cv, 1), c.on, c.status))
        else:
            trace.append((s, "done", c.status)); break
    # print transitions only
    prev = None
    for t in trace:
        key = t[1:]
        if key != prev:
            print("  t=%3s" % t[0], key)
            prev = key
    q = ResultQuery(cfg.data_dir)
    runs = q.runs()
    print("  runs:", [(r["function"], r["ch"], r["status"]) for r in runs])
    cols, rows = q.query(datetime.now() - timedelta(minutes=5), datetime.now() + timedelta(minutes=1), 10, [1], True, ["전압", "전류", "온도"], runs=runs)
    print("  query cols", cols, "rows", len(rows))
    hub.data.close()

# OCP + Wait
hub, cfg = mk("segment")
f2 = Function("W", "manual", 25, 50, [Segment(80.0, None, 5, True, [Step(5, None, None, 40.0, 61.0)])])
hub.functions.save(f2)
cfg.wait_limit_min = 0  # immediate AL-04
hub.assign([2], "W", 1.0, 1, 1.0)   # OCP 1 A -> 40V/23Ω ≈ 1.6 A => OCP trip
hub.start_func([2])
for _ in range(5):
    hub._tick(1.0)
print("OCP test status:", hub.ch[2].status, "alarms:", hub.alarms)
hub.assign([3], "W", 61.0, 1, 1.0)
hub.chamber.sp_t = 25  # make wait unreachable quickly? chamber sim moves fast; set tolerance tiny
cfg.wait_tol_t = 0.0001
hub.start_func([3])
for _ in range(12):
    hub._tick(1.0)
print("Wait test status:", hub.ch[3].status, "alarms:", hub.alarms)

# 저장 여부 선택: 운전은 하되 run_id/샘플을 만들지 않음
hub.functions.save(f)
hub.assign([4], "T1", 61.0, 1, 1.0)
hub.start_func([4], record=False)
hub._tick(1.0); hub.data.flush()
assert hub.ch[4].run is not None and hub.data.run_id(4) is None
assert not [r for r in ResultQuery(cfg.data_dir, hub.data).runs() if r["ch"] == 4]
print("No-save Function: running=True, run_id=None, DB session=0")

# 챔버 단독 운전도 저장 안 함/저장을 시작 시 선택한 값대로 적용
hub2, cfg2 = mk("record-choice")
hub2.chamber_run(record=False)
hub2.cr = hub2.chamber.read()
hub2._log_and_emit(datetime.now()); hub2.data.flush()
count0 = hub2.data.cursor().execute("SELECT count(*) FROM chamber_sample").fetchone()[0]
hub2.chamber_stop(); hub2.chamber_run(record=True)
hub2.cr = hub2.chamber.read()
hub2._log_and_emit(datetime.now()); hub2.data.flush()
count1 = hub2.data.cursor().execute("SELECT count(*) FROM chamber_sample").fetchone()[0]
assert count0 == 0 and count1 == 1
print("No-save Chamber: 0 rows / Save Chamber: 1 row")
hub2.data.close()
hub.data.close()
