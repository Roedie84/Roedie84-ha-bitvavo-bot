"""Schrijft bestandscontrole.json: sha256 van elk bestand in de integratie."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "custom_components/bitvavo_bot")
out = root / "bestandscontrole.json"
files = {
    p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(root.rglob("*"))
    if p.is_file() and p != out and "__pycache__" not in p.parts
}
version = json.loads((root / "manifest.json").read_text())["version"]
out.write_text(json.dumps({"versie": version, "bestanden": files}, indent=1) + "\n")
print(f"{len(files)} bestanden, versie {version}")
