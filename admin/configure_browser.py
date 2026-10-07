"""Enable one explicit HTTPS browser frontend after HTTPS is working on the LXC."""
import argparse,json,os,shutil
from pathlib import Path
from urllib.parse import urlsplit

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='/etc/genealogy/config.json')
    p.add_argument('--origin',default='https://blarm1959.github.io')
    args=p.parse_args();origin=urlsplit(args.origin)
    if origin.scheme!='https' or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password or '*' in args.origin:
        p.error('Use an exact HTTPS origin without path, query or credentials.')
    path=Path(args.config);data=json.loads(path.read_text());backup=path.with_name(path.name+'.before-browser')
    if backup.exists():p.error('Backup already exists; inspect it before configuring again.')
    shutil.copy2(path,backup)
    data['browser_origins']=[args.origin];data['secure_cookie']=True
    # Preserve the private config file ownership/mode; never print its contents.
    with path.open('w') as f:json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
    print('Configured HTTPS browser origin. Restart genealogy.service. Use the HTTPS backend address in the GitHub frontend.')
if __name__=='__main__':main()
