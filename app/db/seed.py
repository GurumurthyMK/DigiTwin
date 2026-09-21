"""Idempotent reference-data seed: subjects + common skills."""

from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal

SUBJECTS = [
    ("MATH-101", "Mathematics Foundations", "Core numeracy, algebra and problem solving."),
    ("ENG-101", "Academic English", "Reading, writing and communication skills."),
    ("SCI-101", "Science Foundations", "Scientific method and core concepts."),
    ("CS-101", "Computing Foundations", "Computational thinking and digital literacy."),
    ("STAT-101", "Statistics & Data", "Descriptive stats, probability and data reasoning."),
]

SKILLS = [
    "Python",
    "Mathematics",
    "Critical Thinking",
    "Time Management",
    "Academic Writing",
    "Data Literacy",
]


def seed_reference_data() -> None:
    db = SessionLocal()
    try:
        for code, name, desc in SUBJECTS:
            if not db.scalar(select(models.Subject).where(models.Subject.code == code)):
                db.add(models.Subject(code=code, name=name, description=desc))
        for name in SKILLS:
            if not db.scalar(select(models.Skill).where(models.Skill.name == name)):
                db.add(models.Skill(name=name))
        db.commit()
        seed_learning_data(db)
        seed_skill_graph(db)
        db.commit()
    finally:
        db.close()


