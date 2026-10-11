import { signal } from "@preact/signals";
import { ApiError, health, logout, me as fetchMe, onUnauthorized } from "./api";
import { parseRoute, type Route } from "./route";
import type { Me } from "./types";

/** The signed-in user; null shows the sign-in screen. */
export const me = signal<Me | null | "loading">("loading");
/** Why the sign-in check failed, when it was not simply "signed out". */
export const authError = signal<string | null>(null);

// Any 401 from the API means the session cookie is gone: back to sign-in.
onUnauthorized(() => {
  me.value = null;
  authError.value = null;
});

/** The signed-in user, once known. */
export const currentUser = (): Me | null => (me.value === "loading" ? null : me.value);

export async function loadMe(): Promise<void> {
  try {
    me.value = await fetchMe();
    authError.value = null;
  } catch (e) {
    me.value = null;
    authError.value =
      e instanceof ApiError && e.status === 401 ? null : "Couldn't reach the server. Try again.";
  }
}

/** The release channel when it is not "live" (e.g. "lab"), so an unstable server is never
 * mistaken for the real one. null on live, or while unknown. */
export const releaseChannel = signal<string | null>(null);

export async function loadChannel(): Promise<void> {
  try {
    const env = (await health()).environment;
    releaseChannel.value = env && env !== "live" ? env : null;
  } catch {
    /* an old server without the field, or offline: show nothing */
  }
}

export async function signOut(): Promise<void> {
  try {
    await logout();
  } catch {
    /* the cookie may already be gone */
  }
  me.value = null;
}

const here = () => location.pathname + location.search;

export const route = signal<Route>(parseRoute(location.pathname, location.search));

export function navigate(path: string): void {
  if (here() !== path) history.pushState(null, "", path);
  route.value = parseRoute(location.pathname, location.search);
}

window.addEventListener("popstate", () => {
  route.value = parseRoute(location.pathname, location.search);
});
