import csv, hashlib, io, os, secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, ForeignKey, Boolean, DateTime, select, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from ortools.sat.python import cp_model
URL=os.getenv('DATABASE_URL','sqlite:///'+str(Path(__file__).resolve().parent/'smartseat.db'))
engine=create_engine(URL,connect_args={'check_same_thread':False,'timeout':30} if URL.startswith('sqlite') else {})
ph=PasswordHasher()
ORIGINS=os.getenv('FRONTEND_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',')
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    usn:Mapped[str]=mapped_column(String(80),unique=True)
    name:Mapped[str]=mapped_column(String(120))
    branch:Mapped[str]=mapped_column(String(80),default='')
    role:Mapped[str]=mapped_column(String(20),default='student')
    password:Mapped[str]=mapped_column(String(255))
    must_change:Mapped[bool]=mapped_column(Boolean,default=True)
class LoginSession(Base):
    __tablename__='sessions'
    token:Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    expires:Mapped[datetime]=mapped_column(DateTime)
class Room(Base):
    __tablename__='rooms'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    rows:Mapped[int]=mapped_column(Integer)
    benches:Mapped[int]=mapped_column(Integer)
    seats:Mapped[int]=mapped_column(Integer)
class Exam(Base):
    __tablename__='exams'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(160))
    starts:Mapped[str]=mapped_column(String(40))
    duration:Mapped[int]=mapped_column(Integer,default=180)
    published:Mapped[bool]=mapped_column(Boolean,default=False)
    behind:Mapped[bool]=mapped_column(Boolean,default=True)