# Phase 2A curriculum seed: real starter content (topics, lessons, quizzes).
# Format per subject: [(topic_title, topic_desc, lesson_title, lesson_body,
#   quiz_title, quiz_difficulty, [(prompt, [options], correct_idx, explanation), ...]), ...]
CURRICULUM: dict[str, list] = {
    "MATH-101": [
        (
            "Linear Equations",
            "Solve for x: one-variable equations and checking solutions.",
            "Reading: balancing equations",
            (
                "An equation states that two expressions are equal. Whatever you do to one "
                "side, do to the other. Isolate the variable, then substitute back to check."
            ),
            "Linear Equations Check",
            "easy",
            [
                (
                    "If x + 7 = 15, what is x?",
                    ["6", "7", "8", "22"],
                    2,
                    "Subtract 7 from both sides: 15 − 7 = 8.",
                ),
                (
                    "If 3x = 21, what is x?",
                    ["6", "7", "8", "24"],
                    1,
                    "Divide both sides by 3: 21 ÷ 3 = 7.",
                ),
                (
                    "Check: does x = 4 satisfy 2x + 3 = 11?",
                    ["Yes", "No"],
                    0,
                    "2(4) + 3 = 11. Correct.",
                ),
            ],
        ),
        (
            "Fractions & Ratios",
            "Compare, add and scale fractional quantities.",
            "Reading: why common denominators work",
            (
                "You can only add slices of the same size. Convert to a common denominator "
                "first, then add the numerators."
            ),
            "Fractions & Ratios Check",
            "medium",
            [
                (
                    "What is 1/4 + 1/2?",
                    ["2/6", "3/4", "1/6", "2/4"],
                    1,
                    "1/2 = 2/4, so 1/4 + 2/4 = 3/4.",
                ),
                (
                    "A recipe serves 4 and needs 2 cups of flour. For 2 servings?",
                    ["4 cups", "2 cups", "1 cup", "0.5 cups"],
                    2,
                    "Half the servings, half the flour: 1 cup.",
                ),
                (
                    "Which is largest: 2/3, 3/4, 5/8?",
                    ["2/3", "3/4", "5/8", "All equal"],
                    1,
                    "As decimals: 0.67, 0.75, 0.625. So 3/4.",
                ),
            ],
        ),
    ],
    "ENG-101": [
        (
            "Thesis Statements",
            "Write arguable, specific central claims for essays.",
            "Reading: arguable vs factual claims",
            (
                "A thesis must be arguable — a reasonable reader could disagree — and specific "
                "enough to guide the essay. Facts and announcements are not theses."
            ),
            "Thesis Statements Check",
            "easy",
            [
                (
                    "Which is an arguable thesis?",
                    [
                        "Water boils at 100°C.",
                        "This essay is about dogs.",
                        "Cities should fund free night buses.",
                        "Shakespeare wrote plays.",
                    ],
                    2,
                    "Only the policy claim invites disagreement and argument.",
                ),
                (
                    "What is wrong with 'Pollution is bad'?",
                    ["Too long", "Too vague to argue", "Too specific", "Nothing"],
                    1,
                    "It states the obvious; narrow it to a claim.",
                ),
                (
                    "A good thesis usually appears…",
                    [
                        "in the title",
                        "at the end of the intro",
                        "in the conclusion only",
                        "in footnotes",
                    ],
                    1,
                    "Readers expect the claim at the end of the introduction.",
                ),
            ],
        ),
        (
            "Paragraph Structure",
            "PEEL paragraphs: point, evidence, explanation, link.",
            "Reading: the PEEL pattern",
            (
                "One paragraph, one point. State it, support it with evidence, explain how the "
                "evidence supports it, and link back to the thesis."
            ),
            "Paragraph Structure Check",
            "medium",
            [
                (
                    "In PEEL, the second E stands for…",
                    ["Example", "Explanation", "Ending", "Emphasis"],
                    1,
                    "Explanation connects evidence to the point.",
                ),
                (
                    "How many main points per paragraph?",
                    ["One", "Two", "As many as fit", "Zero"],
                    0,
                    "One point keeps paragraphs focused and readable.",
                ),
            ],
        ),
    ],
    "SCI-101": [
        (
            "The Scientific Method",
            "Hypotheses, variables and fair tests.",
            "Reading: fair tests",
            (
                "Change one variable at a time. The independent variable is what you change; "
                "the dependent variable is what you measure; controls stay constant."
            ),
            "Scientific Method Check",
            "easy",
            [
                (
                    "A hypothesis should be…",
                    ["vague", "testable", "famous", "long"],
                    1,
                    "Testability is what separates science from opinion.",
                ),
                (
                    "In a plant-growth experiment testing light, the dependent variable is…",
                    ["light amount", "plant height", "soil type", "pot size"],
                    1,
                    "Height is measured; light is changed.",
                ),
                (
                    "Controls exist to…",
                    ["speed things up", "rule out other causes", "impress reviewers", "save money"],
                    1,
                    "Everything else held constant isolates the cause.",
                ),
            ],
        ),
        (
            "Units & Measurement",
            "SI units, precision and significant figures.",
            "Reading: measure like a scientist",
            (
                "Record the unit every time, and never report more digits than your instrument "
                "can justify."
            ),
            "Units & Measurement Check",
            "medium",
            [
                (
                    "The SI unit of force is the…",
                    ["joule", "watt", "newton", "pascal"],
                    2,
                    "Newtons measure force; joules measure energy.",
                ),
                (
                    "A ruler marked in mm justifies which reading?",
                    ["12 cm", "12.0 cm", "12.00 cm", "12.000 cm"],
                    1,
                    "Millimetre marks justify one decimal place in cm.",
                ),
            ],
        ),
    ],
    "CS-101": [
        (
            "Algorithms & Pseudocode",
            "Sequences, selection and iteration in plain steps.",
            "Reading: thinking in steps",
            (
                "An algorithm is an unambiguous sequence of steps. Selection (if/else) branches; "
                "iteration (loops) repeats until a condition changes."
            ),
            "Algorithms Check",
            "easy",
            [
                (
                    "An algorithm must be…",
                    ["fast", "unambiguous", "written in Python", "short"],
                    1,
                    "Clarity first; speed is an optimization concern.",
                ),
                (
                    "Counting 1 to 10 uses…",
                    ["selection", "iteration", "recursion only", "none"],
                    1,
                    "Repetition until a condition is iteration.",
                ),
                (
                    "An if/else is an example of…",
                    ["iteration", "selection", "sorting", "input"],
                    1,
                    "Branching on a condition is selection.",
                ),
            ],
        ),
        (
            "Data: Bits & Types",
            "Binary, and why types constrain values.",
            "Reading: bits to meaning",
            (
                "Bits become numbers, text or images only through an agreed interpretation — "
                "the type. The same byte pattern means different things under different types."
            ),
            "Bits & Types Check",
            "medium",
            [
                ("Binary 101 in decimal is…", ["3", "5", "6", "101"], 1, "4 + 0 + 1 = 5."),
                (
                    "Why do types matter?",
                    [
                        "They speed up CPUs",
                        "They fix how bits are interpreted",
                        "They compress data",
                        "They encrypt data",
                    ],
                    1,
                    "A type is the agreement about what bits mean.",
                ),
            ],
        ),
    ],
    "STAT-101": [
        (
            "Mean, Median, Mode",
            "Centers of data and when each misleads.",
            "Reading: pick the right center",
            (
                "The mean feels every outlier; the median ignores them; the mode finds the "
                "most common value. Report the center that survives your data's shape."
            ),
            "Centers of Data Check",
            "easy",
            [
                ("Mean of 2, 4, 6?", ["3", "4", "5", "12"], 1, "(2+4+6)/3 = 4."),
                (
                    "Incomes [30k, 32k, 35k, 900k], best center?",
                    ["Mean", "Median", "Mode", "Range"],
                    1,
                    "The outlier drags the mean; the median stays representative.",
                ),
                ("Mode of A, B, B, C?", ["A", "B", "C", "None"], 1, "B appears twice."),
            ],
        ),
        (
            "Sampling & Bias",
            "Random samples, and how convenience samples lie.",
            "Reading: who is missing?",
            (
                "Every conclusion inherits its sample. Ask who had no chance of being included "
                "before trusting a statistic."
            ),
            "Sampling & Bias Check",
            "medium",
            [
                (
                    "Surveying only mall shoppers risks…",
                    ["random error", "selection bias", "rounding error", "nothing"],
                    1,
                    "Non-shoppers had zero chance of inclusion.",
                ),
                (
                    "Random sampling helps because…",
                    ["it is cheap", "every member can be picked", "it needs no math", "it is fast"],
                    1,
                    "Equal inclusion chance removes systematic skew.",
                ),
            ],
        ),
    ],
}


