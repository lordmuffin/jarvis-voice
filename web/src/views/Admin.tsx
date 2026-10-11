import { useEffect, useState } from "preact/hooks";
import { ApiError, listUsers } from "../api";
import { when } from "../format";
import { currentUser } from "../store";
import type { AdminUser } from "../types";
import { Link } from "./Link";

/** Everyone who has signed in, with their session counts. Admins only. */
export function Admin() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = currentUser()?.is_admin ?? false;

  useEffect(() => {
    if (!isAdmin) return;
    let stopped = false;
    listUsers().then(
      (u) => !stopped && setUsers(u),
      (e) => {
        if (stopped || (e instanceof ApiError && e.status === 401)) return;
        setError(e instanceof ApiError && e.status === 403 ? "Admins only." : "Couldn't load users.");
      },
    );
    return () => {
      stopped = true;
    };
  }, []);

  return (
    <section class="page">
      <h1 class="h1">Users</h1>
      {!isAdmin && <p class="muted">Admins only.</p>}
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {isAdmin && users === null && !error && <p class="muted">Loading…</p>}
      {users !== null && (
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col" class="num">
                  Sessions
                </th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Joined</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} data-testid="user-row">
                  <th scope="row">
                    <Link href={`/?user=${encodeURIComponent(u.id)}`}>{u.display_name}</Link>
                    {u.is_admin && <span class="tag">Admin</span>}
                  </th>
                  <td>{u.email ?? "—"}</td>
                  <td class="num">{u.sessions}</td>
                  <td>{u.last_login_at ? when(u.last_login_at) : "—"}</td>
                  <td>{when(u.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
