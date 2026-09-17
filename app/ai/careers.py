"""Starter career taxonomy (version careers-4a.1). Transparent by design: every
weight is inspectable, scores are weighted means — no black box. Weights refer
to subject CODES and skill NAMES from the seeded catalog. This is a starter
taxonomy for development, not labor-market truth (see docs/ai-models.md)."""

TAXONOMY_VERSION = "careers-4a.1"

CAREERS = [
    {
        "id": "data-analyst",
        "title": "Data Analyst",
        "blurb": "Turns data into decisions with statistics and clear communication.",
        "keywords": ["data", "analyst", "analytics", "statistics", "insight"],
        "subjects": {"STAT-101": 0.4, "MATH-101": 0.25, "CS-101": 0.15, "ENG-101": 0.2},
        "skills": {
            "Data Literacy": 0.35,
            "Mathematics": 0.25,
            "Critical Thinking": 0.2,
            "Python": 0.2,
        },
    },
    {
        "id": "software-developer",
        "title": "Software Developer",
        "blurb": "Designs and builds software systems.",
        "keywords": ["software", "developer", "engineer", "programming", "coding", "app"],
        "subjects": {"CS-101": 0.5, "MATH-101": 0.25, "STAT-101": 0.1, "ENG-101": 0.15},
        "skills": {
            "Python": 0.4,
            "Critical Thinking": 0.25,
            "Mathematics": 0.2,
            "Time Management": 0.15,
        },
    },
    {
        "id": "research-assistant",
        "title": "Research Assistant",
        "blurb": "Supports scientific inquiry with method and measurement.",
        "keywords": ["research", "science", "scientist", "lab", "experiment"],
        "subjects": {"SCI-101": 0.45, "STAT-101": 0.25, "ENG-101": 0.2, "MATH-101": 0.1},
        "skills": {
            "Critical Thinking": 0.35,
            "Data Literacy": 0.25,
            "Academic Writing": 0.25,
            "Time Management": 0.15,
        },
    },
    {
        "id": "technical-writer",
        "title": "Technical Writer",
        "blurb": "Makes complex topics clear in writing.",
        "keywords": ["writer", "writing", "content", "communication", "documentation"],
        "subjects": {"ENG-101": 0.5, "CS-101": 0.2, "SCI-101": 0.15, "STAT-101": 0.15},
        "skills": {
            "Academic Writing": 0.4,
            "Critical Thinking": 0.3,
            "Data Literacy": 0.15,
            "Time Management": 0.15,
        },
    },
    {
        "id": "data-informed-educator",
        "title": "Data-Informed Educator",
        "blurb": "Uses evidence and communication to help others learn.",
        "keywords": ["teacher", "educator", "tutor", "mentor", "coach"],
        "subjects": {
            "ENG-101": 0.3,
            "MATH-101": 0.25,
            "STAT-101": 0.2,
            "SCI-101": 0.15,
            "CS-101": 0.1,
        },
        "skills": {
            "Critical Thinking": 0.3,
            "Academic Writing": 0.25,
            "Time Management": 0.25,
            "Data Literacy": 0.2,
        },
    },
]
