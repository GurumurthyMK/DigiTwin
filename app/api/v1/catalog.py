"""Reference data: subjects + skill catalog (read-only for clients)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s

router = APIRouter(tags=["catalog"])


@router.get("/subjects", response_model=list[s.SubjectOut])
def list_subjects(db: Session = Depends(get_db)):
    return db.scalars(select(models.Subject).order_by(models.Subject.code)).all()


@router.get("/skills", response_model=list[s.SkillOut])
def list_skills(db: Session = Depends(get_db)):
    return db.scalars(select(models.Skill).order_by(models.Skill.name)).all()
