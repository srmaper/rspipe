@echo off
rem Find a usable Python 3.11+ and export PY to the caller ("py -3.12" or "python").
rem Usage: call "%~dp0find_python.cmd"   then   if errorlevel 1 ...

set "PY="

for %%V in (3.13 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)

if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python"
)

if not defined PY exit /b 1
exit /b 0
