"""Enforce one allocation per physical seat in an exam."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None
def upgrade():
    existing={i['name'] for i in sa.inspect(op.get_bind()).get_indexes('allocations')}
    if 'uq_exam_physical_seat' not in existing:op.create_index('uq_exam_physical_seat','allocations',['exam_id','room_id','row','bench','seat'],unique=True)
def downgrade():raise RuntimeError('Restore from backup to remove schema protections.')
