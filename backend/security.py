import hashlib
import hmac
import os
import secrets
import getpass


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def password_valid(password, encoded):
    try:
        scheme, rounds, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 2000000: return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError): return False

if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Create private Genealogy configuration; password is never shown.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--database', default=os.environ.get('GENEALOGY_SETUP_DATABASE'))
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    if not args.database: raise SystemExit('Database connection is required.')
    path = Path(args.config)
    if path.exists(): raise SystemExit('Configuration exists; no changes made.')
    password = getpass.getpass('Choose an owner password (at least 12 characters): ')
    if not 12 <= len(password) <= 256: raise SystemExit('Password must be between 12 and 256 characters.')
    if password != getpass.getpass('Repeat password: '): raise SystemExit('Passwords differ.')
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    config = {'database_url': args.database, 'data_dir': args.data_dir,
              'password_hash': password_hash(password), 'secure_cookie': False}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(config, f, indent=2)
    print('Configuration created. Enable secure_cookie after configuring HTTPS.')
