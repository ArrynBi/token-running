@echo off
rem token-running 一键启动
cd /d "%~dp0"
set PYTHONPATH=src
start "" pythonw.exe main.py