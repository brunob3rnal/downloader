' Gestor de descargas: abre la ventana sin consola (specs/002-desktop-app, research R11).
' Doble clic desde el Explorador. Necesita uv en el PATH.
' Solo ASCII: WSH lee los .vbs como ANSI; las tildes de los mensajes van con ChrW.
Option Explicit

Const TITULO = "Gestor de descargas"
Dim shell, fso, codigo
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Function Texto(s)
    Texto = Replace(Replace(s, "{a}", ChrW(225)), "{o}", ChrW(243))
End Function

' El directorio de trabajo es la carpeta del propio lanzador (la raiz del repositorio).
shell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' uv en el PATH: 'where' sale con 0 si lo encuentra.
codigo = shell.Run("cmd /c where uv >nul 2>nul", 0, True)
If codigo <> 0 Then
    MsgBox Texto("No se encuentra uv. Inst{a}lelo con `winget install --id=astral-sh.uv -e`, " & _
        "abra una terminal nueva, ejecute `uv sync` en la carpeta del repositorio y vuelva a " & _
        "abrir el Gestor de descargas."), vbCritical, TITULO
    WScript.Quit 1
End If

' Sincronizar el entorno, oculto, y esperar a que termine.
codigo = shell.Run("uv sync --quiet", 0, True)
If codigo <> 0 Then
    MsgBox Texto("No se pudo preparar el entorno del Gestor de descargas. Compruebe la " & _
        "conexi{o}n a internet y vuelva a intentarlo. Si el problema sigue, ejecute `uv sync` " & _
        "en una terminal, en la carpeta del repositorio, para ver el error."), vbCritical, TITULO
    WScript.Quit 1
End If

' Abrir la ventana sin consola y sin esperar.
shell.Run "uv run --gui-script src\downloader\app\gestor.pyw", 0, False
