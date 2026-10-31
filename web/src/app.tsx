import { Brand, Button, CenterCard, FamilySwitcher, Loading, Shell, TabIcon } from "./ds/components";
import { familyApps, me, navigate, releaseChannel, route, signOut } from "./store";
import type { Route } from "./route";
import { Admin } from "./views/Admin";
import { DeletedView } from "./views/Deleted";
import { Devices } from "./views/Devices";
import { Link } from "./views/Link";
import { Login } from "./views/Login";
import { Publishing } from "./views/Publishing";
import { HeaderVersion, ServerVersion } from "./views/ServerVersion";
import { SessionPage } from "./views/SessionPage";
import { SessionsView } from "./views/Sessions";
import { ThemeToggle } from "./views/ThemeToggle";
import { ViewingBanner } from "./views/ViewingBanner";
import { WorkItemsPage } from "./views/WorkItemsPage";

function page(r: Route) {
  switch (r.name) {
    case "session":
      return <SessionPage key={r.id} id={r.id} />;
    case "deleted":
      return <DeletedView />;
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
  const tabs: { name: Route["name"]; href: string; label: string; ico: string }[] = [
    { name: "sessions", href: "/", label: "Sessions", ico: "📋" },
    { name: "deleted", href: "/deleted", label: "Recently Deleted", ico: "🗑" },
    { name: "devices", href: "/devices", label: "Devices", ico: "📱" },
    ...(user.work_items
      ? [{ name: "work-items" as const, href: "/work-items", label: "Work Items", ico: "🤖" }]
      : []),
    ...(user.can_publish
      ? [{ name: "publishing" as const, href: "/publishing", label: "Publishing", ico: "📝" }]
      : []),
    ...(user.is_admin ? [{ name: "admin" as const, href: "/admin", label: "Admin", ico: "⚙" }] : []),
  ];
  return (
    <Shell
      brand={
        <>
          <FamilySwitcher apps={familyApps.value} channel={releaseChannel.value} />
          <Brand
            href="/"
            channel={releaseChannel.value}
            onNavigate={(e) => {
              e.preventDefault();
              navigate("/");
            }}
          />
        </>
      }
      bottom={
        <>
          {tabs.map((t) => (
            <Link href={t.href} aria-current={current(t.name)}>
              <TabIcon>{t.ico}</TabIcon>
              {t.label}
            </Link>
          ))}
        </>
      }
      nav={
        <>
          {tabs.map((t) => (
            <Link href={t.href} aria-current={current(t.name)}>
              {t.label}
            </Link>
          ))}
        </>
      }
      tools={
        <>
          <HeaderVersion />
          <span data-testid="user-name" title={user.email ?? undefined}>
            {user.display_name}
          </span>
          <ThemeToggle />
          <Button variant="quiet" size="sm" onClick={() => void signOut()}>
            Sign Out
          </Button>
        </>
      }
      banner={<ViewingBanner />}
      footer={<ServerVersion />}
    >
      {page(r)}
    </Shell>
  );
}
