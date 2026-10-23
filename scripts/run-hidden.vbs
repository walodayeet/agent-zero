' Run a PowerShell script with no visible console window.
' Task Scheduler runs "interactive" actions in the user session, so a plain
' powershell.exe action flashes a console window (the watchdog did this every
' five minutes). Window style 0 creates the console hidden, and the server
' process inherits it.
Dim sh, script
If WScript.Arguments.Count < 1 Then
    WScript.Quit 1
End If
script = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """", 0, False
