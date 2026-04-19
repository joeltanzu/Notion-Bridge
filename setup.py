from setuptools import setup
import glob
import os

APP = ["main.py"]


def _frontend_data_files():
    """Collect all frontend/dist files for bundling."""
    result = []
    for dirpath, _dirs, files in os.walk("frontend/dist"):
        if not files:
            continue
        dest = dirpath  # keep relative path as-is inside the bundle
        sources = [os.path.join(dirpath, f) for f in files]
        result.append((dest, sources))
    return result


DATA_FILES = _frontend_data_files()
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "packages": [
        "webview",
        "backend",
        "notion_client",
        "watchdog",
        "frontmatter",
        "keyring",
        "httpx",
        "pydantic",
        "markdown_it",
        "anyio",
    ],
    "includes": [
        "AppKit",
        "Foundation",
    ],
    "excludes": [
        "tkinter",
        "_tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "debugpy",
        "IPython",
        "ipykernel",
        "setuptools",
        "pip",
        "zmq",
        "mypy",
        "mypy_extensions",
        "mypyc",
        "shiboken6",
        "PySide6",
    ],
    "plist": {
        "CFBundleName": "Notion Bridge",
        "CFBundleDisplayName": "Notion Bridge",
        "CFBundleIdentifier": "com.joeltan.notion-bridge",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "PyRuntimeLocations": [
            "@executable_path/../MacOS/python",
        ],
    },
}

setup(
    name="Notion Bridge",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
