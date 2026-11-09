"""Create local demo secrets without printing them or overwriting existing configuration."""
import secrets
from pathlib import Path
path=Path(__file__).resolve().parents[1]/'.env'
with path.open('x') as f:
    f.write(f'POSTGRES_PASSWORD={secrets.token_urlsafe(24)}\nDEMO_ADMIN_PASSWORD={secrets.token_urlsafe(18)}\nDEVICE_TOKEN={secrets.token_urlsafe(32)}\nHTTP_PORT=8080\n')
path.chmod(0o600)
print('Created .env. Open it locally to obtain the demo password and device provisioning token.')
