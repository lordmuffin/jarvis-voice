import { Brand, Button, CenterCard, Loading, Shell } from "./ds/components";
import { me, navigate, releaseChannel, route, signOut } from "./store";
import type { Route } from "./route";
import { Admin } from "./views/Admin";
import { Devices } from "./views/Devices";
import { Link } from "./views/Link";
import { Login } from "./views/Login";
import { Publishing } from "./views/Publishing";
import { ServerVersion } from "./views/ServerVersion";
import { SessionPage } from "./views/SessionPage";
import { SessionsView } from "./views/Sessions";
import { ThemeToggle } from "./views/ThemeToggle";
import { ViewingBanner } from "./views/ViewingBanner";
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
      <CenterCard>
        <Loading />
      </CenterCard>
    );
  if (!user) return <Login />;
  const r = route.value;
  const current = (name: Route["name"]) => (r.name === name ? ("page" as const) : undefined);
  return (
    <Shell
      brand={
        <Brand
          href="/"
          channel={releaseChannel.value}
          onNavigate={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        />
      }
      nav={
        <>
          <Link href="/" aria-current={current("sessions")}>
            Sessions
          </Link>
          <Link href="/devices" aria-current={current("devices")}>
            Devices
          </Link>
          {user.work_items && (
            <Link href="/work-items" aria-current={current("work-items")}>
              Work Items
            </Link>
          )}
          {user.can_publish && (
            <Link href="/publishing" aria-current={current("publishing")}>
              Publishing
            </Link>
          )}
          {user.is_admin && (
            <Link href="/admin" aria-current={current("admin")}>
              Admin
            </Link>
          )}
        </>
      }
      tools={
        <>
          <ServerVersion />
          <span data-testid="user-name" title={user.email ?? undefined}>
            {user.display_name}
          </span>
          <ThemeToggle />
          <Button variant="quiet" size="sm" onClick={() => void signOut()}>
            Sign out
          </Button>
        </>
      }
      banner={<ViewingBanner />}
    >
      {page(r)}
    </Shell>
  );
}
