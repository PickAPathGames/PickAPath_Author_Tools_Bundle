@echo off
cd ..
start "" http://localhost:8000
python -m engine.web.server_launcher
pause