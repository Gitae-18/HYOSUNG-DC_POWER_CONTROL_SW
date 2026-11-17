"""장비 없이 실제 통신 드라이버를 시험하기 위한 가짜 시리얼 포트 (프로토콜 에뮬레이터).

- UspBusEmu : USP 1U·2U 여러 대가 붙은 RS-485 멀티드롭 버스 (SCPI 문자열, CADDR 선택)
- NexEmu    : NEX1200 MODBUS RTU 슬레이브 (D-Register)

사용:  from infrastructure.devices import devices; import tools.emulators as E; devices.SERIAL_FACTORY = E.factory(...)
실제 장비 응답 형식(끝 문자, 응답 지연 등)은 현장에서 확인해야 하며, 이 에뮬레이터는 통신설명서에 적힌 대로만 동작한다.
"""
from __future__ import annotations

import struct
import time

from infrastructure.devices.devices import crc16


class _Port:
    def __init__(self):
        self.rx = bytearray()      # 장치 → PC
        self.is_open = True

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        self.is_open = False

    def read(self, n):
        out, self.rx[:] = bytes(self.rx[:n]), self.rx[n:]
        return out

    def read_until(self, term=b"\n"):
        i = self.rx.find(term)
        if i < 0:
            return self.read(len(self.rx))
        return self.read(i + len(term))


# ---------------------------------------------------------------- USP
class UspDevice:
    def __init__(self, addr, load_ohm=20.0):
        self.addr, self.r = addr, load_ohm
        self.v = self.c = 0.0
        self.out = False
        self.alarm = "0"
        self.ocp_on, self.ocp_a = False, 0.0
        self.remote = False
        self.log: list[str] = []

    def meas(self):
        if not self.out:
            return 0.0, 0.0
        a = min(self.v / self.r, self.c)
        if self.ocp_on and a > self.ocp_a > 0:
            self.out, self.alarm = False, "2"
            return 0.0, 0.0
        return self.v, a


class UspBusEmu(_Port):
    def __init__(self, addrs=(1, 2, 3, 4, 5, 6), term=b"\n"):
        super().__init__()
        self.dev = {a: UspDevice(a, 18.0 + a) for a in addrs}
        self.sel = None
        self.term = term
        self.buf = bytearray()
        self.n_tx = 0

    def write(self, data: bytes):
        self.buf += data
        while True:
            i = self.buf.find(self.term)
            if i < 0:
                break
            line, self.buf[:] = self.buf[:i].decode(), self.buf[i + len(self.term):]
            self.n_tx += 1
            self._handle(line.strip())
        return len(data)

    def _reply(self, txt):
        self.rx += txt.encode() + self.term

    def _handle(self, line):
        up = line.upper()
        if up.startswith("CADDR"):
            a = int(line.split()[1])
            if a in self.dev:            # 없는 주소는 아무도 응답하지 않음
                self.sel = a
                self._reply("OK")
            else:
                self.sel = None
            return
        d = self.dev.get(self.sel)
        if d is None:
            return
        d.log.append(line)
        d.meas()                          # 장비는 스스로 보호 동작 (OCP 등)
        cmd, _, arg = up.partition(" ")
        if cmd == "*IDN?":
            self._reply(f"EMU,USP-1000,SN{d.addr:04d},V1.0")
        elif cmd == "SYST:REM":
            d.remote = True
        elif cmd == "SYST:LOC":
            d.remote = False
        elif cmd == "VOLT":
            d.v = float(arg)
        elif cmd == "CURR":
            d.c = float(arg)
        elif cmd == "OUTP":
            d.out = arg.strip() == "1"
        elif cmd == "OUTP?":
            self._reply("1" if d.out else "0")
        elif cmd == "MEAS:VOLT?":
            self._reply(f"{d.meas()[0]:.3f}")
        elif cmd == "MEAS:CURR?":
            self._reply(f"{d.meas()[1]:.4f}")
        elif cmd == "ASWRS?":
            self._reply(d.alarm)
        elif cmd == "ASWRC":
            d.alarm = "0"
        elif cmd == "PORT:OCP":
            d.ocp_on = arg.strip() == "1"
        elif cmd == "PORT:OCP:CURR":
            d.ocp_a = float(arg)
        elif cmd == "VOLT?":
            self._reply(f"{d.v:.3f}")


