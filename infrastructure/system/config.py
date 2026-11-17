"""환경 설정 (POP-380) — config.json 으로 저장·로드.

미결 사항(스토리보드 V1.3.0 / 질의 답변) 중 값이 정해지지 않은 항목은
여기서 기본값으로 두고, 확정되면 이 파일 또는 config.json 만 고치면 된다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_DIR = Path(os.environ.get("HYOSUNG_HMI_HOME", Path.home() / "HyosungChamber"))
CONFIG_PATH = APP_DIR / "Settings" / "config.json"


@dataclass
class PowerPort:
    name: str
    com: str = ""          # (이전 버전 호환용, 사용 안 함) — 6대가 psu_com 한 포트를 함께 씀
    enabled: bool = True
    address: int = 0       # RS-485 주소 (USP 'CADDR n'). 0 이면 채널 번호와 같게


@dataclass
class Config:
    # --- 런처 (SCR-000) : 외부 프로그램 경로 (미결 11)
    lin_exe: str = r"C:\LIN\lin_hyoseong.exe"
    pwm_exe: str = r"C:\Program Files\PWM Tester\PWM Tester.exe"
    dcpower_cmd: str = ""          # 비워두면 같은 파이썬으로 app_dcpower 실행

    # --- 통신 (POP-380 통신 탭) — 2026-11-03 통신 설명서 반영
    simulate: bool = True          # 장비 없이 시뮬레이터로 동작 (실제 장비 연결 시 False)
    # NEX1200: RS-485, MODBUS RTU (설명서 2장 기본값 9600 / NONE / 8 / 1 / 주소 1)
    nex_com: str = "COM1"
    nex_address: int = 1
    nex_baud: int = 9600
    nex_parity: str = "N"
    nex_stopbits: int = 1
    nex_timeout_s: float = 0.5
    nex_rate_unit: str = "eus"     # 변화율 D0103/D0104 단위: "eus"(범위 대비 %/min) | "unit"(℃/min) — 현장 확인 후 확정
    # USP DC Power ×6: RS-485 멀티드롭 한 포트, SCPI 문자열 (CADDR n 으로 장비 선택)
    psu_com: str = "COM2"
    psu_baud: int = 9600           # TODO: 장비 메뉴의 통신 속도와 맞출 것
    psu_parity: str = "N"
    psu_stopbits: int = 1
    psu_timeout_s: float = 0.3
    psu_terminator: str = "\n"    # 명령 끝 문자: "\n" 또는 "\r\n" (현장 확인)
    psu_cmd_gap_ms: int = 30       # 응답 없는 설정 명령 뒤 쉬는 시간
    psu_status_every: int = 5      # OUTP?/ASWRS? 는 N 번 읽을 때 한 번
    psu_idle_every: int = 5        # 출력 OFF 채널은 N 번에 한 번만 측정
    psu_hw_ocp: bool = True        # Function 시작 시 장비 OCP(PORT:OCP:CURR) 를 할당 OCP 값으로 설정
    comm_trace: bool = False       # 송수신 기록 Log\COMM_YYYYMMDD.log
    powers: list[PowerPort] = field(default_factory=lambda: [
        PowerPort(f"Motor {i}", address=i) for i in range(1, 7)])

    # --- 장비 정격 / 챔버 사양 (질의 0, 4 — 고객사 회신 후 확정)
    psu_v_max: float = 60.0        # TODO: UPS 1000W 정격 전압
    psu_i_max: float = 61.0        # TODO: UPS 1000W 정격 전류
    nex_sp_t: tuple = (-50.0, 150.0)   # 차단 기준: NEX1200 SP 하/상한 (시작 시 장비에서 읽어 덮어씀)
    nex_sp_h: tuple = (0.0, 100.0)
    chamber_t: tuple = (-40.0, 120.0)  # 경고 기준: 챔버 운전 범위 (TODO)
    chamber_h: tuple = (20.0, 95.0)
    chamber_rate_max: float = 3.0      # 경고 기준: 최대 승·강온 속도 ℃/min (TODO)
    wait_tol_t: float = 1.0            # Wait 도달 판정 범위 (미결 3)
    wait_tol_h: float = 3.0
    wait_limit_min: int = 60           # Wait 대기 한도 → AL-04
    # --- 전류 기준 Min/Max (2026-11-13): Run Function · Test Power 에서 입력, 채널별 저장 {"1": [min, max], ...}
    #     출력 중 전류가 한 번이라도 범위를 벗어나면 AL-09 + 메인 카드 알람 (알람 전체 해제 전까지 유지)
    i_limits: dict = field(default_factory=dict)
    i_check_delay_s: float = 3.0       # 출력 ON·Step 전환 직후 이 시간 동안은 판정 안 함 (전류가 자리잡는 시간)

    # --- 저장 / 그래프  (data_dir\DB\연도\월\HyosungData_YYYYMMDD.duckdb — DuckDB 일별 파일, 1초 주기)
    data_dir: str = str(APP_DIR / "Data")
    chamber_log_interval_s: float = 1.0   # (2026-11-04) 사용 안 함 — DB 저장 주기 1초 고정
    disk_warn_gb: float = 1.0
    graph_default_min: int = 30
    # (2026-11-02) '그래프 저장' 버튼 삭제 → show_save_graph_button 항목 제거. 데이터는 DB 에 자동 저장.

    # --- 시뮬레이터 데모: 실행하면 이 채널들을 켠 상태로 시작 [(채널, 전압 V, 전류 제한 A), ...]
    sim_demo_on: list = field(default_factory=lambda: [[1, 12.0, 30.0], [2, 13.5, 30.0], [3, 14.0, 30.0]])

    # --- 매크로 실행 규칙 (질의 7)
    step_policy: str = "segment"   # "segment" = A안(Segment 시간 우선), "step" = B안

    admin_password: str = "0000"

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            raw["powers"] = [PowerPort(**{k: v for k, v in p.items() if k in PowerPort.__dataclass_fields__})
                             for p in raw.get("powers", [])] or cls().powers
            for i, p in enumerate(raw["powers"], 1):
                if not p.address:
                    p.address = i
            for k in ("nex_sp_t", "nex_sp_h", "chamber_t", "chamber_h"):
                if k in raw:
                    raw[k] = tuple(raw[k])
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in raw.items() if k in known})
        cfg = cls()
        cfg.save()
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
