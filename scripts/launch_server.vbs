If WScript.Arguments.Count < 4 Then
    WScript.Quit 2
End If

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Set shell = CreateObject("WScript.Shell")
pythonPath = WScript.Arguments(0)
serverPath = WScript.Arguments(1)
workDir = WScript.Arguments(2)
logPath = WScript.Arguments(3)
shell.CurrentDirectory = workDir
commandBody = Quote(pythonPath) & " -u " & Quote(serverPath) & " >> " & Quote(logPath) & " 2>&1"
command = Quote(shell.ExpandEnvironmentStrings("%ComSpec%")) & " /d /s /c " & Quote(commandBody)
result = shell.Run(command, 0, False)
WScript.Quit result
