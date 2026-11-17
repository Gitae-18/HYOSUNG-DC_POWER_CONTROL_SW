"""Function(매크로) 데이터 모델 — 스토리보드 POP-340 탭② (재구상안 반영).

Function = Name + Segment[] ; Segment = 목표 온/습도 + 시간 (+Wait) + Step[] ;
Step = 시간 / 온도 / 습도 조건 + 전압·전류.  변화율은 저장하지 않고 항상 계산한다.

2026-11-13: 시간 입력을 숫자 연속 입력(hhmmss)으로도 받음 (parse_hms).
            Function Profile 내보내기/가져오기 파일(.hfp, JSON) — write_profile / read_profile.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path


def hms(sec: int | None) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_hms(text: str) -> int | None:
    """시간 입력 → 초.

    2026-11-13: 숫자만 연속 입력 가능 (hhmmss). 뒤에서부터 2자리씩 초·분, 나머지는 시간.
      "013000" → 01:30:00 · "500" → 00:05:00 · "1000000" → 100:00:00
    기존 "H:M:S" (콜론·공백 구분) 입력도 그대로 받는다.
    """
    text = (text or "").strip()
    if text in ("", "—", "-"):
        return None
    if text.isdigit():
        d = text.zfill(6)
        h, m, s = int(d[:-4]), int(d[-4:-2]), int(d[-2:])
    else:
        if not re.fullmatch(r"[0-9:\s]+", text):
            raise ValueError("시간은 숫자(hhmmss) 또는 H:M:S 로 입력합니다")
        parts = [int(p) for p in re.split(r"[:\s]+", text) if p != ""]
        if not parts or len(parts) > 3:
            raise ValueError("시간은 숫자(hhmmss) 또는 H:M:S 로 입력합니다")
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts
    if m > 59 or s > 59:
        raise ValueError("분·초는 59 이하입니다 (hhmmss)")
    return h * 3600 + m * 60 + s


@dataclass
class Step:
    time_s: int | None = 600       # 시간 조건 (None = 사용 안 함)
    temp: float | None = None      # 온도 도달 조건
    humi: float | None = None      # 습도 도달 조건
    volt: float = 0.0
    curr: float = 0.0


@dataclass
class Segment:
    temp: float = 25.0
    humi: float | None = 50.0      # None = 습도 제어 안 함
    time_s: int = 3600
    wait: bool = False
    steps: list[Step] = field(default_factory=lambda: [Step()])


@dataclass
class Function:
    name: str = "새 Function"
    start_mode: str = "pv"         # "pv" = 현재 측정값, "manual" = 직접 입력
    start_temp: float = 25.0
    start_humi: float = 50.0
    segments: list[Segment] = field(default_factory=lambda: [Segment()])

    # ---------------------------------------------------------- 계산
    def rates(self, pv_t: float | None = None, pv_h: float | None = None) -> list[tuple[float, float | None]]:
        """Segment 별 (온도 ℃/min, 습도 %/min). 변화율 = (목표 − 이전 목표) ÷ 시간(분)."""
        prev_t = self.start_temp if self.start_mode == "manual" or pv_t is None else pv_t
        prev_h = self.start_humi if self.start_mode == "manual" or pv_h is None else pv_h
        out = []
        for s in self.segments:
            m = max(s.time_s, 1) / 60
            rt = (s.temp - prev_t) / m
            rh = None if s.humi is None else (s.humi - prev_h) / m
            out.append((rt, rh))
            prev_t = s.temp
            prev_h = s.humi if s.humi is not None else prev_h
        return out

    @property
    def total_s(self) -> int:
        return sum(s.time_s for s in self.segments)

    @property
    def step_count(self) -> int:
        return sum(len(s.steps) for s in self.segments)

    def validate(self, cfg) -> tuple[list[str], list[str]]:
        """(오류 = 저장 불가, 경고 = 확인 후 저장) — 질의 4·7 답변 규칙."""
        err, warn = [], []
        if not self.name.strip():
            err.append("Function 이름을 입력하세요")
        if re.search(r'[\\/:*?"<>|]', self.name):
            err.append("이름에 \\ / : * ? \" < > | 는 쓸 수 없습니다")
        if not self.segments:
            err.append("Segment 가 1개 이상 필요합니다")
        (tl, th), (hl, hh) = cfg.nex_sp_t, cfg.nex_sp_h
        for i, (s, (rt, rh)) in enumerate(zip(self.segments, self.rates())):
            n = f"Segment {i + 1}"
            if s.time_s <= 0:
                err.append(f"{n}: 시간은 0보다 커야 합니다")
            if not tl <= s.temp <= th:
                err.append(f"{n}: 목표 온도 {s.temp} ℃ — NEX1200 범위({tl}~{th}) 밖")
            elif not cfg.chamber_t[0] <= s.temp <= cfg.chamber_t[1]:
                warn.append(f"{n}: 목표 온도 {s.temp} ℃ — 챔버 사양({cfg.chamber_t[0]}~{cfg.chamber_t[1]}) 밖")
            if s.humi is not None:
                if not hl <= s.humi <= hh:
                    err.append(f"{n}: 목표 습도 {s.humi} % — NEX1200 범위 밖")
                elif not cfg.chamber_h[0] <= s.humi <= cfg.chamber_h[1]:
                    warn.append(f"{n}: 목표 습도 {s.humi} % — 챔버 사양 밖")
            if abs(rt) > cfg.chamber_rate_max:
                warn.append(f"{n}: 온도 변화율 {rt:+.3f} ℃/min — 챔버 최대 {cfg.chamber_rate_max} ℃/min 초과")
            if not s.steps:
                err.append(f"{n}: Step 이 1개 이상 필요합니다")
            for j, st in enumerate(s.steps):
                m = f"{n} Step {j + 1}"
                if st.time_s is None and st.temp is None and st.humi is None:
                    err.append(f"{m}: 시간·온도·습도 조건 중 하나는 필요합니다")
                if not 0 <= st.volt <= cfg.psu_v_max:
                    err.append(f"{m}: 전압 {st.volt} V — 정격 0~{cfg.psu_v_max} V 밖")
                if not 0 <= st.curr <= cfg.psu_i_max:
                    err.append(f"{m}: 전류 {st.curr} A — 정격 0~{cfg.psu_i_max} A 밖")
            st_sum = sum(st.time_s or 0 for st in s.steps)
            only_time = all(st.time_s is not None and st.temp is None and st.humi is None for st in s.steps)
            if only_time and st_sum != s.time_s:
                d = st_sum - s.time_s
                how = ("마지막 Step 출력 유지" if d < 0 else "남은 Step 건너뜀") if cfg.step_policy == "segment" \
                    else ("Step 종료 후 출력 OFF" if d < 0 else "Segment 연장")
                warn.append(f"{n}: Step 합계 {hms(st_sum)} / Segment {hms(s.time_s)} "
                            f"({hms(abs(d))} {'부족' if d < 0 else '초과'}) → {how}")
        return err, warn

    # ---------------------------------------------------------- 저장
    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d) -> "Function":
        # 모르는 항목은 무시 (다른 버전 PC 에서 만든 Profile 도 읽도록)
        pick = lambda c, x: {k: v for k, v in x.items() if k in c.__dataclass_fields__}
        segs = [Segment(**{**pick(Segment, s), "steps": [Step(**pick(Step, st)) for st in s.get("steps", [])]})
                for s in d.get("segments", [])]
        return cls(**{**{k: v for k, v in pick(cls, d).items() if k != "segments"}, "segments": segs})


class FunctionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if not any(self.root.glob("*.json")):
            for f in sample_functions():
                self.save(f)

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    def load(self, name: str) -> Function:
        return Function.from_dict(json.loads((self.root / f"{name}.json").read_text(encoding="utf-8")))

    def save(self, f: Function, old_name: str | None = None):
        if old_name and old_name != f.name:
            (self.root / f"{old_name}.json").unlink(missing_ok=True)
        (self.root / f"{f.name}.json").write_text(json.dumps(f.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, name: str):
        (self.root / f"{name}.json").unlink(missing_ok=True)

    def exists(self, name: str) -> bool:
        return (self.root / f"{name}.json").exists()


# ---------------------------------------------------------------- Function Profile (2026-11-13)
PROFILE_FORMAT = "HyosungChamber.FunctionProfile"
PROFILE_EXT = ".hfp"


def write_profile(path, funcs: list[Function], source: str = "") -> None:
    """Function 상세 Profile(Temperature Segment + Step)을 한 파일로 — 다른 PC 로 옮길 때 사용."""
    from datetime import datetime
    data = {"format": PROFILE_FORMAT, "version": 1, "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source, "count": len(funcs), "functions": [f.to_dict() for f in funcs]}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_profile(path) -> tuple[list[Function], dict, list[str]]:
    """(Function 목록, 파일 정보, 읽지 못한 항목 메시지). Profile 파일(.hfp)과 Function 폴더의 단일 .json 모두 읽는다."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict) and raw.get("format") == PROFILE_FORMAT:
        items, meta = raw.get("functions", []), raw
    elif isinstance(raw, dict) and "segments" in raw:
        items, meta = [raw], {"source": Path(path).name}
    elif isinstance(raw, list) and all(isinstance(x, dict) and "segments" in x for x in raw):
        items, meta = raw, {"source": Path(path).name}
    else:
        raise ValueError("Function Profile 파일이 아닙니다")
    funcs, bad = [], []
    for i, d in enumerate(items, 1):
        try:
            f = Function.from_dict(d)
            if not f.segments:
                raise ValueError("Segment 없음")
            funcs.append(f)
        except Exception as e:  # noqa
            bad.append(f"{i}번째 항목 ({d.get('name', '?') if isinstance(d, dict) else '?'}): {e}")
    return funcs, meta, bad


