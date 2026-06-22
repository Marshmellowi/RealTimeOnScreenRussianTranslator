' Double-click to launch the Russian Translator with no console window.
' Derives its own folder, so it keeps working if you move the project.
' The DeepL key is read from your DEEPL_API_KEY user environment variable.

Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Environment("PROCESS")("PYTHONUTF8") = "1"

pythonw = """" & root & "\.venv\Scripts\pythonw.exe"""
script  = """" & root & "\src\main.py"""

' 0 = hidden window, False = don't wait for it to exit.
sh.Run pythonw & " " & script, 0, False
