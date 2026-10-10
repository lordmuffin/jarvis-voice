import { useState } from "preact/hooks";
import { checkToken } from "../api";
import { signIn } from "../store";

export function Login() {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: Event) {
    e.preventDefault();
    const t = value.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    try {
      await checkToken(t);
      signIn(t);
    } catch (err) {
      const status = (err as { status?: number }).status;
      setError(
        status === 401
          ? "That token isn't valid. Check it and try again."
          : "Couldn't reach the server. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="login">
      <form class="login-card" onSubmit={submit}>
        <h1>Jarvis Live</h1>
        <p class="muted">Enter a device token to watch your sessions.</p>
        <label class="label" for="token">
          Device token
        </label>
        <input
          id="token"
          class="field mono"
          type="password"
          autocomplete="off"
          spellcheck={false}
          value={value}
          onInput={(e) => setValue((e.target as HTMLInputElement).value)}
        />
        {error && (
          <p class="error" role="alert">
            {error}
          </p>
        )}
        <button class="btn btn-primary" type="submit" disabled={busy || !value.trim()}>
          {busy ? "Checking…" : "Continue"}
        </button>
        <p class="muted small">
          Create one with <code>jarvis-live create-device</code>. It is kept in this browser only.
        </p>
      </form>
    </div>
  );
}
