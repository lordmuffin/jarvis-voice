export type Route = { name: "sessions" } | { name: "session"; id: string };

export function parseRoute(pathname: string): Route {
  const m = /^\/sessions\/([^/]+)\/?$/.exec(pathname);
  return m ? { name: "session", id: decodeURIComponent(m[1]!) } : { name: "sessions" };
}
