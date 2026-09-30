@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [1/3] 正在创建 Python 虚拟环境...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.12 -m venv .venv
        if errorlevel 1 python -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto :error
) else (
    echo [1/3] 已找到虚拟环境。
)

"%VENV_PYTHON%" -c "import streamlit, altair, openai, pydantic, pandas" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 正在安装项目依赖...
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
) else (
    echo [2/3] 项目依赖已就绪。
)

echo [3/3] 正在启动求职时间线 Agent...
echo 浏览器地址：http://127.0.0.1:8505
echo 关闭本窗口或按 Ctrl+C 可停止服务。
"%VENV_PYTHON%" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8505
if errorlevel 1 goto :error
goto :end

:error
echo.
echo 启动失败，请检查上方错误信息。项目需要 Python 3.12 或更新版本。
pause
exit /b 1

:end
endlocal
