"""Consistent backup of a STOPPED application. Archive permissions are private."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit, unquote


def backup(config_path, destination):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    data_dir = Path(cfg['data_dir']).resolve()
    out = Path(destination).resolve()
    if out == data_dir or data_dir in out.parents:
        raise ValueError('Backup destination must be outside the application data directory.')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = out / ('genealogy-'+stamp+'.tar.gz')
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        url = cfg['database_url']
        if url.startswith('sqlite:///'):
            with sqlite3.connect(url[len('sqlite:///'):]) as source, sqlite3.connect(temp/'database.sqlite') as dest:
                source.backup(dest)
            database_type = 'sqlite'
        elif url.startswith('postgresql'):
            u = urlsplit(url.replace('postgresql+psycopg:', 'postgresql:', 1))
            env = dict(os.environ, PGPASSWORD=unquote(u.password or ''))
            subprocess.run(['pg_dump', '--host', u.hostname or '127.0.0.1', '--port', str(u.port or 5432),
                            '--username', unquote(u.username or ''), '--dbname', unquote(u.path.lstrip('/')),
                            '--format=custom', '--no-owner', '--no-acl', '--file', str(temp/'database.dump')], env=env, check=True)
            database_type = 'postgresql'
        else: raise ValueError('Unsupported database type.')
        (temp/'manifest.json').write_text(json.dumps({'created':stamp,'database_type':database_type,'data_dir':str(data_dir),'format_version':1},indent=2))
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output, tarfile.open(fileobj=output, mode='w:gz') as tar:
                for f in temp.iterdir(): tar.add(f, arcname=f.name)
                tar.add(config_path, arcname='private-config.json')
                tar.add(data_dir, arcname='data')
        except Exception:
            target.unlink(missing_ok=True); raise
    with target.open('rb') as archive_input:
        digest = hashlib.file_digest(archive_input, 'sha256').hexdigest()
    checksum = target.with_suffix(target.suffix+'.sha256')
    fd = os.open(checksum, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd,'w') as f: f.write(f'{digest}  {target.name}\n')
    return target

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--config',required=True);p.add_argument('--destination',required=True)
    a=p.parse_args();print(backup(a.config,a.destination))
