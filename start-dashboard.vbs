' POV 舆情验证系统 · 开机自启看板（含内嵌自动调度：采集每 6h / 验证每 2h）
' 由 Windows 启动文件夹在登录时执行；pythonw 无窗口运行，停止方法见 README「自动调度」
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "E:\0AISoftwareCustom\new-auto\pov"
sh.Run """C:\Users\Administrator.SY-202311081432\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"" ""E:\0AISoftwareCustom\new-auto\pov\pov.py"" serve", 0, False
