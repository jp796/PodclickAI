"""phase2b_base_caption_on_posts

Move base content from post_variants (platform='base') to posts.base_caption.
Removes 'base' from ck_post_variants_platform constraint per SOW Section 4.7.

Revision ID: a1f3c8d2e094
Revises: d03de2d7e071
Create Date: 2026-05-27 14:29:21.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1f3c8d2e094'
down_revision: Union[str, None] = 'd03de2d7e071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    1. Add base_caption TEXT to posts table.
    2. Copy caption from post_variants(platform='base') → posts.base_caption.
    3. Delete 'base' rows from post_variants.
    4. Drop old ck_post_variants_platform constraint (includes 'base').
    5. Create new constraint without 'base'.
    """

    # 1. Add base_caption column to posts
    op.add_column('posts', sa.Column('base_caption', sa.Text(), nullable=True))

    # 2. Migrate data: copy 'base' variant captions to posts.base_caption
    op.execute(
        """
        UPDATE posts
        SET base_caption = pv.caption
        FROM post_variants pv
        WHERE pv.post_id = posts.id
          AND pv.platform = 'base'
        """
    )

    # 3. Delete 'base' rows from post_variants
    op.execute("DELETE FROM post_variants WHERE platform = 'base'")

    # 4. Drop the old constraint that includes 'base'
    op.drop_constraint('ck_post_variants_platform', 'post_variants', type_='check')

    # 5. Add the corrected constraint matching SOW Section 4.7 (no 'base')
    op.create_check_constraint(
        'ck_post_variants_platform',
        'post_variants',
        "platform IN ('linkedin', 'instagram', 'facebook', 'tiktok', 'youtube', 'x', 'gmb', 'threads')",
    )


def downgrade() -> None:
    """
    Reverse: restore 'base' variants from posts.base_caption, remove column.
    """

    # 1. Drop the corrected constraint
    op.drop_constraint('ck_post_variants_platform', 'post_variants', type_='check')

    # 2. Restore old constraint (includes 'base')
    op.create_check_constraint(
        'ck_post_variants_platform',
        'post_variants',
        "platform IN ('base', 'linkedin', 'instagram', 'facebook', 'tiktok', 'youtube', 'x', 'gmb', 'threads')",
    )

    # 3. Re-insert 'base' variant rows from posts.base_caption
    op.execute(
        """
        INSERT INTO post_variants (id, post_id, platform, caption, platform_specific)
        SELECT gen_random_uuid(), id, 'base', base_caption, '{}'::jsonb
        FROM posts
        WHERE base_caption IS NOT NULL
        """
    )

    # 4. Drop base_caption column
    op.drop_column('posts', 'base_caption')
