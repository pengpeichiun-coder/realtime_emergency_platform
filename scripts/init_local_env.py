"""Create/extend the private Compose env without replacing existing credentials."""
import base64
import os
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / '.env'
values = {
    'METRICS_TOKEN': secrets.token_urlsafe(32),
    'POSTGRES_PASSWORD': secrets.token_hex(24),
    'RUNTIME_DATABASE_PASSWORD': secrets.token_hex(32),
    'MIGRATION_DATABASE_PASSWORD': secrets.token_hex(32),
    'JWT_SECRET': secrets.token_urlsafe(48),
    'WEBHOOK_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
}
if not path.exists():
    contents = path.with_name('.env.example').read_text()
else:
    contents = path.read_text()
changed = False
for key, generated in values.items():
    lines = contents.splitlines()
    matching = [index for index, line in enumerate(lines) if line.startswith(key + '=')]
    if not matching:
        lines.append(f'{key}={generated}')
        changed = True
    elif not lines[matching[0]].split('=', 1)[1]:
        lines[matching[0]] = f'{key}={generated}'
        changed = True
    contents = '\n'.join(lines) + '\n'
if changed:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if path.exists() else os.O_EXCL)
    with os.fdopen(os.open(path, flags, 0o600), 'w') as file:
        file.write(contents)
    path.chmod(0o600)
    print('Created or extended private root .env; existing non-empty secrets were retained.')
else:
    print('Existing root .env retained; all required local secrets are present.')
