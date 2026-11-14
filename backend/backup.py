"""Create a consistent local SQLite backup without exposing credentials."""
import sqlite3,argparse
from pathlib import Path
from datetime import datetime,timezone
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',default='backend/smartseat.db');p.add_argument('--output');args=p.parse_args()
    source=Path(args.source).resolve()
    if not source.is_file():raise SystemExit('Source SQLite database not found.')
    output=Path(args.output or ('backups/smartseat-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.db')).resolve()
    if output.exists():raise SystemExit('Backup file already exists; choose another name.')
    output.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(output) as dst:src.backup(dst)
    print('SQLite backup created:',output)
