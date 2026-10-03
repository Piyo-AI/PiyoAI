from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN, scripted

from piyo.models.turn import TextDelta, ToolCall, TurnDone
from piyo.safety import ApprovalDeferred, ApprovalRequest
from piyo.scheduler import RuleError, RunResult, Scheduler, SchedulerStore, describe, next_run, parse_rule
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import Risk, RunContext, Tool
from piyo.tools.schedule import schedule_tools

IST = ZoneInfo("Asia/Kolkata")  # UTC+5:30, no DST
NY = ZoneInfo("America/New_York")


def at(text, tz=UTC):
    return datetime.fromisoformat(text).replace(tzinfo=tz)


# -- rules ---------------------------------------------------------------------------------


def test_daily_weekdays_and_weekly_pick_the_next_local_time():
    now = at("2026-10-05T07:00", IST)  # a Monday, 07:00 in India
    assert next_run({"kind": "daily", "time": "08:00"}, now, IST) == at("2026-10-05T08:00", IST)
    assert next_run({"kind": "daily", "time": "08:00"}, at("2026-10-05T08:00", IST), IST) == at(
        "2026-10-06T08:00", IST
    )
    friday_evening = at("2026-10-09T18:00", IST)
    assert next_run({"kind": "weekdays", "time": "08:00"}, friday_evening, IST) == at("2026-10-12T08:00", IST)
    weekly = {"kind": "weekly", "days": [2, 6], "time": "09:30"}  # Wednesday and Sunday
    assert next_run(weekly, now, IST) == at("2026-10-07T09:30", IST)
    assert next_run(weekly, at("2026-10-07T10:00", IST), IST) == at("2026-10-11T09:30", IST)


def test_a_daily_run_stays_at_local_time_across_a_dst_change():
    rule = {"kind": "daily", "time": "08:00"}
    before = next_run(rule, at("2026-11-01T00:00", NY), NY)  # clocks go back that night
    after = next_run(rule, at("2026-11-01T09:00", NY), NY)
    assert before.astimezone(NY).hour == 8 and after.astimezone(NY).hour == 8
    assert after.astimezone(NY).date().day == 2
    assert after - before == timedelta(hours=24)


def test_once_and_every():
    now = at("2026-10-05T10:00", IST)
    once = {"kind": "once", "at": "2026-10-05T12:00"}
    assert next_run(once, now, IST) == at("2026-10-05T12:00", IST)
    assert next_run(once, at("2026-10-05T12:00", IST), IST) is None  # already passed
    assert next_run({"kind": "once", "at": "2026-10-05T12:00+00:00"}, now, IST) == at("2026-10-05T12:00", UTC)
    assert next_run({"kind": "every", "minutes": 90}, now, IST) == at("2026-10-05T11:30", IST)


@pytest.mark.parametrize(
    "rule, message",
    [
        (None, "required"),
        ({"kind": "hourly"}, "Unknown"),
        ({"kind": "daily", "time": "8am"}, "HH:MM"),
        ({"kind": "daily", "time": "25:00"}, "HH:MM"),
        ({"kind": "weekly", "days": [], "time": "08:00"}, "weekday"),
        ({"kind": "weekly", "days": [7], "time": "08:00"}, "weekday"),
        ({"kind": "every", "minutes": 1}, "5 minutes"),
        ({"kind": "every", "minutes": True}, "5 minutes"),
        ({"kind": "once", "at": "tomorrow"}, "YYYY-MM-DD"),
    ],
)
def test_bad_rules_have_readable_messages(rule, message):
    with pytest.raises(RuleError, match=message):
        parse_rule(rule)


def test_rules_are_described_in_words():
    assert describe(parse_rule({"kind": "weekdays", "time": "8:00"})) == "Weekdays at 08:00"
    assert (
        describe(parse_rule({"kind": "weekly", "days": [0, 4, 4], "time": "18:30"}))
        == "Every Monday, Friday at 18:30"
    )
    assert describe(parse_rule({"kind": "every", "minutes": 120})) == "Every 2 hour(s)"