class Enrollment(Base):
    __tablename__='enrollments'
    exam_id:Mapped[int]=mapped_column(ForeignKey('exams.id'),primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    paper:Mapped[str]=mapped_column(String(120))
class Allocation(Base):
    __tablename__='allocations'
    exam_id:Mapped[int]=mapped_column(ForeignKey('exams.id'),primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    room_id:Mapped[int]=mapped_column(ForeignKey('rooms.id'))
    row:Mapped[int]=mapped_column(Integer)
    bench:Mapped[int]=mapped_column(Integer)
    seat:Mapped[int]=mapped_column(Integer)

app=FastAPI(title='SmartSeat API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
def db(request:Request):
    with Session(engine) as s:
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in ('/api/assistant/chat','/api/rooms/detect-photo','/api/rooms/stitch-frames'):
            if engine.dialect.name=='postgresql':s.connection().exec_driver_sql('SELECT pg_advisory_xact_lock(739281)')
            elif engine.dialect.name=='sqlite':s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        yield s
def current(request:Request,s:Session=Depends(db)):
    token=request.cookies.get('smartseat_session','')
    sess=s.get(LoginSession,hashlib.sha256(token.encode()).hexdigest())
    if not sess or sess.expires<datetime.now(timezone.utc).replace(tzinfo=None): raise HTTPException(401,'Please sign in.')
    user=s.get(User,sess.user_id)
    if not user: raise HTTPException(401,'Please sign in.')
    if is_archived(s,'student',user.id):raise HTTPException(403,'This account has been archived. Contact your teacher.')
    return user
def ready(user:User=Depends(current)):
    if user.must_change: raise HTTPException(403,'Change your temporary password first.')
    return user
def admin(user:User=Depends(ready)):
    if user.role!='admin': raise HTTPException(403,'Teacher access required.')
    return user
def public(u):return dict(id=u.id,usn=u.usn,name=u.name,branch=u.branch,role=u.role,must_change=u.must_change)
def get_exam(s,eid):
    e=s.get(Exam,eid)
    if not e or is_archived(s,'exam',eid):raise HTTPException(404,'Exam not found or archived.')
    return e
@app.middleware('http')
async def guard(request:Request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') and request.headers['origin'] not in ORIGINS:
        return JSONResponse({'detail':'Untrusted request origin.'},status_code=403)
    session_token=request.cookies.get('smartseat_session')
    if session_token and request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in ('/api/auth/login','/api/auth/setup'):
        expected=hashlib.sha256(('csrf:'+session_token).encode()).hexdigest()
        if not secrets.compare_digest(request.headers.get('x-csrf-token',''),expected):return JSONResponse({'detail':'Session protection expired. Refresh the page and try again.'},status_code=403)
    audit_actor='anonymous'
    if request.method not in ('GET','HEAD','OPTIONS') and session_token:
        with Session(engine) as audit_session:
            record=audit_session.get(LoginSession,hashlib.sha256(session_token.encode()).hexdigest())
            account=audit_session.get(User,record.user_id) if record else None
            if account:audit_actor=account.usn
    response=await call_next(request)
    if request.method not in ('GET','HEAD','OPTIONS') and response.status_code<400 and request.url.path.startswith('/api/'):
        from backend.extensions import Audit
        try:
            with Session(engine) as audit_session:
                audit_session.add(Audit(actor=audit_actor,action=request.method+' '+request.url.path[:100],target='api',created=datetime.now(timezone.utc).isoformat()));audit_session.commit()
        except Exception:
            import logging
            logging.getLogger(__name__).error('Could not store API audit event')
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Permissions-Policy']='camera=(self), microphone=()'
    return response
class Credentials(BaseModel):
    usn:str=Field(min_length=1,max_length=80)
    password:str=Field(min_length=1,max_length=128)
attempts={}
@app.post('/api/auth/login')
def login(data:Credentials,request:Request,response:Response,s:Session=Depends(db)):
    now=datetime.now(timezone.utc).timestamp();key=(request.client.host if request.client else 'local',data.usn.strip().upper())
    if len(attempts)>10000:attempts.clear()
    history=[t for t in attempts.get(key,[]) if now-t<300]
    if len(history)>=10:raise HTTPException(429,'Too many attempts. Try again in five minutes.')
    if os.getenv('REDIS_URL'):
        try:
            from redis import Redis
            client=Redis.from_url(os.environ['REDIS_URL'],socket_timeout=3)
            count=client.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],300) end; return n",1,'login:'+hashlib.sha256(str(key).encode()).hexdigest())
            if count>10:raise HTTPException(429,'Too many attempts. Try again in five minutes.')
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Login protection is temporarily unavailable. Try again shortly.')
    attempts[key]=history+[now]
    user=s.scalar(select(User).where(User.usn==data.usn.strip().upper()))
    try:valid=user is not None and ph.verify(user.password,data.password)
    except (VerifyMismatchError,InvalidHashError):valid=False
    if not valid:raise HTTPException(401,'Incorrect USN or password.')
    attempts.pop(key,None);token=secrets.token_urlsafe(48)
    s.add(LoginSession(token=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(hours=8)));s.commit()
    response.set_cookie('smartseat_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','false').lower()=='true',samesite='lax',max_age=28800,path='/')
    response.set_cookie('smartseat_csrf',hashlib.sha256(('csrf:'+token).encode()).hexdigest(),secure=os.getenv('COOKIE_SECURE','false').lower()=='true',samesite='lax',max_age=28800,path='/')
    return public(user)
@app.get('/api/auth/me')
def me(user:User=Depends(current)):return public(user)
@app.post('/api/auth/logout')
def logout(request:Request,response:Response,s:Session=Depends(db)):
    token=request.cookies.get('smartseat_session','');s.execute(delete(LoginSession).where(LoginSession.token==hashlib.sha256(token.encode()).hexdigest()));s.commit();response.delete_cookie('smartseat_session',path='/');response.delete_cookie('smartseat_csrf',path='/');return {'ok':True}
class PasswordChange(BaseModel):
    old_password:str
    new_password:str=Field(min_length=10,max_length=128)
@app.post('/api/auth/password')
def password(data:PasswordChange,response:Response,user:User=Depends(current),s:Session=Depends(db)):
    try:ph.verify(user.password,data.old_password)
    except (VerifyMismatchError,InvalidHashError):raise HTTPException(400,'Current password is incorrect.')
    if data.old_password==data.new_password:raise HTTPException(400,'Choose a different password.')
    stored=s.get(User,user.id);stored.password=ph.hash(data.new_password);stored.must_change=False
    s.execute(delete(LoginSession).where(LoginSession.user_id==user.id));s.commit();response.delete_cookie('smartseat_session',path='/');response.delete_cookie('smartseat_csrf',path='/');return {'ok':True}
class StudentInput(BaseModel):
    usn:str=Field(min_length=1,max_length=80)
    name:str=Field(min_length=1,max_length=120)
    branch:str=Field(min_length=1,max_length=80)
    password:str|None=Field(default=None,min_length=10,max_length=128)
@app.get('/api/students')
def students(_:User=Depends(admin),s:Session=Depends(db)):
    required=required_students(s)
    return [{**public(u),'accessible':u.id in required} for u in s.scalars(select(User).where(User.role=='student').order_by(User.usn)) if not is_archived(s,'student',u.id)]
@app.post('/api/students')
def add_student(data:StudentInput,_:User=Depends(admin),s:Session=Depends(db)):
    usn=data.usn.strip().upper()
    if not usn or not data.name.strip() or not data.branch.strip():raise HTTPException(400,'All fields are required.')
    if s.scalar(select(User).where(User.usn==usn)):raise HTTPException(409,'USN already exists.')
    temporary=data.password or secrets.token_urlsafe(12)
    u=User(usn=usn,name=data.name.strip(),branch=data.branch.strip().upper(),password=ph.hash(temporary));s.add(u);s.commit();return {**public(u),'temporary_password':temporary}
@app.post('/api/students/import')
async def import_students(file:UploadFile=File(...),_:User=Depends(admin),s:Session=Depends(db)):
    raw=await file.read(2_000_001)
    if len(raw)>2_000_000:raise HTTPException(400,'Maximum upload size is 2 MB.')
    try:
        if (file.filename or '').lower().endswith('.xlsx'):
            from zipfile import ZipFile
            with ZipFile(io.BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist())>20_000_000:raise ValueError('Expanded workbook is too large.')
            from openpyxl import load_workbook
            wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True);values=iter(wb.active.values)
            headers=[str(v).strip().lower() for v in next(values)];rows=[]
            for vals in values:
                if any(v is not None for v in vals):rows.append(dict(zip(headers,vals)))
                if len(rows)>1000:raise ValueError('Maximum 1,000 students per upload.')
            wb.close()
        else:rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        if not rows or len(rows)>1000:raise ValueError('Upload between 1 and 1,000 students.')
        seen=set(s.scalars(select(User.usn)));prepared=[];credentials=[]
        for n,row in enumerate(rows,2):
            data=StudentInput(**{k:str(row.get(k) or '').strip() for k in ('usn','name','branch')},password=str(row.get('password') or '').strip() or None);usn=data.usn.upper()
            if usn in seen:raise ValueError(f'Row {n}: duplicate USN {usn}. Nothing imported.')
            seen.add(usn);temporary=data.password or secrets.token_urlsafe(12)
            prepared.append(User(usn=usn,name=data.name,branch=data.branch.upper(),password=ph.hash(temporary)))
            credentials.append({'usn':usn,'name':data.name,'temporary_password':temporary})
        s.add_all(prepared);s.commit();return {'imported':len(prepared),'credentials':credentials}
    except Exception as exc:
        s.rollback();raise HTTPException(400,str(exc)[:500])
