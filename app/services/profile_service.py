"""Profile-domain service: keeps business rules out of routers and clients."""

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db import models
from app.schemas import v1 as s


def get_my_profile(db: Session, user_id: str) -> models.StudentProfile:
    profile = db.scalar(
        select(models.StudentProfile).where(models.StudentProfile.user_id == user_id)
    )
    if not profile:
        raise AppError("profile_missing", "Student profile not found.", 404)
    return profile


def update_my_profile(db: Session, user_id: str, patch: s.ProfileUpdate) -> models.StudentProfile:
    profile = get_my_profile(db, user_id)
    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


def add_education(db: Session, user_id: str, data: s.EducationIn) -> models.EducationInfo:
    profile = get_my_profile(db, user_id)
    row = models.EducationInfo(profile_id=profile.id, **data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_education(db: Session, user_id: str, education_id: str) -> None:
    profile = get_my_profile(db, user_id)
    row = db.get(models.EducationInfo, education_id)
    if not row or row.profile_id != profile.id:
        raise AppError("not_found", "Education entry not found.", 404)
    db.delete(row)
    db.commit()


def set_skill(db: Session, user_id: str, data: s.ProfileSkillIn) -> models.ProfileSkill:
    profile = get_my_profile(db, user_id)
    name = data.name.strip()
    skill = db.scalar(select(models.Skill).where(models.Skill.name == name)) or db.scalar(
        select(models.Skill).where(func.lower(models.Skill.name) == name.lower())
    )
    if not skill:
        # Concurrent first-use of the same skill name can race the unique
        # constraint; on conflict, re-read instead of 500ing.
        try:
            skill = models.Skill(name=name)
            db.add(skill)
            db.flush()
        except IntegrityError:
            db.rollback()
            skill = db.scalar(
                select(models.Skill).where(func.lower(models.Skill.name) == name.lower())
            )
            if not skill:
                raise AppError("skill_conflict", "Skill is being created, retry.", 409)
    link = db.scalar(
        select(models.ProfileSkill).where(
            models.ProfileSkill.profile_id == profile.id, models.ProfileSkill.skill_id == skill.id
        )
    )
    if link:
        link.level = data.level
    else:
        link = models.ProfileSkill(profile_id=profile.id, skill_id=skill.id, level=data.level)
        db.add(link)
    # V2-C1: the self-report track changed — recompute + snapshot the twin in
    # the same transaction so history stays attributable (trigger
    # profile_updated, no attempt reference). No notify fan-out here.
    db.flush()
    from app.services import twin_service

    twin_service.update_after_profile_change(db, profile.id, skill.id)
    db.commit()
    db.refresh(link)
    return link


def remove_skill(db: Session, user_id: str, skill_id: str) -> None:
    profile = get_my_profile(db, user_id)
    link = db.scalar(
        select(models.ProfileSkill).where(
            models.ProfileSkill.profile_id == profile.id, models.ProfileSkill.skill_id == skill_id
        )
    )
    if not link:
        raise AppError("not_found", "Skill not linked to profile.", 404)
    db.delete(link)
    # V2-C1: same self-report hook as set_skill (same transaction).
    db.flush()
    from app.services import twin_service

    twin_service.update_after_profile_change(db, profile.id, skill_id)
    db.commit()


def add_goal(db: Session, user_id: str, data: s.GoalIn) -> models.CareerGoal:
    profile = get_my_profile(db, user_id)
    row = models.CareerGoal(profile_id=profile.id, **data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_goal(db: Session, user_id: str, goal_id: str) -> None:
    profile = get_my_profile(db, user_id)
    row = db.get(models.CareerGoal, goal_id)
    if not row or row.profile_id != profile.id:
        raise AppError("not_found", "Goal not found.", 404)
    db.delete(row)
    db.commit()


def add_availability(db: Session, user_id: str, data: s.AvailabilityIn) -> models.StudyAvailability:
    profile = get_my_profile(db, user_id)
    if data.start_time >= data.end_time:
        raise AppError("invalid_slot", "start_time must be before end_time.", 422)
    row = models.StudyAvailability(profile_id=profile.id, **data.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise AppError("slot_conflict", "Availability slot already exists.", 409) from e
    db.refresh(row)
    return row


def delete_availability(db: Session, user_id: str, slot_id: str) -> None:
    profile = get_my_profile(db, user_id)
    row = db.get(models.StudyAvailability, slot_id)
    if not row or row.profile_id != profile.id:
        raise AppError("not_found", "Availability slot not found.", 404)
    db.delete(row)
    db.commit()


def set_subjects(db: Session, user_id: str, subject_ids: list[str]) -> list[models.Subject]:
    profile = get_my_profile(db, user_id)
    unique_ids = list(dict.fromkeys(subject_ids))  # tolerate client duplicates
    subjects = (
        db.scalars(select(models.Subject).where(models.Subject.id.in_(unique_ids))).all()
        if unique_ids
        else []
    )
    if len(subjects) != len(unique_ids):
        raise AppError("invalid_subjects", "One or more subject IDs are invalid.", 422)
    db.execute(delete(models.ProfileSubject).where(models.ProfileSubject.profile_id == profile.id))
    for subj in subjects:
        db.add(models.ProfileSubject(profile_id=profile.id, subject_id=subj.id))
    db.commit()
    return list(subjects)
