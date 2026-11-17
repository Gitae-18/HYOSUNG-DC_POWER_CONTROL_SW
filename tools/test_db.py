"""DuckDB 일별 DB 시험 (장비·화면 없이).

    python tools/test_db.py            # 기능 시험
    python tools/test_db.py --perf     # + 하루치(6채널 × 86,400초) 용량·조회 속도 측정

1) 자정 전환: 날짜가 바뀌면 새 파일, 날짜를 넘긴 실행은 양쪽 파일에 이력
2) 여러 날 조회: 간격(1초/1분)·대표값, 실행 이력 병합
3) 비정상 종료 복구: 강제 종료된 프로세스의 데이터(WAL)와 '실행 중' 이력 정리
4) 손상 파일: 옆으로 치우고 새 파일, AL-06 알림
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from infrastructure.persistence import datalog  # noqa: E402
from infrastructure.persistence.datalog import DataLogger, ResultQuery, db_path  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    ok += bool(cond)
    fail += not cond
    print(f"  [{'OK' if cond else 'FAIL'}]   {name} {'' if cond else extra}")


class FakeDate(date):
    today_value = date.today()

    @classmethod
    def today(cls):
        return cls.today_value


class R:  # ChamberReading 대용
    def __init__(self, t):
        self.pv_t, self.sp_t, self.pv_h, self.sp_h, self.mv_t, self.mv_h = t, 25.0, 50.0, 50.0, 40.0, 20.0
        self.running, self.mode, self.pattern, self.segment, self.error = True, "fix", 0, 0, ""


if len(sys.argv) > 1 and sys.argv[1] == "--crash-child":
    lg = DataLogger(sys.argv[2])
    lg.start_run(2, "CRASH-TEST")
    t = datetime.now().replace(microsecond=0)
    for k in range(5):
        lg.power_sample(2, t + timedelta(seconds=k), dict(status="RUN", volt=12.0 + k, curr=1.0))
    lg.flush()
    os._exit(1)                                   # 강제 종료 (close 없이)

root = Path(tempfile.mkdtemp(prefix="hmi_db_"))
errs = []
datalog.date = FakeDate
today = date.today()
FakeDate.today_value = today

print("1) 자정 전환")
lg = DataLogger(root, on_error=lambda c, m: errs.append((c, m)))
p_today = db_path(root, today)
check("오늘 파일 생성 DB\\YYYY\\MM\\HyosungData_YYYYMMDD.duckdb", p_today.exists(), p_today)
rid = lg.start_run(1, "LONG-RUN", snapshot={"segments": 1}, cfg_info={"ocp": 5})
t0 = datetime.combine(today, datetime.min.time()) + timedelta(hours=23, minutes=59, seconds=50)
for k in range(10):
    lg.power_sample(1, t0 + timedelta(seconds=k), dict(status="RUN", set_v=12, set_a=5, volt=12.0, curr=1.0 + k / 10,
                                                       ocp=5, step="1/1", cycle="1/1", elapsed_s=k))
    lg.chamber(t0 + timedelta(seconds=k), R(30 + k / 10))
lg.flush()
FakeDate.today_value = today + timedelta(days=1)          # 자정이 지남
for k in range(10, 20):
    lg.power_sample(1, t0 + timedelta(seconds=k), dict(status="RUN", volt=12.0, curr=2.0, step="1/1", cycle="1/1"))
    lg.chamber(t0 + timedelta(seconds=k), R(31 + k / 10))
lg.flush()
p_tom = db_path(root, today + timedelta(days=1))
check("다음 날 파일로 전환", p_tom.exists() and lg.path == p_tom, lg.path)
q = ResultQuery(root, lg)
cols, rows = q.query(t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 1, [1], True, ["전압", "전류", "온도"])
check("두 파일에 걸친 1초 조회 = 20행", len(rows) == 20, len(rows))
check("열 순서 P1 V, P1 A, 온도", cols == ["시각", "P1 V", "P1 A", "온도 ℃"], cols)
cols, rows = q.query(t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 60, [1], True, ["전류"])
check("1분 간격 = 2행(23:59, 00:00), 마지막 값", len(rows) == 2 and rows[0][1] == 1.9 and rows[1][1] == 2.0, rows)
lg.end_run(1, "정상 종료")
runs = q.runs()
check("날짜를 넘긴 실행 1건으로 병합 · 정상 종료", len(runs) == 1 and runs[0]["status"] == "정상 종료" and runs[0]["id"] == rid, runs)
cols, rows = q.query(t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 1, [1], False, ["전압"], runs=runs)
check("실행 이력으로 조회 (run_id 필터)", len(rows) == 20, len(rows))
lg.event("ALARM", "AL-03 시험", "AL-03")
check("event_log 기록", any(e[2] == "AL-03" for e in q.events(t0 - timedelta(days=1), datetime.now() + timedelta(days=2))))
lg.close()
check("닫은 뒤 읽기 전용 조회", len(ResultQuery(root).query(t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 1, [1], True,
                                                            ["전압"])[1]) == 20)

print("2) 비정상 종료 복구")
datalog.date = date
root2 = Path(tempfile.mkdtemp(prefix="hmi_db_crash_"))
r = subprocess.run([sys.executable, __file__, "--crash-child", str(root2)], capture_output=True, text=True, timeout=60)
check("자식 프로세스 강제 종료", r.returncode == 1, r.stderr[-300:])
lg2 = DataLogger(root2)
q2 = ResultQuery(root2, lg2)
runs = q2.runs()
check("'실행 중' 으로 남은 이력 → '비정상 종료'", len(runs) == 1 and runs[0]["status"] == "비정상 종료", runs)
n = len(q2.query(datetime.now() - timedelta(hours=1), datetime.now() + timedelta(hours=1), 1, [2], False, ["전압"])[1])
check("강제 종료 전 기록 5행 보존 (WAL 복구)", n == 5, n)
lg2.close()

print("3) 손상 파일")
root3 = Path(tempfile.mkdtemp(prefix="hmi_db_bad_"))
bad = db_path(root3, date.today())
bad.parent.mkdir(parents=True)
bad.write_bytes(b"this is not a duckdb file" * 100)
errs.clear()
lg3 = DataLogger(root3, on_error=lambda c, m: errs.append((c, m)))
check("손상 파일은 .corrupt_ 로 이름 변경, 새 파일 생성", any(".corrupt_" in p.name for p in bad.parent.iterdir()) and lg3.con)
check("AL-06 알림", any(c == "AL-06" for c, _ in errs), errs)
lg3.close()

if "--perf" in sys.argv:
    print("4) 하루치 용량·조회 속도 (6채널 × 86,400초 + 챔버)")
    root4 = Path(tempfile.mkdtemp(prefix="hmi_db_perf_"))
    lg4 = DataLogger(root4)
    for ch in range(1, 7):
        lg4.start_run(ch, "PERF")
    base = datetime.combine(date.today(), datetime.min.time())
    t_start = time.time()
    con = lg4.cursor()
    con.execute("""INSERT INTO power_sample SELECT ts, ch, 'R', 'RUN', 12, 5, 12 + random() * 0.05, 1 + random() * 0.1, 5,
                   '1/3', '1/10', 0 FROM (SELECT ? + to_seconds(i) AS ts FROM range(86400) t(i)), range(1, 7) c(ch)""", [base])
    con.execute("""INSERT INTO chamber_sample SELECT ? + to_seconds(i), 25 + random(), 25, 50 + random(), 50, 40, 20, true,
                   'fix', 0, 0, '' FROM range(86400) t(i)""", [base])
    con.execute("CHECKPOINT")
    con.close()
    lg4.close()
    size = db_path(root4, date.today()).stat().st_size / 1e6
    print(f"     삽입 {time.time() - t_start:.1f}s · 파일 {size:.1f} MB (power 518,400행 + chamber 86,400행)")
    q4 = ResultQuery(root4)
    for iv in (1, 10, 60, 300):
        t1 = time.time()
        cols, rows = q4.query(base, base + timedelta(days=1), iv, [1, 2, 3, 4, 5, 6], True, ["전압", "전류", "온도", "습도"])
        print(f"     하루 · 6채널 · {iv:>3}초 간격 조회: {len(rows):>6}행 {time.time() - t1:.2f}s")

print(f"\n결과: {ok} 통과 / {fail} 실패")
sys.exit(1 if fail else 0)
