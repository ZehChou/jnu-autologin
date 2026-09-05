' JNU ePortal 自动登录 - 后台启动器(可移植版)
' 将本文件与 jnu_autologin.py 放在同一目录,双击即可在后台静默启动。
' 优先使用同目录下的 pythonw.exe,找不到则回退到系统 PATH 中的 pythonw。
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = scriptDir & "\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If
sh.Run """" & pythonw & """ """ & scriptDir & "\jnu_autologin.py""", 0, False
