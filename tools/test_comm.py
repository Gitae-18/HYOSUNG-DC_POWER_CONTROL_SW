"""통신 드라이버 시험 (장비 없이, 에뮬레이터 사용).

    python tools/test_comm.py

1) MODBUS CRC 를 NEX1200 통신설명서 예제와 비교
2) NEX1200 드라이버: 모니터링 읽기, SP/운전/변화율/시간설정 쓰기 → 레지스터 번호·값 확인
3) USP 드라이버: CADDR 선택, 설정·측정, 알람
4) DeviceHub 를 실제 드라이버(simulate=False)로 돌려 Test Power · Function · OCP · 통신 두절 확인
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("HYOSUNG_HMI_HOME", tempfile.mkdtemp(prefix="hmi_comm_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from infrastructure.devices import devices         # noqa: E402
from infrastructure.devices.devices import crc16, Nex1200Modbus, UspBus, UspPowerSupply  # noqa: E402
import emulators as E                              # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]   {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


print("1) CRC — 통신설명서 예제")
# ※ 설명서 13쪽 수신 예제 010306013608ED01F4 의 CRC 는 '8AE2' 로 적혀 있으나 표준 계산값은 '7AE2' —
#   나머지 5개 예제가 모두 맞으므로 설명서 오기로 보고 7AE2 로 확인한다.
for hexs, want in [("0103000B0001", "F5C8"), ("010300000003", "05CB"), ("0106051301F4", "78D4"),
                   ("0110051300020403E801F4", "0D81"), ("010306013608ED01F4", "7AE2"), ("011005130002", "B0C1")]:
    got = crc16(bytes.fromhex(hexs)).hex().upper()
    check(f"{hexs} → {want}", got == want, f"(계산 {got})")

print("2) NEX1200 드라이버")
nex, usp = E.NexEmu(), E.UspBusEmu(addrs=(1, 2, 3, 4, 5))       # 6번 장비는 없음(응답 없음 시험)
devices.SERIAL_FACTORY = E.factory(usp, nex)
c = Nex1200Modbus("COM1", 1)
c.open()
r = c.read()
check("PV 읽기 24.3 ℃ / 48.0 %", (r.pv_t, r.pv_h) == (24.3, 48.0), r)
check("정지 상태 · 정치 모드", (r.running, r.mode) == (False, "fix"), r)
check("SP 범위 D0391~D0394", c.sp_limits() == ((-50.0, 150.0), (0.0, 100.0)), c.sp_limits())
nex.writes.clear()
c.set_mode("fix")
c.set_rate(0, 0)
c.set_sp(-20.5, 85.0)
c.set_timed(True, 1.5, 2 * 3600 + 40 * 60 + 30)
c.run()
w = nex.writes
check("D0100 ← 1 (정치)", (100, 1) in w, w)
check("D1300 ← -205 (−20.5 ℃, 음수 2의 보수)", (1300, (-205) & 0xFFFF) in w, w)
check("D1301 ← 850", (1301, 850) in w, w)
check("D0105~D0108 시간설정 [1, 2시, 2430초, 15]", [x for x in w if 105 <= x[0] <= 108] == [(105, 1), (106, 2), (107, 2430), (108, 15)], w)
check("D1303 ← 1 (RUN)", w[-1] == (1303, 1), w)
r = c.read()
check("운전 중 · 목표 SP −20.5", r.running and r.sp_t == -20.5, r)
c.set_rate(3.0, 0)
check("변화율 D0103 = 3 ℃/min ÷ 범위 200 → 1.50 %/min (×100 = 150)", (103, 150) in nex.writes, nex.writes[-2:])
c.set_mode("program", 3)
check("D0100 ← 0, D1302 ← 3 (패턴 운전)", nex.writes[-2:] == [(100, 0), (1302, 3)], nex.writes[-2:])
c.stop()
check("D1303 ← 4 (STOP)", nex.writes[-1] == (1303, 4))
nex.set(12, 0x0002)
check("센서 B.OUT → 에러 문자열", "온도 센서 B.OUT" in c.read().error, c.read().error)
nex.set(12, 0)
nex.set(85, 1)
nex.set(86, 0x0004)
check("DI 에러 DI3", "DI3" in c.read().error, c.read().error)
nex.set(85, 0)
nex.set(86, 0)
nex.fail = True
try:
    c.read()
    check("응답 없음 → DeviceError", False)
except devices.DeviceError as e:
    check(f"응답 없음 → DeviceError ({e})", True)
nex.fail = False
c.close()

print("3) USP 드라이버 (멀티드롭)")
bus = UspBus("COM2")
p1, p3 = UspPowerSupply(bus, 1, 5, 5), UspPowerSupply(bus, 3, 5, 5)
p1.open()
p3.open()
check("*IDN? 식별", p1.identify().startswith("EMU,USP"), p1.identify())
p1.set_cv_cc(12.0, 5.0)
p1.output(True)
rd = p1.read()
check("P1 12 V / 부하 19 Ω → 0.6316 A", rd.on and abs(rd.v - 12.0) < 1e-6 and abs(rd.a - 12 / 19) < 1e-3, rd)
check("CADDR 로 장비별 설정 (P3 은 0 V)", usp.dev[3].v == 0.0 and usp.dev[1].v == 12.0)
check("SYST:REM 원격 모드", usp.dev[1].remote and usp.dev[3].remote)
n0 = usp.n_tx
p1.read()
p1.read()
check("같은 장비 연속 읽기 시 CADDR 생략 (2회 = 4명령)", usp.n_tx - n0 == 4, usp.n_tx - n0)
p1.set_ocp(0.5)
p1.n = 0
rd = p1.read()
check("장비 OCP 동작 → alarm 'OCP', 출력 OFF", rd.alarm == "OCP" and not rd.on, rd)
p6 = UspPowerSupply(bus, 6)
try:
    p6.read()
    check("없는 주소 6 → DeviceError", False)
except devices.DeviceError as e:
    check(f"없는 주소 6 → DeviceError ({e})", True)
p1.close()
p3.close()

print("4) DeviceHub + 실제 드라이버 (에뮬레이터)")
from PySide6.QtCore import QCoreApplication  # noqa: E402
app = QCoreApplication(sys.argv)
from infrastructure.system.config import Config  # noqa: E402
from application.services.hub import DeviceHub  # noqa: E402
from domain.models.function_model import Function, Segment, Step  # noqa: E402

nex2, usp2 = E.NexEmu(), E.UspBusEmu(addrs=(1, 2, 3, 4, 5))
devices.SERIAL_FACTORY = E.factory(usp2, nex2)
cfg = Config()
cfg.simulate = False
cfg.sim_demo_on = []
cfg.data_dir = os.path.join(os.environ["HYOSUNG_HMI_HOME"], "Data")
cfg.powers[4].enabled = False                          # P05 미사용
hub = DeviceHub(cfg)
alarms, snaps, results = [], [], []
hub.alarm.connect(lambda c_, m: alarms.append((c_, m)))
hub.updated.connect(lambda s: snaps.append(s))
hub.comm_result.connect(results.append)
hub.functions.save(Function(name="T-COMM", segments=[Segment(temp=30.0, humi=60.0, time_s=60, wait=False,
                                                       steps=[Step(time_s=60, volt=13.5, curr=4.0)])]))


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.05)


hub.start()
pump(1.5)
hub.call(hub.test_power_set, [1, 2], 12.0, 3.0)
hub.call(hub.test_power_on, [1, 2], True)
hub.call(hub.assign, [3], "T-COMM", 2.0, 1, 1.0)
hub.call(hub.start_func, [3])
pump(4.5)
s = snaps[-1]
ch = {d["ch"]: d for d in s["channels"]}
check("P1·P2 Test Power RUN 12 V", ch[1]["status"] == "RUN" and abs(ch[1]["v"] - 12) < 0.01 and ch[2]["status"] == "RUN", ch[1])
check("P3 Function RUN 13.5 V", ch[3]["status"] == "RUN" and abs(ch[3]["v"] - 13.5) < 0.01, ch[3])
check("P3 장비 OCP 2.0 A 설정 (PORT:OCP:CURR)", usp2.dev[3].ocp_on and usp2.dev[3].ocp_a == 2.0)
check("챔버 SP 30.0 ℃ / 60 % 쓰기 + RUN", nex2.tsp_t == 30.0 and nex2.tsp_h == 60.0 and nex2.running)
check("P05 미사용 → 통신 안 함", usp2.dev[5].log == [], usp2.dev[5].log[:3])
check("P06 없는 장비 → AL-02", any(a[0] == "AL-02" and "P06" in a[1] for a in alarms), alarms)
check("챔버 통신 정상", s["chamber_ok"] and s["chamber"]["pv_t"] > 24, s["chamber"])
usp2.dev[2].alarm = "C"                                # P2 과열 알람
pump(6.0)
check("P2 장비 과열 → AL-08", any(a[0] == "AL-08" and "OVER TEMPERATURE" in a[1] for a in alarms), alarms)
nex2.fail = True
pump(4.5)
check("NEX1200 3회 무응답 → AL-01", any(a[0] == "AL-01" for a in alarms), alarms)
check("화면 스냅샷 chamber_ok=False", not snaps[-1]["chamber_ok"])
nex2.fail = False
pump(2.0)
check("NEX1200 재연결", snaps[-1]["chamber_ok"])
hub.call(hub.comm_exec, "NEX", "R 7002 2")
hub.call(hub.comm_exec, "P1", "MEAS:VOLT?")
hub.call(hub.comm_exec, "P1", "read")
pump(1.5)
for r_ in results:
    print("     ", r_.replace("\n", " | "))
check("통신 시험: NEX 직접 읽기", any("D7002=" in r_ for r_ in results))
check("통신 시험: USP MEAS:VOLT?", any("MEAS:VOLT?" in r_ and "OK" in r_ for r_ in results))
hub.shutdown(stop_devices=True)
check("종료 시 모든 출력 OFF + 챔버 STOP", not any(d.out for d in usp2.dev.values()) and not nex2.running)
check("종료 시 SYST:LOC (로컬 복귀)", not usp2.dev[1].remote)
print(f"\n결과: {ok} 통과 / {fail} 실패")
sys.exit(1 if fail else 0)
