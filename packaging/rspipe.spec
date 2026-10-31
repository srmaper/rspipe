# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 形式，产出 dist/rspipe/rspipe.exe。

构建：
    pyinstaller --noconfirm --clean packaging/rspipe.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "web" / "dist"), "web/dist"),
    (str(ROOT / "configs"), "configs"),
]
binaries = []
hiddenimports = [
    "rspipe",
    "rspipe.web.app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "jaraco.text",
    "jaraco.functools",
    "jaraco.context",
    "more_itertools",
]

for pkg in (
    "rasterio",
    "rioxarray",
    "geopandas",
    "pyproj",
    "shapely",
    "pyogrio",
    "fiona",
    "pystac",
    "pystac_client",
    "rio_cogeo",
    "rio_stac",
    "xarray",
    "pandas",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "structlog",
    "apscheduler",
    "PIL",
    "yaml",
    "httpx",
    "tenacity",
    "numpy",
    "scipy",
):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "scripts" / "frozen_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "PyQt5",
        "PySide2",
        "PySide6",
        "tkinter",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "mypy",
        "ruff",
        "torch",
        "torchvision",
        "torchaudio",
        "polars",
        "selenium",
        "sklearn",
        "lightgbm",
        "transformers",
        "cv2",
        "boto3",
        "fiona",
        "s2cloudless",
        "usgsxplore",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rspipe",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "packaging" / "rspipe.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="rspipe",
)
