"""데이터 저장 — DuckDB 일별(Daily) 시계열 DB + 운영 로그.

2026-11-04: SQLite 단일 파일 → DuckDB 일별 파일로 변경 (설계: docs/DB_DESIGN.md)

    <데이터 폴더>\\DB\\YYYY\\MM\\HyosungData_YYYYMMDD.duckdb    하루 1파일, 자정에 새 파일
    <데이터 폴더>\\DB\\fallback\\YYYYMMDD_fallback.csv           DB 쓰기가 계속 실패할 때 비상 기록

저장 규칙 (1초 주기)
    power_sample   DC Power 출력 ON(Test Power·Function) 인 채널마다 1초에 1행
    chamber_sample NEX1200 운전 중이거나 DC Power 하나라도 출력 중이면 1초에 1행
    run_log        실행(Test Power ON~OFF, Function 시작~종료) 1건 = 1행. 여러 날에 걸친 실행은 그날 파일마다 1행씩
    event_log      알람·조작·통신 로그 (Log\\YYYYMMDD.log 와 같은 내용)

쓰기는 전용 스레드(DbWriter) 하나가 1초마다 모아서 한 트랜잭션으로 넣는다 → 장비 감시 주기에 영향 없음.
"""
from __future__ import annotations

import csv
import json
import queue
import shutil
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import duckdb

MANUAL = "수동운전"
TS = "%Y-%m-%d %H:%M:%S"
SCHEMA_VERSION = 1
APP_VERSION = "1.3.1"
CHECKPOINT_S = 300          # 5분마다 WAL → DB 파일 반영
MAX_PENDING = 7200          # DB 장애 시 메모리에 붙잡아 둘 최대 행 수 (넘으면 비상 CSV)

DDL = [
    """CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS run_log (
        run_id      VARCHAR PRIMARY KEY,   -- 'YYYYMMDD-HHMMSS-Pnn' (실행 시작 시각 + 채널)
        function    VARCHAR NOT NULL,      -- Function 이름 또는 '수동운전'
        ch          UTINYINT NOT NULL,     -- 1~6
        start_ts    TIMESTAMP NOT NULL,
        end_ts      TIMESTAMP,             -- NULL = 이 파일 기준 아직 실행 중
        status      VARCHAR,               -- 실행 중 / 정상 종료 / 사용자 중지 / OCP 차단 / …
        info        VARCHAR,               -- JSON: OCP, Cycle, CV/CC, Step 정책 등
        snapshot    VARCHAR                -- JSON: 시작 시점 Function 내용 (나중에 Function 이 바뀌어도 추적)
    )""",
    """CREATE TABLE IF NOT EXISTS power_sample (
        ts          TIMESTAMP NOT NULL,
        ch          UTINYINT NOT NULL,
        run_id      VARCHAR,
        status      VARCHAR,
        set_v       DOUBLE, set_a DOUBLE,  -- 설정 CV / CC
        volt        DOUBLE, curr DOUBLE,   -- 측정값
        ocp         DOUBLE,
        step        VARCHAR, cycle VARCHAR,
        elapsed_s   DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS chamber_sample (
        ts          TIMESTAMP NOT NULL,
        pv_t DOUBLE, sp_t DOUBLE, pv_h DOUBLE, sp_h DOUBLE, mv_t DOUBLE, mv_h DOUBLE,
        running     BOOLEAN, mode VARCHAR, pattern INTEGER, segment INTEGER, error VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS event_log (ts TIMESTAMP NOT NULL, kind VARCHAR, code VARCHAR, message VARCHAR)""",
]
POWER_COLS = ["ts", "ch", "run_id", "status", "set_v", "set_a", "volt", "curr", "ocp", "step", "cycle", "elapsed_s"]
CHAMBER_COLS = ["ts", "pv_t", "sp_t", "pv_h", "sp_h", "mv_t", "mv_h", "running", "mode", "pattern", "segment", "error"]

_ACTIVE: dict[str, "DataLogger"] = {}      # 열려 있는 오늘 DB 경로 → 쓰는 쪽 (같은 프로세스 조회용)


