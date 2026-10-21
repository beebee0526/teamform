#!/bin/bash
cd "$(dirname "$0")"
echo "[1/3] 파이썬 확인"
if ! command -v python3 >/dev/null; then
  echo "python3이 없습니다. python.org 에서 설치하세요."; read -p "Enter를 누르면 닫힘"; exit 1
fi
python3 --version
echo "[2/3] 프로그램 설치(.venv 폴더에 설치)"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || { echo "설치 실패. 위 오류를 확인하세요."; read -p "Enter"; exit 1; }
echo "[3/3] .env 만들기"
[ -f .env ] || cp .env.example .env
echo "텍스트 편집기가 열리면 따옴표 안의 값 세 개를 바꾸고 저장(Cmd+S)한 뒤 닫으세요."
open -e .env
echo "끝났습니다. 이제 run_mac.command 를 더블클릭하면 앱이 열립니다."
read -p "Enter를 누르면 닫힘"
