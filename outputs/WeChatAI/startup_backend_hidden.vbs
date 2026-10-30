Option Explicit

Dim shell, fso, scriptDir, startupScript, powershell, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
startupScript = fso.BuildPath(scriptDir, "startup_backend.ps1")
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & _
    "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = Chr(34) & powershell & Chr(34) & _
    " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & _
    Chr(34) & startupScript & Chr(34)

' WScript is a GUI subsystem host. Window style 0 keeps PowerShell and its
' console children hidden from the moment they are created.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
