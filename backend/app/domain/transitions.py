"""Explicit state transitions. Validate before mutating a related aggregate."""


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


UNIT_TRANSITIONS = {
    "available": {"dispatched"},
    "dispatched": {"en_route"},
    "en_route": {"on_scene"},
    "on_scene": {"available"},
    "offline": set(),
}
INCIDENT_TRANSITIONS = {
    "active": {"dispatched"},
    "dispatched": {"on_scene"},
    "on_scene": {"resolved"},
    "resolved": set(),
}
DISPATCH_TRANSITIONS = {
    "created": {"en_route"},
    "en_route": {"arrived"},
    "arrived": {"completed"},
    "completed": set(),
}


def require_transition(entity: str, current: str, target: str) -> None:
    transitions = {
        "unit": UNIT_TRANSITIONS,
        "incident": INCIDENT_TRANSITIONS,
        "dispatch": DISPATCH_TRANSITIONS,
    }[entity]
    if target not in transitions.get(current, set()):
        raise DomainError(
            "INVALID_TRANSITION",
            f"Cannot transition {entity} from {current} to {target}.",
            400,
        )
