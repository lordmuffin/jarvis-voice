import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, createDevice, listDevices, revokeDevice } from "../api";
import { when } from "../format";
import type { Device, NewDevice } from "../types";

/** Device tokens for recorders (the Mac app): add one, see it once, revoke it. */
export function Devices() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<NewDevice | null>(null);

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
    if (!confirm(`Revoke “${d.name}”? It stops working right away.`)) return;
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
    <section class="page">
      <h1 class="h1">Devices</h1>
      <p class="muted">
        A device token lets a recorder, such as the Jarvis Live Mac app, stream sessions to your
        account. Revoke a token to sign that device out.
      </p>

      <form class="inline-form" onSubmit={add}>
        <label class="label" for="device-name">
          New device
        </label>
        <div class="inline-row">
          <input
            id="device-name"
            class="field"
            placeholder="e.g. Work MacBook"
            maxLength={200}
            autocomplete="off"
            value={name}
            onInput={(e) => setName((e.target as HTMLInputElement).value)}
          />
          <button class="btn" type="submit" disabled={adding || !name.trim()}>
            {adding ? "Adding…" : "Add Device"}
          </button>
        </div>
      </form>

      {created && <NewToken device={created} onDone={() => setCreated(null)} />}

      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {devices === null && !error && <p class="muted">Loading…</p>}
      {devices !== null && devices.length === 0 && <p class="muted">No devices yet.</p>}
      <ul class="rows" aria-label="Devices">
        {(devices ?? []).map((d) => (
          <li key={d.id} class="row row-static" data-testid="device-row">
            <span class="row-main">
              <span class="row-title">{d.name}</span>
              <span class="meta">Added {when(d.created_at)}</span>
            </span>
            {d.revoked_at ? (
              <span class="chip chip-revoked" title={`Revoked ${when(d.revoked_at)}`}>
                <span aria-hidden="true">✕ </span>Revoked
              </span>
            ) : (
              <button
                class="btn btn-sm btn-danger"
                type="button"
                aria-label={`Revoke ${d.name}`}
                onClick={() => void revoke(d)}
              >
                Revoke
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function NewToken({ device, onDone }: { device: NewDevice; onDone: () => void }) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    field.current?.select();
  }, [device.id]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(device.token);
      setCopied("yes");
    } catch {
      field.current?.select();
      setCopied("no");
    }
  }

  return (
    <div class="token-card" role="status" data-testid="new-token">
      <p>
        <strong>{device.name}</strong> is ready. Paste this token into the Jarvis Live Mac app.
        It is shown only once.
      </p>
      <div class="inline-row">
        <input
          ref={field}
          class="field mono"
          readOnly
          aria-label="Device token"
          value={device.token}
          onFocus={(e) => (e.target as HTMLInputElement).select()}
        />
        <button class="btn" type="button" onClick={() => void copy()}>
          ⎘ Copy
        </button>
        <button class="btn btn-quiet" type="button" onClick={onDone}>
          Done
        </button>
      </div>
      {copied === "yes" && <p class="meta">Copied to the clipboard.</p>}
      {copied === "no" && <p class="meta">Couldn't copy. The token is selected: copy it by hand.</p>}
    </div>
  );
}
