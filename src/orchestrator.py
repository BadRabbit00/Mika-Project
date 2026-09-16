"""Immutable learner transitions. Time and outcomes are explicit input data."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from src.core.time_utils import require_aware


class Phase(StrEnum):
    IDLE = "IDLE"
    ANNOUNCED = "ANNOUNCED"
    READING = "READING"
    INGESTED = "INGESTED"
    WAITING = "WAITING"
    SELF_QUIZ = "SELF_QUIZ"
    SUMMARY = "SUMMARY"
    EXAM = "EXAM"
    TOPIC_DONE = "TOPIC_DONE"
    REMEDIAL = "REMEDIAL"


@dataclass(frozen=True)
class State:
    min_articles: int
    quiz_threshold: float
    phase: Phase = Phase.IDLE
    topic: str | None = None
    article_id: str | None = None
    prepared: bool = False
    articles: tuple[str, ...] = ()
    post_id: str | None = None
    exam_trace_id: str | None = None
    pending_articles: tuple[str, ...] = ()
    seen: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.min_articles) is not int or self.min_articles < 1:
            raise ValueError("A positive article threshold is required")
        if not 0 <= self.quiz_threshold <= 1:
            raise ValueError("Invalid quiz threshold")
        if not isinstance(self.phase, Phase):
            raise TypeError("A declared learner phase is required")
        if any(
            type(value) is not tuple
            for value in (
                self.articles,
                self.pending_articles,
                self.seen,
            )
        ):
            raise TypeError("State collections must be immutable")


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    trace_id: str
    at: datetime
    article_id: str | None = None
    topic: str | None = None
    total: int = 0
    answered: int = 0
    status: str | None = None
    post_id: str | None = None
    next_topic: str | None = None
    exam_trace_id: str | None = None
    article_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))
        if not self.id or not self.kind or not self.trace_id:
            raise ValueError("Event identity, kind, and trace are required")
        if type(self.article_ids) is not tuple:
            raise TypeError("Event collections must be immutable")
        if (
            type(self.total) is not int
            or type(self.answered) is not int
            or not 0 <= self.answered <= self.total
        ):
            raise ValueError("Invalid quiz outcome counts")


@dataclass(frozen=True)
class Action:
    kind: str
    trace_id: str
    topic: str | None
    article_id: str | None = None
    post_id: str | None = None
    core_articles: int = 0
    adjacent_articles: int = 0
    exam_trace_id: str | None = None
    article_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.article_ids) is not tuple:
            raise TypeError("Action collections must be immutable")


def transition(state: State, event: Event) -> tuple[State, list[Action]]:
    if event.id in state.seen:
        return state, []
    actions = []
    new = replace(state, seen=(*state.seen, event.id))

    def action(kind, **fields):
        actions.append(
            Action(
                kind,
                event.trace_id,
                new.topic,
                article_id=new.article_id,
                post_id=new.post_id,
                exam_trace_id=new.exam_trace_id,
                article_ids=new.pending_articles,
                **fields,
            )
        )

    match event.kind:
        case "article" if state.phase in {Phase.IDLE, Phase.WAITING}:
            if not event.article_id or not event.topic:
                raise ValueError("An article identity and topic are required")
            if state.topic not in (None, event.topic):
                raise ValueError("Select the new topic explicitly before admission")
            if event.article_id in state.articles:
                return new, []
            new = replace(
                new,
                phase=Phase.ANNOUNCED,
                topic=event.topic,
                article_id=event.article_id,
                prepared=False,
                post_id=None,
                pending_articles=tuple(
                    key for key in state.pending_articles if key != event.article_id
                ),
            )
            # Extraction may finish before the visible reading pause has elapsed.
            action("extract")
            action("found")
            action("reading_timer")
        case "extracted" if state.phase in {Phase.ANNOUNCED, Phase.READING}:
            if event.article_id != state.article_id:
                raise ValueError("Extraction belongs to a different article")
            new = replace(new, prepared=True)
            if state.phase == Phase.READING:
                action("finish_ingestion")
        case "reading_due" if state.phase == Phase.ANNOUNCED:
            new = replace(new, phase=Phase.READING)
            action("impression")
            if new.prepared:
                action("finish_ingestion")
        case "ingested" if state.phase == Phase.READING and state.prepared:
            articles = tuple(dict.fromkeys((*state.articles, state.article_id)))
            new = replace(new, phase=Phase.INGESTED, articles=articles)
            action("assess")
        case "assess" if state.phase == Phase.INGESTED:
            if len(state.articles) < state.min_articles:
                new = replace(new, phase=Phase.WAITING)
                action("struggle")
            else:
                new = replace(new, phase=Phase.SELF_QUIZ)
                action("quiz")
        case "quiz_done" if state.phase == Phase.SELF_QUIZ:
            passed = (
                event.total >= 5
                and event.answered / event.total >= state.quiz_threshold
            )
            new = replace(new, phase=Phase.SUMMARY if passed else Phase.WAITING)
            action("summary" if passed else "struggle")
        case "summary_written" if state.phase == Phase.SUMMARY:
            if event.status == "draft" and event.post_id:
                new = replace(new, post_id=event.post_id)
                action("exam")
            elif event.status == "killed":
                action("alert")
            else:
                raise ValueError("Only a validated summary can schedule an exam")
        case "exam_prepared" if state.phase == Phase.SUMMARY and state.post_id:
            new = replace(
                new,
                phase=Phase.EXAM,
                exam_trace_id=event.exam_trace_id or event.trace_id,
            )
            action("grade")
        case "graded" if state.phase == Phase.EXAM:
            if event.status not in {"pass", "fail"}:
                raise ValueError("A declared exam verdict is required")
            passed = event.status == "pass"
            new = replace(new, phase=Phase.TOPIC_DONE if passed else Phase.REMEDIAL)
            action(
                "select_articles",
                core_articles=2 if passed else 4,
                adjacent_articles=4 if passed else 2,
            )
        case "articles_selected" if state.phase == Phase.TOPIC_DONE:
            if not event.next_topic:
                raise ValueError("A validated adjacent topic is required")
            new = replace(
                new,
                phase=Phase.IDLE,
                topic=event.next_topic,
                articles=(),
                article_id=None,
                post_id=None,
                prepared=False,
                pending_articles=event.article_ids,
            )
            action("announce_topic")
        case "remediation_ingested" if state.phase == Phase.REMEDIAL:
            new = replace(
                new,
                phase=Phase.INGESTED,
                articles=tuple(dict.fromkeys((*state.articles, *event.article_ids))),
            )
            action("assess")
        case _:
            raise ValueError(f"Invalid transition: {state.phase} / {event.kind}")
    return new, actions
