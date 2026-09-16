"""Adapters from durable learner actions to the existing validated components."""

import asyncio
import json

import structlog

from src.core.time_utils import add_elapsed
from src.orchestrator import Event
from src.runner import Deferred
from src.writer import WriteResult


class LearningPipeline:
    def __init__(
        self,
        database,
        sources,
        extractor,
        quiz,
        writer,
        curator,
        *,
        context_provider,
        rhythm,
        rng,
        on_draft=None,
        alert=None,
        pass_rule=None,
        topics_map=None,
        on_curator=None,
    ):
        self.database, self.sources = database, sources
        self.extractor, self.quiz, self.writer, self.curator = (
            extractor,
            quiz,
            writer,
            curator,
        )
        self.context_provider, self.rhythm, self.rng = context_provider, rhythm, rng
        self.on_draft, self.alert = on_draft, alert
        self.pass_rule, self.topics_map, self.on_curator = (
            pass_rule,
            topics_map,
            on_curator,
        )
        self.handlers = {
            "extract": self.extract,
            "found": self.write_found,
            "reading_timer": self.reading_timer,
            "impression": self.write_impression,
            "finish_ingestion": self.finish_ingestion,
            "assess": self.assess,
            "struggle": self.write_struggle,
            "quiz": self.self_quiz,
            "summary": self.write_summary,
            "exam": self.prepare_exam,
            "grade": self.grade_exam,
            "select_articles": self.select_articles,
            "announce_topic": self.announce_topic,
            "alert": self.send_alert,
        }

    @staticmethod
    def outcome(action, kind, at, **values):
        identity = structlog.contextvars.get_contextvars().get("action_id") or (
            f"{action.trace_id}:{action.kind}:{action.article_id}"
        )
        return Event(
            f"{identity}:{kind}",
            kind,
            action.trace_id,
            at,
            **values,
        )

    async def extract(self, action, at):
        await self.extractor.extract(self.sources[action.article_id])
        return self.outcome(action, "extracted", at, article_id=action.article_id)

    async def reading_timer(self, action, at):
        # Section 3 constrains this particular pause to 10–25 minutes.
        delay = min(25, max(10, self.rhythm.gap_minutes(self.rng)))
        due = add_elapsed(at, minutes=delay)
        if self.rhythm.window(due) is None:
            due = self.rhythm.next_window(due).start
        return Deferred(self.outcome(action, "reading_due", due), due)

    async def finish_ingestion(self, action, at):
        return self.outcome(action, "ingested", at)

    async def assess(self, action, at):
        return self.outcome(action, "assess", at)

    def _receipt(self, action_id):
        if not action_id:
            return None
        with self.database.connection() as c:
            row = c.execute(
                "SELECT p.* FROM runs r JOIN posts p "
                "ON p.id=json_extract(r.params_json,'$.post_id') "
                "WHERE json_extract(r.params_json,'$.action_id')=? "
                "ORDER BY r.at DESC LIMIT 1",
                (action_id,),
            ).fetchone()
        return WriteResult(row["id"], row["state"], row["text"], 0) if row else None

    async def _write(self, kind, action, at, **values):
        action_id = structlog.contextvars.get_contextvars().get("action_id")
        result = await asyncio.to_thread(self._receipt, action_id)
        if result is None:
            blocks = await self.context_provider(at)
            result = await self.writer.generate(kind, **blocks, **values)
        if result.status == "blocked":
            raise ValueError(
                "Generation was blocked after scheduling; reschedule explicitly"
            )
        if result.status in {"draft", "queued", "published"} and self.on_draft:
            await self.on_draft(result, trace_id=action.trace_id)
        return result

    async def write_found(self, action, at):
        source = self.sources[action.article_id]
        await self._write(
            "found",
            action,
            at,
            article_title=source.title,
            article_source=source.url or source.path.name,
            article_kind=source.kind,
            given_by=source.given_by,
            topic=source.topic,
        )

    async def write_impression(self, action, at):
        names, _ = await asyncio.to_thread(self.quiz._question_context, action.topic)
        await self._write(
            "impression",
            action,
            at,
            article_title=self.sources[action.article_id].title,
            fresh_nodes=names,
            topic=action.topic,
        )

    async def write_struggle(self, action, at):
        def snapshot():
            with self.database.connection() as c:
                count = c.execute(
                    "SELECT count(*) FROM sources WHERE topic=?", (action.topic,)
                ).fetchone()[0]
                questions = [
                    dict(row)
                    for row in c.execute(
                        "SELECT text,verdict FROM questions WHERE topic=? "
                        "AND verdict!='answered'",
                        (action.topic,),
                    )
                ]
            return count, questions

        count, questions = await asyncio.to_thread(snapshot)
        await self._write(
            "struggle",
            action,
            at,
            topic=action.topic,
            articles_read=count,
            confusion=questions,
        )

    async def self_quiz(self, action, at):
        result = await self.quiz.run(action.topic)
        return self.outcome(
            action, "quiz_done", at, total=len(result.results), answered=result.answered
        )

    async def write_summary(self, action, at):
        result = await self._write("summary", action, at, topic=action.topic)
        return self.outcome(
            action,
            "summary_written",
            at,
            post_id=result.id,
            status="draft"
            if result.status in {"queued", "published"}
            else result.status,
        )

    async def prepare_exam(self, action, at):
        def snapshot():
            with self.database.connection() as c:
                post = c.execute(
                    "SELECT text FROM posts WHERE id=?", (action.post_id,)
                ).fetchone()
                count = c.execute(
                    "SELECT count(*) FROM sources WHERE topic=?", (action.topic,)
                ).fetchone()[0]
            return post[0], count

        summary, count = await asyncio.to_thread(snapshot)
        exam_trace = f"{action.trace_id}:exam:{action.post_id}"
        receipt = await self.curator.prepare_exam(
            action.topic,
            summary_post=summary,
            given=count,
            read=count,
            n=self.quiz.settings.questions_per_round,
            trace_id=exam_trace,
        )
        await self._curator_receipt("exam", receipt, action)
        return self.outcome(action, "exam_prepared", at, exam_trace_id=exam_trace)

    async def _curator_receipt(self, phase, receipt, action):
        if self.on_curator:
            await self.on_curator(phase, receipt, trace_id=action.trace_id)

    async def grade_exam(self, action, at):
        if not self.pass_rule:
            raise ValueError(
                "TODO(CURATOR-GRADING-POLICY): an explicit pass rule is required"
            )
        if not action.exam_trace_id:
            raise ValueError("The exam receipt identity is required")
        receipt = await asyncio.to_thread(
            self.curator._replay, "grade", action.exam_trace_id
        )
        if receipt is None:
            exam = await asyncio.to_thread(
                self.curator._replay, "exam", action.exam_trace_id
            )
            if exam is None:
                raise ValueError("The prepared exam receipt is missing")

            def questions():
                with self.database.connection() as c:
                    return [
                        dict(
                            c.execute(
                                "SELECT id,question,answer FROM exams "
                                "WHERE id=? AND topic=?",
                                (key, action.topic),
                            ).fetchone()
                        )
                        for key in exam["exam_ids"]
                    ]

            answers = {}
            for question in await asyncio.to_thread(questions):
                answer = question["answer"]
                if answer is None:
                    result = await self.quiz.answer(
                        question["question"], topic=action.topic
                    )
                    answer = result.answer if result.verdict == "answered" else ""
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c, key=question["id"], answer=answer: c.execute(
                            "UPDATE exams SET answer=? WHERE id=? AND topic=?",
                            (answer, key, action.topic),
                        ),
                    )
                answers[question["id"]] = answer
            receipt = await self.curator.grade(
                action.topic,
                answers=answers,
                pass_rule=self.pass_rule,
                trace_id=action.exam_trace_id,
            )
        await self._curator_receipt("grade", receipt, action)
        return self.outcome(action, "graded", at, status=receipt["result"]["overall"])

    async def select_articles(self, action, at):
        if self.topics_map is None:
            raise ValueError(
                "TODO(TOPIC-CATALOGUE): an explicit topic catalogue is required"
            )
        if not action.exam_trace_id:
            raise ValueError("The exam receipt identity is required")

        def given_articles():
            with self.database.connection() as c:
                ids = {row[0] for row in c.execute("SELECT id FROM sources")}
                for row in c.execute(
                    "SELECT reasoning FROM curator_log WHERE json_valid(reasoning) "
                    "AND json_extract(reasoning,'$.phase')='select' "
                    "AND json_extract(reasoning,'$.trace_id')!=?",
                    (action.exam_trace_id,),
                ):
                    ids.update(
                        item["id"]
                        for item in json.loads(row[0])["receipt"]["result"]["articles"]
                    )
            return sorted(ids)

        passed = (action.core_articles, action.adjacent_articles) == (2, 4)
        if not passed and (action.core_articles, action.adjacent_articles) != (4, 2):
            raise ValueError("Invalid article allocation")
        receipt = await self.curator.select_articles(
            action.topic,
            status="TOPIC_DONE" if passed else "REMEDIAL",
            topics_map=self.topics_map,
            library_index=[
                dict(
                    id=source.id,
                    topic=source.topic,
                    title=source.title,
                    kind=source.kind,
                )
                for source in self.sources.values()
            ],
            given_articles=await asyncio.to_thread(given_articles),
            exam_result="pass" if passed else "fail",
            trace_id=action.exam_trace_id,
        )
        selection = receipt["result"]
        await self._curator_receipt("select", receipt, action)
        if selection["shortage"]:
            if self.alert:
                await self.alert(action.trace_id, "library_shortage")
            return None
        ids = tuple(item["id"] for item in selection["articles"])
        if passed:
            adjacent_topics = {
                self.sources[item["id"]].topic
                for item in selection["articles"]
                if item["kind"] == "adjacent"
            }
            if (
                not selection["topic_switch"]
                or selection["next_topic"] not in adjacent_topics
            ):
                raise ValueError(
                    "The next topic must come from the selected adjacent articles"
                )
            return self.outcome(
                action,
                "articles_selected",
                at,
                next_topic=selection["next_topic"],
                article_ids=ids,
            )
        for key in ids:
            await self.extractor.extract(self.sources[key])
        return self.outcome(
            action,
            "remediation_ingested",
            at,
            article_ids=tuple(
                key for key in ids if self.sources[key].topic == action.topic
            ),
        )

    async def announce_topic(self, action, at):
        # The validated selection's public comment is delivered by on_curator.
        structlog.get_logger("blogai.pipeline").info(
            "topic_selected",
            topic=action.topic,
            article_ids=action.article_ids,
            trace_id=action.trace_id,
        )

    async def send_alert(self, action, at):
        if self.alert:
            await self.alert(action.trace_id, "summary_killed")
