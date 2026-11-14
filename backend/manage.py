import getpass
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.main import engine,User,ph
if __name__=='__main__':
    usn=input('Teacher username [ADMIN]: ').strip().upper() or 'ADMIN'
    name=input('Teacher name: ').strip() or 'Exam coordinator'
    password=getpass.getpass('Password (at least 10 characters): ')
    if len(password)<10:raise SystemExit('Password must have at least 10 characters.')
    with Session(engine) as s:
        if s.scalar(select(User).where(User.usn==usn)):raise SystemExit('Username already exists.')
        s.add(User(usn=usn,name=name,branch='',role='admin',password=ph.hash(password),must_change=False));s.commit()
    print('Teacher created. Start the app and sign in.')
