"""Groq via LangChain. The model proposes; validated application APIs execute."""
import os,json
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from sqlalchemy import select
from backend import main as c
from backend.extensions import is_archived,room_public
router=APIRouter(prefix='/api/assistant')
class Draft(BaseModel):
    name:str=Field(max_length=160)
    starts:str=Field(description='ISO datetime with numeric timezone offset')
    duration:int=Field(ge=15,le=480)
    behind:bool=True
    branches:list[str]=Field(description='Exact branch codes selected from context')
    paper_codes:dict[str,str]=Field(description='Actual exam paper code per selected branch')
class Reply(BaseModel):
    answer:str
    draft:Draft|None=None
class Chat(BaseModel):message:str=Field(min_length=1,max_length=3000)
@router.get('/status')
def status(_:c.User=Depends(c.admin)):
    return {'configured':bool(os.getenv('GROQ_API_KEY')),'provider':'Groq','model':os.getenv('GROQ_MODEL','llama-3.3-70b-versatile')}
@router.post('/chat')
def chat(data:Chat,_:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    if not os.getenv('GROQ_API_KEY'):raise HTTPException(503,'Add GROQ_API_KEY to your local .env and restart the backend to activate the assistant.')
    students=[u for u in s.scalars(select(c.User).where(c.User.role=='student')) if not is_archived(s,'student',u.id)]
    branches={b:sum(u.branch==b for u in students) for b in sorted({u.branch for u in students})}
    rooms=[room_public(s,r) for r in s.scalars(select(c.Room)) if not is_archived(s,'room',r.id)]
    context={'current_utc':datetime.now(timezone.utc).isoformat(),'campus_timezone':'Asia/Kolkata','branch_counts':branches,'rooms':[{'name':r['name'],'capacity':r['capacity']} for r in rooms],'published_exams':sum(e.published for e in s.scalars(select(c.Exam)) if not is_archived(s,'exam',e.id))}
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage,HumanMessage
        model=ChatGroq(model=os.getenv('GROQ_MODEL','llama-3.3-70b-versatile'),temperature=0,timeout=45,max_retries=1,max_tokens=1800)
        planner=model.with_structured_output(Reply,method='function_calling')
        reply=planner.invoke([SystemMessage(content='You are SmartSeat teacher assistant. Answer simply using the trusted campus summary below. No student identity or password is available. You cannot publish, reset passwords, delete, or change data. To prepare an exam, return a DRAFT only when the user supplies a date, time and actual paper codes for selected branches. Ask for missing information. Never assume branch equals paper. Use Asia/Kolkata unless the user specifies another timezone. Reject unsupported requests honestly. Classroom capacity does not prove seating feasibility. The teacher reviews your proposal before creating a draft through the seating solver. Campus summary: '+json.dumps(context)),HumanMessage(content=data.message)])
        if reply.draft:
            if not reply.draft.branches or any(b not in branches for b in reply.draft.branches):raise ValueError('Invalid branch in model proposal')
            if any(not reply.draft.paper_codes.get(b,'').strip() for b in reply.draft.branches):raise ValueError('Missing paper code')
            start=datetime.fromisoformat(reply.draft.starts)
            if not start.tzinfo:raise ValueError('Missing timezone')
        return reply.model_dump()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('Assistant request failed (%s)',type(exc).__name__)
        raise HTTPException(502,'The assistant could not produce a valid response. Check your Groq configuration or try a clearer request.')
class Prepare(BaseModel):draft:Draft
@router.post('/prepare')
def prepare(data:Prepare,actor:c.User=Depends(c.admin),s:Session=Depends(c.db)):
    draft=data.draft;users=[u for u in s.scalars(select(c.User).where(c.User.role=='student',c.User.branch.in_(draft.branches))) if not is_archived(s,'student',u.id)]
    rooms=[r for r in s.scalars(select(c.Room)) if not is_archived(s,'room',r.id)]
    if not users or not rooms:raise HTTPException(400,'Add active students and rooms first.')
    if any(not draft.paper_codes.get(u.branch,'').strip() for u in users):raise HTTPException(400,'Provide a paper code for each selected branch.')
    payload=c.ExamInput(name=draft.name,starts=draft.starts,duration=draft.duration,behind=draft.behind,student_ids=[u.id for u in users],papers={str(u.id):draft.paper_codes[u.branch] for u in users},room_ids=[r.id for r in rooms])
    return c.create_exam(payload,actor,s)
