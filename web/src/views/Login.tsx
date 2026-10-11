import { loginUrl } from "../api";
import { authError } from "../store";

export function Login() {
  const error = authError.value;
  return (
    <div class="login">
      <div class="login-card">
        <h1>Jarvis Live</h1>
        <p class="muted">Sign in to watch your sessions.</p>
        {error && (
          <p class="error" role="alert">
            {error}
          </p>
        )}
        <button
          class="btn btn-primary"
          type="button"
          onClick={() => {
            // A full page load: the server bounces through Pocket-ID and sets the cookie.
            location.href = loginUrl(location.pathname + location.search);
          }}
        >
          Sign in with Pocket-ID
        </button>
      </div>
    </div>
  );
}
