"""Local-owner recovery. Never exposes password text in arguments or output."""
import getpass
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from backend.main import engine,User,LoginSession,ph
from backend.extensions import Audit
from datetime import datetime,timezone
if __name__=='__main__':
    username=input('Teacher username [ADMIN]: ').strip().upper() or 'ADMIN'
    first=getpass.getpass('New password (at least 10 characters): ')
    second=getpass.getpass('Repeat new password: ')
    if first!=second or len(first)<10 or len(first)>128:raise SystemExit('Passwords must match and have 10-128 characters.')
    with Session(engine) as s:
        user=s.scalar(select(User).where(User.usn==username,User.role=='admin'))
        if not user:raise SystemExit('Teacher account not found.')
        user.password=ph.hash(first);user.must_change=False
        s.execute(delete(LoginSession).where(LoginSession.user_id==user.id))
        s.add(Audit(actor='local-owner',action='teacher.password_recovered',target=username,created=datetime.now(timezone.utc).isoformat()));s.commit()
    print('Teacher password reset. All previous sessions were revoked.')
