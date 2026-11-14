"""Flexible layouts, support needs, archives, attendance and audit history."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None
def upgrade():
    tables=[
      ('room_layouts',[sa.Column('room_id',sa.Integer,sa.ForeignKey('rooms.id'),primary_key=True),sa.Column('data',sa.Text,nullable=False)]),
      ('student_support',[sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id'),primary_key=True),sa.Column('accessible',sa.Boolean,nullable=False)]),
      ('archived_records',[sa.Column('kind',sa.String(20),primary_key=True),sa.Column('record_id',sa.Integer,primary_key=True)]),
      ('attendance',[sa.Column('exam_id',sa.Integer,sa.ForeignKey('exams.id'),primary_key=True),sa.Column('user_id',sa.Integer,sa.ForeignKey('users.id'),primary_key=True),sa.Column('status',sa.String(15),nullable=False)]),
      ('audit_events',[sa.Column('id',sa.Integer,primary_key=True),sa.Column('actor',sa.String(80),nullable=False),sa.Column('action',sa.String(120),nullable=False),sa.Column('target',sa.String(120),nullable=False),sa.Column('created',sa.String(40),nullable=False)])]
    for name,columns in tables:
        if not sa.inspect(op.get_bind()).has_table(name):op.create_table(name,*columns)
def downgrade():raise RuntimeError('Restore from backup instead of dropping management data.')
