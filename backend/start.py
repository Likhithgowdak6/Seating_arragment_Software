import os,subprocess,sys
if __name__=='__main__':
    subprocess.run([sys.executable,'-m','alembic','-c','backend/alembic.ini','upgrade','head'],check=True)
    os.execv(sys.executable,[sys.executable,'-m','uvicorn','backend.main:app','--host','0.0.0.0','--port','8000','--workers','1'])
