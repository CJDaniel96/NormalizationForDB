@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto directory_error

where uv >nul 2>nul
if errorlevel 1 goto uv_missing

set "AOI_CONFIG=%~dp0config\zj_z36_holly.json"
if not "%~1"=="" set "AOI_CONFIG=%~f1"
if not exist "%AOI_CONFIG%" goto config_missing

uv sync --locked --no-dev
if errorlevel 1 goto sync_error

echo Starting recursive XML monitoring. Press Ctrl+C to stop.
uv run --locked --no-sync aoi-normalizer --config "%AOI_CONFIG%" --watch
set "AOI_EXIT_CODE=%ERRORLEVEL%"
if not "%AOI_EXIT_CODE%"=="0" pause
popd
exit /b %AOI_EXIT_CODE%

:uv_missing
echo uv was not found. Install uv and reopen this launcher.
echo Installation guide: https://docs.astral.sh/uv/getting-started/installation/
goto failure

:config_missing
echo Configuration file was not found: "%AOI_CONFIG%"
goto failure

:sync_error
echo Environment setup failed. Review the uv error above.
goto failure

:directory_error
echo Cannot open the project directory.
pause
exit /b 2

:failure
pause
popd
exit /b 2
