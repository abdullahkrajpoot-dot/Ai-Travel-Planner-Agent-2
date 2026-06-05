Set shell = CreateObject("WScript.Shell")
shell.Run "cmd.exe /c taskkill /F /IM python.exe /FI " & Chr(34) & "WINDOWTITLE eq Streamlit*" & Chr(34), 0, True
shell.Run "cmd.exe /c for /f " & Chr(34) & "tokens=5" & Chr(34) & " %a in ('netstat -ano ^| findstr :8501') do taskkill /F /PID %a", 0, True
