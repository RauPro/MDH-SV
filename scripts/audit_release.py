"""Fail if sensitive runtime files, prototype datasets or likely credentials enter Git."""
import re
import subprocess
from pathlib import Path

patterns = [r'AIza[0-9A-Za-z_-]{30,}', r'gh[pousr]_[A-Za-z0-9_]{20,}', r'github_pat_[A-Za-z0-9_]{20,}', r'sk-[A-Za-z0-9]{20,}', r'AKIA[0-9A-Z]{16}', r'-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----']
files = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
failures = []
for name in filter(None, files):
    path = Path(name)
    if (name.startswith(('legacy/', '.local-quarantine/', '.source-location-cache/', 'data/ngo_reports/', 'apps/web/src/data/')) or
        path.suffix in ('.sqlite', '.db') or
        path.name.startswith('.env') and path.name != '.env.example' or
        'node_modules' in path.parts):
        failures.append(f'Forbidden release path: {name}')
    try:
        content = path.read_text()
    except (UnicodeError, OSError):
        continue
    if any(re.search(pattern, content) for pattern in patterns):
        failures.append(f'Potential credential in {name} (value withheld)')
if failures:
    raise SystemExit('\n'.join(failures))
print(f'Release boundary checked: {len(files)-1} tracked paths; no forbidden data or matched credentials.')
