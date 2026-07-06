@echo off
rem One-click launcher for the StellenRadar dashboard.
rem Uses the project venv if present, otherwise the system Python.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
start "" http://localhost:8501
streamlit run dashboard\streamlit_app.py