class RoomInput(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    rows:int=Field(ge=1,le=20)
    benches:int=Field(ge=1,le=12)
    seats:int=Field(ge=1,le=3)
@app.get('/api/rooms')
def rooms(_:User=Depends(admin),s:Session=Depends(db)):
    return [room_public(s,r) for r in s.scalars(select(Room).order_by(Room.name)) if not is_archived(s,'room',r.id)]
@app.post('/api/rooms')
def add_room(data:RoomInput,_:User=Depends(admin),s:Session=Depends(db)):
    if not data.name.strip():raise HTTPException(400,'Room name is required.')
    if s.scalar(select(Room).where(Room.name==data.name.strip())):raise HTTPException(409,'Room name already exists.')
    r=Room(**{**data.model_dump(),'name':data.name.strip()});s.add(r);s.commit();return {'id':r.id}
class ExamInput(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    starts:str
    duration:int=Field(ge=15,le=480)
    behind:bool=True
    student_ids:list[int]=Field(min_length=1,max_length=1000)
    papers:dict[str,str]=Field(default_factory=dict)
    room_ids:list[int]=Field(default_factory=list,max_length=30)
@app.post('/api/exams')
def create_exam(data:ExamInput,_:User=Depends(admin),s:Session=Depends(db)):
    try:
        parsed=datetime.fromisoformat(data.starts)
        if parsed.tzinfo is None:raise ValueError()
    except ValueError:raise HTTPException(400,'Supply a date with a timezone.')
    ids=set(data.student_ids);users=list(s.scalars(select(User).where(User.id.in_(ids),User.role=='student')))
    if any(is_archived(s,'student',u.id) for u in users):raise HTTPException(400,'Archived students cannot be enrolled.')
    if len(users)!=len(ids):raise HTTPException(400,'Some students do not exist.')
    if not data.name.strip():raise HTTPException(400,'Exam name is required.')
    e=Exam(name=data.name.strip(),starts=parsed.isoformat(),duration=data.duration,behind=data.behind);s.add(e);s.flush()
    for u in users:
        paper=data.papers.get(str(u.id),u.branch).strip().upper()
        if not paper or len(paper)>120:raise HTTPException(400,'Every student needs a valid paper code.')
        s.add(Enrollment(exam_id=e.id,user_id=u.id,paper=paper))
    if data.room_ids:
        chosen=list(s.scalars(select(Room).where(Room.id.in_(set(data.room_ids)))))
        if len(chosen)!=len(set(data.room_ids)):raise HTTPException(400,'Room not found.')
        entries=[(u.id,data.papers.get(str(u.id),u.branch).strip().upper()) for u in users]
        attach_layout(s,chosen)
        if any(is_archived(s,'room',r.id) for r in chosen):raise HTTPException(400,'Archived room selected.')
        output,error=solve_layout(chosen,entries,data.behind,required_ids=required_students(s))
        if error:raise HTTPException(422,error+' No exam was saved; correct the form and retry.')
        for uid,room,row,bench,seat in output:s.add(Allocation(exam_id=e.id,user_id=uid,room_id=room,row=row,bench=bench,seat=seat))
    s.commit();return {'id':e.id}
@app.get('/api/exams')
def exams(_:User=Depends(admin),s:Session=Depends(db)):
    return [dict(id=e.id,name=e.name,starts=e.starts,duration=e.duration,behind=e.behind,published=e.published,students=len(list(s.scalars(select(Enrollment).where(Enrollment.exam_id==e.id)))),allocated=len(list(s.scalars(select(Allocation).where(Allocation.exam_id==e.id))))) for e in s.scalars(select(Exam).order_by(Exam.id.desc())) if not is_archived(s,'exam',e.id)]
def validate_layout(output,papers,behind):
    if len(output)!=len(papers) or {a[0] for a in output}!=set(papers):raise ValueError('Missing or duplicate students')
    seats={};bench_groups=set()
    for uid,room,row,bench,seat in output:
        key=(room,row,bench,seat)
        if key in seats:raise ValueError('Duplicate seat')
        seats[key]=papers[uid];group=(room,row,bench,papers[uid])
        if group in bench_groups:raise ValueError('Bench conflict')
        bench_groups.add(group)
    if behind:
        for (room,row,bench,seat),paper in seats.items():
            if seats.get((room,row+1,bench,seat))==paper:raise ValueError('Front/back conflict')
def solve_layout(rooms,entries,behind=True,seconds=15,required_ids=None):
    required_ids=required_ids or set()
    slots=[(r.id,x['row'],x['bench'],x['seat']) for r in rooms for x in getattr(r,'_slots',grid(r)) if x['enabled']]
    accessible={(r.id,x['row'],x['bench'],x['seat']) for r in rooms for x in getattr(r,'_slots',grid(r)) if x['enabled'] and x['accessible']}
    if len(entries)>len(slots):return None,'Not enough seats. Add a room or select fewer students.'
    groups={}
    for uid,paper in entries:groups.setdefault(paper,[]).append(uid)
    if len(slots)*len(groups)>100000:return None,'Layout too large for the local solver. Split into smaller sessions.'
    m=cp_model.CpModel();v={(i,p):m.new_bool_var(f's{i}p{j}') for i in range(len(slots)) for j,p in enumerate(groups)}
    for i in range(len(slots)):m.add(sum(v[i,p] for p in groups)<=1)
    for p,users in groups.items():m.add(sum(v[i,p] for i in range(len(slots)))==len(users))
    benches={};lookup={slot:i for i,slot in enumerate(slots)}
    for i,slot in enumerate(slots):benches.setdefault(slot[:3],[]).append(i)
    for positions in benches.values():
        for p in groups:m.add(sum(v[i,p] for i in positions)<=1)
    if behind:
        for i,(room,row,bench,seat) in enumerate(slots):
            j=lookup.get((room,row+1,bench,seat))
            if j is not None:
                for p in groups:m.add(v[i,p]+v[j,p]<=1)
    for p,users in groups.items():m.add(sum(v[i,p] for i,slot in enumerate(slots) if slot in accessible)>=len(set(users)&required_ids))
    for r in rooms:
        for edge in getattr(r,'_edges',[]):
            a=lookup.get((r.id,*edge['a']));b=lookup.get((r.id,*edge['b']))
            if a is not None and b is not None:
                for p in groups:m.add(v[a,p]+v[b,p]<=1)
    solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=seconds;solver.parameters.num_search_workers=4;status=solver.solve(m)
    if status==cp_model.INFEASIBLE:return None,'No valid arrangement exists. Add rooms, reduce the largest paper group, or create an exam without the front/back rule.'
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):return None,'Search time limit reached. A solution may exist; try a smaller session.'
    output=[]
    for p,users in groups.items():
        chosen=[slot for i,slot in enumerate(slots) if solver.value(v[i,p])]
        required=[uid for uid in users if uid in required_ids];others=[uid for uid in users if uid not in required_ids]
        chosen.sort(key=lambda slot:slot not in accessible)
        for uid,slot in zip(required+others,chosen):output.append((uid,*slot))
    validate_layout(output,dict(entries),behind);return output,None
class GenerateInput(BaseModel):room_ids:list[int]=Field(min_length=1,max_length=30)
@app.post('/api/exams/{eid}/generate')
def generate(eid:int,data:GenerateInput,_:User=Depends(admin),s:Session=Depends(db)):
    e=get_exam(s,eid)
    if e.published:raise HTTPException(409,'Unpublish before regenerating seats.')
    selected=list(s.scalars(select(Room).where(Room.id.in_(set(data.room_ids))).order_by(Room.id)))
    if len(selected)!=len(set(data.room_ids)):raise HTTPException(400,'Room not found.')
    entries=[(en.user_id,en.paper) for en in s.scalars(select(Enrollment).where(Enrollment.exam_id==eid).order_by(Enrollment.user_id))]
    if any(is_archived(s,'student',uid) for uid,_ in entries):raise HTTPException(409,'This draft includes an archived student. Restore that student or create a new exam without them.')
    attach_layout(s,selected)
    if any(is_archived(s,'room',r.id) for r in selected):raise HTTPException(400,'Archived room selected.')
    output,error=solve_layout(selected,entries,e.behind,required_ids=required_students(s))
    if error:raise HTTPException(422,error)
    from backend.extensions import Attendance
    s.execute(delete(Attendance).where(Attendance.exam_id==eid))
    s.execute(delete(Allocation).where(Allocation.exam_id==eid))
    for uid,room,row,bench,seat in output:s.add(Allocation(exam_id=eid,user_id=uid,room_id=room,row=row,bench=bench,seat=seat))
    s.commit();return {'allocated':len(output)}
@app.get('/api/exams/{eid}/chart')
def chart(eid:int,_:User=Depends(admin),s:Session=Depends(db)):
    get_exam(s,eid);output=[]
    query=select(Allocation,User,Room,Enrollment).join(User,User.id==Allocation.user_id).join(Room,Room.id==Allocation.room_id).join(Enrollment,(Enrollment.user_id==Allocation.user_id)&(Enrollment.exam_id==Allocation.exam_id)).where(Allocation.exam_id==eid).order_by(Allocation.room_id,Allocation.row,Allocation.bench,Allocation.seat)
    for a,u,r,en in s.execute(query):output.append(dict(usn=u.usn,name=u.name,paper=en.paper,room=r.name,room_id=r.id,rows=r.rows,benches=r.benches,seats=r.seats,row=a.row,bench=a.bench,seat=a.seat,slots=room_config(s,r)['slots']))
    return output
class PublishInput(BaseModel):published:bool
@app.post('/api/exams/{eid}/publish')
def publish(eid:int,data:PublishInput,_:User=Depends(admin),s:Session=Depends(db)):
    e=get_exam(s,eid)
    if data.published:
        papers=dict(s.execute(select(Enrollment.user_id,Enrollment.paper).where(Enrollment.exam_id==eid)).all());allocations=list(s.scalars(select(Allocation).where(Allocation.exam_id==eid)))
        try:validate_layout([(a.user_id,a.room_id,a.row,a.bench,a.seat) for a in allocations],papers,e.behind)
        except (AssertionError,ValueError):raise HTTPException(409,'Generate a complete, valid allocation before publishing.')
        if not papers:raise HTTPException(409,'No students enrolled.')
        validate_extended(s,[(a.user_id,a.room_id,a.row,a.bench,a.seat) for a in allocations],papers)
    if data.published:
        start=datetime.fromisoformat(e.starts);end=start+timedelta(minutes=e.duration)
        own_rooms={a.room_id for a in allocations};own_students=set(papers)
        for other in s.scalars(select(Exam).where(Exam.published==True,Exam.id!=e.id)):
            other_start=datetime.fromisoformat(other.starts);other_end=other_start+timedelta(minutes=other.duration)
            if start<other_end and other_start<end:
                other_allocations=list(s.scalars(select(Allocation).where(Allocation.exam_id==other.id)))
                if own_rooms & {a.room_id for a in other_allocations}:raise HTTPException(409,f'Classroom conflict with published exam: {other.name}. Choose another room or time.')
                if own_students & {a.user_id for a in other_allocations}:raise HTTPException(409,f'Student timetable conflict with published exam: {other.name}.')
    e.published=data.published;s.commit();return {'published':e.published}
@app.get('/api/exams/{eid}/export/{kind}')
def export(eid:int,kind:str,user:User=Depends(admin),s:Session=Depends(db)):
    e=get_exam(s,eid);rows=chart(eid,user,s)
    if not rows:raise HTTPException(400,'Generate seating first.')
    headers=['USN','Name','Paper','Room','Row','Bench','Seat'];values=[[r[k] for k in ('usn','name','paper','room','row','bench','seat')] for r in rows];out=io.BytesIO()
    if kind=='xlsx':
        from openpyxl import Workbook
        wb=Workbook();ws=wb.active;ws.title='Seating';ws.append(headers)
        for row in values:
            ws.append(row)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value,str):cell.data_type='s'
        ws.freeze_panes='A2'
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=22
        wb.save(out);media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif kind=='pdf':
        from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from xml.sax.saxutils import escape
        styles=getSampleStyleSheet();safe=[[Paragraph(escape(str(c)),styles['BodyText']) for c in row] for row in values]
        table=Table([headers]+safe,repeatRows=1,colWidths=[80,105,65,80,35,45,35]);table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#173d35')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#d8e1dd'))]))
        SimpleDocTemplate(out).build([Paragraph(escape(e.name)+' - seating list',styles['Title']),Spacer(1,16),table]);media='application/pdf'
    else:raise HTTPException(404,'Choose pdf or xlsx.')
    out.seek(0);return StreamingResponse(out,media_type=media,headers={'Content-Disposition':f'attachment; filename="exam-{eid}.{kind}"'})
