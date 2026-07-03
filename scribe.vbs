' Scribe launcher - starts the tool with NO console window (pythonw).
' Double-click this file to run. Quit with Ctrl+Alt+Q. Logs go to scribe.log.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\dev\scribe-voice"
sh.Run """C:\Users\saigo\AppData\Local\Programs\Python\Python313\pythonw.exe"" scribe_realtime.py", 0, False