def unique_name(store: "FunctionStore", name: str, taken: set[str] | None = None) -> str:
    """같은 이름이 있으면 '이름 (2)', '이름 (3)' … 로."""
    taken = taken or set()
    if not store.exists(name) and name not in taken:
        return name
    n = 2
    while store.exists(f"{name} ({n})") or f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def sample_functions() -> list[Function]:
    return [
        Function("DENSO HTHE", "manual", 25.0, 50.0, [
            Segment(65.0, 93.0, 3600, True, [Step(1200, None, None, 13.5, 30.0), Step(None, 60.0, None, 14.0, 30.0),
                                             Step(1200, None, None, 14.0, 25.0)]),
            Segment(65.0, 93.0, 7200, False, [Step(7200, None, None, 14.0, 30.0)]),
            Segment(25.0, 50.0, 2400, False, [Step(1200, None, None, 12.0, 20.0), Step(1200, None, None, 0.0, 0.0)]),
        ]),
        Function("Blocked rotor", "pv", 25.0, 50.0, [Segment(25.0, None, 7200, False, [Step(7200, None, None, 12.0, 61.0)])]),
        Function("Derating Test", "pv", 25.0, 50.0, [
            Segment(85.0, None, 1800, True, [Step(1800, None, None, 13.5, 40.0)]),
            Segment(85.0, None, 3600, False, [Step(1800, None, None, 13.5, 35.0), Step(1800, None, None, 13.5, 30.0)]),
        ]),
    ]
