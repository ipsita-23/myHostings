import uuid
import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models import Group, GroupMember, User


async def create_group(
    db: AsyncSession, name: str, creator_user_id: uuid.UUID
) -> Group:
    group = Group(name=name)
    db.add(group)
    await db.flush()  # get the group.id before adding member
    member = GroupMember(
        group_id=group.id,
        user_id=creator_user_id,
        joined_at=datetime.date.today(),
    )
    db.add(member)
    await db.commit()
    await db.refresh(group)
    return group


async def get_user_groups(db: AsyncSession, user_id: uuid.UUID) -> list[Group]:
    result = await db.execute(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user_id)
        .where(GroupMember.left_at == None)
        .order_by(Group.created_at.desc())
    )
    return list(result.scalars().all())


async def get_group(db: AsyncSession, group_id: uuid.UUID) -> Optional[Group]:
    result = await db.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def add_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    joined_at: datetime.date,
) -> GroupMember:
    # Check if already an active member
    result = await db.execute(
        select(GroupMember)
        .where(GroupMember.group_id == group_id)
        .where(GroupMember.user_id == user_id)
        .where(GroupMember.left_at == None)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    member = GroupMember(
        group_id=group_id,
        user_id=user_id,
        joined_at=joined_at,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def remove_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    left_at: datetime.date,
) -> None:
    """Sets left_at on the active membership. Never deletes the row."""
    result = await db.execute(
        select(GroupMember)
        .where(GroupMember.group_id == group_id)
        .where(GroupMember.user_id == user_id)
        .where(GroupMember.left_at == None)
    )
    member = result.scalar_one_or_none()
    if member:
        member.left_at = left_at
        await db.commit()


async def active_members_on(
    db: AsyncSession, group_id: uuid.UUID, on_date: datetime.date
) -> list[User]:
    """
    Returns users who were active members of the group on a specific date.
    A user is active if: joined_at <= on_date AND (left_at IS NULL OR left_at > on_date)
    """
    result = await db.execute(
        select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .where(GroupMember.joined_at <= on_date)
        .where(
            or_(
                GroupMember.left_at == None,
                GroupMember.left_at > on_date,
            )
        )
        .order_by(User.name)
    )
    return list(result.scalars().all())


async def find_user_by_email(
    db: AsyncSession, email: str
) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_group_members(
    db: AsyncSession, group_id: uuid.UUID
) -> list[GroupMember]:
    result = await db.execute(
        select(GroupMember)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at)
    )
    return list(result.scalars().all())
