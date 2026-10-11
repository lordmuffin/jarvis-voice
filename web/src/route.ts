export type Route =
  | { name: "sessions"; all?: true; user?: string }
  | { name: "session"; id: string }
  | { name: "devices" }
  | { name: "publishing" }
  | { name: "admin" };

/**
 * `/` (with `?all=1` or `?user=<id>` for admins), `/sessions/:id`, `/devices`, `/publishing`,
 * `/admin`.
 */
export function parseRoute(pathname: string, search = ""): Route {
  const m = /^\/sessions\/([^/]+)\/?$/.exec(pathname);
  if (m) return { name: "session", id: decodeURIComponent(m[1]!) };
  if (/^\/devices\/?$/.test(pathname)) return { name: "devices" };
  if (/^\/publishing\/?$/.test(pathname)) return { name: "publishing" };
  if (/^\/admin\/?$/.test(pathname)) return { name: "admin" };
  const q = new URLSearchParams(search);
  const user = q.get("user");
  if (user) return { name: "sessions", user };
  if (q.get("all") === "1") return { name: "sessions", all: true };
  return { name: "sessions" };
}
