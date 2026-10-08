@echo off
set UV_LINK_MODE=copy
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul || (
    echo Installation de uv, une seule fois...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
)

pushd "%~dp0"
uv run --isolated --python 3.12 --with-requirements requirements.txt streamlit run app.py
popd
pause