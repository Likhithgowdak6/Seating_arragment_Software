"""Management, flexible layouts, attendance, audit and assistant APIs."""
import json, secrets
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, Integer, Boolean, ForeignKey, Text, select, delete
from sqlalchemy.orm import Mapped, mapped_column, Session
from backend import main as c
router=APIRouter(prefix='/api')
class RoomLayout(c.Base):
    __tablename__='room_layouts'
    room_id:Mapped[int]=mapped_column(ForeignKey('rooms.id'),primary_key=True)
    data:Mapped[str]=mapped_column(Text)
class StudentSupport(c.Base):
    __tablename__='student_support'
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    accessible:Mapped[bool]=mapped_column(Boolean,default=False)
class Archived(c.Base):
    __tablename__='archived_records'
    kind:Mapped[str]=mapped_column(String(20),primary_key=True)
    record_id:Mapped[int]=mapped_column(Integer,primary_key=True)
class Attendance(c.Base):
    __tablename__='attendance'
    exam_id:Mapped[int]=mapped_column(ForeignKey('exams.id'),primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    status:Mapped[str]=mapped_column(String(15),default='unmarked')
class Audit(c.Base):
    __tablename__='audit_events'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(80))
    action:Mapped[str]=mapped_column(String(120))
    target:Mapped[str]=mapped_column(String(120))
    created:Mapped[str]=mapped_column(String(40))
def audit(s,actor,action,target):
    s.add(Audit(actor=actor.usn,action=action,target=str(target),created=datetime.now(timezone.utc).isoformat()))
def is_archived(s,kind,id):return s.get(Archived,(kind,id)) is not None
def required_students(s):return set(s.scalars(select(StudentSupport.user_id).where(StudentSupport.accessible==True)))
def grid(r):return [dict(row=row,bench=b,seat=seat,enabled=True,accessible=False) for row in range(1,r.rows+1) for b in range(1,r.benches+1) for seat in range(1,r.seats+1)]
def room_config(s,r):
    entry=s.get(RoomLayout,r.id)
    return json.loads(entry.data) if entry else {'slots':grid(r),'edges':[]}
def attach_layout(s,rooms):
    for r in rooms:
        cfg=room_config(s,r);r._slots=cfg['slots'];r._edges=cfg['edges']
    return rooms
def room_public(s,r):
    cfg=room_config(s,r)
    return dict(id=r.id,name=r.name,rows=r.rows,benches=r.benches,seats=r.seats,capacity=sum(x['enabled'] for x in cfg['slots']),slots=cfg['slots'],edges=cfg['edges'])
def ensure_editable(s,kind,id):
    if kind=='student':query=select(c.Exam).join(c.Enrollment,c.Enrollment.exam_id==c.Exam.id).where(c.Enrollment.user_id==id,c.Exam.published==True)
    else:query=select(c.Exam).join(c.Allocation,c.Allocation.exam_id==c.Exam.id).where(c.Allocation.room_id==id,c.Exam.published==True)
    if s.scalar(query):raise HTTPException(409,'This record is used by a published exam. Unpublish the affected exam before editing or archiving.')
def invalidate(s,kind,id):
    if kind=='student':
        exam_ids=list(s.scalars(select(c.Enrollment.exam_id).where(c.Enrollment.user_id==id)))
    else:exam_ids=list(s.scalars(select(c.Allocation.exam_id).where(c.Allocation.room_id==id)))
    if exam_ids:
        s.execute(delete(Attendance).where(Attendance.exam_id.in_(exam_ids)))
        s.execute(delete(c.Allocation).where(c.Allocation.exam_id.in_(exam_ids)))
class StudentEdit(BaseModel):
    usn:str=Field(min_length=1,max_length=80)
    name:str=Field(min_length=1,max_length=120)
    branch:str=Field(min_length=1,max_length=80)
    accessible:bool=False
