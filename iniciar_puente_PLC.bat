@echo off
chcp 65001 >nul
title Puente PLC (Modbus) - LadderVoice

rem ============================================================
rem  Levanta el backend LOCAL que carga programas al PLC fisico
rem  y que ademas sirve el editor web:
rem      http://localhost:8000/web/ladder.html
rem  Es lo UNICO que hay que abrir en la PC del PLC (ya no hace
rem  falta iniciar_pagina_local.bat). Abre el navegador solo.
rem  Debe correr en una PC de la MISMA red del PLC (Modbus TCP).
rem  No necesita GROQ_API_KEY: el chat y la generacion viven en
rem  Render. Esto sirve para "Cargar" y "Simular en lentes".
rem  El editor se toma de la carpeta del frontend que esta junto a
rem  esta (..\Proyecto_Final_Prospectiva_Tecnologia) o de LAVO_WEB_DIR.
rem
rem  Para detenerlo: presiona Ctrl + C  (o cierra esta ventana).
rem  Al detenerse, la ventana se cierra sola.
rem ============================================================

cd /d "%~dp0"

rem --- Si algun dia creas un entorno virtual (.venv), se usa solo ---
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

rem --- Verifica que Python y las dependencias esten listos ---
python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] No se encontro Python en el PATH.
  echo Instala Python o abrelo desde el entorno donde lo tengas.
  echo.
  pause
  exit /b 1
)

python -c "import uvicorn, pymodbus" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Faltan dependencias (uvicorn o pymodbus^).
  echo Ejecuta una vez:  pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

set "WEB=%LAVO_WEB_DIR%"
if "%WEB%"=="" set "WEB=%~dp0..\Proyecto_Final_Prospectiva_Tecnologia"
if not exist "%WEB%\ladder.html" (
  echo.
  echo [AVISO] No se encontro el editor web en:
  echo   %WEB%
  echo El puente funciona, pero no servira la pagina. Pon la carpeta
  echo Proyecto_Final_Prospectiva_Tecnologia junto a esta o define LAVO_WEB_DIR.
  echo.
)

echo.
echo  Puente PLC iniciando en http://localhost:8000
echo  Editor: http://localhost:8000/web/ladder.html  (se abre solo)
echo  Deja esta ventana abierta mientras cargas programas al PLC.
echo  Para detener: Ctrl + C  (la ventana se cierra sola).
echo.

rem --- El backend abre el navegador en cuanto esta listo ---
set "LAVO_ABRIR_NAVEGADOR=1"

rem --- Arranca el servidor. Al detenerlo, el .bat termina y la ventana se cierra. ---
python -m uvicorn app:app --host 0.0.0.0 --port 8000
