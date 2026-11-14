import os,tempfile
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from backend.main import app,engine,User,ph,Room,solve_layout,validate_layout

def test_solver_valid_and_impossible():
    room=Room(id=1,name='A',rows=2,benches=2,seats=2)
    entries=[(i,'A' if i<=4 else 'B') for i in range(1,9)]
    result,error=solve_layout([room],entries)
    assert error is None
    validate_layout(result,dict(entries),True)
    result,error=solve_layout([room],[(i,'A') for i in range(1,6)])
    assert result is None and 'No valid' in error
    result,error=solve_layout([room],[(i,str(i)) for i in range(9)])
    assert result is None and 'Not enough' in error

def test_three_seats_and_multiple_rooms():
    rooms=[Room(id=i,name=str(i),rows=2,benches=1,seats=3) for i in [1,2]]
    entries=[(i,['A','B','C'][i%3]) for i in range(12)]
    result,error=solve_layout(rooms,entries)
    assert error is None
    validate_layout(result,dict(entries),True)
    assert {r[1] for r in result}=={1,2}

def test_end_to_end_and_privacy():
    client=TestClient(app)
    assert client.get('/api/auth/setup').json()['required'] is True
    assert client.post('/api/auth/setup',json={'name':'Teacher','password':'TeacherPass123'}).status_code==200
    assert client.get('/api/auth/setup').json()['required'] is False
    assert client.post('/api/auth/setup',json={'name':'Other','password':'TeacherPass123'}).status_code==409
    assert client.get('/api/rooms').status_code==401
    assert client.post('/api/auth/login',json={'usn':'ADMIN','password':'TeacherPass123'}).status_code==200
    ids=[]
    for n,branch in enumerate(['CSE','ECE','CSE','ECE']):
        r=client.post('/api/students',json={'usn':f'S{n}','name':f'Student {n}','branch':branch,'password':'StudentPass123'})
        assert r.status_code==200;ids.append(r.json()['id'])
    assert client.post('/api/students',json={'usn':'S0','name':'Duplicate','branch':'CSE','password':'StudentPass123'}).status_code==409
    rid=client.post('/api/rooms',json={'name':'A101','rows':2,'benches':1,'seats':2}).json()['id']
    eid=client.post('/api/exams',json={'name':'Test exam','starts':'2026-12-01T09:00:00+05:30','duration':180,'student_ids':ids}).json()['id']
    assert client.post(f'/api/exams/{eid}/publish',json={'published':True}).status_code==409
    assert client.post(f'/api/exams/{eid}/generate',json={'room_ids':[rid]}).status_code==200
    assert len(client.get(f'/api/exams/{eid}/chart').json())==4
    for kind in ['pdf','xlsx']:assert client.get(f'/api/exams/{eid}/export/{kind}').status_code==200
    student=TestClient(app)
    assert student.post('/api/auth/login',json={'usn':'S0','password':'StudentPass123'}).status_code==200
    assert student.get('/api/student/exams').status_code==403
    assert student.post('/api/auth/password',json={'old_password':'StudentPass123','new_password':'MyNewPassword123'}).status_code==200
    assert student.get('/api/auth/me').status_code==401
    student.post('/api/auth/login',json={'usn':'S0','password':'MyNewPassword123'})
    assert student.get('/api/rooms').status_code==403
    assert student.get('/api/student/exams').json()[0]['allocation'] is None
    assert client.post(f'/api/exams/{eid}/publish',json={'published':True}).status_code==200
    result=student.get('/api/student/exams').json()[0]
    assert result['allocation']['room']=='A101'
    assert 'S1' not in str(result)
    assert student.get(f'/api/exams/{eid}/chart').status_code==403
    assert student.get(f'/api/exams/{eid}/export/pdf').status_code==403
    assert client.post(f'/api/exams/{eid}/generate',json={'room_ids':[rid]}).status_code==409
    assert client.post('/api/rooms',headers={'Origin':'https://evil.invalid'},json={'name':'BAD','rows':1,'benches':1,'seats':2}).status_code==403
    raw='usn,name,branch,password\nNEW,New student,EEE,Temporary123\nS0,Duplicate,CSE,Temporary123\n'
    assert client.post('/api/students/import',files={'file':('test.csv',raw,'text/csv')}).status_code==400
    assert len(client.get('/api/students').json())==4

    second=client.post('/api/exams',json={'name':'Overlapping exam','starts':'2026-12-01T10:00:00+05:30','duration':180,'student_ids':ids}).json()['id']
    assert client.post(f'/api/exams/{second}/generate',json={'room_ids':[rid]}).status_code==200
    assert client.post(f'/api/exams/{second}/publish',json={'published':True}).status_code==409
    assert client.post(f'/api/exams/{eid}/publish',json={'published':False}).status_code==200
    assert student.get('/api/student/exams').json()[0]['allocation'] is None

def test_twenty_student_target():
    room=Room(id=1,name='A101',rows=5,benches=2,seats=2)
    entries=[(i,'CSE' if i%2 else 'ECE') for i in range(1,21)]
    result,error=solve_layout([room],entries)
    assert error is None and len(result)==20
    validate_layout(result,dict(entries),True)

def test_streamlined_creation_and_auto_passwords():
    c=TestClient(app);assert c.post('/api/auth/login',json={'usn':'ADMIN','password':'TeacherPass123'}).status_code==200
    raw='usn,name,branch\nAUTO1,First,CSE\nAUTO2,Second,ECE\n'
    r=c.post('/api/students/import',files={'file':('new.csv',raw,'text/csv')})
    assert r.status_code==200 and r.json()['imported']==2
    slips=r.json()['credentials'];assert len(slips)==2 and slips[0]['temporary_password']!=slips[1]['temporary_password']
    u=TestClient(app);assert u.post('/api/auth/login',json={'usn':'AUTO1','password':slips[0]['temporary_password']}).status_code==200
    assert u.get('/api/students').status_code==403
    listing=c.get('/api/students').json();assert 'temporary_password' not in str(listing)
    ids=[s['id'] for s in listing if s['usn'].startswith('AUTO')]
    rid=c.post('/api/rooms',json={'name':'Quick room','rows':1,'benches':1,'seats':2}).json()['id']
    body={'name':'Quick exam','starts':'2027-01-01T09:00:00+05:30','duration':120,'student_ids':ids,'room_ids':[rid]}
    eid=c.post('/api/exams',json=body).json()['id']
    assert len(c.get(f'/api/exams/{eid}/chart').json())==2
    assert next(e for e in c.get('/api/exams').json() if e['id']==eid)['published'] is False
    count=len(c.get('/api/exams').json());body['papers']={str(i):'SAME' for i in ids}
    assert c.post('/api/exams',json=body).status_code==422
    assert len(c.get('/api/exams').json())==count
    assert TestClient(app).post('/api/rooms/detect-photo',files={'file':('fake.jpg',b'bad','image/jpeg')}).status_code==401
    assert c.post('/api/rooms/detect-photo',files={'file':('fake.jpg',b'bad','image/jpeg')}).status_code==400

def test_photo_duplicate_filter():
    from backend.vision import remove_duplicates
    boxes=[{'box':[.1,.1,.4,.4],'score':.9},{'box':[.11,.11,.39,.39],'score':.7},{'box':[.6,.6,.9,.9],'score':.8}]
    assert len(remove_duplicates(boxes))==2
