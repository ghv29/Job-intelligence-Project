@echo off
rem One-click launcher for the StellenRadar dashboard.
rem Uses the project venv if present, otherwise the system Python.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
rem Let Streamlit open the browser itself once the server is actually ready.
rem (Opening the browser before the server is listening was showing a blank
rem page.) --server.headless false overrides config.toml for this launch only.
streamlit run dashboard\streamlit_app.py --server.headless false
