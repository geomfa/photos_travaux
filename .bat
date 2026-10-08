@echo off
pushd "%~dp0"
uv run --isolated --python 3.12 --with-requirements requirements.txt streamlit run app.py
popd
pause