def seed_learning_data(db) -> None:
    for code, topics in CURRICULUM.items():
        subject = db.scalar(select(models.Subject).where(models.Subject.code == code))
        if not subject:
            continue
        for order, (t_title, t_desc, l_title, l_body, q_title, diff, questions) in enumerate(
            topics
        ):
            topic = db.scalar(
                select(models.Topic).where(
                    models.Topic.subject_id == subject.id, models.Topic.title == t_title
                )
            )
            if not topic:
                topic = models.Topic(
                    subject_id=subject.id, title=t_title, description=t_desc, order_index=order
                )
                db.add(topic)
                db.flush()
            if not db.scalar(
                select(models.ContentItem).where(
                    models.ContentItem.topic_id == topic.id, models.ContentItem.title == l_title
                )
            ):
                db.add(
                    models.ContentItem(
                        topic_id=topic.id,
                        kind="lesson",
                        title=l_title,
                        body=l_body,
                        duration_minutes=5,
                        order_index=0,
                    )
                )
            existing = db.scalar(
                select(models.Assessment).where(
                    models.Assessment.topic_id == topic.id, models.Assessment.title == q_title
                )
            )
            if existing:
                continue
            assessment = models.Assessment(
                subject_id=subject.id,
                topic_id=topic.id,
                title=q_title,
                description=f"Self-check for {t_title}.",
                difficulty=diff,
                time_limit_seconds=300,
                is_published=True,
            )
            db.add(assessment)
            db.flush()
            for qi, (prompt, options, correct_idx, expl) in enumerate(questions):
                q = models.Question(
                    assessment_id=assessment.id,
                    kind="single_choice",
                    prompt=prompt,
                    points=1,
                    explanation=expl,
                    order_index=qi,
                )
                db.add(q)
                db.flush()
                for oi, label in enumerate(options):
                    db.add(
                        models.QuestionOption(
                            question_id=q.id,
                            label=label,
                            is_correct=(oi == correct_idx),
                            order_index=oi,
                        )
                    )


