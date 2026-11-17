"""장비 계층: DC Power Supply ×6, NEX1200 온습도 컨트롤러.

- Sim*  : 장비 없이 화면·로직을 확인하기 위한 시뮬레이터 (config.simulate=True)
- 실제 장비 (2026-11-03, 통신 설명서 기준)
    UspBus / UspPowerSupply : USP 1U·2U DC Power — RS-485 멀티드롭(COM2), SCPI 문자열, 'CADDR n' 으로 장비 선택
    Nex1200Modbus           : NEX1200 온습도 컨트롤러 — RS-485(COM1), MODBUS RTU, D-Register
UI 는 이 파일의 인터페이스(PowerSupply, Chamber)만 사용하므로 실제 드라이버로 교체해도 화면 코드는 바뀌지 않는다.
"""
from __future__ import annotations

import math
import random
import re
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class DeviceError(Exception):
    pass


# ============================================================ data
@dataclass
class PsuReading:
    v: float
    a: float
    on: bool
    alarm: str = ""     # 장비 자체 알람 (USP ASWRS? 코드 → 이름), 정상이면 ""


@dataclass
class ChamberReading:
    pv_t: float
    pv_h: float
    sp_t: float
    sp_h: float
    mv_t: float
    mv_h: float
    running: bool
    mode: str           # "fix" | "program"
    pattern: int = 0
    segment: int = 0
    error: str = ""     # DI 에러 / IO COM / 센서 단선


# ============================================================ interfaces
class PowerSupply:
    def open(self): ...
    def close(self): ...
    def identify(self) -> str: return ""
    def set_cv_cc(self, v: float, a: float): raise NotImplementedError
    def output(self, on: bool): raise NotImplementedError
    def read(self) -> PsuReading: raise NotImplementedError
    def set_ocp(self, amps: float | None): ...      # 장비 OCP 보호 설정 (None = 해제)
    def clear_alarm(self): ...


