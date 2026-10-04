import { ReactNode, useState } from "react";

import type { SkillOffer } from "./useSkillOffer";
import type { UpdateApi } from "./useUpdate";

export const RESTART_WARNING =
  "Piyo will close and start again to finish the update. Anything you are typing is lost, and scheduled jobs pause until it is back.";

export interface ToastAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
}

/** One notification in the bottom-right stack, in the manner of VS Code: message, buttons, a close button and a "…" menu. */
export function Toast({
  message,
  detail,
  progress,
  actions = [],
  more = [],
  onClose,
  tone,
  children,
}: {
  message: ReactNode;
  detail?: ReactNode;
  /** Percent, or null for "working, size unknown"; leave out for no bar. */
  progress?: number | null;
  actions?: ToastAction[];
  /** Entries of the "…" menu, for choices like "Don't show again". */
  more?: ToastAction[];
  onClose?: () => void;
  tone?: "error";
  children?: ReactNode;
}) {
  return (
    <div className={`toast${tone ? ` ${tone}` : ""}`} role="status">
      <div className="toast-head">
        <p className="toast-message">{message}</p>
        {onClose && (
          <button type="button" className="ghost toast-close" onClick={onClose} aria-label="Dismiss notification">
            ✕
          </button>
        )}
      </div>
      {detail && <p className="hint toast-detail">{detail}</p>}
      {children}
      {progress !== undefined && <progress max={100} value={progress ?? undefined} />}
      {(actions.length > 0 || more.length > 0) && (
        <div className="toast-actions">
          {actions.map((a) => (
            <button key={a.label} type="button" className={a.primary ? "" : "ghost"} disabled={a.disabled} onClick={a.onClick}>
              {a.label}
            </button>
          ))}
          {more.length > 0 && (
            <details className="toast-more">
              <summary aria-label="More options">⋯</summary>
              <div className="toast-menu">
                {more.map((m) => (
                  <button key={m.label} type="button" className="ghost" onClick={m.onClick}>
                    {m.label}
                  </button>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export function ToastStack({ children }: { children: ReactNode }) {
  return (
    <div className="toasts" role="region" aria-label="Notifications">
      {children}
    </div>
  );
}

/** A new version: download it in the background, then install it (after a warning) when it suits the user. */
export function UpdateToast({ update, busy, onDetails }: { update: UpdateApi; busy: boolean; onDetails: () => void }) {
  const { status, download, install } = update;
  const [hidden, setHidden] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const version = "version" in status ? status.version : "";
  const key = `${status.state}:${version}`;
  const close = () => setHidden(key); // a later stage (the download finishing) shows a toast again
  if (hidden === key) return null;

  if (status.state === "available" && !status.flagged) {
    return (
      <Toast
        message={`Piyo ${status.version} is available.`}
        actions={[
          { label: "Download", primary: true, onClick: download },
          { label: "Details", onClick: onDetails },
        ]}
        onClose={close}
      />
    );
  }
  if (status.state === "downloading") {
    return (
      <Toast
        message={`Downloading the update${status.percent !== null ? ` (${status.percent}%)` : ""}…`}
        detail="You can keep working. You choose when to install it."
        progress={status.percent}
        onClose={close}
      />
    );
  }
  if (status.state === "ready") {
    if (confirming) {
      return (
        <Toast
          message="Restart Piyo to install the update?"
          detail={`${RESTART_WARNING}${busy ? " A chat is still running; it will be stopped." : ""}`}
          actions={[
            { label: "Restart and install", primary: true, onClick: install },
            { label: "Not yet", onClick: () => setConfirming(false) },
          ]}
          onClose={() => {
            setConfirming(false);
            close();
          }}
        />
      );
    }
    return (
      <Toast
        message={`Piyo ${status.version} is ready to install.`}
        actions={[
          { label: "Install and restart", primary: true, onClick: () => setConfirming(true) },
          { label: "Later", onClick: close },
        ]}
        onClose={close}
      />
    );
  }
  if (status.state === "installing") return <Toast message="Installing the update. Piyo restarts in a moment…" progress={null} />;
  if (status.state === "error") {
    return <Toast tone="error" message={`Could not update: ${status.message}`} onClose={close} />;
  }
  return null;
}

/** After a finished chat: offer to save it as a skill, or to improve the skill it used. */
export function SkillToast({
  offer,
  note,
  onNote,
  error,
  busy,
  onStart,
  onNotNow,
  onHideSession,
  onHideForever,
}: {
  offer: SkillOffer;
  note: string;
  onNote: (value: string) => void;
  error: string | null;
  busy: boolean;
  onStart: () => void;
  onNotNow: () => void;
  onHideSession: () => void;
  onHideForever: () => void;
}) {
  const refine = offer.kind === "refine";
  const message = !refine
    ? "That took a few steps. Save this as a skill so Piyo can do it again?"
    : offer.reason === "failed"
      ? `Something went wrong while using the ${offer.skill} skill. Want to fix the skill from this chat?`
      : offer.reason === "corrected"
        ? `It looks like you corrected Piyo. Want to teach the ${offer.skill} skill from it?`
        : `Want to improve the ${offer.skill} skill from this chat?`;
  return (
    <Toast
      message={message}
      actions={[
        { label: busy ? "Drafting…" : refine ? "Suggest changes" : "Draft a skill", primary: true, disabled: busy, onClick: onStart },
        { label: "Not now", onClick: onNotNow },
      ]}
      more={[
        { label: "Don't suggest skills this session", onClick: onHideSession },
        { label: "Never suggest skills", onClick: onHideForever },
      ]}
      onClose={onNotNow}
    >
      {refine && (
        <input
          type="text"
          value={note}
          maxLength={500}
          placeholder="What should be different next time? (optional)"
          aria-label="What should be different next time"
          onChange={(e) => onNote(e.target.value)}
        />
      )}
      {error && <p className="error">{error}</p>}
    </Toast>
  );
}
