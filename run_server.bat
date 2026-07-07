@echo off
rem Start the Observable AI Backend for the live demo.
rem Logs appear here on screen AND in server.log (for the grep step).
cd /d "%~dp0"
if exist server.log del server.log
set LOG_FILE=server.log
echo Starting server on http://localhost:8000  (Ctrl+C to stop)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
