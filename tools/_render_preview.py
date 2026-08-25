"""Render the deck to PNGs using the installed PowerPoint, for a quick preview."""

from __future__ import annotations

import os
from pathlib import Path

import win32com.client

ROOT = Path(__file__).resolve().parents[1]
PPTX = ROOT / "docs" / "Sentinel_Pitch.pptx"
OUT = ROOT / "docs" / "preview"
OUT.mkdir(exist_ok=True)

powerpoint = win32com.client.Dispatch("PowerPoint.Application")
# Some builds refuse WindowState changes while hidden; keep it simple and just open.
deck = powerpoint.Presentations.Open(str(PPTX), WithWindow=False)
try:
    for slide in deck.Slides:
        slide.Export(str(OUT / f"slide_{slide.SlideIndex:02d}.png"), "PNG", 1600, 900)
    print(f"exported {deck.Slides.Count} slides to {OUT}")
finally:
    deck.Close()
    powerpoint.Quit()
