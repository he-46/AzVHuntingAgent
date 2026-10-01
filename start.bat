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
    if errorlevel 1 goto :python_error
) else (
    echo [1/3] 已找到虚拟环境。
)

"%VENV_PYTHON%" -c "import streamlit, altair, openai, pydantic, pandas, docx, pypdf, reportlab" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 正在安装项目依赖...
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :dependency_error
) else (
    echo [2/3] 项目依赖已就绪。
)

echo [3/3] 正在启动求职时间线 Agent...
echo 关闭本窗口或按 Ctrl+C 可停止服务。
"%VENV_PYTHON%" launcher.py
if errorlevel 1 goto :runtime_error
goto :end

:python_error
echo.
echo 虚拟环境创建失败，请确认已安装 Python 3.12 或更新版本。
pause
exit /b 1

:dependency_error
echo.
echo 项目依赖安装失败，请检查上方的 pip 错误和网络连接。
pause
exit /b 1

:runtime_error
echo.
echo 应用启动失败，请根据上方的具体错误检查端口或环境。
pause
exit /b 1

:end
endlocal
