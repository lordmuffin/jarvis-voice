import { me, route, signOut } from "./store";
import type { Route } from "./route";
import { Admin } from "./views/Admin";
import { Devices } from "./views/Devices";
import { Link } from "./views/Link";
import { Login } from "./views/Login";
import { Publishing } from "./views/Publishing";
import { ServerVersion } from "./views/ServerVersion";
import { SessionPage } from "./views/SessionPage";
import { SessionsView } from "./views/Sessions";
import { Wordmark } from "./views/Wordmark";
import { WorkItemsPage } from "./views/WorkItemsPage";

function page(r: Route) {
  switch (r.name) {
    case "session":
      return <SessionPage key={r.id} id={r.id} />;
    case "devices":
      return <Devices />;
    case "publishing":
      return <Publishing />;
    case "work-items":
      return <WorkItemsPage />;
    case "admin":
      return <Admin />;
    case "sessions":
      // A new filter is a new list: remount so paging and polling start over.
      return <SessionsView key={`${r.all ?? ""}:${r.user ?? ""}`} all={!!r.all} userId={r.user} />;
  }
}

export function App() {
  const user = me.value;
  if (user === "loading")
    return (
      <div class="login">
        <p class="muted">Loading…</p>
      </div>
    );
  if (!user) return <Login />;
  const r = route.value;
  const current = (name: Route["name"]) => (r.name === name ? ("page" as const) : undefined);
  return (
    <div class="shell">
      <header class="bar">
        <Link class="brand" href="/">
          <Wordmark />
        </Link>
        <nav class="nav" aria-label="Main">
          <Link class="nav-link" href="/" aria-current={current("sessions")}>
            Sessions
          </Link>
          <Link class="nav-link" href="/devices" aria-current={current("devices")}>
            Devices
          </Link>
          {user.work_items && (
            <Link class="nav-link" href="/work-items" aria-current={current("work-items")}>
              Work Items
            </Link>
          )}
          {user.can_publish && (
            <Link class="nav-link" href="/publishing" aria-current={current("publishing")}>
              Publishing
            </Link>
          )}
          {user.is_admin && (
            <Link class="nav-link" href="/admin" aria-current={current("admin")}>
              Admin
            </Link>
          )}
        </nav>
        <ServerVersion />
        <span class="user meta" data-testid="user-name" title={user.email ?? undefined}>
          {user.display_name}
        </span>
        <button class="btn btn-quiet" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <main class="main">{page(r)}</main>
    </div>
  );
}