@router.put('/students/{uid}')
def edit_student(uid:int,data:StudentEdit,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    u=s.get(c.User,uid)
    if not u or u.role!='student':raise HTTPException(404,'Student not found.')
    ensure_editable(s,'student',uid)
    if not all(v.strip() for v in (data.usn,data.name,data.branch)):raise HTTPException(400,'All student fields are required.')
    existing=s.scalar(select(c.User).where(c.User.usn==data.usn.strip().upper(),c.User.id!=uid))
    if existing:raise HTTPException(409,'USN already exists.')
    u.usn=data.usn.strip().upper();u.name=data.name.strip();u.branch=data.branch.strip().upper()
    support=s.get(StudentSupport,uid) or StudentSupport(user_id=uid);support.accessible=data.accessible;s.add(support)
    invalidate(s,'student',uid);audit(s,actor,'student.updated',uid);s.commit();return {'ok':True}
@router.post('/students/{uid}/reset-password')
def reset_student(uid:int,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    u=s.get(c.User,uid)
    if not u or u.role!='student' or is_archived(s,'student',uid):raise HTTPException(404,'Student not found.')
    temporary=secrets.token_urlsafe(14);u.password=c.ph.hash(temporary);u.must_change=True
    s.execute(delete(c.LoginSession).where(c.LoginSession.user_id==uid));audit(s,actor,'student.password_reset',uid);s.commit()
    return {'usn':u.usn,'name':u.name,'temporary_password':temporary}
class DeleteStudentInput(BaseModel):
    confirm_usn:str=Field(min_length=1,max_length=80)
@router.delete('/students/{uid}')
def delete_student(uid:int,data:DeleteStudentInput,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    user=s.get(c.User,uid)
    if not user or user.role!='student':raise HTTPException(404,'Student not found.')
    if data.confirm_usn.strip().upper()!=user.usn:raise HTTPException(400,'Enter the student USN exactly to confirm deletion.')
    published=s.scalar(select(c.Exam.id).join(c.Enrollment,c.Enrollment.exam_id==c.Exam.id).where(c.Enrollment.user_id==uid,c.Exam.published==True))
    if published is not None:raise HTTPException(409,'This student is linked to a published exam. Unpublish the affected exam before deleting the student, or keep the record archived.')
    # Removing one enrollment invalidates the whole affected draft seat plan.
    invalidate(s,'student',uid)
    s.execute(delete(Attendance).where(Attendance.user_id==uid))
    s.execute(delete(c.Allocation).where(c.Allocation.user_id==uid))
    s.execute(delete(c.Enrollment).where(c.Enrollment.user_id==uid))
    s.execute(delete(c.LoginSession).where(c.LoginSession.user_id==uid))
    s.execute(delete(StudentSupport).where(StudentSupport.user_id==uid))
    s.execute(delete(Archived).where(Archived.kind=='student',Archived.record_id==uid))
    audit(s,actor,'student.deleted',uid)
    s.delete(user);s.commit()
    return {'ok':True}
class Slot(BaseModel):
    row:int=Field(ge=1,le=20)
    bench:int=Field(ge=1,le=12)
    seat:int=Field(ge=1,le=3)
    enabled:bool=True
    accessible:bool=False
class Edge(BaseModel):
    a:tuple[int,int,int]
    b:tuple[int,int,int]
    kind:Literal['opposite','nearby']='opposite'
class LayoutInput(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    rows:int=Field(ge=1,le=20)
    benches:int=Field(ge=1,le=12)
    seats:int=Field(ge=1,le=3)
    slots:list[Slot]=Field(min_length=1,max_length=720)
    edges:list[Edge]=Field(default_factory=list,max_length=2000)
@router.put('/rooms/{rid}/layout')
def edit_layout(rid:int,data:LayoutInput,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    r=s.get(c.Room,rid)
    if not r:raise HTTPException(404,'Room not found.')
    ensure_editable(s,'room',rid)
    if not data.name.strip():raise HTTPException(400,'Room name is required.')
    if s.scalar(select(c.Room).where(c.Room.name==data.name.strip(),c.Room.id!=rid)):raise HTTPException(409,'Room name already exists.')
    keys={(x.row,x.bench,x.seat) for x in data.slots}
    if len(keys)!=len(data.slots) or any(x.row>data.rows or x.bench>data.benches or x.seat>data.seats for x in data.slots):raise HTTPException(400,'Seat coordinates must be unique and within the room bounds.')
    if any(tuple(e.a) not in keys or tuple(e.b) not in keys or e.a==e.b for e in data.edges):raise HTTPException(400,'Seat links must connect two different seats in this layout.')
    r.name=data.name.strip();r.rows=data.rows;r.benches=data.benches;r.seats=data.seats
    cfg=s.get(RoomLayout,rid) or RoomLayout(room_id=rid);cfg.data=json.dumps({'slots':[x.model_dump() for x in data.slots],'edges':[x.model_dump() for x in data.edges]});s.add(cfg)
    invalidate(s,'room',rid);audit(s,actor,'room.layout_updated',rid);s.commit();return room_public(s,r)
class ArchiveInput(BaseModel):archived:bool=True
@router.post('/records/{kind}/{rid}/archive')
def archive(kind:Literal['student','room','exam'],rid:int,data:ArchiveInput,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    model={'student':c.User,'room':c.Room,'exam':c.Exam}[kind];record=s.get(model,rid)
    if not record or (kind=='student' and record.role!='student'):raise HTTPException(404,'Record not found.')
    if data.archived:
        if kind=='exam':
            if record.published:raise HTTPException(409,'Unpublish this exam before archiving.')
        else:ensure_editable(s,kind,rid)
        if not is_archived(s,kind,rid):s.add(Archived(kind=kind,record_id=rid))
        if kind=='student':s.execute(delete(c.LoginSession).where(c.LoginSession.user_id==rid))
        if kind!='exam':invalidate(s,kind,rid)
    else:
        entry=s.get(Archived,(kind,rid))
        if entry:s.delete(entry)
    audit(s,actor,'record.archived' if data.archived else 'record.restored',f'{kind}:{rid}');s.commit();return {'ok':True}
@router.get('/records/archived')
def archived_records(_:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    output=[]
    for a in s.scalars(select(Archived)):
        model={'student':c.User,'room':c.Room,'exam':c.Exam}[a.kind];r=s.get(model,a.record_id)
        if r:output.append({'kind':a.kind,'id':a.record_id,'name':r.name})
    return output
class ExamEdit(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    starts:str
    duration:int=Field(ge=15,le=480)
@router.put('/exams/{eid}')
def edit_exam(eid:int,data:ExamEdit,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    e=c.get_exam(s,eid)
    if e.published:raise HTTPException(409,'Unpublish before editing.')
    try:
        start=datetime.fromisoformat(data.starts)
        if not start.tzinfo:raise ValueError()
    except ValueError:raise HTTPException(400,'Provide a timezone-aware exam date.')
    if not data.name.strip():raise HTTPException(400,'Exam name is required.')
    e.name=data.name.strip();e.starts=start.isoformat();e.duration=data.duration;audit(s,actor,'exam.updated',eid);s.commit();return {'ok':True}
@router.get('/exams/{eid}/attendance')
def get_attendance(eid:int,_:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    c.get_exam(s,eid);output=[]
    for en,u in s.execute(select(c.Enrollment,c.User).join(c.User,c.User.id==c.Enrollment.user_id).where(c.Enrollment.exam_id==eid).order_by(c.User.usn)):
        mark=s.get(Attendance,(eid,u.id));a=s.get(c.Allocation,(eid,u.id));r=s.get(c.Room,a.room_id) if a else None
        output.append(dict(user_id=u.id,usn=u.usn,name=u.name,paper=en.paper,status=mark.status if mark else 'unmarked',room=r.name if r else '',row=a.row if a else None,bench=a.bench if a else None,seat=a.seat if a else None))
    return output
class Mark(BaseModel):user_id:int;status:Literal['present','absent','unmarked']
class AttendanceInput(BaseModel):marks:list[Mark]=Field(max_length=1000)
@router.put('/exams/{eid}/attendance')
def mark_attendance(eid:int,data:AttendanceInput,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    e=c.get_exam(s,eid)
    if not e.published:raise HTTPException(409,'Publish the seating allocation before marking attendance.')
    for mark in data.marks:
        if not s.get(c.Enrollment,(eid,mark.user_id)):raise HTTPException(400,'Student is not enrolled in this exam.')
        entry=s.get(Attendance,(eid,mark.user_id)) or Attendance(exam_id=eid,user_id=mark.user_id);entry.status=mark.status;s.add(entry)
    audit(s,actor,'attendance.updated',eid);s.commit();return {'ok':True}
@router.get('/exams/{eid}/attendance/export')
def attendance_export(eid:int,user:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    import csv,io
    rows=get_attendance(eid,user,s);out=io.StringIO();writer=csv.writer(out);writer.writerow(['USN','Name','Paper','Room','Row','Bench','Seat','Attendance'])
    def safe(v):
        text=str(v or '');return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    for r in rows:writer.writerow([safe(r[k]) for k in ('usn','name','paper','room','row','bench','seat','status')])
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="attendance-{eid}.csv"'})
@router.get('/audit')
def get_audit(_:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    return [dict(id=e.id,actor=e.actor,action=e.action,target=e.target,created=e.created) for e in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))]
def validate_extended(s,output,papers):
    required=required_students(s);configs={}
    for uid,rid,row,b,seat in output:
        if rid not in configs:configs[rid]=room_config(s,s.get(c.Room,rid))
        slots={(x['row'],x['bench'],x['seat']):x for x in configs[rid]['slots']}
        slot=slots.get((row,b,seat))
        if not slot or not slot['enabled']:raise HTTPException(409,'Allocation uses an unavailable seat. Regenerate.')
        if uid in required and not slot['accessible']:raise HTTPException(409,'An accessibility requirement is unmet. Regenerate.')
    placed={(r,row,b,seat):papers[uid] for uid,r,row,b,seat in output}
    for rid,cfg in configs.items():
        for edge in cfg['edges']:
            a=placed.get((rid,*edge['a']));b=placed.get((rid,*edge['b']))
            if a is not None and a==b:raise HTTPException(409,'Same-paper conflict across linked seats. Regenerate.')

class Move(BaseModel):
    usn:str=Field(min_length=1,max_length=80)
    room_id:int
    row:int=Field(ge=1,le=20)
    bench:int=Field(ge=1,le=12)
    seat:int=Field(ge=1,le=3)
    swap:bool=True
@router.post('/exams/{eid}/move-seat')
def move_seat(eid:int,data:Move,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    exam=c.get_exam(s,eid)
    if exam.published:raise HTTPException(409,'Unpublish before adjusting seats.')
    user=s.scalar(select(c.User).where(c.User.usn==data.usn.strip().upper()))
    if not user or is_archived(s,'student',user.id):raise HTTPException(404,'Active student not found.')
    allocations=list(s.scalars(select(c.Allocation).where(c.Allocation.exam_id==eid)))
    own=next((a for a in allocations if a.user_id==user.id),None)
    if not own:raise HTTPException(400,'Generate seating for this student first.')
    room=s.get(c.Room,data.room_id)
    if not room or is_archived(s,'room',room.id):raise HTTPException(400,'Choose an active classroom.')
    target=(data.room_id,data.row,data.bench,data.seat)
    old=(own.room_id,own.row,own.bench,own.seat)
    other=next((a for a in allocations if (a.room_id,a.row,a.bench,a.seat)==target and a.user_id!=own.user_id),None)
    if other and not data.swap:raise HTTPException(409,'That seat is occupied.')
    output=[]
    for a in allocations:
        slot=target if a.user_id==own.user_id else old if other and a.user_id==other.user_id else (a.room_id,a.row,a.bench,a.seat)
        output.append((a.user_id,*slot))
    papers=dict(s.execute(select(c.Enrollment.user_id,c.Enrollment.paper).where(c.Enrollment.exam_id==eid)).all())
    try:c.validate_layout(output,papers,exam.behind)
    except ValueError as exc:raise HTTPException(409,'Seat change violates a strict rule: '+str(exc))
    validate_extended(s,output,papers)
    # The database enforces unique physical seats. Reinsert inside this transaction
    # so a swap cannot temporarily collide with the old occupant's seat.
    s.execute(delete(c.Allocation).where(c.Allocation.exam_id==eid));s.flush()
    for uid,rid,row,bench,seat in output:s.add(c.Allocation(exam_id=eid,user_id=uid,room_id=rid,row=row,bench=bench,seat=seat))
    s.execute(delete(Attendance).where(Attendance.exam_id==eid));audit(s,actor,'seating.adjusted',eid);s.commit();return {'ok':True}
