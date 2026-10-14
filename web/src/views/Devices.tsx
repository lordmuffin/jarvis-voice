import { useEffect, useState } from "preact/hooks";
import { ApiError, createDevice, listDevices, revokeDevice } from "../api";
import {
  Banner,
  Button,
  Dialog,
  EmptyState,
  Field,
  Form,
  FormRow,
  Input,
  ItemMain,
  List,
  ListItem,
  Loading,
  PageTitle,
  Pill,
  Section,
  SectionLabel,
  ShowOnceCard,
  Text,
} from "../ds/components";
import { when } from "../format";
import type { Device, NewDevice } from "../types";

/** Device tokens for recorders (the Mac app): add one, see it once, revoke it. */
export function Devices() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<NewDevice | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);

  async function load() {
    try {
      setDevices(await listDevices());
      setError(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError("Couldn't load devices.");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function add(e: Event) {
    e.preventDefault();
    const n = name.trim();
    if (!n) return;
    setAdding(true);
    try {
      setCreated(await createDevice(n));
      setName("");
      setError(null);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return;
      setError("Couldn't add the device. Try again.");
    } finally {
      setAdding(false);
    }
  }

  async function revoke(d: Device) {
    setRevoking(null);
    try {
      await revokeDevice(d.id);
      if (created?.id === d.id) setCreated(null);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return;
      setError(`Couldn't revoke “${d.name}”.`);
    }
  }

  return (
    <Section>
      <PageTitle>Devices</PageTitle>
      <Text as="p" tone="muted" prose>
        A device token lets a recorder, such as the Jarvis Live Mac app, stream sessions to your
        account. Revoke a token to sign that device out.
      </Text>

      <Form narrow onSubmit={add}>
        <FormRow>
          <Field label="New device" htmlFor="device-name">
            <Input
              id="device-name"
              placeholder="e.g. Work MacBook"
              maxLength={200}
              autocomplete="off"
              value={name}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
          </Field>
          <Button variant="primary" type="submit" disabled={adding || !name.trim()}>
            {adding ? "Adding…" : "Add Device"}
          </Button>
        </FormRow>
      </Form>

      {created && (
        <ShowOnceCard
          heading="Device token"
          intro={
            <>
              <strong>{created.name}</strong> is ready. Paste this token into the Jarvis Live Mac app.
            </>
          }
          warning="It is shown only once. Copy it now. If you lose it, revoke the device and add it again."
          value={created.token}
          valueLabel="Device token"
          footnote="The token is never logged."
          onDone={() => setCreated(null)}
        />
      )}

      {error && <Banner variant="error">{error}</Banner>}
      <SectionLabel>Your devices{devices ? ` (${devices.length})` : ""}</SectionLabel>
      {devices === null && !error && <Loading />}
      {devices !== null && devices.length === 0 && (
        <EmptyState hint="Name one above, then paste its token into the Mac app.">No devices yet.</EmptyState>
      )}
      <List aria-label="Devices">
        {(devices ?? []).map((d) => (
          <ListItem key={d.id} data-testid="device-row">
            <ItemMain>
              <Text strong>{d.name}</Text>
              <Text size="meta" tone="muted">
                Added {when(d.created_at)}
              </Text>
            </ItemMain>
            {d.revoked_at ? (
              <Pill tone="failed" title={`Revoked ${when(d.revoked_at)}`}>
                <span aria-hidden="true">✕ </span>Revoked
              </Pill>
            ) : (
              <Button
                variant="danger-quiet"
                size="sm"
                aria-label={`Revoke ${d.name}`}
                onClick={() => setRevoking(d)}
              >
                Revoke
              </Button>
            )}
          </ListItem>
        ))}
      </List>
      <Text size="meta" tone="muted">
        Jarvis keeps only a hash of each token, so it can’t show one again.
      </Text>

      {revoking && (
        <Dialog
          title={`Revoke “${revoking.name}”?`}
          onClose={() => setRevoking(null)}
          actions={
            <>
              <Button variant="quiet" onClick={() => setRevoking(null)}>
                Cancel
              </Button>
              <Button variant="danger" onClick={() => void revoke(revoking)}>
                Revoke {revoking.name}
              </Button>
            </>
          }
        >
          <Text>It stops working right away.</Text>
        </Dialog>
      )}
    </Section>
  );
}
