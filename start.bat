@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   论文雷达 · 本地看板
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo [*] 首次运行，正在创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [!] 创建失败：请确认已安装 Python 3.9+ 并加入 PATH。
    pause
    exit /b 1
  )
  echo [*] 安装依赖（约 1 分钟）...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [!] 依赖安装失败，检查网络后重跑本脚本。
    pause
    exit /b 1
  )
)

if not exist "data\llm.json" goto SETUP
if not exist "config.json" goto SETUP
goto RUN

:SETUP
echo.
echo [*] 还没配置完，先跑一次向导。
echo     它会问你：模型接口与 Key、盯哪些研究方向、盯哪些期刊会议。
echo.
".venv\Scripts\python.exe" setup.py
echo.
pause

:RUN
start "" http://127.0.0.1:8780
echo 服务地址： http://127.0.0.1:8780
echo 关闭本窗口即停止服务。
echo.
".venv\Scripts\python.exe" server.py
pause
