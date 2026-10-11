"""Deterministic check for information the caller gave but the model did not save.

Escalation rules only fire on saved fields, so a deadline the model forgets to save would silently
skip a critical escalation. After the model drafts its reply, the caller's latest message is
scanned for deadline cues, phone numbers, email addresses and refusals. If the matching field is
still unsaved, the draft is discarded and the model gets one automated nudge to save it. The model
still decides the value and status; this code only notices the omission.
"""

import re
from dataclasses import dataclass

from app.domain.conversation_state import ConversationState
from app.models.enums import FieldStatus, IntakeStatus

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DEADLINE_CUE = re.compile(
    r"\b(?:deadline|due date|tomorrow|tonight|hearing|summons|court date|respond by|reply by|"
    rf"by {_DAYS}|next {_DAYS}|this {_DAYS}|next (?:week|month)|end of (?:the )?(?:week|month)|"
    rf"within \d+ (?:days?|weeks?)|{_ISO_DATE.pattern}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?(?: of)? {_MONTHS}\b|{_MONTHS} \d{{1,2}}(?:st|nd|rd|th)?)",
    re.I,
)
_NO_DEADLINE = re.compile(r"\bno (?:deadlines?|notice|due date|hearing)\b", re.I)
_PHONE = re.compile(r"\+?\d[\d\s\-().]{5,}\d")
_EMAIL = re.compile(r"[^@\s(),;:]+@[^@\s(),;:]+\.[A-Za-z]{2,}")
_REFUSAL = re.compile(
    r"\b(?:rather not|prefer not to|don't want to (?:share|give)|do not want to (?:share|give)|"
    r"won't (?:share|give)|not (?:comfortable|willing) (?:sharing|giving|to share|to give))\b",
    re.I,
)
_CONTACT_FIELDS = ("caller_name", "phone", "email")
# Words that tell which field a refusal is about.
_REFUSED_FIELD_WORDS = {
    "phone": re.compile(r"\b(?:number|phone|mobile)\b", re.I),
    "email": re.compile(r"\be-?mail\b", re.I),
    "caller_name": re.compile(r"\bname\b", re.I),
}
_LEGAL_QUESTION = re.compile(
    r"\b(?:can i (?:sue|claim|get|take)|should i|what are my (?:chances|rights|options)|"
    r"is (?:it|this|that) legal|am i (?:entitled|allowed)|will i (?:win|lose|get)|"
    r"do i have a (?:case|claim)|what should i do|chances of winning)\b",
    re.I,
)
_FINISHED = re.compile(
    r"\b(?:that's all|that is all|that's everything|that is everything|nothing else|"
    r"that's it|good ?bye|bye)\b",
    re.I,
)


@dataclass(frozen=True)
class Omission:
    code: str
    hint: str


def _status(state: ConversationState, key: str) -> FieldStatus | None:
    return next((f.status for f in state.fields if f.key == key), None)


def find_omissions(caller_text: str, state: ConversationState) -> list[Omission]:
    if state.intake_reference is None:
        return []
    found: list[Omission] = []
    if (
        _status(state, "reported_deadline") is FieldStatus.MISSING
        and _DEADLINE_CUE.search(caller_text)
        and not _NO_DEADLINE.search(caller_text)
    ):
        found.append(
            Omission(
                "possible_deadline",
                "The caller mentioned a date or deadline, but reported_deadline is not saved. "
                "If they gave one, save it now: an exact date as REPORTED_BY_CALLER, or "
                "PENDING_HUMAN_REVIEW with their words in note if it is vague.",
            )
        )
    without_dates = _ISO_DATE.sub(" ", caller_text)
    digits = [m for m in _PHONE.findall(without_dates) if len(re.sub(r"\D", "", m)) >= 7]
    if digits and _status(state, "phone") is FieldStatus.MISSING:
        found.append(
            Omission(
                "possible_phone",
                "The caller's message contains what looks like a phone number, but phone is not "
                "saved. Save it now if it is their contact number.",
            )
        )
    if _EMAIL.search(caller_text) and _status(state, "email") is FieldStatus.MISSING:
        found.append(
            Omission(
                "possible_email",
                "The caller gave an email address, but email is not saved. Save it now.",
            )
        )
    if _REFUSAL.search(caller_text):
        named = [k for k, words in _REFUSED_FIELD_WORDS.items() if words.search(caller_text)]
        unsaved = [k for k in named if _status(state, k) is FieldStatus.MISSING]
        nothing_recorded = not any(
            _status(state, k) is FieldStatus.DECLINED_BY_CALLER for k in _CONTACT_FIELDS
        )
        if unsaved or (not named and nothing_recorded):
            target = ", ".join(unsaved) if unsaved else "each field they refused"
            found.append(
                Omission(
                    "possible_refusal",
                    "The caller refused to share something, but the refusal is not recorded. "
                    f"Save DECLINED_BY_CALLER (value null) for: {target}.",
                )
            )
    if (
        _LEGAL_QUESTION.search(caller_text)
        and _status(state, "caller_questions") is FieldStatus.MISSING
    ):
        found.append(
            Omission(
                "possible_legal_question",
                "The caller asked a legal question, but caller_questions is not saved. Save their "
                "question in caller_questions (REPORTED_BY_CALLER); do not answer it.",
            )
        )
    if state.intake_status is IntakeStatus.IN_PROGRESS and _FINISHED.search(caller_text):
        found.append(
            Omission(
                "possible_finish",
                "The caller said they are done, but the intake is not finalized. Call "
                "finalize_intake_summary now (missing fields are fine; the team will follow up), "
                "then say goodbye without asking more questions.",
            )
        )
    return found


def nudge(omissions: list[Omission]) -> str:
    lines = "\n".join(f"- {o.hint}" for o in omissions)
    return (
        "[Automated check] Your draft reply was not sent because information from the caller's "
        f"last message may not be saved:\n{lines}\nSave what applies, then reply to the caller."
    )
