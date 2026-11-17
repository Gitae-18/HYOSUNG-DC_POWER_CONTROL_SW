"""SCR-000 초기 화면(런처) — LIN · DC Power & 온/습도 · PWM 제어 프로그램 실행/정지 (스토리보드 15p).

런처는 장비 COM 포트를 열지 않고, 프로그램 실행·정지와 실행 상태 표시만 담당한다.

2026-11-02 변경
- 좌측 상단·창 제목 아이콘에 회사 로고
- 블럭 순서/이름: LIN 제어 프로그램 → DC Power & 온/습도 제어 프로그램 → PWM 제어 프로그램, 제목줄에 프로그램 아이콘
- [실행]/[정지] 토글 버튼 (▶ 실행 = 초록, ■ 정지 = 빨강)
  · LIN / PWM 정지: 해당 프로그램에 창 닫기 요청 (Windows taskkill, /F 없이 → 프로그램이 스스로 종료 처리)
  · DC Power 정지: DC Power 프로그램에 종료 요청 → 그 프로그램의 종료 확인창에서 장비 처리 방법을 고름
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                               QFileDialog, QDialog, QFormLayout, QLineEdit, QInputDialog, QPushButton)

from presentation.styling import resources as R
from presentation.styling import theme as T
from infrastructure.system.config import Config
from presentation.widgets.widgets import Panel, button, label, info, confirm

HERE = Path(__file__).resolve().parent
DC_CTRL = "HyosungChamber.DCPower.ctl"        # app_dcpower.CTRL_NAME 과 같아야 함
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def running_exe(image: str) -> bool:
    """Windows: tasklist 로 실행 중인 이미지 이름 확인."""
    if os.name != "nt" or not image:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], capture_output=True, text=True,
                             creationflags=NOWIN, timeout=3).stdout
        return image.lower() in out.lower()
    except Exception:  # noqa
        return False


def dc_send(cmd: str | None = None, timeout_ms: int = 300) -> bool:
    """DC Power 프로그램 제어 채널에 연결(=실행 중 확인)하고, cmd 가 있으면 보낸다."""
    s = QLocalSocket()
    s.connectToServer(DC_CTRL)
    if not s.waitForConnected(timeout_ms):
        return False
    if cmd:
        s.write(cmd.encode())
        s.flush()
        s.waitForBytesWritten(timeout_ms)
        s.waitForReadyRead(timeout_ms)
    s.disconnectFromServer()
    return True


class ProgCard(Panel):
    """프로그램 블럭 — 제목줄(아이콘 + 이름) · 설명 · 상태 · 실행/정지 토글."""

    def __init__(self, title, icon_name, accent, desc, on_start, on_stop):
        super().__init__("")
        self.title.hide()
        self.on_start, self.on_stop = on_start, on_stop
        self.running = False
        head = QFrame(objectName="cardHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(R.pixmap(icon_name, accent, 26))
        hl.addWidget(ic)
        hl.addWidget(label(title, bold=True, size=12.5))
        hl.addStretch(1)
        self.layout().insertWidget(0, head)
        lay = self.body_layout("v", 18, 12)
        mid = QHBoxLayout()
        mid.setSpacing(16)
        big = QLabel()
        big.setPixmap(R.pixmap(icon_name, accent, 72))
        big.setFixedSize(84, 84)
        big.setAlignment(Qt.AlignCenter)
        big.setStyleSheet(f"background:{T.CARD}; border:1px solid {T.BRD}; border-radius:8px;")
        mid.addWidget(big, 0, Qt.AlignTop)
        d = label(desc, sub=True, size=11)
        d.setWordWrap(True)
        mid.addWidget(d, 1, Qt.AlignTop)
        lay.addLayout(mid)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(label("상태", sub=True, size=11))
        self.state = QLabel()
        row.addWidget(self.state, 1)
        lay.addLayout(row)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BRD};")
        lay.addWidget(line)
        self.btn = QPushButton()
        self.btn.setObjectName("toggle")
        self.btn.setCheckable(True)
        self.btn.setFixedHeight(52)
        self.btn.setIconSize(QSize(20, 20))
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._clicked)
        lay.addWidget(self.btn)
        self.set_running(False)

    def _clicked(self):
        # 버튼 모양은 실제 실행 상태(poll)로만 바뀐다
        self.btn.setChecked(self.running)
        (self.on_stop if self.running else self.on_start)()

    def set_running(self, on: bool, stopping: bool = False):
        self.running = on
        col, txt = (T.YEL, "종료 요청 중") if (on and stopping) else ((T.GRN, "실행 중") if on else (T.GRY, "대기"))
        self.state.setText(f"<span style='color:{col}; font-size:11pt'>● <b>{txt}</b></span>")
        self.btn.setChecked(on)
        self.btn.setText("  정지" if on else "  실행")
        self.btn.setIcon(R.icon("stop" if on else "play", "#FFFFFF", 20))
        self.btn.setToolTip("프로그램 종료" if on else "프로그램 실행")


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.procs: dict[str, subprocess.Popen] = {}
        self.stopping: set[str] = set()
        self.setWindowTitle("효성전기 챔버 제어 시스템")
        self.setWindowIcon(R.logo_icon())
        self.resize(1180, 660)
        mb = self.menuBar()
        m = mb.addMenu("프로그램(&P)")
        m.addAction("종료", self.close)
        m2 = mb.addMenu("환경설정(&E)")
        m2.addAction("실행 파일 경로…", self.open_paths)
        mb.addMenu("도움말(&H)").addAction("버전 정보", lambda: info(self, "버전", "효성전기 챔버 제어 시스템 런처 Ver 1.3.1\n㈜필링크코리아"))
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(20, 14, 20, 16)
        v.setSpacing(16)
        h = QHBoxLayout()
        h.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(R.logo_pixmap(40))
        logo.setFixedHeight(44)
        h.addWidget(logo)
        h.addWidget(label("온/습도 챔버 제어 시스템", bold=True, size=16))
        h.addStretch(1)
        self.clock = label("", sub=True, size=11)
        h.addWidget(self.clock)
        v.addLayout(h)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.c_lin = ProgCard("LIN 제어 프로그램", "lin", T.BLU, "BLDC 모터 LIN 6ch\n제어 · 모니터링",
                              lambda: self.run_ext("lin"), lambda: self.stop_ext("lin"))
        self.c_dc = ProgCard("DC Power & 온/습도 제어 프로그램", "dcpower", T.YEL, "DC 전원 6ch 출력 ·\n챔버 온/습도 제어 · 결과 조회",
                             self.run_dc, self.stop_dc)
        self.c_pwm = ProgCard("PWM 제어 프로그램", "pwm", T.GRN, "BLDC 모터 PWM 1ch\n제어",
                              lambda: self.run_ext("pwm"), lambda: self.stop_ext("pwm"))
        for c in (self.c_lin, self.c_dc, self.c_pwm):
            cards.addWidget(c, 1)
        v.addLayout(cards, 1)
        f = QHBoxLayout()
        f.addWidget(label("Ver 1.3.1 · ㈜필링크코리아", color=T.DIM))
        f.addStretch(1)
        b_set = button("환경 설정", min_w=120, slot=self.open_paths)
        b_set.setIcon(R.icon("setup", T.TXT, 16))
        b_exit = button("종료", min_w=110, slot=self.close)
        b_exit.setIcon(R.icon("exit", T.TXT, 16))
        f.addWidget(b_set)
        f.addWidget(b_exit)
        v.addLayout(f)
        self.t = QTimer(self, interval=2000, timeout=self.poll)
        self.t.start()
        self.poll()

    # ------------------------------------------------------------------ state
    def _alive(self, key) -> bool:
        p = self.procs.get(key)
        return bool(p and p.poll() is None)

    def is_running(self, key) -> bool:
        if key == "dc":
            return self._alive("dc") or dc_send()
        exe = self.cfg.lin_exe if key == "lin" else self.cfg.pwm_exe
        return self._alive(key) or running_exe(Path(exe).name)

    def poll(self):
        self.clock.setText(datetime.now().strftime("%Y-%m-%d  %H:%M"))
        for key, card in (("lin", self.c_lin), ("dc", self.c_dc), ("pwm", self.c_pwm)):
            on = self.is_running(key)
            if not on:
                self.stopping.discard(key)
            card.set_running(on, key in self.stopping)

    # ------------------------------------------------------------------ run
    def _start(self, key, cmd, cwd):
        if self._alive(key):
            info(self, "실행", "이미 실행 중입니다. 해당 창을 확인하세요.")
            return
        try:
            self.procs[key] = subprocess.Popen(cmd, cwd=cwd)
        except OSError as e:
            if confirm(self, "실행 실패", f"실행 파일을 찾을 수 없습니다:\n{cmd[0]}\n\n({e})", ok="경로 설정", cancel="닫기", warn=True):
                self.open_paths()
        QTimer.singleShot(800, self.poll)
        self.poll()

    def run_ext(self, which):
        exe = self.cfg.lin_exe if which == "lin" else self.cfg.pwm_exe
        if running_exe(Path(exe).name):
            return info(self, "실행", "이미 실행 중입니다. 작업 표시줄에서 해당 창을 여세요.")
        # LIN: 작업 폴더 = exe 폴더 (로그가 ./log 하위에 저장되므로 필수)
        self._start(which, [exe], str(Path(exe).parent) if Path(exe).parent.exists() else None)

    def run_dc(self):
        if dc_send("show"):
            return info(self, "실행", "이미 실행 중입니다. 해당 창을 앞으로 띄웠습니다.")
        if self.cfg.dcpower_cmd:
            cmd, cwd = [self.cfg.dcpower_cmd], str(Path(self.cfg.dcpower_cmd).parent)
        elif getattr(sys, "frozen", False):
            # exe 배포: 런처와 같은 폴더의 HyosungDCPower(.exe)
            exe_dir = Path(sys.executable).parent
            cmd, cwd = [str(exe_dir / ("HyosungDCPower.exe" if os.name == "nt" else "HyosungDCPower"))], str(exe_dir)
        else:
            cmd, cwd = [sys.executable, str(HERE / "app_dcpower.py")], str(HERE)
        self._start("dc", cmd, cwd)

    # ------------------------------------------------------------------ stop
    def stop_ext(self, which):
        name = "LIN 제어 프로그램" if which == "lin" else "PWM 제어 프로그램"
        if not confirm(self, "프로그램 정지", f"{name}을(를) 종료합니다.\n저장하지 않은 내용이 있으면 해당 프로그램에서 먼저 저장하세요.",
                       ok="종료", warn=True):
            return
        exe = Path(self.cfg.lin_exe if which == "lin" else self.cfg.pwm_exe).name
        p = self.procs.get(which)
        if os.name == "nt":
            # /F 없이 = 창 닫기 요청 (프로그램이 스스로 정리 후 종료)
            target = ["/PID", str(p.pid)] if (p and p.poll() is None) else ["/IM", exe]
            subprocess.run(["taskkill", *target], capture_output=True, creationflags=NOWIN, timeout=5)
        elif p and p.poll() is None:
            p.terminate()
        self.stopping.add(which)
        QTimer.singleShot(1500, self.poll)
        self.poll()

    def stop_dc(self):
        if not confirm(self, "프로그램 정지",
                       "DC Power & 온/습도 제어 프로그램에 종료를 요청합니다.\n\n"
                       "장비가 동작 중이면 해당 프로그램의 종료 확인창에서\n'프로그램만 종료' 또는 '모두 정지 후 종료'를 선택하세요.",
                       ok="종료 요청"):
            return
        if dc_send("close"):
            self.stopping.add("dc")
        elif self._alive("dc"):
            # 응답이 없을 때만 강제 종료 (장비 출력은 현재 상태 유지)
            if confirm(self, "프로그램 정지", "프로그램이 응답하지 않습니다. 강제로 종료할까요?\n(장비 출력은 현재 상태로 유지됩니다)",
                       ok="강제 종료", warn=True):
                self.procs["dc"].kill()
        QTimer.singleShot(1500, self.poll)
        self.poll()

    # ------------------------------------------------------------------ settings
    def open_paths(self):
        pw, ok = QInputDialog.getText(self, "환경 설정", "관리자 암호", QLineEdit.Password)
        if not ok:
            return
        if pw != self.cfg.admin_password:
            return info(self, "환경 설정", "암호가 올바르지 않습니다.", warn=True)
        d = QDialog(self)
        d.setWindowTitle("실행 파일 경로")
        d.resize(720, 200)
        fl = QFormLayout(d)
        edits = {}
        for key, lab in [("lin_exe", "LIN 제어 프로그램"), ("pwm_exe", "PWM 제어 프로그램")]:
            row = QHBoxLayout()
            e = QLineEdit(getattr(self.cfg, key))
            row.addWidget(e, 1)
            row.addWidget(button("찾아보기", slot=lambda _=False, ee=e: ee.setText(
                QFileDialog.getOpenFileName(d, "실행 파일", ee.text(), "실행 파일 (*.exe)")[0] or ee.text())))
            fl.addRow(lab, row)
            edits[key] = e
        b = QHBoxLayout()
        b.addStretch(1)
        b.addWidget(button("저장", primary=True, slot=d.accept))
        b.addWidget(button("취소", slot=d.reject))
        fl.addRow(b)
        if d.exec():
            for k, e in edits.items():
                setattr(self.cfg, k, e.text())
            self.cfg.save()

    def closeEvent(self, e):
        if any(self.is_running(k) for k in ("lin", "dc", "pwm")):
            info(self, "런처 종료", "런처만 종료되며 실행 중인 프로그램은 계속 동작합니다.")
        e.accept()


def main():
    app = QApplication(sys.argv)
    R.apply_app_identity(app, "Hyosung.ChamberControl.Launcher")
    app.setStyleSheet(T.QSS)
    w = Launcher()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
