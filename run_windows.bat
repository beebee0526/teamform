@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m streamlit run streamlit_app.py
) else (
  python -m streamlit run streamlit_app.py
)
pause
