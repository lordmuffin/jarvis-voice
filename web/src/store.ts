import { signal } from "@preact/signals";
import { clearToken, loadToken, saveToken } from "./api";
import { parseRoute, type Route } from "./route";

export const token = signal<string | null>(loadToken());

export function signIn(value: string): void {
  saveToken(value);
  token.value = value;
}

export function signOut(): void {
  clearToken();
  token.value = null;
}

export const route = signal<Route>(parseRoute(location.pathname));

export function navigate(path: string): void {
  if (location.pathname !== path) history.pushState(null, "", path);
  route.value = parseRoute(path);
}

window.addEventListener("popstate", () => {
  route.value = parseRoute(location.pathname);
});
