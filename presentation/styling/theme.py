"""디자인 샘플 기준 다크 그레이 테마 (스토리보드 V1.3.0 '화면 디자인 원칙').

2026-11-02 변경: Power 채널별 그래프 색(CH_COLORS), 체크박스 체크 표시, 메뉴 바로가기·실행/정지 토글 스타일.
2026-11-13 변경: P6 색 분홍(#FF6FB5), 패널 제목줄 최소화/최대화 버튼, 전류 기준 알람 카드 스타일.
"""
from presentation.styling.resources import asset_url

WIN = "#1E2833"     # 창 바탕
MENU = "#252F3B"
HDR = "#4A5869"     # 패널 제목줄
PNL = "#1B2430"     # 패널 본문
CARD = "#222C38"
BRD = "#3A4757"
THD = "#3B4A5C"     # 표 머리
ROW = "#1C2530"
SEL = "#1F6FB2"     # 선택
BTN = "#4A5768"
INP = "#151D27"
TXT = "#E6EAEE"
SUB = "#9AA4B2"
DIM = "#6B7686"
YEL = "#F2C94C"
GRN = "#4CC38A"
RED = "#E5534B"
BLU = "#4EA1F3"
GRY = "#8A94A3"
CYN = "#2ED3D3"

SERIES = {"V": "#4EA1F3", "A": "#F2C94C", "T": "#E5534B", "H": "#B39DDB"}
# 실시간 모니터링: Power 번호마다 고정 색 (전압 = 실선, 전류 = 파선), 챔버 온도·습도는 별도 색
CH_COLORS = {1: "#4EA1F3", 2: "#F2C94C", 3: "#4CC38A", 4: "#FF8A3D", 5: "#B07CFF", 6: "#FF6FB5"}   # 2026-11-13: P6 청록 → 분홍 (P3 초록과 구분)
TEMP_COLOR = "#E5534B"
HUMI_COLOR = "#DDE3EA"
STATUS = {"RUN": GRN, "WAIT": YEL, "READY": BLU, "OFF": GRY, "OCP": RED, "ERR": RED}
STATUS_TEXT = {"RUN": "RUN", "WAIT": "WAIT", "READY": "READY", "OFF": "OFF", "OCP": "OCP", "ERR": "통신 이상"}

FONT = "Malgun Gothic"

