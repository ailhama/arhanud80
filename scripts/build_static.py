"""Publish only the explicit frontend allowlist, never source or private data."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
output = root / 'public'
output.mkdir(exist_ok=True)
for name in ('index.html','p.html','o.html','t.html','k.html','tv.html','logo.png'):
    shutil.copy2(root/name,output/name)
shutil.copytree(root/'assets',output/'assets',dirs_exist_ok=True)
print('Frontend prepared in public/')
