import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  filtersToSearch,
  isFiltered,
  parseFilters,
  snippetParts,
  sinceFor,
  toListOptions,
} from "../src/sessionFilters";

describe("parseFilters", () => {
  it("is the default with nothing in the URL", () => {
    expect(parseFilters("")).toEqual(DEFAULT_FILTERS);
    expect(parseFilters("?all=1&user=u1")).toEqual(DEFAULT_FILTERS);
  });

  it("reads each filter", () => {
    expect(parseFilters("?status=failed&mode=solo&date=7d&sort=longest&q=pricing&in=transcripts")).toEqual({
      status: "failed",
      mode: "solo",
      date: "7d",
      sort: "longest",
      q: "pricing",
      searchIn: "transcripts",
    });
  });

  it("ignores values it doesn't know rather than failing", () => {
    expect(parseFilters("?status=paused&mode=duet&date=1y&sort=random&in=everything")).toEqual(DEFAULT_FILTERS);
  });
});

describe("filtersToSearch", () => {
  it("writes only what differs from the default, and keeps other params", () => {
    expect(filtersToSearch(DEFAULT_FILTERS, "?all=1")).toBe("?all=1");
    expect(filtersToSearch({ ...DEFAULT_FILTERS, status: "done", q: "plan b" }, "?all=1")).toBe(
      "?all=1&status=done&q=plan+b",
    );
    expect(filtersToSearch({ ...DEFAULT_FILTERS, searchIn: "transcripts", q: "x" }, "")).toBe("?q=x&in=transcripts");
  });

  it("drops a filter that went back to the default", () => {
    expect(filtersToSearch(DEFAULT_FILTERS, "?status=done&sort=oldest")).toBe("");
  });

  it("round-trips", () => {
    const f = { status: "live", mode: "meeting", date: "30d", sort: "oldest", q: "a&b", searchIn: "titles" } as const;
    expect(parseFilters(filtersToSearch(f, ""))).toEqual(f);
  });

  it("does not remember a transcript scope without a query", () => {
    expect(filtersToSearch({ ...DEFAULT_FILTERS, searchIn: "transcripts" }, "")).toBe("");
  });
});

describe("sinceFor", () => {
  const now = new Date(2026, 9, 14, 15, 30, 0); // local time
  it("is nothing for any time", () => {
    expect(sinceFor("any", now)).toBeUndefined();
  });
  it("is local midnight for today", () => {
    expect(new Date(sinceFor("today", now)!).getTime()).toBe(new Date(2026, 9, 14, 0, 0, 0).getTime());
  });
  it("counts back 7 and 30 days from now", () => {
    expect(new Date(sinceFor("7d", now)!).getTime()).toBe(now.getTime() - 7 * 86_400_000);
    expect(new Date(sinceFor("30d", now)!).getTime()).toBe(now.getTime() - 30 * 86_400_000);
  });
});

describe("toListOptions", () => {
  it("passes what the server filters on, and nothing for the defaults", () => {
    const now = new Date(2026, 9, 14, 15, 30, 0);
    expect(toListOptions(DEFAULT_FILTERS, now)).toEqual({});
    expect(
      toListOptions({ status: "failed", mode: "solo", date: "today", sort: "oldest", q: "  hello ", searchIn: "transcripts" }, now),
    ).toEqual({
      status: "failed",
      mode: "solo",
      since: new Date(2026, 9, 14).toISOString(),
      sort: "oldest",
      q: "hello",
      searchIn: "transcripts",
    });
  });

  it("sends a search scope only with a query", () => {
    expect(toListOptions({ ...DEFAULT_FILTERS, searchIn: "transcripts" }, new Date())).toEqual({});
  });
});

describe("isFiltered", () => {
  it("is true for any filter but not for sort alone", () => {
    expect(isFiltered(DEFAULT_FILTERS)).toBe(false);
    expect(isFiltered({ ...DEFAULT_FILTERS, sort: "oldest" })).toBe(false);
    expect(isFiltered({ ...DEFAULT_FILTERS, status: "done" })).toBe(true);
    expect(isFiltered({ ...DEFAULT_FILTERS, q: "x" })).toBe(true);
    expect(isFiltered({ ...DEFAULT_FILTERS, date: "today" })).toBe(true);
  });
});

describe("snippetParts", () => {
  it("splits a snippet at the server's match markers", () => {
    expect(snippetParts("Send me the GPU runner pricing and we'll size it")).toEqual([
      { text: "Send me the GPU runner ", hit: false },
      { text: "pricing", hit: true },
      { text: " and we'll size it", hit: false },
    ]);
  });

  it("handles several matches, an edge match and no match", () => {
    expect(snippetParts("a b c")).toEqual([
      { text: "a", hit: true },
      { text: " b ", hit: false },
      { text: "c", hit: true },
    ]);
    expect(snippetParts("plain")).toEqual([{ text: "plain", hit: false }]);
  });

  it("never turns markup into markup (it only returns text)", () => {
    expect(snippetParts("<b>x</b> <i>y</i>")).toEqual([
      { text: "<b>x</b> ", hit: false },
      { text: "<i>y</i>", hit: true },
    ]);
  });
});
