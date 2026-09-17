"""Profile + nested resources. All routes require auth; users only touch own profile."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status

from app.api.deps import get_current_user
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import profile_service

router = APIRouter(tags=["profile"])


@router.get("/profiles/me", response_model=s.ProfileOut)
def read_profile(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return profile_service.get_my_profile(db, user.id)


@router.put("/profiles/me", response_model=s.ProfileOut)
def update_profile(
    body: s.ProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return profile_service.update_my_profile(db, user.id, body)


@router.get("/profiles/me/education", response_model=list[s.EducationOut])
def list_education(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = profile_service.get_my_profile(db, user.id)
    return db.scalars(
        select(models.EducationInfo).where(models.EducationInfo.profile_id == profile.id)
    ).all()


@router.post(
    "/profiles/me/education", response_model=s.EducationOut, status_code=status.HTTP_201_CREATED
)
def create_education(
    body: s.EducationIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return profile_service.add_education(db, user.id, body)


@router.delete("/profiles/me/education/{education_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_education(
    education_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile_service.delete_education(db, user.id, education_id)


@router.get("/profiles/me/skills", response_model=list[s.ProfileSkillOut])
def list_skills(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = profile_service.get_my_profile(db, user.id)
    links = db.scalars(
        select(models.ProfileSkill).where(models.ProfileSkill.profile_id == profile.id)
    ).all()
    return [s.ProfileSkillOut(skill=l.skill, level=l.level) for l in links]


@router.post(
    "/profiles/me/skills", response_model=s.ProfileSkillOut, status_code=status.HTTP_201_CREATED
)
def upsert_skill(
    body: s.ProfileSkillIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    link = profile_service.set_skill(db, user.id, body)
    return s.ProfileSkillOut(skill=link.skill, level=link.level)


@router.delete("/profiles/me/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_skill(
    skill_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile_service.remove_skill(db, user.id, skill_id)


@router.get("/profiles/me/goals", response_model=list[s.GoalOut])
def list_goals(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = profile_service.get_my_profile(db, user.id)
    return db.scalars(
        select(models.CareerGoal).where(models.CareerGoal.profile_id == profile.id)
    ).all()


@router.post("/profiles/me/goals", response_model=s.GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(
    body: s.GoalIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return profile_service.add_goal(db, user.id, body)


@router.delete("/profiles/me/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_goal(
    goal_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile_service.delete_goal(db, user.id, goal_id)


@router.get("/profiles/me/availability", response_model=list[s.AvailabilityOut])
def list_availability(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = profile_service.get_my_profile(db, user.id)
    return db.scalars(
        select(models.StudyAvailability).where(models.StudyAvailability.profile_id == profile.id)
    ).all()


@router.post(
    "/profiles/me/availability",
    response_model=s.AvailabilityOut,
    status_code=status.HTTP_201_CREATED,
)
def create_availability(
    body: s.AvailabilityIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return profile_service.add_availability(db, user.id, body)


@router.delete("/profiles/me/availability/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_availability(
    slot_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    profile_service.delete_availability(db, user.id, slot_id)


@router.get("/profiles/me/subjects", response_model=list[s.SubjectOut])
def list_my_subjects(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    profile = profile_service.get_my_profile(db, user.id)
    links = db.scalars(
        select(models.ProfileSubject).where(models.ProfileSubject.profile_id == profile.id)
    ).all()
    return [l.subject for l in links]


@router.put("/profiles/me/subjects", response_model=list[s.SubjectOut])
def replace_my_subjects(
    body: s.ProfileSubjectsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return profile_service.set_subjects(db, user.id, body.subject_ids)
