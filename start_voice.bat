@echo off
cd /d "%~dp0"

:: 1. wt の引数に直接フルパスを渡すのが最も安全です
:: 2. cd /d をネストさせないことで「パスが見つからない」エラーを回避
wt --size 25,15 cmd /k python "%~dp0scribe_realtime.py"

:: 3. 起動用ウィンドウは即終了
exit