QSS = f"""
* {{ font-family: '{FONT}', 'Noto Sans CJK KR', 'Segoe UI'; font-size: 10pt; color: {TXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {WIN}; }}
QMenuBar {{ background: {MENU}; }}
QMenuBar::item:selected {{ background: {SEL}; }}
QMenu {{ background: {PNL}; border: 1px solid {BRD}; }}
QMenu::item:selected {{ background: {SEL}; }}
QLabel#big {{ font-size: 24pt; font-weight: bold; }}
QLabel#huge {{ font-size: 32pt; font-weight: bold; }}
QFrame#panel {{ background: {PNL}; border: 1px solid {BRD}; }}
QLabel#panelTitle {{ background: {HDR}; padding: 3px 8px; }}
QFrame#panelHead {{ background: {HDR}; }}
QToolButton#panelBtn {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 0px 6px;
    color: {TXT}; font-size: 10pt; min-width: 22px; }}
QToolButton#panelBtn:hover {{ background: #5B6B7E; border: 1px solid {BRD}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BRD}; }}
QFrame#card[err="true"] {{ border: 2px solid {RED}; }}
QFrame#card[selected="true"] {{ border: 2px solid {SEL}; }}
QFrame#toolbar {{ background: #000000; border-bottom: 1px solid {BRD}; }}
QToolButton#shortcut {{ background: transparent; color: {YEL}; border: 1px solid transparent; border-radius: 4px;
    padding: 4px 6px; font-size: 10pt; }}
QToolButton#shortcut:hover {{ background: #1B232D; border: 1px solid {BRD}; color: #FFFFFF; }}
QToolButton#shortcut:pressed {{ background: {SEL}; }}
QLabel#sep {{ color: {TXT}; }}
QFrame#vsep {{ background: #3A4757; }}
QPushButton#toggle {{ font-size: 12.5pt; font-weight: bold; border-radius: 4px; padding: 6px 16px;
    background: #2E7D5B; color: #FFFFFF; }}
QPushButton#toggle:hover {{ background: #379669; }}
QPushButton#toggle:checked {{ background: #B23B35; }}
QPushButton#toggle:checked:hover {{ background: #C9463F; }}
QFrame#cardHead {{ background: {HDR}; }}
QLabel.sub, QLabel[sub="true"] {{ color: {SUB}; }}
QPushButton {{ background: {BTN}; border: none; border-radius: 2px; padding: 5px 14px; }}
QPushButton:hover {{ background: #56657A; }}
QPushButton:pressed {{ background: {SEL}; }}
QPushButton:disabled {{ color: {DIM}; background: #333E4B; }}
QPushButton[primary="true"] {{ background: {SEL}; }}
QPushButton[primary="true"]:hover {{ background: #2A80C8; }}
QPushButton:checkable:checked {{ background: {SEL}; }}
QPushButton[chip="true"] {{ padding: 2px 8px; color: {SUB}; }}
QPushButton[chip="true"]:checked {{ color: {TXT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit, QTimeEdit {{
    background: {INP}; border: 1px solid {BRD}; border-radius: 2px; padding: 3px 6px; selection-background-color: {SEL}; }}
QPlainTextEdit, QTextEdit {{ background: {INP}; border: 1px solid {BRD}; selection-background-color: {SEL}; }}
QLineEdit[invalid="true"], QDoubleSpinBox[invalid="true"] {{ border: 1px solid {RED}; }}
QLineEdit[warn="true"], QDoubleSpinBox[warn="true"] {{ border: 1px solid {YEL}; }}
QComboBox QAbstractItemView {{ background: {PNL}; selection-background-color: {SEL}; }}
QTableWidget, QTableView, QListWidget {{ background: {ROW}; gridline-color: {BRD}; border: 1px solid {BRD};
    selection-background-color: {SEL}; alternate-background-color: {ROW}; }}
QHeaderView::section {{ background: {THD}; border: none; border-right: 1px solid {BRD}; border-bottom: 1px solid {BRD}; padding: 4px; }}
QTableCornerButton::section {{ background: {THD}; border: none; }}
QTabWidget::pane {{ border: 1px solid {BRD}; top: -1px; background: {WIN}; }}
QTabBar::tab {{ background: {THD}; color: {SUB}; padding: 5px 14px; margin-right: 3px; }}
QTabBar::tab:selected {{ background: {HDR}; color: {TXT}; border-bottom: 3px solid {BLU}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator, QTableView::indicator, QListView::indicator {{ width: 15px; height: 15px; border: 1px solid {SUB};
    border-radius: 2px; background: {INP}; }}
QCheckBox::indicator:hover {{ border: 1px solid {BLU}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{ background: {SEL};
    border: 1px solid {BLU}; image: url({asset_url("check.png")}); }}
QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 7px; border: 1px solid {SUB}; background: {INP}; }}
QRadioButton::indicator:checked {{ background: {BLU}; border: 3px solid {INP}; outline: 1px solid {SUB}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {DIM}; }}
QStatusBar {{ background: {BRD}; }}
QStatusBar QLabel {{ background: #2E3946; border: 1px solid #56657A; border-radius: 2px; padding: 1px 6px; font-size: 9pt; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {PNL}; width: 12px; height: 12px; }}
QScrollBar::handle {{ background: {BTN}; border-radius: 4px; min-height: 20px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {PNL}; border: 1px solid {BRD}; }}
QGroupBox {{ border: 1px solid {BRD}; margin-top: 18px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 2px 8px; background: {HDR}; }}
"""


def status_html(code: str, size_pt: float | None = None) -> str:
    col = STATUS.get(code, GRY)
    txt = STATUS_TEXT.get(code, code)
    sz = f"font-size:{size_pt}pt;" if size_pt else ""
    return f"<span style='color:{col};{sz}'>● <b>{txt}</b></span>"
