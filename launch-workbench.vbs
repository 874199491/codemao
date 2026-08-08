Option Explicit

Dim shell, fso, basePath, serverPath, pageUrl, pythonPath, command, attempt
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

basePath = fso.GetParentFolderName(WScript.ScriptFullName)
serverPath = basePath & "\apps\teacher_workbench\server.py"
pageUrl = "http://127.0.0.1:8876"
pythonPath = ResolvePythonLauncher()
shell.CurrentDirectory = basePath

If Not fso.FileExists(serverPath) Then
    MsgBox "Cannot find teacher workbench server file:" & vbCrLf & serverPath, vbCritical, "Teacher Workbench"
    WScript.Quit 1
End If

If IsWorkbenchRunning(pageUrl) And Not IsExpectedWorkbench(pageUrl) Then
    StopWorkbenchOnPort "8876"
    WScript.Sleep 800
End If

If Not IsExpectedWorkbench(pageUrl) Then
    command = """" & pythonPath & """ -3.10 """ & serverPath & """ --host 127.0.0.1 --port 8876 --no-browser"
    shell.Run command, 0, False

    For attempt = 1 To 50
        WScript.Sleep 300
        If IsExpectedWorkbench(pageUrl) Then Exit For
    Next
End If

If IsExpectedWorkbench(pageUrl) Then
    shell.Run pageUrl, 1, False
Else
    MsgBox "Teacher Workbench failed to start or is still an old version." & vbCrLf & _
        "Please close any old workbench window, then run this launcher again.", _
        vbCritical, "Teacher Workbench"
End If

Function ResolvePythonLauncher()
    Dim candidates, item
    candidates = Array( _
        "C:\Windows\py.exe", _
        shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Launcher\py.exe" _
    )
    For Each item In candidates
        If fso.FileExists(item) Then
            ResolvePythonLauncher = item
            Exit Function
        End If
    Next
    ResolvePythonLauncher = "py"
End Function

Function IsWorkbenchRunning(url)
    Dim request
    On Error Resume Next
    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")
    request.SetTimeouts 500, 500, 500, 500
    request.Open "GET", url & "/api/summary", False
    request.Send
    IsWorkbenchRunning = (Err.Number = 0 And request.Status = 200)
    Err.Clear
    On Error GoTo 0
End Function

Function IsExpectedWorkbench(url)
    Dim request, body
    On Error Resume Next
    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")
    request.SetTimeouts 500, 500, 500, 500
    request.Open "GET", url & "/api/config", False
    request.Send
    If Err.Number <> 0 Or request.Status <> 200 Then
        IsExpectedWorkbench = False
    Else
        body = request.ResponseText
        IsExpectedWorkbench = (InStr(1, body, "feedback_rules", vbTextCompare) > 0)
    End If
    If IsExpectedWorkbench Then
        request.Open "GET", url & "/api/tasks", False
        request.Send
        IsExpectedWorkbench = (Err.Number = 0 And request.Status = 200 And InStr(1, request.ResponseText, "cancel_feedback_send", vbTextCompare) > 0)
    End If
    If IsExpectedWorkbench Then
        request.Open "GET", url & "/api/schedules", False
        request.Send
        IsExpectedWorkbench = (Err.Number = 0 And request.Status = 200 And InStr(1, request.ResponseText, "weekday_labels", vbTextCompare) > 0)
    End If
    Err.Clear
    On Error GoTo 0
End Function

Sub StopWorkbenchOnPort(portText)
    Dim svc, processes, process, commandLine
    On Error Resume Next
    Set svc = GetObject("winmgmts:\\.\root\cimv2")
    Set processes = svc.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='py.exe'")
    For Each process In processes
        commandLine = LCase(CStr(process.CommandLine))
        If InStr(commandLine, "teacher_workbench\server.py") > 0 And InStr(commandLine, "--port " & portText) > 0 Then
            process.Terminate
        End If
    Next
    Err.Clear
    On Error GoTo 0
End Sub
