$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\Artha Dashboard.lnk")
$Shortcut.TargetPath = "C:\trading\START.bat"
$Shortcut.WorkingDirectory = "C:\trading"
$Shortcut.IconLocation = "C:\Windows\System32\shell32.dll,23"
$Shortcut.Description = "Artha Trading Dashboard - One Click"
$Shortcut.Save()
Write-Host "Desktop shortcut created!"
