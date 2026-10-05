Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory
WshShell.Run chr(34) & strPath & "\run_local_server.bat" & chr(34), 0, False
Set WshShell = Nothing
