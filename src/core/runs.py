"""Durable model-call receipts, separate from the shared workflow trace."""

import asyncio
import json

from src.core.time_utils import now


class RunRecorder:
    def __init__(self, database):
        self.database = database

    async def begin(self, *, call_id, trace_id, actor, request, params, tokens_in):
        await asyncio.to_thread(
            self.database.run_audit_transaction,
            lambda c: c.execute(
                "INSERT INTO runs(call_id,trace_id,at,actor,profile,params_json,"
                "system,user,tokens_in,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    trace_id,
                    now(),
                    actor,
                    request.profile,
                    json.dumps(params, ensure_ascii=False),
                    request.system,
                    request.user,
                    tokens_in,
                    "running",
                ),
            ),
        )

    async def finish(
        self,
        call_id,
        *,
        output=None,
        thought=None,
        tokens_out=None,
        duration_ms,
        model=None,
        cost_usd=None,
        error=None,
    ):
        await asyncio.to_thread(
            self.database.run_audit_transaction,
            lambda c: c.execute(
                "UPDATE runs SET output=?,thought=?,tokens_out=?,duration_ms=?,model=?,"
                "cost_usd=?,status=?,error=? WHERE call_id=?",
                (
                    output,
                    thought,
                    tokens_out,
                    duration_ms,
                    model,
                    cost_usd,
                    "failed" if error else "completed",
                    error,
                    call_id,
                ),
            ),
        )
