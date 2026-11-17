# 효성전기 온/습도 챔버 제어 시스템 — HMI (Python) · Ver 1.3.1 (2026-11-16)

HMI 스토리보드 V1.3.0과 고객사 질의 답변, USP 1U·2U(SCPI)·NEX1200(MODBUS RTU) 통신 설명서를 기준으로 만든 PySide6 프로그램입니다.
장비 없이 **시뮬레이터**로 바로 실행됩니다. 자세한 내용은 **개발 가이드** 와 **D/B 설계서** 문서를 보세요.

## 실행

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.launcher                    REM SCR-000 런처
.venv\Scripts\python -m app.app_dcpower --sim           REM SCR-300 DC Power & 온/습도 (시뮬레이터)
.venv\Scripts\python -m app.app_dcpower --sim --fail 6  REM P06 통신 이상까지
```

설정·데이터는 `%USERPROFILE%\HyosungChamber` 아래에 만들어집니다 (`HYOSUNG_HMI_HOME` 환경변수로 변경).
Setup 관리자 암호 초기값은 `0000` 입니다.

## 계층형 MVVM 파일 구성

| 파일 | 역할 |
| --- | --- |
| `app/` | 런처와 DC Power 프로그램 진입점 |
| `application/services/hub.py` | 장비 감시(1초), Function 러너, 알람, DB 저장 호출을 조정하는 Application Service |
| `domain/models/function_model.py` | Function/Segment/Step 모델, 변화율 계산, 입력 검증, Profile 파일(.hfp) |
| `infrastructure/devices/devices.py` | 장비 인터페이스, 시뮬레이터, USP·NEX1200 실제 드라이버 |
| `infrastructure/persistence/datalog.py` | DuckDB 일별 저장·조회와 운영 로그 |
| `infrastructure/system/` | 설정과 중복 실행 방지 기능 |
| `presentation/views/` | 메인 화면, 대화상자, Function 관리 화면 |
| `presentation/widgets/` · `presentation/graphs/` | 공통 위젯과 그래프 조작 도구 |
| `presentation/styling/` | 테마, 로고, 아이콘 리소스 |
| `presentation/viewmodels/` | 화면 상태·명령을 순차적으로 분리할 ViewModel 패키지 |
| `tools/` | 에뮬레이터와 시험: `test_comm.py`(43), `test_db.py`(14, `--perf`), `test_runner.py`, `test_ui.py`(화면 46항목) |

## 그 밖의 저장 위치

| 내용 | 위치 |
| --- | --- |
| Function | `Function\이름.json` |
| Function Profile 내보내기(기본 폴더) | `Profile\*.hfp` (JSON, 다른 PC 의 [Function Profile 가져오기] 로 읽음) |
| 그래프 JPEG 저장(기본 폴더) | `Image\*.jpg` |
| 전류 기준 Min/Max | `Settings\config.json` 의 `i_limits` (판정 유예 `i_check_delay_s`, 기본 3초) |

## 데이터 저장 (DuckDB)

`Data\DB\YYYY\MM\HyosungData_YYYYMMDD.duckdb` — 하루 1파일, 1초 주기 자동 저장.
DC Power 출력 중인 채널과, 챔버 운전 중(또는 Power 출력 중)의 챔버 값을 저장하고 Result 메뉴에서 Power별·기간별로 조회·CSV 내보내기 합니다.

## 실제 장비 연결

Setup › 통신: NEX1200 COM1, USP COM2(주소 1~6), '시뮬레이터로 동작' 해제 → 재시작.
현장 시험 절차는 `UPDATE_20261103.md`, 확인 항목(끝 문자, 통신 속도, 변화율 단위)은 개발 가이드 13장.

## 배포 (exe)

`build.bat` → `dist\HyosungChamber\` (HyosungLauncher.exe, HyosungDCPower.exe, `_internal` — Qt·duckdb·assets 포함).
폴더 전체를 운용 PC 에 복사하고 HyosungLauncher.exe 바로가기를 만듭니다.
