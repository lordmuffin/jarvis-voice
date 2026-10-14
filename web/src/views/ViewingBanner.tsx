import { useEffect, useState } from "preact/hooks";
import { listUsers } from "../api";
import { Banner } from "../ds/components";
import { currentUser, route } from "../store";
import { Link } from "./Link";

/** "Viewing Sam's sessions · Back to your own": shown to admins looking at someone else's list. */
export function ViewingBanner() {
  const r = route.value;
  const userId = r.name === "sessions" ? r.user : undefined;
  const all = r.name === "sessions" && !!r.all;
  const isAdmin = currentUser()?.is_admin ?? false;
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    setName(null);
    if (!userId || !isAdmin) return;
    let stopped = false;
    listUsers().then(
      (users) => !stopped && setName(users.find((u) => u.id === userId)?.display_name ?? null),
      () => undefined,
    );
    return () => {
      stopped = true;
    };
  }, [userId, isAdmin]);

  if (!isAdmin || (!userId && !all)) return null;
  return (
    <Banner
      variant="viewing"
      data-testid="viewing-banner"
      title={userId ? (name ? `Viewing ${name}'s sessions.` : "Viewing one user's sessions.") : "Viewing everyone's sessions."}
      action={
        <Link href="/" data-testid="viewing-back">
          Back to your own
        </Link>
      }
    />
  );
}