@app.get('/api/student/exams')
def student_exams(user:User=Depends(ready),s:Session=Depends(db)):
    if user.role!='student':raise HTTPException(403,'Student access required.')
    results=[]
    for en,e in s.execute(select(Enrollment,Exam).join(Exam,Exam.id==Enrollment.exam_id).where(Enrollment.user_id==user.id).order_by(Exam.starts)):
        item=dict(id=e.id,name=e.name,starts=e.starts,duration=e.duration,paper=en.paper,published=e.published,allocation=None)
        if e.published:
            a=s.get(Allocation,(e.id,user.id))
            if a:
                r=s.get(Room,a.room_id);item['allocation']=dict(room=r.name,row=a.row,bench=a.bench,seat=a.seat,rows=r.rows,benches=r.benches,seats=r.seats,slots=room_config(s,r)['slots'])
        if not is_archived(s,'exam',e.id):results.append(item)
    return results
@app.get('/api/health')
def health():return {'status':'ok'}

class SetupInput(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    password:str=Field(min_length=10,max_length=128)
@app.get('/api/auth/setup')
def setup_status(s:Session=Depends(db)):
    return {'required':s.scalar(select(User).where(User.role=='admin')) is None}
@app.post('/api/auth/setup')
def setup(data:SetupInput,s:Session=Depends(db)):
    if s.scalar(select(User).where(User.role=='admin')):raise HTTPException(409,'A teacher account already exists.')
    if not data.name.strip():raise HTTPException(400,'Teacher name is required.')
    from sqlalchemy.exc import IntegrityError
    s.add(User(usn='ADMIN',name=data.name.strip(),role='admin',branch='',password=ph.hash(data.password),must_change=False))
    try:s.commit()
    except IntegrityError:
        s.rollback();raise HTTPException(409,'Setup already completed. Please sign in.')
    return {'usn':'ADMIN'}

@app.post('/api/rooms/detect-photo')
def detect_photo(file:UploadFile=File(...),confidence:float=Form(.2,ge=.1,le=.6),enhanced:bool=Form(True),room_type:str=Form('classroom',pattern='^(classroom|computer_lab|benches)$'),_:User=Depends(admin)):
    raw=file.file.read(8_000_001)
    if len(raw)>8_000_000:raise HTTPException(400,'Photo must be under 8 MB.')
    from backend.vision import detect
    return detect(raw,confidence=confidence,enhanced=enhanced,room_type=room_type)

from backend.extensions import (router as management_router, is_archived, required_students, grid, attach_layout, room_public, room_config, validate_extended)
app.include_router(management_router)
if os.getenv('AUTO_CREATE_TABLES','true').lower()=='true':Base.metadata.create_all(engine)

@app.get('/api/auth/csrf')
def csrf(request:Request,response:Response,user:User=Depends(current)):
    token=request.cookies['smartseat_session']
    response.set_cookie('smartseat_csrf',hashlib.sha256(('csrf:'+token).encode()).hexdigest(),secure=os.getenv('COOKIE_SECURE','false').lower()=='true',samesite='lax',max_age=28800,path='/')
    return {'ok':True}

from backend.assistant import router as assistant_router
app.include_router(assistant_router)

@app.post('/api/rooms/stitch-frames')
def stitch_frames(files:list[UploadFile]=File(...),_:User=Depends(admin)):
    if not 2<=len(files)<=6:raise HTTPException(400,'Provide between two and six overlapping views.')
    images=[f.file.read(4_000_001) for f in files]
    if any(len(raw)>4_000_000 for raw in images):raise HTTPException(400,'Each frame must be under 4 MB.')
    from backend.vision import stitch_frames as merge
    return merge(images)
