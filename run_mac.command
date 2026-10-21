#!/bin/bash
cd "$(dirname "$0")"
[ -d .venv ] || { echo "먼저 setup_mac.command 를 실행하세요."; read -p "Enter"; exit 1; }
source .venv/bin/activate
python -m streamlit run streamlit_app.py
