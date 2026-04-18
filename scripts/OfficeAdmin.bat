@echo off
start "Office Admin" cmd /k ^
"call C:\Users\MyUser\miniconda3\Scripts\activate.bat && ^
conda activate office-admin-app-env && ^
cd /d C:\Users\MyUser\Documents\office-admin-1.1.0 && ^
start http://127.0.0.1:8000 && ^
python -m uvicorn src.api:app"