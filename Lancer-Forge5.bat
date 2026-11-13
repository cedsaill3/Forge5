@echo off
rem Forge5 - par cedsaill. Ouvre Forge5 dans sa propre fenetre ; la fermer arrete Forge5.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw forge5.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw forge5.py & exit /b)
echo Forge5 a besoin de Python 3.10 ou plus : https://www.python.org/downloads/
echo (pendant l installation, coche "Add python.exe to PATH")
pause