# -- service -------------------------------------------------------------------------------


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def parts(tmp_path):
    runs: list[str] = []
    held_request = ApprovalRequest("mail.send", {"to": "a@b.c"}, Risk.CONFIRM, "Send mail to a@b.c")
    behaviour = {"fail": None, "hold": False}

    async def runner(job, approver):
        runs.append(job.title)
        if behaviour["fail"]:
            raise RuntimeError(behaviour["fail"])
        if behaviour["hold"]:
            with pytest.raises(ApprovalDeferred):
                await approver(held_request)
        return RunResult("conv1", "done", f"Ran {job.title}")

    executed = []

    async def executor(tool, args):
        executed.append((tool, args))
        return "sent"

    clock = Clock(at("2026-10-05T07:00", IST))
    store = SchedulerStore(tmp_path / "s.db")
    scheduler = Scheduler(store, runner, executor, clock=clock, tz=IST)
    return scheduler, store, clock, runs, behaviour, executed


async def test_a_due_job_runs_once_and_moves_forward(parts):
    scheduler, store, clock, runs, _, _ = parts
    job = scheduler.create("Brief", "Give me my brief", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    await scheduler.tick()
    assert runs == []  # not due yet
    clock.now = at("2026-10-05T08:00:30", IST)
    await scheduler.tick()
    await scheduler.tick()  # a second tick must not run it again
    assert runs == ["Brief"]
    saved = store.get_job(job.id)
    assert saved.last_status == "done" and saved.last_conversation_id == "conv1"
    assert datetime.fromisoformat(saved.next_run) == at("2026-10-06T08:00", IST)
    [event] = store.list_events()
    assert event.kind == "finished" and event.body == "Ran Brief" and not event.read


async def test_a_one_time_reminder_runs_then_finishes(parts):
    scheduler, store, clock, runs, _, _ = parts
    job = scheduler.create(
        "Call mum", "Remind me to call mum", {"kind": "once", "at": "2026-10-05T09:00"}, "ollama", "m"
    )
    clock.now = at("2026-10-05T09:01", IST)
    await scheduler.tick()
    saved = store.get_job(job.id)
    assert runs == ["Call mum"] and saved.next_run is None and saved.enabled is False


async def test_catch_up_runs_a_recent_miss_and_records_an_old_one(parts):
    scheduler, store, clock, runs, _, _ = parts
    recent = scheduler.create("Recent", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    old = scheduler.create("Old", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    store.update_job(recent.id, next_run=at("2026-10-05T08:00", IST).astimezone(UTC).isoformat())
    store.update_job(old.id, next_run=at("2026-10-01T08:00", IST).astimezone(UTC).isoformat())
    clock.now = at("2026-10-05T20:00", IST)  # the app was closed
    await scheduler.tick()
    assert runs == ["Recent"]
    assert store.get_job(old.id).last_status == "missed"
    assert datetime.fromisoformat(store.get_job(old.id).next_run) == at("2026-10-06T08:00", IST)
    assert {e.kind for e in store.list_events()} == {"finished", "missed"}


async def test_a_stale_every_job_is_skipped_not_caught_up(parts):
    scheduler, store, clock, runs, _, _ = parts
    job = scheduler.create("Poll", "x", {"kind": "every", "minutes": 30}, "ollama", "m")
    clock.now += timedelta(hours=5)
    await scheduler.tick()
    assert runs == [] and store.get_job(job.id).last_status == "missed"


async def test_failures_are_recorded_and_do_not_stop_the_scheduler(parts):
    scheduler, store, clock, runs, behaviour, _ = parts
    behaviour["fail"] = "No API key for Ollama"
    job = scheduler.create("Brief", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    clock.now = at("2026-10-05T08:01", IST)
    await scheduler.tick()
    assert store.get_job(job.id).last_status == "error"
    assert store.list_events()[0].kind == "failed" and "No API key" in store.list_events()[0].body
    behaviour["fail"] = None
    clock.now = at("2026-10-06T08:01", IST)
    await scheduler.tick()
    assert store.get_job(job.id).last_status == "done"


async def test_actions_needing_approval_are_held_then_run_only_when_approved(parts):
    scheduler, store, clock, runs, behaviour, executed = parts
    behaviour["hold"] = True
    job = scheduler.create("Mail", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    clock.now = at("2026-10-05T08:01", IST)
    await scheduler.tick()
    assert executed == []  # nothing happened
    assert store.get_job(job.id).last_status == "waiting"
    [item] = store.list_pending()
    assert item.summary == "Send mail to a@b.c" and item.conversation_id == "conv1"
    assert {e.kind for e in store.list_events()} == {"finished", "approval"}

    done = await scheduler.approve(item.id)
    assert (
        executed == [("mail.send", {"to": "a@b.c"})] and done.status == "approved" and done.result == "sent"
    )
    with pytest.raises(ValueError, match="already handled"):
        await scheduler.approve(item.id)


async def test_declined_and_expired_actions_never_run(parts):
    scheduler, store, clock, _, behaviour, executed = parts
    behaviour["hold"] = True
    scheduler.create("Mail", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    clock.now = at("2026-10-05T08:01", IST)
    await scheduler.tick()
    clock.now = at("2026-10-05T08:02", IST)
    await scheduler.run_now(store.list_jobs()[0].id)
    first, second = store.list_pending()
    assert scheduler.decline(first.id).status == "declined"
    clock.now += timedelta(days=8)
    with pytest.raises(ValueError, match="too old"):
        await scheduler.approve(second.id)
    assert executed == [] and store.get_pending(second.id).status == "expired"


async def test_a_held_action_that_fails_is_reported(parts):
    scheduler, store, clock, _, behaviour, _ = parts

    async def broken(tool, args):
        raise RuntimeError("mailbox unavailable")

    scheduler._executor = broken
    behaviour["hold"] = True
    scheduler.create("Mail", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    clock.now = at("2026-10-05T08:01", IST)
    await scheduler.tick()
    item = store.list_pending()[0]
    with pytest.raises(ValueError, match="mailbox unavailable"):
        await scheduler.approve(item.id)
    assert store.get_pending(item.id).status == "failed"


def test_create_and_update_validate(parts):
    scheduler, store, clock, *_ = parts
    with pytest.raises(RuleError, match="already passed"):
        scheduler.create("Late", "x", {"kind": "once", "at": "2026-10-05T06:00"}, "ollama", "m")
    with pytest.raises(RuleError, match="title"):
        scheduler.create("  ", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    job = scheduler.create("Brief", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    off = scheduler.update(job.id, enabled=False)
    assert off.enabled is False and off.next_run is None
    on = scheduler.update(job.id, enabled=True, rule={"kind": "weekdays", "time": "09:00"})
    assert on.enabled and on.rule["kind"] == "weekdays" and on.next_run


def test_deleting_a_job_expires_its_held_actions(parts):
    scheduler, store, *_ = parts
    job = scheduler.create("Brief", "x", {"kind": "daily", "time": "08:00"}, "ollama", "m")
    item = store.add_pending(job, "mail.send", {}, "Send")
    store.delete_job(job.id)
    assert store.get_pending(item.id).status == "expired" and store.list_pending() == []


# -- model tools ---------------------------------------------------------------------------


async def test_schedule_tools_need_approval_to_create_but_not_to_list(parts):
    scheduler, *_ = parts
    create, listing, cancel = schedule_tools(scheduler)
    assert create.risk is Risk.CONFIRM and cancel.risk is Risk.CONFIRM and listing.risk is Risk.AUTO
    ctx = RunContext(skills=None, provider_id="ollama", model="m")
    out = await create.handler(
        {"title": "Brief", "prompt": "Give me my brief", "when": {"kind": "daily", "time": "08:00"}}, ctx
    )
    assert "Scheduled [" in out and "Every day at 08:00" in out
    assert "Brief: Every day at 08:00" in await listing.handler({}, ctx)
    assert "Every day at 08:00" in create.summary_of(
        {"title": "Brief", "prompt": "p", "when": {"kind": "daily", "time": "08:00"}}
    )
    with pytest.raises(ValueError, match="HH:MM"):
        await create.handler({"title": "x", "prompt": "y", "when": {"kind": "daily", "time": "morning"}}, ctx)
    with pytest.raises(ValueError, match="not available"):
        await create.handler({"title": "x", "prompt": "y", "when": {}}, RunContext(skills=None))
    job_id = scheduler.store.list_jobs()[0].id
    assert "Cancelled Brief" in await cancel.handler({"id": job_id}, ctx)
    assert await listing.handler({}, ctx) == "Nothing is scheduled."


# -- API and a real scheduled run ----------------------------------------------------------


def make_client(tmp_path, turn_fn):
    skills = SkillRegistry(builtin_dir=tmp_path / "a", user_dir=tmp_path / "b")
    store = SchedulerStore(tmp_path / "api.db")
    app = server.create_app(
        TOKEN, skills=skills, turn_fn=turn_fn, scheduler_store=store, start_scheduler=False
    )
    sent = []

    async def send(args, ctx):
        sent.append(args)
        return "sent"

    app.state.tools.register(
        Tool(
            "mail.send",
            "Send mail",
            send,
            risk=Risk.CONFIRM,
            core=True,
            summarize=lambda a: f"Send mail to {a['to']}",
        )
    )
    return TestClient(app), sent


def test_api_job_lifecycle_and_validation(tmp_path):
    client, _ = make_client(tmp_path, scripted())
    body = {
        "title": "Brief",
        "prompt": "Give me my brief",
        "rule": {"kind": "daily", "time": "08:00"},
        "provider": "ollama",
        "model": "m",
    }
    made = client.post("/api/scheduler/jobs", json=body, headers=AUTH)
    assert made.status_code == 201 and made.json()["when"] == "Every day at 08:00" and made.json()["next_run"]
    jid = made.json()["id"]
    assert (
        client.post(
            "/api/scheduler/jobs", json={**body, "rule": {"kind": "every", "minutes": 1}}, headers=AUTH
        ).status_code
        == 400
    )
    assert (
        client.post("/api/scheduler/jobs", json={**body, "provider": "nope"}, headers=AUTH).status_code == 404
    )
    off = client.put(f"/api/scheduler/jobs/{jid}", json={"enabled": False}, headers=AUTH).json()
    assert off["enabled"] is False and off["next_run"] is None
    assert client.put("/api/scheduler/jobs/nope", json={}, headers=AUTH).status_code == 404
    assert len(client.get("/api/scheduler/jobs", headers=AUTH).json()) == 1
    assert client.delete(f"/api/scheduler/jobs/{jid}", headers=AUTH).status_code == 204
    assert client.get("/api/scheduler/jobs", headers=AUTH).json() == []


def test_a_scheduled_run_holds_a_send_until_the_user_approves(tmp_path):
    turn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="mail__send", arguments={"to": "a@b.c"})])],
        [TextDelta("Waiting for approval."), TurnDone(text="Waiting for approval.")],
    )
    client, sent = make_client(tmp_path, turn)
    body = {
        "title": "Mail",
        "prompt": "Send the report to a@b.c",
        "rule": {"kind": "daily", "time": "08:00"},
        "provider": "ollama",
        "model": "m",
    }
    jid = client.post("/api/scheduler/jobs", json=body, headers=AUTH).json()["id"]

    with client:  # one event loop, so the background run can finish
        assert client.post(f"/api/scheduler/jobs/{jid}/run", headers=AUTH).status_code == 202
        for _ in range(100):
            events = client.get("/api/scheduler/events", params={"unread": True}, headers=AUTH).json()
            if {e["kind"] for e in events} >= {"finished", "approval"}:
                break
            import time

            time.sleep(0.05)
        assert sent == []  # the send did not happen on its own
        [item] = client.get("/api/scheduler/pending", headers=AUTH).json()
        assert item["summary"] == "Send mail to a@b.c" and item["conversation_id"]
        job = client.get("/api/scheduler/jobs", headers=AUTH).json()[0]
        assert job["last_status"] == "waiting"
        conv = client.get(f"/api/conversations/{item['conversation_id']}", headers=AUTH).json()
        assert conv["title"] == "Scheduled: Mail"

        done = client.post(f"/api/scheduler/pending/{item['id']}/approve", headers=AUTH)
        assert done.status_code == 200 and done.json()["status"] == "approved"
        assert sent == [{"to": "a@b.c"}]
        assert client.post(f"/api/scheduler/pending/{item['id']}/approve", headers=AUTH).status_code == 400
        assert client.post(f"/api/scheduler/pending/{item['id']}/maybe", headers=AUTH).status_code == 404
        assert client.get("/api/scheduler/pending", headers=AUTH).json() == []

        assert client.post("/api/scheduler/events/read", json={"ids": None}, headers=AUTH).status_code == 204
        assert client.get("/api/scheduler/events", params={"unread": True}, headers=AUTH).json() == []
