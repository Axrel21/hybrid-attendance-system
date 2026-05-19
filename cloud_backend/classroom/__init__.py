"""Classroom registry and resolution."""

from cloud_backend.classroom.resolver import (
    ClassroomResolution,
    LectureResolution,
    fetch_all_active_lectures,
    resolve_active_lecture,
    resolve_classroom,
)

__all__ = [
    "ClassroomResolution",
    "LectureResolution",
    "fetch_all_active_lectures",
    "resolve_active_lecture",
    "resolve_classroom",
]
