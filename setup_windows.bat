@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 파이썬 확인
python --version || (echo 파이썬이 없습니다. python.org 에서 설치할 때 "Add python.exe to PATH"를 체크하세요. & pause & exit /b 1)
echo [2/3] 프로그램 설치(.venv 폴더에 설치하므로 컴퓨터의 다른 파이썬에는 영향 없음)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo 설치 실패. 위 오류를 확인하세요. & pause & exit /b 1)
echo [3/3] .env 만들기
if not exist .env copy .env.example .env >nul
echo 메모장이 열리면 따옴표 안의 값 세 개를 바꾸고 저장(Ctrl+S)한 뒤 닫으세요.
notepad .env
echo 끝났습니다. 이제 run_windows.bat 를 더블클릭하면 앱이 열립니다.
pause
