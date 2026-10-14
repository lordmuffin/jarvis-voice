import { useEffect, useState } from "preact/hooks";
import { ApiError, listUsers } from "../api";
import { Banner, Cell, EmptyState, Loading, PageTitle, Pill, Section, Table, Text } from "../ds/components";
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
    <Section>
      <PageTitle>Users</PageTitle>
      <Text as="p" tone="muted">
        Everyone who has signed in with Pocket-ID.
      </Text>
      {!isAdmin && <EmptyState>Admins only.</EmptyState>}
      {error && <Banner variant="error">{error}</Banner>}
      {isAdmin && users === null && !error && <Loading />}
      {users !== null && (
        <Table label="Users">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Email</th>
              <Cell as="th" scope="col" num>
                Sessions
              </Cell>
              <th scope="col">Last sign-in</th>
              <th scope="col">Joined</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} data-testid="user-row">
                <th scope="row">
                  <Link href={`/?user=${encodeURIComponent(u.id)}`}>{u.display_name}</Link>
                  {u.is_admin && (
                    <Pill tag>
                      Admin
                    </Pill>
                  )}
                </th>
                <td>{u.email ?? "—"}</td>
                <Cell num>{u.sessions}</Cell>
                <td>{u.last_login_at ? when(u.last_login_at) : "—"}</td>
                <td>{when(u.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <Text size="meta" tone="muted">
        Accounts come from Pocket-ID. Admins can view anyone's sessions.
      </Text>
    </Section>
  );
}
