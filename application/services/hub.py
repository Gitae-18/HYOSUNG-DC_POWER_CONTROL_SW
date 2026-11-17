"""DeviceHub — 장비 폴링, Function 실행(매크로 러너), OCP·통신 알람, 데이터 기록.

모든 장비 I/O 는 작업 스레드 하나에서만 일어난다(시리얼 포트 동시 접근 방지).
UI 는 call() 로 명령을 넣고, updated 시그널로 1초마다 스냅샷을 받는다.

2026-11-13: 전류 기준 Min/Max (채널별) — 출력 중 전류가 한 번이라도 벗어나면 AL-09 를 띄우고 채널에 알람을 걸어 둔다.
            알람은 clear_alarms() (메인 화면 [알람 전체 해제]) 전까지 유지. 기준값은 config.i_limits 에 저장.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from infrastructure.system.config import Config, APP_DIR
from infrastructure.persistence.datalog import AppLog, DataLogger
from infrastructure.devices.devices import (SimPowerSupply, SimChamber, UspBus, UspPowerSupply, Nex1200Modbus, ChamberReading, CommTrace,
                     DeviceError)
from domain.models.function_model import Function, FunctionStore, hms

ALARMS = {"AL-01": "NEX1200 통신 두절", "AL-02": "Power 통신 두절", "AL-03": "OCP 발생", "AL-04": "온/습도 목표 미도달",
          "AL-05": "NEX1200 에러", "AL-06": "파일 오류", "AL-07": "저장 공간 부족", "AL-08": "Power 장비 알람",
          "AL-09": "전류 기준 이탈"}


@dataclass
class Assign:
    func: str
    ocp: float
    cycles: int
    log_s: float


@dataclass
class Run:
    func: Function
    a: Assign
    cycle: int = 1
    seg_i: int = 0
    step_i: int = 0
    seg_t: float = 0.0
    step_t: float = 0.0
    wait_t: float = 0.0
    waiting: bool = False
    steps_done: bool = False
    wait_alarmed: bool = False
    t0: float = field(default_factory=time.monotonic)
    last_log: float = 0.0
    owner: bool = False


@dataclass
class Channel:
    ch: int
    name: str
    status: str = "OFF"
    v: float = 0.0
    a: float = 0.0
    on: bool = False
    cv: float = 0.0
    cc: float = 0.0
    fails: int = 0
    assign: Assign | None = None
    run: Run | None = None
    manual: bool = False
    manual_t0: float = 0.0
    last_log: float = 0.0
    i_min: float | None = None      # 전류 기준 (A), None = 사용 안 함
    i_max: float | None = None
    i_alarm: str = ""               # 기준 이탈 내용 (빈 문자열 = 알람 없음), 해제 전까지 유지
    i_hold: float = 0.0             # 이 시각(monotonic)까지는 기준 판정 안 함 (출력 ON·Step 전환 직후)

    @property
    def ocp(self):
        return self.assign.ocp if self.assign else self.cc

    def view(self) -> dict:
        r = self.run
        if r:
            seg = r.func.segments[r.seg_i]
            step = f"{r.step_i + 1}/{len(seg.steps)}"
            cyc = f"{r.cycle}/{r.a.cycles}"
            el = time.monotonic() - r.t0
        else:
            step = cyc = "0/0"
            el = time.monotonic() - self.manual_t0 if self.manual else 0
        return dict(ch=self.ch, name=self.name, status=self.status, v=self.v, a=self.a, on=self.on, cv=self.cv,
                    cc=self.cc, ocp=self.ocp, step=step, cycle=cyc, elapsed=hms(el),
                    func=self.assign.func if self.assign else "", cycles=self.assign.cycles if self.assign else 0,
                    log_s=self.assign.log_s if self.assign else 0,
                    i_min=self.i_min, i_max=self.i_max, i_alarm=self.i_alarm,
                    progress=(f"{r.cycle}/{r.a.cycles} · {int(100 * ((r.cycle - 1) * r.func.total_s + sum(s.time_s for s in r.func.segments[:r.seg_i]) + r.seg_t) / max(1, r.a.cycles * r.func.total_s))}%" if r else "—"))


class DeviceHub(QObject):
    updated = Signal(object)        # snapshot dict
    alarm = Signal(str, str)        # code, message
    info = Signal(str)              # 상태바/안내용 메시지
    comm_result = Signal(str)       # 통신 시험 창 결과

    def __init__(self, cfg: Config, sim_fail: tuple[int, ...] = ()):
        super().__init__()
        self.cfg = cfg
        root = Path(cfg.data_dir)
        # DB(DuckDB 일별 파일): 쓰기 실패는 AL-06 으로 알림, 운영 로그는 DB event_log 에도 기록
        self.data = DataLogger(root, on_error=lambda code, msg: self._raise(code, msg) if hasattr(self, "log") else None)
        self.log = AppLog(APP_DIR / "Log", sink=lambda kind, msg, code, t: self.data.event(kind, msg, code, t))
        self.functions = FunctionStore(APP_DIR / "Function")
        CommTrace.folder, CommTrace.enabled = APP_DIR / "Log", cfg.comm_trace
        if cfg.simulate:
            self.psu = {i: SimPowerSupply(i, fail=i in sim_fail) for i in range(1, 7)}
            self.chamber = SimChamber()
        else:
            # USP ×6 : 한 RS-485 포트(psu_com)에 멀티드롭, 주소로 구분
            self.bus = UspBus(cfg.psu_com, cfg.psu_baud, cfg.psu_parity, cfg.psu_stopbits, cfg.psu_timeout_s,
                              cfg.psu_terminator, cfg.psu_cmd_gap_ms)
            self.psu = {i: UspPowerSupply(self.bus, p.address or i, cfg.psu_status_every, cfg.psu_idle_every)
                        for i, p in enumerate(cfg.powers, 1)}
            self.chamber = Nex1200Modbus(cfg.nex_com, cfg.nex_address, cfg.nex_baud, cfg.nex_parity,
                                         cfg.nex_stopbits, cfg.nex_timeout_s, cfg.nex_rate_unit)
        self.enabled = {i: (cfg.powers[i - 1].enabled if i <= len(cfg.powers) else True) for i in range(1, 7)}
        self.ch = {i: Channel(i, cfg.powers[i - 1].name) for i in range(1, 7)}
        for k, (lo, hi) in (cfg.i_limits or {}).items():
            if int(k) in self.ch:
                self.ch[int(k)].i_min, self.ch[int(k)].i_max = lo, hi
        self.cr: ChamberReading | None = None
        self.c_fails = 0
        self.c_err = ""
        self.active_alarms: set[str] = set()
        self.q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._last_chlog = 0.0
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nex1200")

    # ================================================================ lifecycle
    def start(self):
        try:
            self.chamber.open()
        except Exception as e:  # noqa
            self.log.write("COMM", f"NEX1200 연결 실패: {e}")
        for i, d in self.psu.items():
            if not self.enabled[i]:
                continue
            try:
                d.open()
                self.log.write("COMM", f"P{i:02d} 연결 {d.identify()}")
            except Exception as e:  # noqa
                self.log.write("COMM", f"P{i:02d} 연결 실패: {e}")
        try:
            self.cfg.nex_sp_t, self.cfg.nex_sp_h = self.chamber.sp_limits()
        except Exception:  # noqa
            pass
        self.log.write("SYSTEM", "Program Ready")
        if self.cfg.simulate:
            # 시뮬레이터 데모: 지정 채널(기본 P01~P03)을 Test Power 와 같은 방식으로 켠다 → 수동운전으로 기록됨
            for ch, v, a in self.cfg.sim_demo_on:
                if ch in self.psu:
                    self.call(self.test_power_set, [ch], v, a)
            on = [ch for ch, _, _ in self.cfg.sim_demo_on if ch in self.psu]
            if on:
                self.call(self.test_power_on, on, True)
        self._th.start()

    def shutdown(self, stop_devices: bool):
        if stop_devices:
            done = threading.Event()

            def job():
                for c in self.ch.values():
                    self._power_off(c, "프로그램 종료")
                try:
                    self.chamber.stop()
                except Exception:  # noqa
                    pass
                done.set()
            self.q.put(job)
            done.wait(5)
        self._stop.set()
        self._th.join(3)
        self._pool.shutdown(wait=False)
        for d in [self.chamber, *self.psu.values()]:
            self._safe(d.close)
        self.data.close()
        self.log.write("SYSTEM", "Program Exit")

    def call(self, fn, *a, **kw):
        self.q.put(lambda: fn(*a, **kw))

    # ================================================================ worker loop
    def _loop(self):
        nxt = time.monotonic()
        last = nxt
        while not self._stop.is_set():
            while True:
                try:
                    job = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    job()
                except Exception as e:  # noqa
                    self.info.emit(f"명령 실패: {e}")
                    self.log.write("ERROR", str(e))
            now = time.monotonic()
            if now >= nxt:
                dt, last = now - last, now
                try:
                    self._tick(dt)
                except Exception as e:  # noqa — 한 주기 오류로 감시가 멈추지 않게
                    self.log.write("ERROR", f"tick: {e!r}")
                nxt += 1.0
                if nxt < now:
                    nxt = now + 1.0
            time.sleep(0.05)

    def _raise(self, code, msg):
        self.log.write("ALARM", f"{code} {msg}", code=code)
        self.alarm.emit(code, msg)

    def _tick(self, dt: float):
        t = datetime.now()
        t_poll = time.monotonic()
        # ---- chamber: NEX1200(COM1)과 USP 버스(COM2)는 포트가 달라 동시에 읽는다 → 주기 단축
        fut = self._pool.submit(self.chamber.read)
        # ---- powers (USP 6대, 한 포트 순서대로)
        self._read_powers()
        try:
            self.cr = fut.result(timeout=5)
            if self.c_fails >= 3:
                self.log.write("COMM", "NEX1200 재연결")
            self.c_fails = 0
            if self.cr.error and self.cr.error != self.c_err:
                self._raise("AL-05", f"{ALARMS['AL-05']}: {self.cr.error}")
            self.c_err = self.cr.error
        except Exception as e:  # noqa
            self.c_fails += 1
            if self.c_fails == 3:
                self._raise("AL-01", f"{ALARMS['AL-01']} ({e})")
        self._advance_powers(dt)
        self.poll_ms = (time.monotonic() - t_poll) * 1000     # 장비 읽기에 걸린 시간 (1000 ms 넘으면 통신 속도 확인)
        self._log_and_emit(t)

    def _read_powers(self):
        self._readings = {}
        for i, c in self.ch.items():
            if not self.enabled[i]:
                continue
            try:
                self._readings[i] = self.psu[i].read()
            except Exception as e:  # noqa
                self._readings[i] = e

    def _advance_powers(self, dt):
        for i, c in self.ch.items():
            if not self.enabled[i]:
                c.status = "OFF"
                continue
            try:
                r = self._readings.get(i)
                if isinstance(r, Exception) or r is None:
                    raise r or DeviceError("읽기 없음")
                if c.fails >= 3:
                    self.log.write("COMM", f"P{i:02d} 재연결")
                    c.status = "RUN" if r.on else ("READY" if c.assign else "OFF")
                c.fails, c.v, c.a, c.on = 0, r.v, r.a, r.on
                if c.status == "ERR":
                    c.status = "OFF"
                # 프로그램 밖(장비 앞면 등)에서 켜진 출력도 저장되도록 수동운전으로 기록 시작 / 꺼지면 종료
                if r.on and not c.run and not c.manual and c.status != "OCP":
                    c.manual, c.manual_t0, c.status = True, time.monotonic(), "RUN"
                    c.i_hold = time.monotonic() + self.cfg.i_check_delay_s
                    self.data.start_run(i, None, cfg_info={"cv": c.cv, "cc": c.cc, "detected": "장비 출력 ON 감지"})
                    self.log.write("POWER", f"P{i:02d} 출력 ON 감지 → 수동운전 기록 시작")
                elif not r.on and c.manual and not c.run:
                    self._end(c, "출력 OFF 감지")
                    c.status = "READY" if c.assign else "OFF"
            except Exception as e:  # noqa
                c.fails += 1
                if c.fails == 3:
                    c.status = "ERR"
                    self._raise("AL-02", f"P{i:02d} {ALARMS['AL-02']} ({e})")
                if c.fails < 3 and c.run:
                    self._advance(c, dt)          # 한두 번 놓친 응답으로 Function 시간이 멈추지 않게
                continue
            # 장비 자체 알람 (USP ASWRS?) — OCP 는 AL-03, 그 밖(OVP, 과열 …)은 AL-08
            if r.alarm != getattr(c, "dev_alarm", ""):
                c.dev_alarm = r.alarm
                if r.alarm == "OCP":
                    self._safe(self.psu[i].output, False)
                    c.status = "OCP"
                    self._end(c, "OCP 차단(장비)")
                    self._raise("AL-03", f"P{i:02d} {ALARMS['AL-03']} (장비 OCP 동작)")
                    continue
                if r.alarm and r.alarm not in ("CV→CC", "CC→CV"):
                    self._raise("AL-08", f"P{i:02d} {ALARMS['AL-08']}: {r.alarm}")
            # OCP (AL-03)
            if c.on and c.a > c.ocp > 0:
                self._safe(self.psu[i].output, False)
                c.status = "OCP"
                self._end(c, "OCP 차단")
                self._raise("AL-03", f"P{i:02d} {ALARMS['AL-03']} ({c.a:.2f} A > {c.ocp:.2f} A)")
                continue
            self._check_current(c)
            if c.run:
                self._advance(c, dt)

    def _check_current(self, c: Channel):
        """전류 기준 Min/Max — 출력 중 한 번이라도 벗어나면 알람을 걸어 둔다 (해제 전까지 다시 띄우지 않음)."""
        if c.i_alarm or not c.on or (c.i_min is None and c.i_max is None):
            return
        if not (c.run or c.manual) or time.monotonic() < c.i_hold:
            return
        lo, hi = c.i_min, c.i_max
        if lo is not None and c.a < lo:
            why = f"{c.a:.2f} A < Min {lo:.2f} A"
        elif hi is not None and c.a > hi:
            why = f"{c.a:.2f} A > Max {hi:.2f} A"
        else:
            return
        c.i_alarm = f"{why} ({datetime.now():%m-%d %H:%M:%S})"
        self._raise("AL-09", f"P{c.ch:02d} {ALARMS['AL-09']}: {c.i_alarm}")

    def _log_and_emit(self, t):
        # ---- DB 저장 (1초 주기, 2026-11-04 요구사항)
        #   DC Power: 출력 중(Test Power·Function) 채널마다 1행
        #   챔버: NEX1200 운전 중이거나 DC Power 가 하나라도 출력 중이면 1행 (아무것도 동작하지 않으면 저장 안 함)
        ts = t.replace(microsecond=0)
        active = [c for c in self.ch.values() if c.run or c.manual]
        if self.cr and self.c_fails < 3 and (self.cr.running or active):
            self.data.chamber(ts, self.cr)
        for c in active:
            if c.fails >= 3:
                continue                                   # 통신 두절 중에는 값이 없으므로 저장하지 않음
            vw = c.view()
            self.data.power_sample(c.ch, ts, dict(
                status=c.status, set_v=c.cv, set_a=c.cc, volt=round(c.v, 4), curr=round(c.a, 5), ocp=c.ocp,
                step=vw["step"], cycle=vw["cycle"],
                elapsed_s=round(time.monotonic() - (c.run.t0 if c.run else c.manual_t0), 1)))
        if int(time.time()) % 60 == 0 and self.data.free_gb() < self.cfg.disk_warn_gb:
            self._raise("AL-07", f"{ALARMS['AL-07']} ({self.data.free_gb():.1f} GB)")
        self.updated.emit(self.snapshot())

    def snapshot(self) -> dict:
        cr = self.cr
        return dict(time=datetime.now(), channels=[c.view() for c in self.ch.values()],
                    chamber=None if cr is None else dict(pv_t=cr.pv_t, pv_h=cr.pv_h, sp_t=cr.sp_t, sp_h=cr.sp_h,
                                                         running=cr.running, mode=cr.mode, pattern=cr.pattern,
                                                         segment=cr.segment, error=cr.error),
                    chamber_ok=self.c_fails < 3, data_dir=self.cfg.data_dir,
                    poll_ms=getattr(self, "poll_ms", 0.0), simulate=self.cfg.simulate)

    @staticmethod
    def _safe(fn, *a):
        try:
            fn(*a)
        except Exception:  # noqa
            pass

    # ================================================================ macro runner
    def _enter_segment(self, c: Channel):
        r = c.run
        seg = r.func.segments[r.seg_i]
        r.seg_t = r.step_t = r.wait_t = 0.0
        r.step_i, r.waiting, r.steps_done, r.wait_alarmed = 0, False, False, False
        if r.owner:
            pv_t = self.cr.pv_t if self.cr else None
            pv_h = self.cr.pv_h if self.cr else None
            rt, rh = r.func.rates(pv_t, pv_h)[r.seg_i]
            self.chamber.set_mode("fix")
            self.chamber.set_rate(rt, rh or 0.0)
            self.chamber.set_sp(seg.temp, seg.humi)
            if not (self.cr and self.cr.running):
                self.chamber.run()
            self.log.write("CHAMBER", f"P{c.ch:02d} {r.func.name} Seg{r.seg_i + 1}: SP {seg.temp}℃/{seg.humi}% rate {rt:+.3f}℃/min")
        self._enter_step(c)

    def _enter_step(self, c: Channel):
        r = c.run
        st = r.func.segments[r.seg_i].steps[r.step_i]
        r.step_t = 0.0
        self.psu[c.ch].set_cv_cc(st.volt, st.curr)
        c.cv, c.cc = st.volt, st.curr
        self.psu[c.ch].output(st.volt > 0 or st.curr > 0)
        c.i_hold = time.monotonic() + self.cfg.i_check_delay_s      # Step 전환 직후 전류 기준 판정 유예
        self.log.write("POWER", f"P{c.ch:02d} Seg{r.seg_i + 1} Step{r.step_i + 1}: {st.volt:.2f} V / {st.curr:.2f} A")

    def _reached(self, temp, humi) -> bool:
        if not self.cr:
            return False
        ok = abs(self.cr.pv_t - temp) <= self.cfg.wait_tol_t
        if humi is not None:
            ok = ok and abs(self.cr.pv_h - humi) <= self.cfg.wait_tol_h
        return ok

    def _advance(self, c: Channel, dt: float):
        r = c.run
        seg = r.func.segments[r.seg_i]
        policy_a = self.cfg.step_policy == "segment"
        if not r.waiting:
            r.seg_t += dt
        r.step_t += dt
        # ---- step 전환
        if not r.steps_done:
            st = seg.steps[r.step_i]
            cr = self.cr
            done = (st.time_s is not None and r.step_t >= st.time_s) or \
                   (st.temp is not None and cr is not None and abs(cr.pv_t - st.temp) <= self.cfg.wait_tol_t) or \
                   (st.humi is not None and cr is not None and abs(cr.pv_h - st.humi) <= self.cfg.wait_tol_h)
            if done:
                if r.step_i + 1 < len(seg.steps):
                    r.step_i += 1
                    self._enter_step(c)
                else:
                    r.steps_done = True
                    if not policy_a:   # B안: Step 종료 후 출력 OFF, Segment 끝까지 대기
                        self.psu[c.ch].output(False)
                    elif r.seg_t < seg.time_s:
                        self.log.write("POWER", f"P{c.ch:02d} Seg{r.seg_i + 1}: Step 종료, 마지막 출력 유지 ({hms(seg.time_s - r.seg_t)})")
        # ---- segment 전환
        seg_over = r.seg_t >= seg.time_s and (policy_a or r.steps_done)
        if not seg_over:
            c.status = "WAIT" if r.waiting else "RUN"
            return
        if seg.wait and not self._reached(seg.temp, seg.humi):
            r.waiting = True
            c.status = "WAIT"
            r.wait_t += dt
            if r.wait_t > self.cfg.wait_limit_min * 60 and not r.wait_alarmed:
                r.wait_alarmed = True
                self._raise("AL-04", f"P{c.ch:02d} Seg{r.seg_i + 1} {ALARMS['AL-04']} ({self.cfg.wait_limit_min}분 초과) — 다음 Segment 진행")
                r.waiting = False
            else:
                return
        if not r.steps_done and policy_a:
            self.log.write("POWER", f"P{c.ch:02d} Seg{r.seg_i + 1}: Segment 종료로 남은 Step 건너뜀")
        r.waiting = False
        r.seg_i += 1
        if r.seg_i >= len(r.func.segments):
            r.cycle += 1
            r.seg_i = 0
            if r.cycle > r.a.cycles:
                self._power_off(c, "정상 종료")
                c.status = "READY"
                self.info.emit(f"P{c.ch:02d} {r.func.name} 완료")
                return
        self._enter_segment(c)
        c.status = "RUN"

    def _end(self, c: Channel, status: str):
        if c.run:
            if c.run.owner:
                for o in self.ch.values():       # 챔버 제어권을 다음 실행 채널에 넘김
                    if o.run and o is not c:
                        o.run.owner = True
                        break
            c.run = None
            self.data.end_run(c.ch, status)
        if c.manual:
            c.manual = False
            self.data.end_run(c.ch, status)

    def _power_off(self, c: Channel, status: str):
        self._safe(self.psu[c.ch].output, False)
        c.on = False
        self._end(c, status)
        c.status = "READY" if c.assign else "OFF"

    # ================================================================ commands (UI -> call())
    def test_power_set(self, chs, v, a):
        for i in chs:
            self.psu[i].set_cv_cc(v, a)
            self.ch[i].cv, self.ch[i].cc = v, a
            self.ch[i].i_hold = time.monotonic() + self.cfg.i_check_delay_s
        self.log.write("POWER", f"Set Values P{chs}: {v:.2f} V / {a:.2f} A")

    def test_power_on(self, chs, on: bool):
        done, skipped, failed = [], [], []
        for i in chs:
            c = self.ch[i]
            if c.run:
                skipped.append(f"P{i:02d}")          # Function 실행 중 채널은 Run Function 에서 정지
                continue
            try:
                if on:
                    self.psu[i].output(True)
                    c.on = True
                    c.i_hold = time.monotonic() + self.cfg.i_check_delay_s
                    if not c.manual:
                        c.manual, c.manual_t0 = True, time.monotonic()
                        self.data.start_run(i, None, cfg_info={"cv": c.cv, "cc": c.cc})
                    c.status = "RUN"
                else:
                    self.psu[i].output(False)
                    self._power_off(c, "수동 종료")
                done.append(f"P{i:02d}")
            except Exception as e:  # noqa
                failed.append(f"P{i:02d}({e})")
        self.log.write("POWER", f"Test Power {'On' if on else 'Off'} {done} 건너뜀 {skipped} 실패 {failed}")
        msg = f"{', '.join(done) or '-'} 출력 {'ON' if on else 'OFF'} 완료"
        if skipped:
            msg += f" · {', '.join(skipped)} 는 Function 실행 중이라 제외"
        if failed:
            msg += f" · 실패 {', '.join(failed)}"
        self.info.emit(msg)

    def chamber_apply(self, mode, t, h, pattern=1, timed=False, dev=0.0, secs=0, run_after=False):
        self.chamber.set_mode(mode, pattern)
        if mode == "fix":
            self.chamber.set_rate(0, 0)
            self.chamber.set_sp(t, h)
            self.chamber.set_timed(timed, dev, secs)
        self.log.write("CHAMBER", f"설정 {mode} {t}℃/{h}% pattern={pattern}")
        if run_after:
            self.chamber_run()

    def chamber_run(self):
        self.chamber.run()
        self.log.write("CHAMBER", "RUN")

    def chamber_stop(self, stop_power=False):
        self.chamber.stop()
        if stop_power:
            for c in self.ch.values():
                if c.run:
                    self._power_off(c, "챔버 정지로 중단")
        self.log.write("CHAMBER", "STOP")

    def assign(self, chs, func_name, ocp, cycles, log_s, i_limits=False):
        """i_limits=(min, max) 를 주면 전류 기준도 함께 설정 (False = 기준은 그대로 둠)."""
        done = []
        for i in chs:
            if self.ch[i].run:
                continue
            self.ch[i].assign = Assign(func_name, ocp, cycles, log_s)
            done.append(i)
            if self.ch[i].status in ("OFF", "READY"):
                self.ch[i].status = "READY"
        self.log.write("USER", f"할당 P{chs} ← {func_name} OCP {ocp} Cycle {cycles} Log {log_s}s")
        if i_limits is not False and done:
            self.set_current_limits(done, *i_limits)

    # ---- 전류 기준 Min/Max (2026-11-13)
    def set_current_limits(self, chs, lo, hi):
        """lo/hi = None 이면 그 쪽 기준은 사용 안 함. 실행 중에도 바꿀 수 있다."""
        for i in chs:
            c = self.ch[i]
            c.i_min, c.i_max = lo, hi
            if lo is None and hi is None:
                self.cfg.i_limits.pop(str(i), None)
            else:
                self.cfg.i_limits[str(i)] = [lo, hi]
        try:
            self.cfg.save()
        except Exception as e:  # noqa
            self.log.write("ERROR", f"전류 기준 저장 실패: {e}")
        fmt = lambda x: "—" if x is None else f"{x:.2f}"
        self.log.write("USER", f"전류 기준 P{list(chs)}: Min {fmt(lo)} A / Max {fmt(hi)} A")
        self.info.emit(f"{', '.join(f'P{i:02d}' for i in chs)} 전류 기준 Min {fmt(lo)} A / Max {fmt(hi)} A 적용")

    def clear_alarms(self):
        """메인 화면 [알람 전체 해제]: 전류 기준 이탈 알람과 OCP 정지 표시를 지운다."""
        cleared = []
        for c in self.ch.values():
            if c.i_alarm:
                cleared.append(f"P{c.ch:02d} 전류 기준 이탈 ({c.i_alarm})")
                c.i_alarm = ""
            if c.status == "OCP" and not c.on:
                cleared.append(f"P{c.ch:02d} OCP")
                c.status = "READY" if c.assign else "OFF"
            c.i_hold = time.monotonic() + self.cfg.i_check_delay_s
        self.active_alarms.clear()
        self.log.write("USER", "알람 전체 해제" + (f": {'; '.join(cleared)}" if cleared else " (해제할 알람 없음)"))
        self.info.emit("알람 전체 해제" + (f" — {len(cleared)}건" if cleared else ""))

    def unassign(self, chs):
        for i in chs:
            if not self.ch[i].run:
                self.ch[i].assign = None
                if self.ch[i].status == "READY":
                    self.ch[i].status = "OFF"

    def start_func(self, chs):
        for i in chs:
            c = self.ch[i]
            if not c.assign or c.run or c.status == "ERR":
                continue
            try:
                f = self.functions.load(c.assign.func)
            except Exception as e:  # noqa
                self._raise("AL-06", f"{ALARMS['AL-06']}: {c.assign.func} ({e})")
                continue
            if c.manual:
                self._power_off(c, "Function 시작")
            if self.cfg.psu_hw_ocp and not self.cfg.simulate:
                self._safe(self.psu[i].set_ocp, c.assign.ocp)      # 장비 OCP 도 함께 (1초 주기 감시보다 빠름)
            owner = not any(o.run and o.run.owner for o in self.ch.values())
            c.run = Run(f, c.assign, owner=owner)
            c.last_log = 0
            self.data.start_run(i, f.name, snapshot=f.to_dict(),
                                cfg_info={"ocp": c.assign.ocp, "cycles": c.assign.cycles, "log_s": c.assign.log_s,
                                          "step_policy": self.cfg.step_policy, "chamber_owner": owner})
            self._enter_segment(c)
            c.status = "RUN"
            self.log.write("POWER", f"P{i:02d} Function '{f.name}' 시작 (챔버 제어권 {'O' if owner else 'X'})")

    # ================================================================ 통신 시험 (Setup › 통신 시험)
    def comm_exec(self, target: str, cmd: str):
        """target: "NEX" 또는 "P1"~"P6". cmd: 미리 정한 동작 이름 또는 원시 명령.

        NEX 원시 명령: "R 7001 19" (D7001 부터 19개 읽기) · "W 1300 500" (D1300 에 500 쓰기)
        Power 원시 명령: SCPI 문자열 그대로 (예: "MEAS:VOLT?", "*IDN?")
        결과는 comm_result 시그널로 화면에 보낸다.
        """
        t0 = time.monotonic()
        try:
            if target == "NEX":
                dev = self.chamber
                if cmd == "identify":
                    out = dev.identify() if hasattr(dev, "identify") else "시뮬레이터"
                elif cmd == "read":
                    r = dev.read()
                    out = (f"온도 PV {r.pv_t} / SP {r.sp_t} ℃ · 습도 PV {r.pv_h} / SP {r.sp_h} % · 출력 {r.mv_t:.1f}/{r.mv_h:.1f} % · "
                           f"{'운전' if r.running else '정지'} {r.mode} 패턴 {r.pattern} 세그 {r.segment} · 에러 '{r.error}'")
                    raw = getattr(dev, "last_raw", None)
                    if raw:
                        out += "\n  raw " + " ".join(f"{k}={v}" for k, v in raw.items())
                elif cmd == "limits":
                    (tl, th), (hl, hh) = dev.sp_limits()
                    out = f"SP 범위 온도 {tl} ~ {th} ℃ · 습도 {hl} ~ {hh} %"
                else:
                    p = cmd.split()
                    if not hasattr(dev, "read_regs"):
                        raise DeviceError("시뮬레이터에서는 원시 명령을 쓸 수 없습니다")
                    if p[0].upper() == "R":
                        no, n = int(p[1]), int(p[2]) if len(p) > 2 else 1
                        vals = dev.read_regs(no, n, signed=False)
                        out = "  ".join(f"D{no + k:04d}={v} (0x{v:04X})" for k, v in enumerate(vals))
                    elif p[0].upper() == "W":
                        dev.write_reg(int(p[1]), int(p[2]))
                        out = f"D{int(p[1]):04d} ← {int(p[2])} 쓰기 OK"
                    else:
                        raise DeviceError("형식: R <D번호> [개수] · W <D번호> <값>")
            else:
                i = int(target[1:])
                dev = self.psu[i]
                if cmd == "identify":
                    out = dev.raw("*IDN?") if hasattr(dev, "raw") else "시뮬레이터"
                elif cmd == "read":
                    r = dev.read()
                    out = f"{r.v:.2f} V / {r.a:.2f} A · 출력 {'ON' if r.on else 'OFF'} · 알람 '{r.alarm or '정상'}'"
                elif cmd == "clear":
                    dev.clear_alarm()
                    out = "알람 해제 (ASWRC 0) 보냄"
                else:
                    if not hasattr(dev, "raw"):
                        raise DeviceError("시뮬레이터에서는 원시 명령을 쓸 수 없습니다")
                    out = dev.raw(cmd)
            msg = f"OK ({(time.monotonic() - t0) * 1000:.0f} ms)  {out}"
        except Exception as e:  # noqa
            msg = f"실패 ({(time.monotonic() - t0) * 1000:.0f} ms)  {e}"
        self.log.write("COMM", f"[시험] {target} {cmd} → {msg}")
        self.comm_result.emit(f"{datetime.now():%H:%M:%S} {target:<3} {cmd}\n    {msg}")

    def stop_func(self, chs):
        for i in chs:
            if self.ch[i].run:
                self._power_off(self.ch[i], "사용자 중지")
