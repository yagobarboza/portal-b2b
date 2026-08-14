"""Bloco 9: ticket_status_history e attachment_file_id em ticket_messages

Revision ID: caffea54e7cc
Revises: ae6a775378af
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'caffea54e7cc'
down_revision = 'ae6a775378af'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # O tipo ENUM 'ticket_status' JÁ EXISTE (criado no Bloco 2 ao criar a
    # tabela 'tickets'). Referenciamos o tipo existente com create_type=False
    # para NÃO recriá-lo (evita DuplicateObjectError).
    ticket_status = postgresql.ENUM(
        'open', 'under_review', 'awaiting_customer', 'awaiting_company',
        'resolved', 'closed', name='ticket_status', create_type=False,
    )

    op.create_table(
        'ticket_status_history',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('ticket_id', sa.UUID(), nullable=False),
        sa.Column('from_status', ticket_status, nullable=True),
        sa.Column('to_status', ticket_status, nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ticket_status_history_ticket_id', 'ticket_status_history', ['ticket_id'])
    op.create_index('ix_ticket_status_history_tenant_id', 'ticket_status_history', ['tenant_id'])

    op.add_column('ticket_messages', sa.Column('attachment_file_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_ticket_messages_attachment_file_id', 'ticket_messages',
        'files', ['attachment_file_id'], ['id'], ondelete='SET NULL',
    )

def downgrade() -> None:
    op.drop_constraint('fk_ticket_messages_attachment_file_id', 'ticket_messages', type_='foreignkey')
    op.drop_column('ticket_messages', 'attachment_file_id')
    op.drop_index('ix_ticket_status_history_ticket_id', table_name='ticket_status_history')
    op.drop_index('ix_ticket_status_history_tenant_id', table_name='ticket_status_history')
    op.drop_table('ticket_status_history')