def db_root(data_dir) -> Path:
    return Path(data_dir) / "DB"


def db_path(data_dir, day: date) -> Path:
    return db_root(data_dir) / f"{day:%Y}" / f"{day:%m}" / f"HyosungData_{day:%Y%m%d}.duckdb"


def _day_of(path: Path) -> date | None:
    try:
        return datetime.strptime(path.stem.split("_")[-1], "%Y%m%d").date()
    except ValueError:
        return None


# ====================================================================== 운영 로그
class AppLog:
    """Log\\YYYYMMDD.log 파일 + (연결되면) DB event_log."""

    def __init__(self, root: Path, sink=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lk = threading.Lock()
        self.sink = sink

    def write(self, kind: str, msg: str, code: str | None = None):
        now = datetime.now()
        line = f"{now:%H:%M:%S.%f}"[:-3] + f" [{kind}] {msg}\n"
        with self._lk, open(self.root / f"{now:%Y%m%d}.log", "a", encoding="utf-8") as f:
            f.write(line)
        if self.sink:
            try:
                self.sink(kind, msg, code, now)
            except Exception:  # noqa — 로그 때문에 동작이 멈추면 안 됨
                pass


# ====================================================================== 쓰기
class DataLogger:
    """hub 에서 호출하는 저장 API. 실제 DB 쓰기는 내부 스레드(_writer)가 한다.

    chamber(t, reading) · start_run(ch, func, snapshot, cfg_info) → run_id · power_sample(ch, t, dict)
    end_run(ch, status) · event(kind, msg, code, t) · flush() · close() · free_gb()
    """

    def __init__(self, data_dir, on_error=None):
        self.root = Path(data_dir)
        self.dbdir = db_root(self.root)
        self.dbdir.mkdir(parents=True, exist_ok=True)
        self.on_error = on_error                     # (code, msg) → hub 알람(AL-06)
        self.open_runs: dict[int, dict] = {}         # ch → run 정보 (날짜가 바뀌면 새 파일에 다시 기록)
        self.q: queue.Queue = queue.Queue()
        self.con: duckdb.DuckDBPyConnection | None = None
        self.day: date | None = None
        self.path: Path | None = None
        self._con_lk = threading.RLock()
        self._pending: list[tuple] = []
        self._fail_since: float | None = None
        self._last_ckpt = time.monotonic()
        self._stop = threading.Event()
        self.rows_written = 0
        self._open_day(date.today())
        self._th = threading.Thread(target=self._writer, name="DbWriter", daemon=True)
        self._th.start()

    # ---------------------------------------------------------- 공개 API (hub 스레드)
    def chamber(self, t: datetime, r):
        self.q.put(("chamber", (t, r.pv_t, r.sp_t, r.pv_h, r.sp_h, round(r.mv_t, 1), round(r.mv_h, 1),
                                bool(r.running), r.mode, int(r.pattern or 0), int(r.segment or 0), r.error or "")))

    def start_run(self, ch: int, func_name: str | None, snapshot: dict | None = None, cfg_info: dict | None = None) -> str:
        self.end_run(ch, "중단(재시작)")
        t = datetime.now().replace(microsecond=0)
        run = dict(run_id=f"{t:%Y%m%d-%H%M%S}-P{ch:02d}", function=func_name or MANUAL, ch=ch, start_ts=t, end_ts=None,
                   status="실행 중", info=json.dumps(cfg_info or {}, ensure_ascii=False),
                   snapshot=json.dumps(snapshot, ensure_ascii=False) if snapshot else None)
        self.open_runs[ch] = run
        self.q.put(("run", dict(run)))
        return run["run_id"]

    def run_id(self, ch: int) -> str | None:
        r = self.open_runs.get(ch)
        return r["run_id"] if r else None

    def power_sample(self, ch: int, t: datetime, d: dict):
        self.q.put(("power", (t, ch, self.run_id(ch), d.get("status"), d.get("set_v"), d.get("set_a"), d.get("volt"),
                              d.get("curr"), d.get("ocp"), d.get("step"), d.get("cycle"), d.get("elapsed_s"))))

    def end_run(self, ch: int, status: str):
        run = self.open_runs.pop(ch, None)
        if not run:
            return
        run.update(end_ts=datetime.now().replace(microsecond=0), status=status)
        self.q.put(("run", run))

    def event(self, kind: str, msg: str, code: str | None = None, t: datetime | None = None):
        self.q.put(("event", (t or datetime.now(), kind, code, msg)))

    def flush(self, timeout=5.0):
        ev = threading.Event()
        self.q.put(("flush", ev))
        ev.wait(timeout)

    def close(self):
        for ch in list(self.open_runs):
            self.end_run(ch, "프로그램 종료")
        self.flush()
        self._stop.set()
        self._th.join(5)
        self._close_day()

    def free_gb(self) -> float:
        return shutil.disk_usage(self.root).free / 1e9

    def cursor(self):
        """같은 프로세스에서 오늘 DB 를 읽을 때 (쓰기 연결과 같은 DB 인스턴스를 공유)."""
        with self._con_lk:
            return self.con.cursor() if self.con else None

    # ---------------------------------------------------------- DB 파일
    def _open_day(self, day: date):
        path = db_path(self.root, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._con_lk:
            try:
                con = duckdb.connect(str(path))
                con.execute("SELECT 1").fetchone()
            except Exception as e:  # noqa — 파일 손상: 옆으로 치우고 새로 만든다 (데이터는 지우지 않음)
                bad = path.with_name(path.stem + f".corrupt_{datetime.now():%H%M%S}" + path.suffix)
                try:
                    path.rename(bad)
                    wal = path.with_name(path.name + ".wal")
                    if wal.exists():
                        wal.rename(bad.with_name(bad.name + ".wal"))
                except OSError:
                    pass
                self._err("AL-06", f"DB 파일 손상으로 새 파일 생성: {bad.name} ({e})")
                con = duckdb.connect(str(path))
            for ddl in DDL:
                con.execute(ddl)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?), ('app_version', ?), ('date', ?)",
                        [str(SCHEMA_VERSION), APP_VERSION, f"{day:%Y-%m-%d}"])
            con.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", [datetime.now().strftime(TS)])
            # 이전 실행이 비정상 종료되어 '실행 중' 으로 남은 이력 정리 (이 프로세스가 연 실행은 아직 없음)
            open_ids = [r["run_id"] for r in self.open_runs.values()]
            stale = con.execute("SELECT run_id FROM run_log WHERE end_ts IS NULL").fetchall()
            for (rid,) in stale:
                if rid not in open_ids:
                    last = con.execute("SELECT max(ts) FROM power_sample WHERE run_id = ?", [rid]).fetchone()[0]
                    con.execute("UPDATE run_log SET end_ts = coalesce(?, start_ts), status = '비정상 종료' WHERE run_id = ?",
                                [last, rid])
            self.con, self.day, self.path = con, day, path
            _ACTIVE[str(path)] = self
            # 날짜를 넘겨 계속되는 실행은 새 파일에도 이력 행을 만든다 (그날 파일만으로 조회 가능하게)
            for run in self.open_runs.values():
                self._upsert_run(run)

    def _close_day(self):
        with self._con_lk:
            if not self.con:
                return
            try:
                self.con.execute("INSERT OR REPLACE INTO meta VALUES ('closed_at', ?)", [datetime.now().strftime(TS)])
                self.con.execute("FORCE CHECKPOINT")
            except Exception:  # noqa
                pass
            try:
                self.con.close()
            finally:
                _ACTIVE.pop(str(self.path), None)
                self.con = None

    def _upsert_run(self, run: dict):
        self.con.execute("INSERT OR REPLACE INTO run_log VALUES (?,?,?,?,?,?,?,?)",
                         [run["run_id"], run["function"], run["ch"], run["start_ts"], run["end_ts"], run["status"],
                          run["info"], run["snapshot"]])

    # ---------------------------------------------------------- 쓰기 스레드
    def _writer(self):
        while not self._stop.is_set():
            items, flushes = [], []
            try:
                items.append(self.q.get(timeout=1.0))
                while True:
                    items.append(self.q.get_nowait())
            except queue.Empty:
                pass
            for kind, payload in items:
                if kind == "flush":
                    flushes.append(payload)
                else:
                    self._pending.append((kind, payload))
            if date.today() != self.day:
                self._rotate()
            if self._pending:
                self._write_pending()
            if time.monotonic() - self._last_ckpt > CHECKPOINT_S:
                self._last_ckpt = time.monotonic()
                with self._con_lk:
                    try:
                        self.con.execute("CHECKPOINT")
                    except Exception:  # noqa — 조회 중이면 다음 기회에
                        pass
            for ev in flushes:
                ev.set()
        if self._pending:
            self._write_pending()

    def _rotate(self):
        try:
            self._close_day()
            self._open_day(date.today())
            self.event("SYSTEM", f"일별 DB 새 파일 {self.path.name}")
        except Exception as e:  # noqa
            self._err("AL-06", f"일별 DB 전환 실패: {e}")

    def _write_pending(self):
        rows, self._pending = self._pending, []
        # 날짜별로 나눔 (자정 직전·직후 행이 섞여 들어오는 경우 — 전날 행은 이미 닫힌 파일이므로 오늘 파일에 넣지 않고 그 날 파일에)
        by_day: dict[date, list] = {}
        for kind, p in rows:
            ts = p["start_ts"] if kind == "run" else p[0]
            by_day.setdefault(ts.date(), []).append((kind, p))
        for day, part in sorted(by_day.items()):
            try:
                if day == self.day:
                    with self._con_lk:
                        self._insert(self.con, part)
                else:
                    self._insert_other_day(day, part)
                self.rows_written += len(part)
                self._fail_since = None
            except Exception as e:  # noqa
                if self._fail_since is None:
                    self._fail_since = time.monotonic()
                    self._err("AL-06", f"DB 쓰기 실패 — 다시 시도합니다 ({e})")
                if len(self._pending) + len(part) > MAX_PENDING or time.monotonic() - self._fail_since > 60:
                    self._fallback_csv(part)
                else:
                    self._pending = part + self._pending
                try:                                   # 연결이 깨졌으면 다시 연다
                    if day == self.day:
                        self._close_day()
                        self._open_day(self.day)
                except Exception:  # noqa
                    pass

    def _insert(self, con, part):
        power = [p for k, p in part if k == "power"]
        chamber = [p for k, p in part if k == "chamber"]
        events = [p for k, p in part if k == "event"]
        runs = [p for k, p in part if k == "run"]
        con.execute("BEGIN TRANSACTION")
        try:
            if power:
                con.executemany(f"INSERT INTO power_sample VALUES ({','.join('?' * len(POWER_COLS))})", power)
            if chamber:
                con.executemany(f"INSERT INTO chamber_sample VALUES ({','.join('?' * len(CHAMBER_COLS))})", chamber)
            if events:
                con.executemany("INSERT INTO event_log VALUES (?,?,?,?)", events)
            for r in runs:
                con.execute("INSERT OR REPLACE INTO run_log VALUES (?,?,?,?,?,?,?,?)",
                            [r["run_id"], r["function"], r["ch"], r["start_ts"], r["end_ts"], r["status"], r["info"],
                             r["snapshot"]])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    def _insert_other_day(self, day, part):
        """이미 닫힌(또는 지난) 날짜의 행 — 그날 파일을 잠깐 열어 넣는다.
        실행 이력(run)은 시작일이 지났어도 '오늘' 파일에도 들어가야 하므로 오늘 파일로 보낸다."""
        runs = [x for x in part if x[0] == "run"]
        rest = [x for x in part if x[0] != "run"]
        if runs:
            with self._con_lk:
                self._insert(self.con, runs)
        if rest:
            path = db_path(self.root, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(10):          # 조회 창이 읽기 전용으로 잠깐 열고 있으면 기다렸다가
                try:
                    con = duckdb.connect(str(path))
                    break
                except duckdb.ConnectionException:
                    if attempt == 9:
                        raise
                    time.sleep(0.2)
            try:
                for ddl in DDL:
                    con.execute(ddl)
                self._insert(con, rest)
            finally:
                con.close()

    def _fallback_csv(self, part):
        p = self.dbdir / "fallback" / f"{datetime.now():%Y%m%d}_fallback.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            for kind, payload in part:
                if kind == "run":
                    w.writerow([kind, json.dumps(payload, default=str, ensure_ascii=False)])
                else:
                    w.writerow([kind] + [x.strftime(TS) if isinstance(x, datetime) else x for x in payload])
        self._err("AL-06", f"DB 쓰기 장애 지속 — 비상 CSV 에 기록 ({p.name}, {len(part)}행)")

    def _err(self, code, msg):
        if self.on_error:
            try:
                self.on_error(code, msg)
            except Exception:  # noqa
                pass


# ====================================================================== 조회 (Result)
class ResultQuery:
    """여러 날짜의 일별 DB 를 묶어서 조회. 오늘 파일은 쓰는 쪽 DB 를 공유하고, 지난 파일은 읽기 전용으로 연다."""
    _run_cache: dict[str, tuple[float, list[dict]]] = {}

    def __init__(self, data_dir, logger: DataLogger | None = None):
        self.root = Path(data_dir)
        self.logger = logger

    # ---- 파일
    def files(self) -> list[Path]:
        d = db_root(self.root)
        return sorted(p for p in d.glob("*/*/HyosungData_*.duckdb") if ".corrupt" not in p.name) if d.exists() else []

    def _connect(self, path: Path):
        live = _ACTIVE.get(str(path))
        if live is not None:
            c = live.cursor()
            if c is not None:
                return c
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.ConnectionException:
            # 같은 프로세스에서 방금 쓰기용으로 열린 적이 있는 파일 → 같은 DB 인스턴스를 공유해서 읽는다 (SELECT 만 함)
            return duckdb.connect(str(path))

    def _days(self, t0: datetime, t1: datetime) -> list[Path]:
        out, d = [], t0.date()
        while d <= t1.date():
            p = db_path(self.root, d)
            if p.exists():
                out.append(p)
            d += timedelta(days=1)
        return out

    @staticmethod
    def _flush_active():
        """조회 직전에 쓰기 대기 중인 행을 DB 에 반영 (최대 2초 기다림)."""
        for lg in list(_ACTIVE.values()):
            try:
                lg.flush(2.0)
            except Exception:  # noqa
                pass

    # ---- 실행 이력
    def runs(self) -> list[dict]:
        self._flush_active()
        merged: dict[str, dict] = {}
        active = set()
        for lg in ([self.logger] if self.logger else []) + list(_ACTIVE.values()):
            active |= {r["run_id"] for r in lg.open_runs.values()}
        for p in self.files():
            live = str(p) in _ACTIVE
            key = str(p)
            mt = p.stat().st_mtime
            if not live and key in self._run_cache and self._run_cache[key][0] == mt:
                rows = self._run_cache[key][1]
            else:
                try:
                    con = self._connect(p)
                    try:
                        rows = [dict(zip(["run_id", "function", "ch", "start_ts", "end_ts", "status"], r)) for r in
                                con.execute("SELECT run_id, function, ch, start_ts, end_ts, status FROM run_log").fetchall()]
                    finally:
                        con.close()
                except Exception:  # noqa — 읽을 수 없는 파일은 건너뜀
                    continue
                if not live:
                    self._run_cache[key] = (mt, rows)
            for r in rows:
                m = merged.get(r["run_id"])
                if m is None:
                    merged[r["run_id"]] = dict(r)
                else:
                    m["start_ts"] = min(m["start_ts"], r["start_ts"])
                    if r["end_ts"] is not None and (m["end_ts"] is None or r["end_ts"] >= m["end_ts"]):
                        m["end_ts"], m["status"] = r["end_ts"], r["status"]
        out = []
        for r in merged.values():
            end = r["end_ts"]
            status = r["status"] if end else ("실행 중" if r["run_id"] in active else "기록 끊김")
            out.append(dict(id=r["run_id"], function=r["function"], ch=int(r["ch"]),
                            start=r["start_ts"].strftime(TS), end=end.strftime(TS) if end else None, status=status))
        return sorted(out, key=lambda r: r["start"])

    def function_names(self) -> list[str]:
        return sorted({r["function"] for r in self.runs()})

    # ---- 시계열
    def query(self, t0: datetime, t1: datetime, interval_s: int, channels: list[int], chamber: bool,
              items: list[str], runs: list[dict] | None = None, mode: str = "last"):
        """(columns, rows) — rows 첫 열은 datetime. interval_s 구간마다 대표값 1개 (last = 마지막 값, mean = 평균)."""
        self._flush_active()
        agg = (lambda c: f"arg_max({c}, ts)") if mode == "last" else (lambda c: f"avg({c})")
        txt = lambda c: f"arg_max({c}, ts)"
        step_cycle = "step || ' / ' || cycle"
        series: dict[str, dict] = {}
        want_v, want_a = "전압" in items, "전류" in items
        want_step, want_st = "STEP/CYCLE" in items, "상태" in items
        for path in self._days(t0, t1):
            try:
                con = self._connect(path)
            except Exception:  # noqa
                continue
            try:
                if channels and (want_v or want_a or want_step or want_st):
                    sql = (f"SELECT time_bucket(to_seconds(?), ts) AS b, ch, {agg('volt')}, {agg('curr')}, {txt('status')}, "
                           f"{txt(step_cycle)} "
                           f"FROM power_sample WHERE ts BETWEEN ? AND ? AND ch IN ({','.join('?' * len(channels))})")
                    args = [int(interval_s), t0, t1, *channels]
                    if runs is not None:
                        sql += f" AND run_id IN ({','.join('?' * len(runs))})"
                        args += [r["id"] for r in runs]
                    for b, ch, v, a, st, sc in con.execute(sql + " GROUP BY b, ch", args).fetchall():
                        if want_v:
                            series.setdefault(f"P{ch} V", {})[b] = round(v, 4) if v is not None else ""
                        if want_a:
                            series.setdefault(f"P{ch} A", {})[b] = round(a, 5) if a is not None else ""
                        if want_step:
                            series.setdefault(f"P{ch} STEP", {})[b] = sc
                        if want_st:
                            series.setdefault(f"P{ch} 상태", {})[b] = st
                if chamber and ("온도" in items or "습도" in items):
                    sql = (f"SELECT time_bucket(to_seconds(?), ts) AS b, {agg('pv_t')}, {agg('pv_h')} "
                           "FROM chamber_sample WHERE ts BETWEEN ? AND ? GROUP BY b")
                    for b, pt, ph in con.execute(sql, [int(interval_s), t0, t1]).fetchall():
                        if "온도" in items:
                            series.setdefault("온도 ℃", {})[b] = round(pt, 2)
                        if "습도" in items:
                            series.setdefault("습도 %", {})[b] = round(ph, 2)
            finally:
                con.close()
        order = {"V": 0, "A": 1, "STEP": 2, "상태": 3, "℃": 0, "%": 1}
        cols = sorted(series, key=lambda c: (not c.startswith("P"), c.split(" ")[0] if c.startswith("P") else "",
                                             order.get(c.split(" ")[-1], 9), c))
        times = sorted({t for s in series.values() for t in s})
        rows = [[t] + [series[c].get(t, "") for c in cols] for t in times]
        return ["시각"] + cols, rows

    def events(self, t0: datetime, t1: datetime, kinds: list[str] | None = None) -> list[tuple]:
        self._flush_active()
        out = []
        for path in self._days(t0, t1):
            con = self._connect(path)
            try:
                sql = "SELECT ts, kind, code, message FROM event_log WHERE ts BETWEEN ? AND ?"
                args = [t0, t1]
                if kinds:
                    sql += f" AND kind IN ({','.join('?' * len(kinds))})"
                    args += kinds
                out += con.execute(sql + " ORDER BY ts", args).fetchall()
            finally:
                con.close()
        return out

    @staticmethod
    def export_csv(path, columns, rows):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(columns)
            for r in rows:
                w.writerow([r[0].strftime(TS)] + list(r[1:]))
