import { navigate, route, signOut, token } from "./store";
import { Login } from "./views/Login";
import { SessionPage } from "./views/SessionPage";
import { SessionsView } from "./views/Sessions";

export function App() {
  if (!token.value) return <Login />;
  const r = route.value;
  return (
    <div class="shell">
      <header class="bar">
        <a
          class="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          Jarvis Live
        </a>
        <button class="btn btn-quiet" onClick={signOut}>
          Sign out
        </button>
      </header>
      <main class="main">
        {r.name === "session" ? <SessionPage key={r.id} id={r.id} /> : <SessionsView />}
      </main>
    </div>
  );
}
