Set shell = CreateObject("WScript.Shell")
command = ""

For i = 0 To WScript.Arguments.Count - 1
    If i > 0 Then
        command = command & " "
    End If
    command = command & WScript.Arguments(i)
Next

If command <> "" Then
    shell.Run command, 0, False
End If
