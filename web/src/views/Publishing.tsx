import { useEffect, useRef, useState } from "preact/hooks";
import {
  ApiError,
  getPublishing,
  publicationAction,
  publishAll,
  setAutoPublish,
  syncPublishing,
  type PublishAction,
} from "../api";
import {
  Actions,
  Banner,
  Bar,
  Button,
  ButtonLink,
  Choice,
  Code,
  EmptyState,
  Head,
  ItemMain,
  List,
  ListItem,
  Loading,
  PageTitle,
  SectionLabel,
  Section,
  Text,
} from "../ds/components";
import { when } from "../format";
import { currentUser, me } from "../store";
import type { PublishingItem, PublishingQueue } from "../types";
import { Link } from "./Link";
import { PublicationChip } from "./PublicationChip";

const POLL_MS = 2000;
const SHOWN = 20; // rows per list until "Show all"

/**
 * Notes on their way to the vault repo: ready to publish, pull requests waiting to merge, and
 * what happened lately. The owner and admins only.
 */
export function Publishing() {
  const [q, setQ] = useState<PublishingQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // a session id, or "sync" / "all"
  const allowed = currentUser()?.can_publish ?? false;
  const poll = useRef<number | undefined>(undefined);

  async function load(fetcher: () => Promise<PublishingQueue> = getPublishing) {
    try {
      const next = await fetcher();
      setQ(next);
      setError(null);
      window.clearTimeout(poll.current);
      if (next.publishing_all) poll.current = window.setTimeout(() => void load(), POLL_MS);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "Couldn't load the publishing queue.");
    }
  }

  useEffect(() => {
    if (allowed) void load();
    return () => window.clearTimeout(poll.current);
  }, []);

  async function act(item: PublishingItem, action: PublishAction) {
    if (action === "merge" && !confirm(`Merge “${item.title}” into the vault's main branch?`)) return;
    setBusy(item.session_id);
    try {
      await publicationAction(item.session_id, action);
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(`“${item.title}”: ${e instanceof ApiError ? e.message : "that didn't work"}`);
    } finally {
      setBusy(null);
    }
  }

  async function sync() {
    setBusy("sync");
    await load(syncPublishing);
    setBusy(null);
  }

  async function all() {
    if (!q || !confirm(`Open a pull request for each of the ${q.ready.length} ready notes?`)) return;
    setBusy("all");
    try {
      await publishAll();
      await load();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401))
        setError(e instanceof ApiError ? e.message : "Couldn't start publishing.");
    } finally {
      setBusy(null);
    }
  }

  async function toggleAuto(on: boolean) {
    try {
      const updated = await setAutoPublish(on);
      me.value = updated;
      if (q) setQ({ ...q, auto_publish: updated.auto_publish });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError("Couldn't change auto-publish.");
    }
  }

  if (!allowed)
    return (
      <Section>
        <PageTitle>Publishing</PageTitle>
        <EmptyState>Only the owner and admins publish notes.</EmptyState>
      </Section>
    );

  return (
    <Section>
      <PageTitle>Publishing</PageTitle>
      <Text as="p" tone="muted" prose>
        Each note goes to {q ? <Code>{q.repo}</Code> : "the vault repo"} as its own pull request
        against <Code>{q?.base_branch ?? "main"}</Code>. Review and merge here or on GitHub.
      </Text>
      <Bar>
        <Choice
          label="Auto-publish new notes"
          checked={q?.auto_publish ?? false}
          disabled={!q}
          onChange={(e) => void toggleAuto((e.target as HTMLInputElement).checked)}
        />
        <Button size="sm" disabled={busy !== null} onClick={() => void sync()}>
          {busy === "sync" ? "Syncing…" : "Sync Now"}
        </Button>
      </Bar>
      {error && <Banner variant="error">{error}</Banner>}
      {!q && !error && <Loading />}
      {q && (
        <>
          <Head>
            <SectionLabel>Ready to publish ({q.ready.length})</SectionLabel>
            {q.ready.length > 0 && (
              <Button
                variant="primary"
                size="sm"
                disabled={busy !== null || q.publishing_all}
                onClick={() => void all()}
              >
                {q.publishing_all ? "Publishing…" : "Publish All"}
              </Button>
            )}
          </Head>
          <Items
            items={q.ready}
            empty="Nothing waiting. New notes show up here when a session ends."
            testid="ready"
            render={(it) => (
              <Button
                variant="primary"
                size="sm"
                disabled={busy !== null || q.publishing_all}
                onClick={() => void act(it, "publish")}
              >
                Publish
              </Button>
            )}
            busy={busy}
          />

          <SectionLabel>Pull requests waiting to merge ({q.open.length})</SectionLabel>
          <Items
            items={q.open}
            empty="No open pull requests."
            testid="open"
            busy={busy}
            render={(it) => (
              <>
                {it.publication?.pr_url && (
                  <ButtonLink
                    size="sm"
                    href={`${it.publication.pr_url}/files`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Review
                  </ButtonLink>
                )}
                {it.publication?.state === "open" && (
                  <>
                    <Button variant="primary" size="sm" disabled={busy !== null} onClick={() => void act(it, "merge")}>
                      Merge
                    </Button>
                    <Button size="sm" disabled={busy !== null} onClick={() => void act(it, "close")}>
                      Close
                    </Button>
                  </>
                )}
              </>
            )}
          />

          <SectionLabel>Recent</SectionLabel>
          <Items
            items={q.recent}
            empty="Nothing yet."
            testid="recent"
            busy={busy}
            render={(it) =>
              it.publication?.state === "failed" ? (
                <Button size="sm" disabled={busy !== null} onClick={() => void act(it, "publish")}>
                  Retry
                </Button>
              ) : it.publication?.state === "skipped" ? (
                <Button variant="quiet" size="sm" disabled={busy !== null} onClick={() => void act(it, "unskip")}>
                  Unskip
                </Button>
              ) : null
            }
          />
        </>
      )}
    </Section>
  );
}

