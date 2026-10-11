"""Deterministic check of the agent's reply against persisted state.

Blocks the two claims the blueprint forbids unless the database supports them: a booking
confirmation without a booked appointment, and a lawyer/firm acceptance without an ACCEPTED
escalation. Detection is pattern-based (conservative); it is a safety net, not a replacement for
tool-verified outcomes.
"""

import re

from app.domain.conversation_state import ConversationState
from app.models.enums import FieldStatus

FALSE_BOOKING = "false_booking_confirmation"
UNCONFIRMED_ACCEPTANCE = "unconfirmed_acceptance_claim"
UNSAVED_QUESTION = "unsaved_question_claim"

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_NEGATION = re.compile(
    r"\b(?:not|never|no|nobody|no one|none|unable|cannot|can't|couldn't|isn't|wasn't|haven't"
    r"|hasn't|yet to)\b|n't\b",
    re.I,
)
_BOOKING_CLAIMS = (
    re.compile(
        r"\b(?:is|are|has been|have been|was|were|'s|\u2019s)\s+(?:now\s+|all\s+|successfully\s+)*"
        r"(?:booked|scheduled|confirmed|reserved)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:i|we)(?:'ve|\u2019ve| have)\s+(?:now\s+|successfully\s+)*"
        r"(?:booked|scheduled|reserved)\b",
        re.I,
    ),
)
_ACCEPTANCE_CLAIMS = (
    re.compile(
        r"\b(?:lawyer|attorney|solicitor|advocate|staff|team|firm|someone)\b[^.!?]{0,40}?"
        r"\b(?:has|have)\s+(?:already\s+|now\s+)*(?:accepted|reviewed|taken on|agreed to)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:case|enquiry|inquiry|matter|request)\b[^.!?]{0,30}?"
        r"\b(?:has been|have been|is|was)\s+(?:now\s+)?(?:accepted|taken on|approved)\b",
        re.I,
    ),
)
_QUESTION_CLAIMS = (
    re.compile(
        r"\b(?:noted|recorded|saved|logged|written down|passed on)\b[^.!?]{0,30}?\bquestions?\b",
        re.I,
    ),
    re.compile(
        r"\bquestions?\b[^.!?]{0,30}?\b(?:has|have) been (?:noted|recorded|saved|logged)\b", re.I
    ),
)
_BOOKING_WORDS = re.compile(r"\b(?:consultation|appointment|meeting|slot|call|session)\b", re.I)


_CLAUSE_BREAK = re.compile(r"[,;:\u2013\u2014]|\s-\s")


def _claims(sentence: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """A claim counts unless it is negated within its own clause, before or inside the matched
    phrase: "no consultation has been booked" is negated, but "Don't worry, the lawyer has
    accepted" and "it has been booked, no need to call" are claims."""
    for pattern in patterns:
        for match in pattern.finditer(sentence):
            breaks = list(_CLAUSE_BREAK.finditer(sentence, 0, match.start()))
            clause_start = breaks[-1].end() if breaks else 0
            if not _NEGATION.search(sentence[clause_start : match.end()]):
                return True
    return False


def check_reply(reply: str, state: ConversationState) -> list[str]:
    violations: list[str] = []
    accepted = any(status == "ACCEPTED" for _, status in state.escalations)
    question_missing = any(
        f.key == "caller_questions" and f.status is FieldStatus.MISSING for f in state.fields
    )
    for sentence in _SENTENCE.findall(reply):
        if (
            state.appointment is None
            and _BOOKING_WORDS.search(sentence)
            and _claims(sentence, _BOOKING_CLAIMS)
            and FALSE_BOOKING not in violations
        ):
            violations.append(FALSE_BOOKING)
        if (
            not accepted
            and _claims(sentence, _ACCEPTANCE_CLAIMS)
            and UNCONFIRMED_ACCEPTANCE not in violations
        ):
            violations.append(UNCONFIRMED_ACCEPTANCE)
        if (
            question_missing
            and _claims(sentence, _QUESTION_CLAIMS)
            and UNSAVED_QUESTION not in violations
        ):
            violations.append(UNSAVED_QUESTION)
    return violations


def correction_instruction(violations: list[str]) -> str:
    facts = []
    if FALSE_BOOKING in violations:
        facts.append("no consultation is booked")
    if UNCONFIRMED_ACCEPTANCE in violations:
        facts.append("no lawyer or staff member has accepted or reviewed the enquiry")
    if UNSAVED_QUESTION in violations:
        facts.append("the caller's question has not been saved")
    return (
        "[Automated check] Your draft reply was blocked: the database shows that "
        + " and ".join(facts)
        + ". Rewrite your reply to the caller without that claim. Do not call any tools."
    )


def safe_reply(violations: list[str]) -> str:
    parts = ["Let me be precise about where things stand."]
    if FALSE_BOOKING in violations:
        parts.append("No consultation has been booked yet.")
    if UNCONFIRMED_ACCEPTANCE in violations:
        parts.append(
            "Your enquiry is waiting for review by the firm's team, and nobody has accepted it yet."
        )
    if UNSAVED_QUESTION in violations:
        parts.append(
            "I haven't recorded your question yet. Could you repeat it so I can note it for "
            "the firm's lawyers?"
        )
    parts.append("Is there anything else you'd like me to note for the team?")
    return " ".join(parts)
