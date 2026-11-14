"""Explicit one-time copy from local SQLite into an EMPTY Docker database."""
import argparse,os
os.environ['AUTO_CREATE_TABLES']='false'
from sqlalchemy import create_engine,select,func,inspect,text
from backend.main import Base,engine
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',default='sqlite:////import/smartseat.db');args=parser.parse_args()
    source=create_engine(args.source)
    with source.connect() as src,engine.begin() as dst:
        if dst.scalar(select(func.count()).select_from(Base.metadata.tables['users'])):raise SystemExit('Target has users. Import refused to avoid overwriting data.')
        names=set(inspect(src).get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name=='sessions' or table.name not in names:continue
            rows=src.execute(select(table)).mappings().all()
            if rows:dst.execute(table.insert(),[dict(r) for r in rows])
            if engine.dialect.name=='postgresql' and 'id' in table.c and table.c.id.primary_key:
                dst.execute(text("SELECT setval(pg_get_serial_sequence(:table_name,'id'),COALESCE((SELECT MAX(id) FROM "+table.name+"),1),EXISTS(SELECT 1 FROM "+table.name+"))"),{'table_name':table.name})
    print('Existing data imported. Sign in with your current teacher account. Sessions were not copied.')
