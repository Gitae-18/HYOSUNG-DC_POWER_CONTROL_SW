"""중복 실행 방지 — 같은 COM 포트를 두 프로그램이 열지 않도록."""
from PySide6.QtCore import QSharedMemory


class SingleInstance:
    def __init__(self, key: str):
        self.mem = QSharedMemory(key)

    def acquire(self) -> bool:
        if self.mem.attach():          # 비정상 종료로 남은 세그먼트 정리
            self.mem.detach()
            if self.mem.attach():
                return False
        return self.mem.create(1)
