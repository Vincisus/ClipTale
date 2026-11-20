"""Crea desktop/cliptale.ico (logo arancione e nero, tutte le misure usate da Windows). Uso: python desktop/make_icon.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop.launcher import make_icon  # noqa: E402

OUT = Path(__file__).with_name("cliptale.ico")

if __name__ == "__main__":
    big = make_icon(size=256)
    big.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)
