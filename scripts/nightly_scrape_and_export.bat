@echo off
rem Nightly: scrape StepStone + Arbeitsagentur nationwide, then export gated matches to career-ops.
rem Register with Task Scheduler (run once from an admin prompt):
rem   schtasks /Create /TN "StellenRadar Nightly" /TR "D:\Ironhack\Github\project\Job-intelligence-Project\scripts\nightly_scrape_and_export.bat" /SC DAILY /ST 06:30
cd /d "%~dp0.."
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python -m scripts.run_scrape --max-roles 12 --max-pages 3 >> scripts\nightly.log 2>&1
python scripts\export_to_career_ops.py --days 1 >> scripts\nightly.log 2>&1
