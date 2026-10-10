import { useEffect, useState } from "preact/hooks";
import { ApiError, getSession } from "../api";
import { navigate, signOut, token } from "../store";
import type { SessionDetail } from "../types";
import { LiveSession } from "./LiveSession";
import { PastSession } from "./PastSession";

export function SessionPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    getSession(token.value ?? "", id).then(
      (d) => !stopped && setDetail(d),
      (e) => {
        if (e instanceof ApiError && e.status === 401) return signOut();
        if (!stopped)
          setError(e instanceof ApiError && e.status === 404 ? "Session not found." : "Couldn't load session.");
      },
    );
    return () => {
      stopped = true;
    };
  }, [id]);

  const back = (
    <a
      class="back"
      href="/"
      onClick={(e) => {
        e.preventDefault();
        navigate("/");
      }}
    >
      ← Sessions
    </a>
  );

  if (error)
    return (
      <>
        {back}
        <p class="error" role="alert">
          {error}
        </p>
      </>
    );
  if (!detail)
    return (
      <>
        {back}
        <p class="muted">Loading…</p>
      </>
    );

  const inProgress = detail.status === "live" || detail.status === "finalizing";
  return (
    <>
      {back}
      {inProgress ? (
        <LiveSession key="live" detail={detail} onFinished={setDetail} />
      ) : (
        <PastSession key="past" detail={detail} />
      )}
    </>
  );
}