# V2-A1 Skill Graph seed: a small deterministic curriculum taxonomy.
# (skill_name, subject_code, topic_title). Pre-existing generic skills
# (SKILLS above) stay topic-less: they remain student-declared vocabulary.
SKILL_TAXONOMY = [
    ("Solving Linear Equations", "MATH-101", "Linear Equations"),
    ("Fraction Arithmetic", "MATH-101", "Fractions & Ratios"),
    ("Writing Thesis Statements", "ENG-101", "Thesis Statements"),
    ("Paragraph Structure", "ENG-101", "Paragraph Structure"),
    ("Experimental Design", "SCI-101", "The Scientific Method"),
    ("SI Units", "SCI-101", "Units & Measurement"),
    ("Measurement Precision", "SCI-101", "Units & Measurement"),
    ("Algorithms & Control Flow", "CS-101", "Algorithms & Pseudocode"),
    ("Binary & Data Types", "CS-101", "Data: Bits & Types"),
    ("Measures of Center", "STAT-101", "Mean, Median, Mode"),
    ("Sampling Methods", "STAT-101", "Sampling & Bias"),
]

# Question prompt -> skill name. A prompt is mapped only when its content
# clearly exercises the skill; ambiguous questions stay unmapped (opt-in).
QUESTION_SKILL_MAP = {
    "If x + 7 = 15, what is x?": "Solving Linear Equations",
    "If 3x = 21, what is x?": "Solving Linear Equations",
    "Check: does x = 4 satisfy 2x + 3 = 11?": "Solving Linear Equations",
    "What is 1/4 + 1/2?": "Fraction Arithmetic",
    "A recipe serves 4 and needs 2 cups of flour. For 2 servings?": "Fraction Arithmetic",
    "Which is largest: 2/3, 3/4, 5/8?": "Fraction Arithmetic",
    "Which is an arguable thesis?": "Writing Thesis Statements",
    "What is wrong with 'Pollution is bad'?": "Writing Thesis Statements",
    "A good thesis usually appears…": "Writing Thesis Statements",
    "In PEEL, the second E stands for…": "Paragraph Structure",
    "How many main points per paragraph?": "Paragraph Structure",
    "A hypothesis should be…": "Experimental Design",
    "In a plant-growth experiment testing light, the dependent variable is…": (
        "Experimental Design"
    ),
    "Controls exist to…": "Experimental Design",
    "The SI unit of force is the…": "SI Units",
    "A ruler marked in mm justifies which reading?": "Measurement Precision",
    "An algorithm must be…": "Algorithms & Control Flow",
    "Counting 1 to 10 uses…": "Algorithms & Control Flow",
    "An if/else is an example of…": "Algorithms & Control Flow",
    "Binary 101 in decimal is…": "Binary & Data Types",
    "Why do types matter?": "Binary & Data Types",
    "Mean of 2, 4, 6?": "Measures of Center",
    "Incomes [30k, 32k, 35k, 900k], best center?": "Measures of Center",
    "Mode of A, B, B, C?": "Measures of Center",
    "Surveying only mall shoppers risks…": "Sampling Methods",
    "Random sampling helps because…": "Sampling Methods",
}


def seed_skill_graph(db) -> None:
    """Idempotent Skill Graph seed: topic skills + question edges.

    Safe on repeat runs and on databases seeded before V2-A1: existing skill
    rows are reused (topic set only when NULL — never clobbered), existing
    edges are skipped, unknown prompts/topics are ignored.
    """
    for skill_name, subject_code, topic_title in SKILL_TAXONOMY:
        skill = db.scalar(select(models.Skill).where(models.Skill.name == skill_name))
        if not skill:
            skill = models.Skill(name=skill_name)
            db.add(skill)
            db.flush()
        if skill.topic_id is None:
            subject = db.scalar(select(models.Subject).where(models.Subject.code == subject_code))
            if subject is None:
                continue
            topic = db.scalar(
                select(models.Topic).where(
                    models.Topic.subject_id == subject.id, models.Topic.title == topic_title
                )
            )
            if topic is not None:
                skill.topic_id = topic.id
    for prompt, skill_name in QUESTION_SKILL_MAP.items():
        question = db.scalar(select(models.Question).where(models.Question.prompt == prompt))
        skill = db.scalar(select(models.Skill).where(models.Skill.name == skill_name))
        if question is None or skill is None:
            continue
        exists = db.scalar(
            select(models.QuestionSkill).where(
                models.QuestionSkill.question_id == question.id,
                models.QuestionSkill.skill_id == skill.id,
            )
        )
        if not exists:
            db.add(models.QuestionSkill(question_id=question.id, skill_id=skill.id))
