#!/usr/bin/env python3
"""Generate AppIcon.icns from 'Notion Bridge.jpeg'."""
import os
import subprocess

from PIL import Image

SRC  = os.path.join(os.path.dirname(__file__), "..", "Notion Bridge.jpeg")
OUT  = os.path.join(os.path.dirname(__file__), "..", "AppIcon.iconset")
ICNS = os.path.join(os.path.dirname(__file__), "..", "AppIcon.icns")

SIZES = [
    ("icon_16x16.png",      16),
    ("icon_16x16@2x.png",   32),
    ("icon_32x32.png",      32),
    ("icon_32x32@2x.png",   64),
    ("icon_128x128.png",    128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png",    256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png",    512),
    ("icon_512x512@2x.png", 1024),
]

os.makedirs(OUT, exist_ok=True)
src = Image.open(SRC).convert("RGBA")
for fname, size in SIZES:
    src.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, fname))
    print(f"  {fname} ({size}x{size})")

subprocess.run(["iconutil", "-c", "icns", OUT, "-o", ICNS], check=True)
print(f"Created {ICNS}")
