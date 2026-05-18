"""Lecture lifecycle exceptions."""

from __future__ import annotations


class LectureLifecycleError(Exception):
    """Raised when a lecture lifecycle transition is invalid."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class LectureNotFoundError(LectureLifecycleError):
    """Raised when a lecture id does not exist."""

    def __init__(self, lecture_id: str) -> None:
        super().__init__(f"lecture not found: {lecture_id}", status_code=404)
        self.lecture_id = lecture_id


class EntityNotFoundError(LectureLifecycleError):
    """Raised when a referenced entity does not exist."""

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} not found: {entity_id}", status_code=404)
        self.entity = entity
        self.entity_id = entity_id
