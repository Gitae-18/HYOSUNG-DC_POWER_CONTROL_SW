"""DC Power & 온/습도 제어 프로그램 실행 진입점 (런처 또는 단독 실행).

    python -m app.app_dcpower            # config.json 의 simulate 값 사용
    python -m app.app_dcpower --sim      # 강제로 시뮬레이터
    python -m app.app_dcpower --sim --fail 6   # P06 통신 이상 시뮬레이션

2026-11-02 변경
- 창 제목 'DC Power & 온/습도 제어 프로그램', 모든 창·팝업 제목 아이콘을 회사 로고로
- 런처의 [정지] 버튼 요청을 받는 로컬 채널(CTRL_NAME) 추가: 'close' 를 받으면 창을 앞으로 띄우고 종료 확인창을 연다
  (장비가 동작 중이면 사용자가 '프로그램만 종료 / 모두 정지 후 종료'를 고른다)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from presentation.styling import resources as R
from presentation.styling import theme
from infrastructure.system.config import Config
from application.services.hub import DeviceHub
from presentation.views.main_window import MainWindow
from infrastructure.system.singleton import SingleInstance

APP_TITLE = "DC Power & 온/습도 제어 프로그램"
CTRL_NAME = "HyosungChamber.DCPower.ctl"     # launcher.py 와 같은 이름


def start_control_server(win) -> QLocalServer:
    QLocalServer.removeServer(CTRL_NAME)          # 비정상 종료로 남은 이름 정리
    srv = QLocalServer(win)
    srv.listen(CTRL_NAME)

    def on_conn():
        sock = srv.nextPendingConnection()

        def on_read():
            cmd = bytes(sock.readAll()).decode(errors="ignore").strip()
            if cmd in ("close", "show"):
                win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
                win.show()
                win.raise_()
                win.activateWindow()
            if cmd == "close":
                QTimer.singleShot(100, win.close)     # 종료 확인창(ExitDialog)을 거쳐 종료
            sock.write(b"ok")
            sock.flush()
        sock.readyRead.connect(on_read)
        sock.disconnected.connect(sock.deleteLater)
    srv.newConnection.connect(on_conn)
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="시뮬레이터로 실행")
    ap.add_argument("--fail", type=int, nargs="*", default=[], help="통신 이상을 흉내낼 채널 번호")
    args = ap.parse_args(argv)
    app = QApplication(sys.argv)
    R.apply_app_identity(app, "Hyosung.ChamberControl.DCPower")
    app.setStyleSheet(theme.QSS)
    lock = SingleInstance("HyosungChamber.DCPower")
    if not lock.acquire():
        from presentation.widgets.widgets import info
        info(None, APP_TITLE, "이미 실행 중입니다. (COM 포트 중복 사용 방지)", warn=True)
        return 1
    cfg = Config.load()
    if args.sim:
        cfg.simulate = True
    hub = DeviceHub(cfg, sim_fail=tuple(args.fail))
    win = MainWindow(hub, cfg)
    win._ctl = start_control_server(win)
    hub.start()
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