# ---------------------------------------------------------------- NEX1200
class NexEmu(_Port):
    def __init__(self, addr=1, dp_t=1, dp_h=1):
        super().__init__()
        self.addr = addr
        self.reg = {}                      # D 번호 → 정수 (16bit)
        self.writes: list[tuple[int, int]] = []
        s = lambda no, v: self.reg.__setitem__(no, v & 0xFFFF)
        self.set = s
        s(305, dp_t)
        s(323, dp_h)
        s(303, 1500)
        s(304, -500 & 0xFFFF)              # 범위 150.0 ~ -50.0
        s(391, 1500)
        s(392, -500 & 0xFFFF)
        s(393, 1000)
        s(394, 0)
        s(100, 1)                          # 정치
        s(1606, 1)
        s(1607, 0)
        s(1608, 0)
        s(1611, addr)
        self.pv_t, self.pv_h = 24.3, 48.0
        self.tsp_t, self.tsp_h = 25.0, 50.0
        self.running = False
        self.pattern = 1
        self.fail = False
        self._t = time.monotonic()
        self._sync()

    def _sync(self):
        dt = min(5.0, time.monotonic() - self._t)
        self._t = time.monotonic()
        if self.running:
            k = min(1.0, 0.5 * dt)
            self.pv_t += (self.tsp_t - self.pv_t) * k
            self.pv_h += (self.tsp_h - self.pv_h) * k
        fix = self.reg.get(100, 1) == 1
        pcss = (0x02 if fix else 0x01) if self.running else 0
        mon = [pcss | 0x1200, round(self.pv_t * 10), round(self.pv_h * 10), round((self.pv_t - 5) * 100),
               round(self.tsp_t * 10), round(self.tsp_h * 10), round(self.tsp_t * 10), round(self.tsp_h * 10),
               500 if self.running else 0, 300 if self.running else 0, 0, 0, 0, 0, 1 if fix else 0, self.pattern,
               0 if fix else 1, 0, 0]
        for i, v in enumerate(mon):
            self.reg[7001 + i] = v & 0xFFFF

    def write(self, data: bytes):
        f = bytes(data)
        if self.fail or len(f) < 4 or crc16(f[:-2]) != f[-2:] or f[0] != self.addr:
            return len(data)                # 응답 없음
        fc = f[1]
        self._sync()
        if fc == 3:
            a, n = struct.unpack(">HH", f[2:6])
            vals = [self.reg.get(a + 1 + k, 0) for k in range(n)]
            body = struct.pack(f">BBB{n}H", self.addr, 3, 2 * n, *vals)
        elif fc == 6:
            a, v = struct.unpack(">HH", f[2:6])
            self._store(a + 1, v)
            body = f[:6]
        elif fc == 16:
            a, n = struct.unpack(">HH", f[2:6])
            vals = struct.unpack(f">{n}H", f[7:7 + 2 * n])
            for k, v in enumerate(vals):
                self._store(a + 1 + k, v)
            body = f[:6]
        else:
            body = bytes([self.addr, fc | 0x80, 1])
        self.rx += body + crc16(body)
        return len(data)

    def _store(self, no, v):
        self.writes.append((no, v))
        self.reg[no] = v
        if no == 1300:
            self.tsp_t = struct.unpack(">h", struct.pack(">H", v))[0] / 10
        elif no == 1301:
            self.tsp_h = v / 10
        elif no == 1302:
            self.pattern = v
        elif no == 1303:
            self.running = v == 1 if v in (1, 4) else self.running


def factory(usp: UspBusEmu, nex: NexEmu, usp_port="COM2", nex_port="COM1"):
    def make(port, baud, parity, stopbits, timeout):
        if port == usp_port:
            usp.is_open = True
            return usp
        if port == nex_port:
            nex.is_open = True
            return nex
        raise OSError(f"could not open port {port}")
    return make