function Items({
  items,
  empty,
  testid,
  busy,
  render,
}: {
  items: PublishingItem[];
  empty: string;
  testid: string;
  busy: string | null;
  render: (it: PublishingItem) => preact.ComponentChildren;
}) {
  const [all, setAll] = useState(false);
  if (items.length === 0)
    return (
      <Text size="caption" tone="muted">
        {empty}
      </Text>
    );
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      <List data-testid={`publishing-${testid}`}>
        {shown.map((it) => (
          <ListItem withActions key={it.session_id} data-testid="publishing-item">
            <ItemMain>
              <Link href={`/sessions/${encodeURIComponent(it.session_id)}`}>
                <Text strong>{it.title}</Text>
              </Link>
              <Text size="meta" tone="muted">
                {when(it.started_at)} · {it.owner}
                {it.publication?.pr_url && (
                  <>
                    {" · "}
                    <a href={it.publication.pr_url} target="_blank" rel="noopener noreferrer">
                      PR #{it.publication.pr_number}
                    </a>
                  </>
                )}
              </Text>
              {it.publication?.state === "failed" && it.publication.last_error && (
                <Text size="caption" tone="error">
                  {it.publication.last_error}
                </Text>
              )}
            </ItemMain>
            <Actions>
              {it.publication && <PublicationChip publication={it.publication} />}
              {busy === it.session_id ? (
                <Text size="meta" tone="muted">
                  Working…
                </Text>
              ) : (
                render(it)
              )}
            </Actions>
          </ListItem>
        ))}
      </List>
      {shown.length < items.length && (
        <div>
          <Button variant="quiet" size="sm" onClick={() => setAll(true)}>
            Show all {items.length}
          </Button>
        </div>
      )}
    </>
  );
}