class Chamber:
    def open(self): ...
    def close(self): ...
    def read(self) -> ChamberReading: raise NotImplementedError
    def set_sp(self, t: float, h: float | None): raise NotImplementedError
    def set_rate(self, t_rate: float, h_rate: float): raise NotImplementedError   # ℃/min, %/min (0 = 즉시)
    def set_mode(self, mode: str, pattern: int = 1): raise NotImplementedError
    def set_timed(self, on: bool, dev_t: float = 0, seconds: int = 0): ...
    def run(self): raise NotImplementedError
    def stop(self): raise NotImplementedError
    def sp_limits(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (-50.0, 150.0), (0.0, 100.0)


# ============================================================ simulators
class SimPowerSupply(PowerSupply):
    """모터 부하를 흉내낸 가상 전원.

    출력 ON 상태에서는 전압이 설정값 주변에서 ±0.05 V 정도 천천히 흔들리고,
    전류는 부하 변동(±5 %, 주기 수십 초) + 잡음으로 조금씩 바뀐다. 채널마다 주기를 달리해 겹치지 않게 했다.
    """

    def __init__(self, ch: int, fail: bool = False):
        self.ch, self.fail = ch, fail
        self.cv, self.cc, self.on = 12.0 + (ch > 2) * 2, 61.0, False
        self.r = 20.0 + ch * 1.5             # 부하 저항(Ω) — 채널별로 다르게
        self.phase = random.uniform(0, 2 * math.pi)
        self.period_v = 40 + ch * 7          # 전압 흔들림 주기(s)
        self.period_i = 25 + ch * 5          # 전류 변동 주기(s)
        self._drift = 0.0

    def _chk(self):
        if self.fail:
            raise DeviceError(f"P{self.ch:02d} 응답 없음")

    def set_cv_cc(self, v, a):
        self._chk()
        self.cv, self.cc = float(v), float(a)

    def output(self, on):
        self._chk()
        self.on = bool(on)

    def read(self):
        self._chk()
        t = time.time()
        if not self.on:
            # 출력 OFF: 값이 변하지 않음 (2026-11-02 요청 — 시뮬레이션 변동은 RUN 상태에서만)
            return PsuReading(0.0, 0.0, False)
        self._drift = max(-0.03, min(0.03, self._drift + random.uniform(-0.004, 0.004)))   # 느린 랜덤 드리프트
        v = self.cv + 0.05 * math.sin(2 * math.pi * t / self.period_v + self.phase) + self._drift + random.uniform(-0.005, 0.005)
        load = 1 + 0.05 * math.sin(2 * math.pi * t / self.period_i + self.phase) + 0.02 * math.sin(2 * math.pi * t / 7.3)
        i = min(max(v, 0) / self.r * load + random.uniform(-0.003, 0.003), self.cc)
        return PsuReading(round(v, 3), round(max(0.0, i), 4), True)


class SimChamber(Chamber):
    def __init__(self):
        self.pv_t, self.pv_h = 24.3, 48.4
        self.sp_t, self.sp_h = 25.0, 50.0
        self.csp_t, self.csp_h = self.sp_t, self.sp_h
        self.rate_t = self.rate_h = 0.0
        self.running, self.mode, self.pattern = False, "fix", 1
        self._t = time.monotonic()

    def _step(self):
        now = time.monotonic()
        dt = min(5.0, now - self._t)
        self._t = now
        if not self.running:
            # 정지 중에도 실내 온·습도처럼 조금씩 흔들리게
            self.pv_t += (24.0 - self.pv_t) * 0.002 * dt + random.uniform(-0.03, 0.03)
            self.pv_h += (48.0 - self.pv_h) * 0.002 * dt + random.uniform(-0.1, 0.1)
            return
        for attr_c, attr_sp, attr_r in (("csp_t", "sp_t", "rate_t"), ("csp_h", "sp_h", "rate_h")):
            c, sp, r = getattr(self, attr_c), getattr(self, attr_sp), getattr(self, attr_r)
            if r <= 0:
                c = sp
            else:
                d = r / 60 * dt * 60     # 시뮬레이션 60배속: 1분 변화를 1초에
                c = min(sp, c + d) if sp > c else max(sp, c - d)
            setattr(self, attr_c, c)
        k = min(1.0, 0.25 * dt)
        self.pv_t += (self.csp_t - self.pv_t) * k + random.uniform(-0.05, 0.05)
        self.pv_h += (self.csp_h - self.pv_h) * k + random.uniform(-0.2, 0.2)

    def read(self):
        self._step()
        mv = lambda pv, sp: max(0.0, min(100.0, 50 + (sp - pv) * 8)) if self.running else 0.0
        return ChamberReading(round(self.pv_t, 1), round(self.pv_h, 1), self.sp_t, self.sp_h,
                              mv(self.pv_t, self.csp_t), mv(self.pv_h, self.csp_h), self.running, self.mode,
                              self.pattern if self.mode == "program" else 0, 1 if self.mode == "program" else 0)

    def set_sp(self, t, h):
        self.sp_t = float(t)
        if h is not None:
            self.sp_h = float(h)

    def set_rate(self, t_rate, h_rate):
        self.rate_t, self.rate_h = abs(t_rate), abs(h_rate)

    def set_mode(self, mode, pattern=1):
        self.mode, self.pattern = mode, pattern

    def run(self):
        self.csp_t, self.csp_h = self.pv_t, self.pv_h
        self.running = True

    def stop(self):
        self.running = False


# ============================================================ 공통: 시리얼 포트 · 통신 기록
SERIAL_FACTORY = None      # 시험용: 가짜 포트를 넣을 때 사용 (test/emulators.py). None 이면 pyserial


def open_serial(port: str, baud: int, parity: str = "N", stopbits: int = 1, timeout: float = 0.5):
    if SERIAL_FACTORY:
        return SERIAL_FACTORY(port, baud, parity, stopbits, timeout)
    import serial  # pyserial
    return serial.Serial(port, baudrate=baud, bytesize=8, parity=parity, stopbits=stopbits,
                         timeout=timeout, write_timeout=1.0)


class CommTrace:
    """송수신 기록 (Setup › 통신 기록 켜기). Log\\COMM_YYYYMMDD.log — 현장 통신 시험 때 원인 확인용."""
    enabled = False
    folder: Path | None = None
    _lk = threading.Lock()

    @classmethod
    def write(cls, dev: str, direction: str, data):
        if not (cls.enabled and cls.folder):
            return
        if isinstance(data, (bytes, bytearray)):
            txt = data.hex(" ").upper()
        else:
            txt = repr(data)
        now = datetime.now()
        with cls._lk, open(Path(cls.folder) / f"COMM_{now:%Y%m%d}.log", "a", encoding="utf-8") as f:
            f.write(f"{now:%H:%M:%S.%f}"[:-3] + f" {dev:<7} {direction} {txt}\n")


# ============================================================ USP 1U·2U DC Power (SCPI over RS-485 멀티드롭)
USP_ALARMS = {"0": "", "1": "OVP", "2": "OCP", "3": "OPP", "4": "CV→CC", "5": "CC→CV", "6": "SLAVE OUT LINE",
              "7": "CURRCOUNT NOT READY", "8": "CURRCOUNT FAIL", "9": "OVER VOLTAGE", "A": "SHORT CIRCUIT",
              "B": "FAN FAULT", "C": "OVER TEMPERATURE", "D": "NTC FAIL", "E": "PRIMARY FAIL"}
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


class UspBus:
    """RS-485 한 포트(COM2)에 USP 6대를 멀티드롭으로 연결.

    통신설명서 2장: 'CADDR n' 을 먼저 보내 OK 를 받으면 그 장비가 이후 명령을 받는다.
    - 설정 명령(VOLT, CURR, OUTP …)은 응답이 없다 → 보낸 뒤 cmd_gap 만큼 쉰다.
    - 질의 명령(… ?)은 한 줄 응답.
    - 모든 장비 I/O 는 hub 작업 스레드 하나에서만 일어나지만, 통신 시험 창도 쓰므로 lock 으로 보호한다.
    """

    def __init__(self, port: str, baud: int = 9600, parity: str = "N", stopbits: int = 1,
                 timeout: float = 0.3, terminator: str = "\n", cmd_gap_ms: int = 30):
        self.port, self.baud, self.parity, self.stopbits = port, baud, parity, stopbits
        self.timeout, self.term, self.gap = timeout, terminator.encode(), cmd_gap_ms / 1000
        self.ser = None
        self.selected: int | None = None
        self.lock = threading.RLock()

    def open(self):
        with self.lock:
            if self.ser is None:
                self.ser = open_serial(self.port, self.baud, self.parity, self.stopbits, self.timeout)
                self.selected = None

    def close(self):
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                finally:
                    self.ser = None
                    self.selected = None

    # ---- 저수준
    def _send(self, cmd: str, tag: str):
        if self.ser is None:
            self.open()
        self.ser.reset_input_buffer()
        CommTrace.write(tag, "TX", cmd)
        self.ser.write(cmd.encode("ascii") + self.term)

    def _readline(self, tag: str) -> str:
        raw = self.ser.read_until(self.term)
        txt = raw.decode("ascii", errors="ignore").strip()
        CommTrace.write(tag, "RX", txt if raw else "(응답 없음)")
        return txt

    def select(self, addr: int):
        """CADDR n → OK. 같은 장비를 연속으로 쓰면 다시 보내지 않는다."""
        if self.selected == addr:
            return
        tag = f"USP{addr}"
        for attempt in range(2):
            self._send(f"CADDR {addr}", tag)
            r = self._readline(tag)
            if r.upper().startswith("OK"):
                self.selected = addr
                return
        self.selected = None
        raise DeviceError(f"USP 주소 {addr} 응답 없음 (CADDR)")

    def write(self, addr: int, cmd: str):
        with self.lock:
            try:
                self.select(addr)
                self._send(cmd, f"USP{addr}")
                time.sleep(self.gap)
            except DeviceError:
                raise
            except Exception as e:  # noqa — 포트 오류
                self.close()
                raise DeviceError(f"{self.port} 오류: {e}")

    def query(self, addr: int, cmd: str) -> str:
        with self.lock:
            try:
                self.select(addr)
                for attempt in range(2):
                    self._send(cmd, f"USP{addr}")
                    r = self._readline(f"USP{addr}")
                    if r:
                        return r
                self.selected = None          # 응답이 없으면 다음에 CADDR 부터 다시
                raise DeviceError(f"USP 주소 {addr} 응답 없음 ({cmd})")
            except DeviceError:
                raise
            except Exception as e:  # noqa
                self.close()
                raise DeviceError(f"{self.port} 오류: {e}")


class UspPowerSupply(PowerSupply):
    """USP 1U·2U 1대 (버스 위 주소 addr).

    읽기 주기 줄이기: 측정(MEAS:VOLT?, MEAS:CURR?)은 출력 ON 일 때 매번, OFF 일 때는 idle_every 번에 한 번.
    출력 상태(OUTP?)·알람(ASWRS?)은 status_every 번에 한 번 (6대 × 질의 수가 1초 주기 안에 들어오도록).
    """
    _refs: dict[int, int] = {}

    def __init__(self, bus: UspBus, addr: int, status_every: int = 5, idle_every: int = 5):
        self.bus, self.addr = bus, addr
        self.status_every, self.idle_every = max(1, status_every), max(1, idle_every)
        self.n = 0
        self.last = PsuReading(0.0, 0.0, False)
        self.on = False
        self.idn = ""
        self.err = ""

    def open(self):
        self.bus.open()
        UspPowerSupply._refs[id(self.bus)] = UspPowerSupply._refs.get(id(self.bus), 0) + 1
        self.bus.write(self.addr, "SYST:REM")            # 원격 제어 모드
        try:
            self.idn = self.bus.query(self.addr, "*IDN?")
        except DeviceError:
            self.idn = ""
        self.on = self._q_out()
        self.last = PsuReading(0.0, 0.0, self.on)

    def close(self):
        try:
            self.bus.write(self.addr, "SYST:LOC")        # 장비 앞면 조작 가능하게 (출력은 그대로)
        except Exception:  # noqa
            pass
        k = id(self.bus)
        UspPowerSupply._refs[k] = UspPowerSupply._refs.get(k, 1) - 1
        if UspPowerSupply._refs[k] <= 0:
            self.bus.close()

    def identify(self) -> str:
        return self.idn

    @staticmethod
    def _num(txt: str) -> float:
        m = _NUM.search(txt)
        if not m:
            raise DeviceError(f"숫자 아님: {txt!r}")
        return float(m.group())

    def _q_out(self) -> bool:
        r = self.bus.query(self.addr, "OUTP?").strip().upper()
        return r in ("1", "ON") or r.startswith("1")

    # ---- 명령
    def set_cv_cc(self, v, a):
        self.bus.write(self.addr, f"VOLT {v:.3f}")
        self.bus.write(self.addr, f"CURR {a:.3f}")

    def output(self, on):
        if on:
            self.clear_alarm()
        self.bus.write(self.addr, f"OUTP {1 if on else 0}")
        self.on = bool(on)
        self.n = 0                                       # 다음 read 에서 상태를 바로 확인

    def set_ocp(self, amps):
        if amps is None or amps <= 0:
            self.bus.write(self.addr, "PORT:OCP 0")
        else:
            self.bus.write(self.addr, f"PORT:OCP:CURR {amps:.3f}")
            self.bus.write(self.addr, "PORT:OCP 1")

    def clear_alarm(self):
        self.bus.write(self.addr, "ASWRC 0")

    def read(self):
        full = self.n % self.status_every == 0
        meas = self.on or self.n % self.idle_every == 0
        self.n += 1
        if self.err and not full:
            # 응답 없는 장비: 버스를 오래 잡지 않도록 status_every 번에 한 번만 다시 시도, 그 사이에는 같은 오류
            raise DeviceError(self.err)
        alarm = self.last.alarm
        try:
            if full:
                self.on = self._q_out()
                code = self.bus.query(self.addr, "ASWRS?").strip().upper()[-1:] or "0"
                alarm = USP_ALARMS.get(code, f"코드 {code}")
                meas = True
            if meas:
                v = self._num(self.bus.query(self.addr, "MEAS:VOLT?"))
                a = self._num(self.bus.query(self.addr, "MEAS:CURR?"))
                self.last = PsuReading(round(v, 3), round(a, 4), self.on, alarm)
            else:
                self.last = PsuReading(self.last.v, self.last.a, self.on, alarm)
        except DeviceError as e:
            self.err = str(e)
            raise
        self.err = ""
        return self.last

    # ---- 통신 시험 창용
    def raw(self, cmd: str) -> str:
        if cmd.strip().endswith("?") or "?" in cmd:
            return self.bus.query(self.addr, cmd.strip())
        self.bus.write(self.addr, cmd.strip())
        return "(설정 명령 — 응답 없음)"


# ============================================================ NEX1200 (MODBUS RTU, D-Register)
def crc16(data: bytes) -> bytes:
    """MODBUS CRC-16 (초기값 FFFF, 다항식 A001), 하위 바이트 먼저. 설명서 6쪽 예제: 0103000B0001 → F5C8."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def d(no: int) -> int:
    """D-Register 번호 → MODBUS 주소 (설명서 4-1: 번호에서 1을 뺀다). D1300 → 1299(0x0513)."""
    return no - 1


class Nex1200Modbus(Chamber):
    """NEX1200 온습도 컨트롤러 — 통신 설명서 V.2504 기준 D-Register.

    읽기 (1초마다 3회 요청)
      D7001~D7019  모니터링 블록: 운전상태, 온도/습도 PV, 현재·목표 SP, 출력량, DI, 운전방식, 패턴, 세그
      D0011~D0013  IO BD 통신상태, 온도·습도 센서 상태(B.OUT, ±OVER, S.OPEN)
      D0085~D0087  DI 에러 상태, DI 상태 1·2
    쓰기
      D0100 운전 방식(패턴 0 / 정치 1) · D1302 패턴 번호 · D1300/D1301 정치 온·습도 SP
      D0103/D0104 설정값 변화율 · D0105~D0109 시간설정 운전 · D1303 운전 명령(1 RUN, 4 STOP)
    소수점: D0305(온도), D0323(습도) 를 연결 시 읽어 배율 결정 (기본 1자리 = ×10)
    """
    # 모니터링 블록 (D7001 부터 19개) 안의 위치
    MON = ["pcss", "pv_t", "pv_h", "pv_wet", "csp_t", "csp_h", "tsp_t", "tsp_h", "out_t", "out_h",
           "di1", "di2", "ttm_h", "ttm_ms", "run_mod", "pattern", "segment", "seg_h", "seg_ms"]
    PCSS = {0x01: "PROGRAM RUN", 0x02: "FIX RUN", 0x04: "HOLD", 0x08: "STEP", 0x10: "AUTO TUNING/TIME", 0x40: "WAIT RUN"}
    ADCS = {0x02: "B.OUT", 0x08: "+OVER", 0x10: "-OVER", 0x20: "S.OPEN"}

    def __init__(self, port: str, address: int = 1, baud: int = 9600, parity: str = "N", stopbits: int = 1,
                 timeout: float = 0.5, rate_unit: str = "eus"):
        self.port, self.addr, self.baud, self.parity, self.stopbits = port, address, baud, parity, stopbits
        self.timeout, self.rate_unit = timeout, rate_unit
        self.ser = None
        self.dp_t = self.dp_h = 1
        self.span_t, self.span_h = 200.0, 100.0
        self.lock = threading.RLock()
        self.last_raw: dict = {}

    # ---- 포트
    def open(self):
        with self.lock:
            if self.ser is None:
                self.ser = open_serial(self.port, self.baud, self.parity, self.stopbits, self.timeout)
        try:
            self.dp_t, = self.read_regs(305, 1)
            self.dp_h, = self.read_regs(323, 1)
            self.dp_t, self.dp_h = max(0, min(2, self.dp_t)), max(0, min(1, self.dp_h))
            rh, rl = self.read_regs(303, 2)                 # 온도 범위 상한·하한 (변화율 EUS% 환산용)
            self.span_t = abs(rh - rl) / 10 ** self.dp_t or 200.0
        except DeviceError:
            pass                                            # 못 읽으면 기본값(소수 1자리) 유지, read() 에서 통신 이상 처리

    def close(self):
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                finally:
                    self.ser = None

    # ---- MODBUS RTU
    def _xfer(self, pdu: bytes, expect: int) -> bytes:
        frame = pdu + crc16(pdu)
        with self.lock:
            if self.ser is None:
                self.open_port_only()
            last_err = None
            for attempt in range(2):
                try:
                    self.ser.reset_input_buffer()
                    CommTrace.write("NEX1200", "TX", frame)
                    self.ser.write(frame)
                    r = self.ser.read(expect)
                    if len(r) >= 5 and r[1] & 0x80:            # 예외 응답 (5 bytes)
                        r = r[:5]
                    CommTrace.write("NEX1200", "RX", r if r else "(응답 없음)")
                    if not r:
                        raise DeviceError("NEX1200 응답 없음")
                    if crc16(r[:-2]) != r[-2:]:
                        raise DeviceError(f"NEX1200 CRC 오류 ({r.hex(' ')})")
                    if r[0] != self.addr:
                        raise DeviceError(f"NEX1200 주소 불일치 ({r[0]})")
                    if r[1] & 0x80:
                        raise DeviceError(f"NEX1200 예외 응답 code {r[2]} (기능 {r[1] & 0x7F})")
                    if len(r) < expect:
                        raise DeviceError(f"NEX1200 응답 길이 부족 ({len(r)}/{expect})")
                    return r
                except DeviceError as e:
                    last_err = e
                    time.sleep(0.05)
                except Exception as e:  # noqa — 포트 오류
                    self.close()
                    raise DeviceError(f"{self.port} 오류: {e}")
            raise last_err

    def open_port_only(self):
        self.ser = open_serial(self.port, self.baud, self.parity, self.stopbits, self.timeout)

    def read_regs(self, dno: int, n: int, signed: bool = True) -> list[int]:
        """기능 03: D{dno} 부터 n 개 (최대 64)."""
        r = self._xfer(struct.pack(">BBHH", self.addr, 3, d(dno), n), 5 + 2 * n)
        if r[2] != 2 * n:
            raise DeviceError(f"NEX1200 데이터 길이 오류 ({r[2]})")
        return list(struct.unpack(f">{n}{'h' if signed else 'H'}", r[3:3 + 2 * n]))

    def write_reg(self, dno: int, value: int):
        """기능 06: D{dno} 에 1개 쓰기. 정상이면 보낸 프레임이 그대로 돌아온다."""
        pdu = struct.pack(">BBHH", self.addr, 6, d(dno), int(value) & 0xFFFF)
        r = self._xfer(pdu, 8)
        if r[:6] != pdu:
            raise DeviceError(f"NEX1200 쓰기 확인 실패 D{dno:04d}")

    def write_regs(self, dno: int, values: list[int]):
        """기능 16: D{dno} 부터 연속 쓰기."""
        n = len(values)
        pdu = struct.pack(f">BBHHB{n}H", self.addr, 16, d(dno), n, 2 * n, *[int(v) & 0xFFFF for v in values])
        r = self._xfer(pdu, 8)
        if struct.unpack(">HH", r[2:6]) != (d(dno), n):
            raise DeviceError(f"NEX1200 연속 쓰기 확인 실패 D{dno:04d}")

    # ---- 배율
    def _t(self, raw: int) -> float:
        return raw / 10 ** self.dp_t

    def _h(self, raw: int) -> float:
        return raw / 10 ** self.dp_h

    def _rt(self, val: float) -> int:
        return round(val * 10 ** self.dp_t)

    def _rh(self, val: float) -> int:
        return round(val * 10 ** self.dp_h)

    # ---- Chamber 인터페이스
    def read(self):
        m = dict(zip(self.MON, self.read_regs(7001, len(self.MON))))
        hw, adc_t, adc_h = self.read_regs(11, 3, signed=False)
        dierr, di1, di2 = self.read_regs(85, 3, signed=False)
        self.last_raw = dict(m, hw=hw, adc_t=adc_t, adc_h=adc_h, dierr=dierr, dierr1=di1, dierr2=di2)
        pcss = m["pcss"] & 0xFF                              # 설명서: 뒤 2자리(하위 바이트)만 사용
        errs = []
        if hw & 0x40:
            errs.append("IO BD1 접속 불량")
        if hw & 0x80:
            errs.append("IO BD2 접속 불량")
        for name, v in (("온도 센서", adc_t), ("습도 센서", adc_h)):
            bad = [t for b, t in self.ADCS.items() if v & b]
            if bad:
                errs.append(f"{name} {'/'.join(bad)}")
        if dierr:
            bits = [f"DI{i + 1}" for i in range(16) if di1 & (1 << i)] + [f"DI{i + 17}" for i in range(16) if di2 & (1 << i)]
            errs.append("DI 에러 " + (",".join(bits) if bits else ""))
        return ChamberReading(
            pv_t=self._t(m["pv_t"]), pv_h=self._h(m["pv_h"]),
            sp_t=self._t(m["tsp_t"]), sp_h=self._h(m["tsp_h"]),          # 화면 '설정' = 목표 설정값(TSP)
            mv_t=m["out_t"] / 10, mv_h=m["out_h"] / 10,
            running=pcss != 0, mode="fix" if m["run_mod"] == 1 else "program",
            pattern=m["pattern"], segment=m["segment"], error=" · ".join(errs).strip())

    def set_sp(self, t, h):
        if h is None:
            self.write_reg(1300, self._rt(t))
        else:
            self.write_regs(1300, [self._rt(t), self._rh(h)])

    def set_rate(self, t_rate, h_rate):
        """D0103/D0104 설정값 변화율. 0 = 변화율 없음(즉시).
        rate_unit "eus": ℃/min 을 범위(EUS) 대비 %/min (소수 2자리) 로 환산 — NEX1200 사용설명서 기준.
        rate_unit "unit": ℃/min 그대로 (온도 소수점 배율). 실제 장비에서 확인 후 config 로 선택."""
        if self.rate_unit == "eus":
            vt = round(abs(t_rate) / self.span_t * 100 * 100)
            vh = round(abs(h_rate) / self.span_h * 100 * 100)
        else:
            vt, vh = self._rt(abs(t_rate)), self._rh(abs(h_rate))
        self.write_regs(103, [vt, vh])

    def set_mode(self, mode, pattern=1):
        self.write_reg(100, 0 if mode == "program" else 1)
        if mode == "program":
            self.write_reg(1302, int(pattern))

    def set_timed(self, on, dev_t=0, seconds=0):
        if not on:
            self.write_reg(105, 0)
            return
        h, rest = divmod(int(seconds), 3600)
        self.write_regs(105, [1, h, rest, self._rt(dev_t)])    # D0105 ON, D0106 시, D0107 분+초(초 단위), D0108 온도 편차

    def run(self):
        self.write_reg(1303, 1)

    def stop(self):
        self.write_reg(1303, 4)

    def sp_limits(self):
        th, tl = self.read_regs(391, 2)          # D0391 설정값 상한, D0392 하한
        hh, hl = self.read_regs(393, 2)          # D0393 / D0394
        return (self._t(tl), self._t(th)), (self._h(hl), self._h(hh))

    # ---- 통신 시험 창용
    def identify(self) -> str:
        try:
            proto, bps, par, stop, data, num = self.read_regs(1606, 6, signed=False)
            return (f"RS485 프로토콜 {['미사용', 'MODBUS RTU', 'MODBUS ASCII'][proto] if proto < 3 else proto} · "
                    f"{[9600, 19200, 38400][bps] if bps < 3 else bps} bps · 패리티 {'NEO'[par] if par < 3 else par} · 주소 {num}")
        except DeviceError as e:
            return f"설정 읽기 실패: {e}"
