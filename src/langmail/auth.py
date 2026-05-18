"""Gmail OAuth2 authentication and encrypted token management.

Flow:
1. User runs `langmail setup`
2. We open a browser to Google's OAuth consent screen
3. User grants access
4. We receive tokens and encrypt them to ~/.langmail/credentials.enc
5. On subsequent runs, we load and decrypt the token automatically
6. Refresh tokens are renewed automatically when expired
"""

import json

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from langmail.config import CREDENTIALS_FILE, DATA_DIR

# Minimum required Gmail scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Key file path (derived from env or local file)
KEY_FILE = DATA_DIR / ".key"


def _get_or_create_key() -> bytes:
    """Load or generate the local encryption key.

    The key is stored in ~/.langmail/.key (not committed to git).
    If lost, users must re-run `langmail setup`.
    """
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()

    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)  # owner read/write only
    return key


def _get_fernet() -> Fernet:
    """Return a Fernet instance using the local encryption key."""
    return Fernet(_get_or_create_key())


def _encrypt(data: str) -> bytes:
    """Encrypt a string using the local Fernet key."""
    return _get_fernet().encrypt(data.encode())


def _decrypt(data: bytes) -> str:
    """Decrypt bytes using the local Fernet key."""
    return _get_fernet().decrypt(data).decode()


def _save_credentials(creds: Credentials) -> None:
    """Serialize and encrypt credentials to disk."""
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    encrypted = _encrypt(json.dumps(token_data))
    CREDENTIALS_FILE.write_bytes(encrypted)
    CREDENTIALS_FILE.chmod(0o600)


def _load_credentials() -> Credentials | None:
    """Load and decrypt credentials from disk. Returns None if not found."""
    if not CREDENTIALS_FILE.exists():
        return None

    try:
        decrypted = _decrypt(CREDENTIALS_FILE.read_bytes())
        token_data = json.loads(decrypted)
        return Credentials(
            token=token_data["token"],
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=token_data["scopes"],
        )
    except Exception:
        return None


def run_oauth_flow(client_secrets_file: str) -> Credentials:
    """Run the OAuth2 browser flow and return credentials.

    Args:
        client_secrets_file: Path to the credentials.json downloaded
                             from Google Cloud Console.
    """
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    return creds


def get_credentials() -> Credentials:
    """Return valid Gmail credentials, refreshing if expired.

    Raises:
        RuntimeError: If no credentials found (user must run `langmail setup`).
    """
    creds = _load_credentials()

    if creds is None:
        raise RuntimeError(
            "No Gmail credentials found. Run `langmail setup` to authenticate."
        )

    # Refresh if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)  # save refreshed token
        else:
            raise RuntimeError(
                "Gmail credentials are invalid. Run `langmail setup` to re-authenticate."
            )

    return creds


def get_gmail_service():
    """Build and return an authenticated Gmail API service client."""
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def is_authenticated() -> bool:
    """Check if valid credentials exist without raising."""
    try:
        creds = _load_credentials()
        return creds is not None and (creds.valid or bool(creds.refresh_token))
    except Exception:
        return False


def revoke_credentials() -> None:
    """Delete stored credentials (logout)."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    if KEY_FILE.exists():
        KEY_FILE.unlink()
