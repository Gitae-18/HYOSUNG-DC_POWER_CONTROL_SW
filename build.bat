@echo off
chcp 65001 > nul
REM ===== 효성전기 챔버 제어 시스템 실행 파일 빌드 (Windows) =====
REM 이 파일이 있는 폴더에서 더블클릭 또는 명령 프롬프트에서 build.bat 실행
cd /d "%~dp0"

if not exist .venv (
    echo [1/3] 가상환경 만드는 중...
    py -3.12 -m venv .venv || python -m venv .venv || goto :err
)

echo [2/3] 패키지 설치 중...
call .venv\Scripts\python -m pip install --upgrade pip > nul
call .venv\Scripts\pip install -r requirements.txt pyinstaller || goto :err

echo [3/3] 실행 파일 만드는 중...
call .venv\Scripts\pyinstaller --noconfirm --clean hyosung.spec || goto :err

echo.
echo 완료: dist\HyosungChamber\HyosungLauncher.exe
explorer dist\HyosungChamber
goto :eof

:err
echo.
echo 빌드 실패 - 위 메시지를 확인하세요.
pause
