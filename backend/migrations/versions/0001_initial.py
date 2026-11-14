"""Initial schema; adopts matching legacy SQLite tables without dropping data."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    tables=[
      ('users',[sa.Column('id',sa.Integer,primary_key=True),sa.Column('usn',sa.String(80),nullable=False,unique=True),sa.Column('name',sa.String(120),nullable=False),sa.Column('branch',sa.String(80),nullable=False),sa.Column('role',sa.String(20),nullable=False),sa.Column('password',sa.String(255),nullable=False),sa.Column('must_change',sa.Boolean,nullable=False)]),
      ('sessions',[sa.Column('token',sa.String(64),primary_key=True),sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id'),nullable=False),sa.Column('expires',sa.DateTime,nullable=False)]),
      ('rooms',[sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(120),nullable=False,unique=True),sa.Column('rows',sa.Integer,nullable=False),sa.Column('benches',sa.Integer,nullable=False),sa.Column('seats',sa.Integer,nullable=False)]),
      ('exams',[sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(160),nullable=False),sa.Column('starts',sa.String(40),nullable=False),sa.Column('duration',sa.Integer,nullable=False),sa.Column('published',sa.Boolean,nullable=False),sa.Column('behind',sa.Boolean,nullable=False)]),
      ('enrollments',[sa.Column('exam_id',sa.Integer,sa.ForeignKey('exams.id'),primary_key=True),sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id'),primary_key=True),sa.Column('paper',sa.String(120),nullable=False)]),
      ('allocations',[sa.Column('exam_id',sa.Integer,sa.ForeignKey('exams.id'),primary_key=True),sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id'),primary_key=True),sa.Column('room_id',sa.Integer,sa.ForeignKey('rooms.id'),nullable=False),sa.Column('row',sa.Integer,nullable=False),sa.Column('bench',sa.Integer,nullable=False),sa.Column('seat',sa.Integer,nullable=False)])]
    for name,columns in tables:
        inspector=sa.inspect(op.get_bind())
        if inspector.has_table(name):
            actual={c['name'] for c in inspector.get_columns(name)}
            if not {c.name for c in columns}<=actual:raise RuntimeError('Legacy table '+name+' has an incompatible schema. Back up and migrate manually.')
        else:op.create_table(name,*columns)
def downgrade():raise RuntimeError('Use a database backup to restore an older version; destructive downgrade is disabled.')
