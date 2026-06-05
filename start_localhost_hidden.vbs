Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = "C:\Users\ALI BABA TRAVEL\AppData\Local\Programs\Python\Python311\python.exe"
outLog = projectDir & "\streamlit-out.log"
errLog = projectDir & "\streamlit-err.log"

shell.CurrentDirectory = projectDir

command = "cmd.exe /c " & _
    Chr(34) & Chr(34) & pythonExe & Chr(34) & _
    " -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true" & _
    " > " & Chr(34) & outLog & Chr(34) & _
    " 2> " & Chr(34) & errLog & Chr(34) & Chr(34)

shell.Run command, 0, False
WScript.Sleep 7000
shell.Run "http://127.0.0.1:8501", 1, False
