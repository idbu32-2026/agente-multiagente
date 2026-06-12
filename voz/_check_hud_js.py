"""Chequeo puntual: extrae el JS inline del HUD para validarlo con node."""
import os
import re
from pathlib import Path

hud = Path(__file__).resolve().parent.parent / "hud" / "jarvis_hud.html"
html = hud.read_text(encoding="utf-8")
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
salida = Path(os.environ["TEMP"]) / "hud_check.js"
salida.write_text("\n".join(s for s in scripts if s.strip()), encoding="utf-8")
print(f"extraido -> {salida}")
