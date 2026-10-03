import { FormEvent, useEffect, useState } from "react";

import { api, SchedulerJob, SchedulerPending } from "./api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type Kind = "once" | "daily" | "weekdays" | "weekly" | "every";

function when(iso: string | null): string {
  if (!iso) return "no more runs";
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const STATUS: Record<string, string> = {
  done: "Last run finished",
  error: "Last run failed",
  waiting: "Last run is waiting for your approval",
  missed: "Missed (Piyo was not running)",
};

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function defaultOnce(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function SchedulePage({
  providerId,
  model,
  onOpenConversation,
}: {
  providerId: string;
  model: string;
  onOpenConversation: (id: string) => void;
}) {
  const [jobs, setJobs] = useState<SchedulerJob[] | null>(null);
  const [pending, setPending] = useState<SchedulerPending[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<Kind>("daily");
  const [time, setTime] = useState("08:00");
  const [onceAt, setOnceAt] = useState(defaultOnce);
  const [days, setDays] = useState<number[]>([0]);
  const [minutes, setMinutes] = useState(60);

  const load = () =>
    Promise.all([api.schedulerJobs(), api.schedulerPending()])
      .then(([j, p]) => {
        setJobs(j);
        setPending(p);
      })
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load();
  }, []);

  const attempt = async (fn: () => Promise<unknown>) => {
    setError(null);
    setNote(null);
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  const rule = () => {
    switch (kind) {
      case "once":
        return { kind, at: onceAt };
      case "weekly":
        return { kind, days, time };
      case "every":
        return { kind, minutes };
      default:
        return { kind, time };
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!providerId || !model) {
      setError("Pick a model in the chat first; scheduled runs use the model you have selected.");
      return;
    }
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
    const ok = await attempt(() => api.createJob({ title, prompt, rule: rule(), provider: providerId, model }));
    if (ok) {
      setTitle("");
      setPrompt("");
    }
  };

  const toggleDay = (d: number) => setDays((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]));

  return (
    <section className="folders">
      <h3>Scheduled</h3>
      <p className="hint">
        Reminders and routines Piyo runs on its own, for example "every morning at 8, give me my brief". They only run
        while Piyo is open; if it was closed at the time, a job that is not too late runs when you start it. A
        scheduled run never sends, changes or deletes anything by itself: those actions wait below for your approval.
      </p>
      {error && <p className="error">{error}</p>}
      {note && <p className="hint">{note}</p>}

      {pending.length > 0 && (
        <>
          <h3>Waiting for your approval</h3>
          <ul className="providers">
            {pending.map((p) => (
              <li key={p.id}>
                <strong>{p.job_title}</strong>
                <p>{p.summary}</p>
                <p className="hint">
                  Details: {p.tool} {JSON.stringify(p.arguments).slice(0, 300)}
                </p>
                <p className="install-actions">
                  <button
                    type="button"
                    onClick={() => attempt(async () => setNote(`Done: ${(await api.decidePending(p.id, "approve")).result ?? ""}`))}
                  >
                    Approve and do it
                  </button>
                  <button type="button" className="ghost" onClick={() => attempt(() => api.decidePending(p.id, "decline"))}>
                    Decline
                  </button>
                  {p.conversation_id && (
                    <button type="button" className="ghost" onClick={() => onOpenConversation(p.conversation_id!)}>
                      Open the chat
                    </button>
                  )}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}

      <form className="schedule-form" onSubmit={create}>
        <h4>New scheduled job</h4>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Name, e.g. Morning brief" maxLength={80} />
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should Piyo do? Write it as you would in the chat, e.g. Give me my morning brief."
          rows={3}
          maxLength={4000}
        />
        <div className="memory-add">
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)} aria-label="How often">
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Certain days</option>
            <option value="every">Every few minutes or hours</option>
            <option value="once">Once</option>
          </select>
          {kind === "once" ? (
            <input type="datetime-local" value={onceAt} onChange={(e) => setOnceAt(e.target.value)} aria-label="When" />
          ) : kind === "every" ? (
            <>
              <span className="hint">every</span>
              <input
                type="number"
                min={5}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                style={{ maxWidth: 90, flex: "none" }}
                aria-label="Minutes"
              />
              <span className="hint">minutes</span>
            </>
          ) : (
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Time" style={{ flex: "none" }} />
          )}
        </div>
        {kind === "weekly" && (
          <p>
            {DAYS.map((d, i) => (
              <label key={d} className="check" style={{ marginRight: 10 }}>
                <input type="checkbox" checked={days.includes(i)} onChange={() => toggleDay(i)} />
                {d}
              </label>
            ))}
          </p>
        )}
        <p className="hint">
          Uses {model ? `${model}` : "the model you pick in the chat"}. Times are on this computer's clock.
        </p>
        <button type="submit" disabled={!title.trim() || !prompt.trim()}>
          Schedule
        </button>
      </form>

      {jobs && jobs.length === 0 && <p className="hint">Nothing is scheduled yet.</p>}
      <ul className="providers">
        {jobs?.map((j) => (
          <li key={j.id}>
            <div className="row-head">
              <strong>{j.title}</strong>
              <span className="hint skill-version">{j.when}</span>
              <label className="check" style={{ marginLeft: "auto" }}>
                <input type="checkbox" checked={j.enabled} onChange={(e) => attempt(() => api.editJob(j.id, { enabled: e.target.checked }))} />
                On
              </label>
            </div>
            <p className="hint">{j.prompt}</p>
            <p className="hint">
              {j.enabled && j.next_run ? `Next: ${when(j.next_run)}. ` : j.enabled ? "No more runs. " : "Off. "}
              {j.last_status ? `${STATUS[j.last_status] ?? j.last_status}${j.last_run ? `, ${when(j.last_run)}` : ""}.` : "Has not run yet."}
            </p>
            <p className="install-actions">
              <button
                type="button"
                className="ghost"
                onClick={() => attempt(async () => { await api.runJob(j.id); setNote("Started. You will get a notification when it finishes."); })}
              >
                Run now
              </button>
              {j.last_conversation_id && (
                <button type="button" className="ghost" onClick={() => onOpenConversation(j.last_conversation_id!)}>
                  Open last result
                </button>
              )}
              <button
                type="button"
                className="ghost"
                onClick={() => window.confirm(`Delete ${j.title}?`) && attempt(() => api.deleteJob(j.id))}
              >
                Delete
              </button>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
