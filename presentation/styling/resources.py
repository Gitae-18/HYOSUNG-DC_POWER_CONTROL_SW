"""로고·아이콘 리소스.

- assets/logo.png : 회사 로고 (창 제목 아이콘, 런처 좌측 상단)
- assets/check.png: 체크박스 체크 표시
- 그 밖의 아이콘은 아래 SVG 를 실행 중에 그려서 쓴다 (색을 바꿔 쓰기 쉽고 파일이 늘지 않음).
PyInstaller 로 만든 exe 에서는 assets 폴더가 _internal 아래에 들어가므로 asset() 으로 경로를 찾는다.
"""
from __future__ import annotations

import math
import os
import sys
from functools import lru_cache
from pathlib import Path

ASSET_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "assets"


def asset(name: str) -> Path:
    return ASSET_DIR / name


def asset_url(name: str) -> str:
    """QSS url() 용 경로 (역슬래시 대신 / 사용)."""
    return asset(name).as_posix()


# ---------------------------------------------------------------- SVG 아이콘 (24×24, {c} = 색)
def _gear() -> str:
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16
        r = 10.5 if i % 2 == 0 else 7.8
        for da in (-0.17, 0.17):
            pts.append((12 + r * math.cos(a + da), 12 + r * math.sin(a + da)))
    d = "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + "Z"
    return (f"<path d='{d}' fill='none' stroke='{{c}}' stroke-width='1.8' stroke-linejoin='round'/>"
            "<circle cx='12' cy='12' r='3.2' fill='none' stroke='{c}' stroke-width='1.8'/>")


_SVG = {
    # 메뉴 바로가기
    "test_power": "<path d='M13.5 2 L5 13.5 h6 l-1.5 8.5 L18.5 10 h-6 z' fill='{c}'/>",
    "run_function": ("<path d='M3 5h9M3 10h9M3 15h6' stroke='{c}' stroke-width='2' stroke-linecap='round'/>"
                     "<path d='M13 12 L21 16.5 L13 21z' fill='{c}'/>"),
    "result": ("<path d='M3 3v18h18' fill='none' stroke='{c}' stroke-width='2' stroke-linecap='round'/>"
               "<path d='M7 15l4-5 3 3 6-7' fill='none' stroke='{c}' stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>"
               "<circle cx='20' cy='6' r='1.6' fill='{c}'/>"),
    "setup": _gear(),
    "exit": ("<path d='M17.7 6.6a8.2 8.2 0 1 1-11.4 0' fill='none' stroke='{c}' stroke-width='2.2' stroke-linecap='round'/>"
             "<path d='M12 2.5v9' stroke='{c}' stroke-width='2.2' stroke-linecap='round'/>"),
    # 런처 프로그램
    "lin": ("<path d='M2 12h20' stroke='{c}' stroke-width='2'/>"
            "<path d='M6 12V7M12 12v5M18 12V7' stroke='{c}' stroke-width='1.8'/>"
            "<rect x='3.5' y='2.5' width='5' height='4.5' rx='1' fill='{c}'/>"
            "<rect x='9.5' y='17' width='5' height='4.5' rx='1' fill='{c}'/>"
            "<rect x='15.5' y='2.5' width='5' height='4.5' rx='1' fill='{c}'/>"),
    "dcpower": ("<path d='M9 1.5 L3 10 h4.2 l-1.2 7 L12 8 H7.8z' fill='{c}'/>"
                "<path d='M17 14.6V5a2 2 0 0 0-4 0v9.6a3.8 3.8 0 1 0 4 0z' fill='none' stroke='{c}' stroke-width='1.7'/>"
                "<circle cx='15' cy='17.7' r='1.9' fill='{c}'/><path d='M15 16V8.5' stroke='{c}' stroke-width='1.6'/>"),
    "pwm": ("<path d='M1.5 18h3.5V6h4.5v12h3.5V6h4.5v12h4.5' fill='none' stroke='{c}' stroke-width='2' stroke-linejoin='round'/>"),
    # 실행·정지
    "play": "<path d='M7 4.5 L19.5 12 L7 19.5z' fill='{c}'/>",
    "stop": "<rect x='5.5' y='5.5' width='13' height='13' rx='1.5' fill='{c}'/>",
    # 챔버
    "thermo": ("<path d='M14 14.8V4.5a2 2 0 0 0-4 0v10.3a4 4 0 1 0 4 0z' fill='none' stroke='{c}' stroke-width='1.8'/>"
               "<circle cx='12' cy='18' r='2' fill='{c}'/><path d='M12 16V9' stroke='{c}' stroke-width='1.8'/>"),
    "drop": "<path d='M12 2.8 C12 2.8 5.5 10 5.5 14.5 a6.5 6.5 0 0 0 13 0 C18.5 10 12 2.8 12 2.8z' fill='none' stroke='{c}' stroke-width='1.8'/>"
            "<path d='M9 15a3 3 0 0 0 3 3' fill='none' stroke='{c}' stroke-width='1.6' stroke-linecap='round'/>",
    "target": ("<circle cx='12' cy='12' r='9' fill='none' stroke='{c}' stroke-width='1.8'/>"
               "<circle cx='12' cy='12' r='5' fill='none' stroke='{c}' stroke-width='1.8'/>"
               "<circle cx='12' cy='12' r='1.8' fill='{c}'/>"),
    "sliders": ("<path d='M4 6h16M4 12h16M4 18h16' stroke='{c}' stroke-width='1.8' stroke-linecap='round'/>"
                "<circle cx='9' cy='6' r='2.3' fill='{c}'/><circle cx='15' cy='12' r='2.3' fill='{c}'/>"
                "<circle cx='7' cy='18' r='2.3' fill='{c}'/>"),
    "delta": ("<path d='M12 3.5 L21 20 H3z' fill='none' stroke='{c}' stroke-width='1.9' stroke-linejoin='round'/>"),
    "check": "<path d='M5 12.5l4.5 4.5L19 7.5' fill='none' stroke='{c}' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'/>",
}


def svg(name: str, color: str) -> bytes:
    body = _SVG[name].replace("{c}", color)
    return f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>{body}</svg>".encode()


@lru_cache(maxsize=256)
def pixmap(name: str, color: str = "#E6EAEE", size: int = 24):
    """SVG 아이콘 → QPixmap (고해상도 화면용으로 2배로 그림). QApplication 생성 후에 호출."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg(name, color))).render(p)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return pm


def icon(name: str, color: str = "#E6EAEE", size: int = 24):
    from PySide6.QtGui import QIcon
    return QIcon(pixmap(name, color, size))


def logo_icon():
    from PySide6.QtGui import QIcon
    return QIcon(str(asset("logo.png")))


def logo_pixmap(height: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    pm = QPixmap(str(asset("logo.png")))
    if pm.isNull():
        return pm
    pm = pm.scaledToHeight(height * 2, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(2.0)
    return pm


def apply_app_identity(app, app_id: str):
    """모든 창·팝업 제목줄에 로고 사용 (QApplication 전체 아이콘) + Windows 작업 표시줄 아이콘."""
    app.setWindowIcon(logo_icon())
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:  # noqa
            pass
