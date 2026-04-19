@echo off
start "Office Admin" cmd /k ^
"cd /d C:\Users\MyUser\Documents\office-admin-claude-impl && ^
start http://127.0.0.1:8000 && ^
uv run python main.py"
