CreateObject("WScript.Shell").Run "pythonw.exe """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\voice-input-gui.pyw""", 0, False
