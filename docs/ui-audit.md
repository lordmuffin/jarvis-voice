# Android UI audit

Audit of the Jarvis Voice Android app against the Instrument design system (the shared system behind Jarvis web and Sleep Dashboard; source of truth `lordmuffin/instrument`, vendored v0.1.0, artboards in `docs/design/instrument/android/` and `system/Comp-States`).

Screenshots: every screen, folded (cover display, 411dp) and unfolded (inner display, 851dp), light and dark, in [`docs/ui-audit/before/`](ui-audit/before/). They come from Paparazzi (`LegacyLayoutsTest`) with fixture data bound to the real layouts, and are reproducible with `./gradlew :app:recordPaparazziDebug`. Displays are Pixel Fold-class geometry (1080x2424 @420dpi and 2076x2152 @390dpi); the Pixel 11 Pro Fold is assumed to match.

## Method

- Design lint (`scripts/lint/check_android_design.py`): clean. No hard-coded colours or non-token dp/sp in layouts or Kotlin.
- Contrast: computed from the vendored `inst_colors.xml` (light and night), WCAG 2.x ratios.
- Touch targets: every clickable view in `layout*/` checked for an effective minimum height (own `minHeight`, else its style's) against the 48dp Android target (`inst_touch_target_android`).
- Layout: screenshots on both displays compared with the Instrument artboards.

## 1. Inconsistencies with the design system

| # | Finding | Where | Spec |
|---|---|---|---|
| D1 | Empty and error states are bare centered text plus a "Try Again" pill. No glyph, title or single action; the empty state offers no way forward. | History (`activity_sessions`), Session Detail error | Comp-States: glyph tile, title, one sentence, one action; `role=alert` for errors |
| D2 | Loading is a full-screen spinner. | History | "Loading in place · no full-page spinners": skeleton on first load, old content at 50% on refresh |
| D3 | No app bar. Screens draw their own 26sp title in the body. | all | 56dp app bar on surface, wordmark, channel badge, hairline |
| D4 | Session Detail has no bottom navigation and no back affordance beyond the system gesture. | Session Detail | "↩ History" back row, nav stays |
| D5 | Unfolded Record is two columns, but not the spec's: the right pane is only the copilot text blob (blank when idle), and the transcript sits under the controls. | Record, inner | Rail 80dp, 320dp session pane (header, markers, Now dock, REC pill, Marker/Stop), feed on the right |
| D6 | Navigation is a 6-tab bottom bar on the inner display too. | all, inner | Rail (80dp, 72dp items, 3dp accent edge) at 840dp |
| D7 | Record has a flat `ProgressBar` level meter, not the REC pill with the live meter. | Record, recording | `i-live`: breathing dot, REC, time, 3-bar meter |
| D8 | History is a separate screen behind a "History" button, so recording and history are never on one page. | Record / History | Pending sessions sit on Record; History reachable and previewed |
| D9 | Copilot output is one concatenated text blob in a scroll view; transcript is a separate list. | Record | One feed, copilot events inline with source time; filter segment |
| D10 | Session status is a tiny right-aligned text tag, not a state chip with glyph. | History | State chips with glyph (`✓ Done`, `✕ Failed`, spinner Finalizing) |

## 2. Spacing and typography drift

Everything in layouts uses `@dimen/inst_*`. Drift sits in `values/`, which the lint does not scan:

| # | Finding | Location |
|---|---|---|
| T1 | `13sp` button and mono-input text. Not a token (scale is 12 `caption`, 14 `body`). | `Widget.Inst.Button`, `Widget.Inst.EditText.Mono`, banner style |
| T2 | `28dp` min height on segmented items and detail tabs; spec is 44dp segments in a 48dp row. | `LiveSess.Tab`, `Widget.Inst.Seg.Item` |
| T3 | Literal `15dp` / `5dp` / `6dp` paddings. | `inst_styles_livesess.xml`, `inst_styles.xml` (chip) |
| T4 | App-only sizes with no token: empty glyph 40sp, nav icon 20sp, nav total 57dp, copilot height 140dp, detail left pane 380dp (spec: 320dp). | `dimens*.xml` |
| T5 | No content max width: Settings, Chat and History stretch to 851dp on the inner display although `inst_content_max` exists. | Settings, Chat, History, inner |

## 3. Touch targets

110 interactive views checked; the ones under 48dp:

| View | Effective height | Screens |
|---|---|---|
| Detail tabs `btn_tab_*` (`LiveSess.Tab`) | 28dp | Session Detail |
| Chat toolbar: Chats, New, Compact, Voice, Type, Model (`.Sm`) | 32dp | Voice Chat |
| Chat Send / Delegate buttons | 36dp | Voice Chat |
| Task and dictionary editors: `Spinner`, `EditText`, Add | 36dp | Agent, Dictionary, dialogs |
| Debug log Copy / Clear | 36dp | Debug Log |
| Dictation history Copy final / Copy raw | 36dp | Dictation History |
| Sessions sheet "New" | 32dp | Chat sheet |

Record, History rows and Settings already meet 48dp. The bottom-nav tabs are 55dp tall but 6 tabs on 411dp are 68dp wide with 10sp labels (T-micro, readable but at the floor).

## 4. Contrast (WCAG 2.x AA)

Text pairs all pass: `text`/`text-2`/`accent`/`success`/`warning`/`error` on `bg`, `surface`, `surface-2` are 4.5:1 or better in light and dark (lowest: light `warning` on `bg` 4.57, light `accent` on `surface-2` 4.56, light `success` on `success-tint` 4.56).

Misses, all token-level (fix upstream, not in the app):

| Token pair | Ratio | Impact |
|---|---|---|
| light `control` (#8C959F) on `bg` | 2.85:1 | Input outline, switch off-track: below the 3:1 non-text minimum (1.4.11) |
| light `text-disabled` on `surface` | 3.04:1 | Exempt (disabled), but the app pairs it with a reason string, as the spec asks |
| dark `control` on `bg` | 4.12:1 | passes 3:1 |
| `line` on `bg` | 1.24:1 | decorative hairline, exempt |

The README's known misses (`jv_glass_border`, `jv_dot_inactive`) belong to the pre-Instrument `jv_*` tokens still used by the overlay pill.

## 5. Layout breaks on the inner display (851dp)

| # | Finding | Screenshot |
|---|---|---|
| L1 | Record idle: controls take 60% of the width, the right pane is an empty white/black column labelled "COPILOT". | `record_idle_inner_*` |
| L2 | Session Detail: content hugs the left edge, tabs are 210dp wide, the rest of the screen is blank, no nav. | `session_detail_inner_*` |
| L3 | History: single column of 840dp-wide rows; no detail pane, so opening a session leaves the list. | `history_list_inner_*` |
| L4 | Settings and Chat: full-width fields and composer, no max width. | `settings_inner_*`, `voice_chat_inner_*` |
| L5 | Onboarding: fine, but the primary button spans the full inner width. | `onboarding_inner_*` |

## Status after this pass

Before/after sheets: [`docs/ui-audit/compare/`](ui-audit/compare/). Baselines for the new screens are the Paparazzi snapshots in `android/app/src/test/snapshots/images/`.

| Finding | Status |
|---|---|
| D1, D2 empty / error / loading | Fixed on Record (recent sessions), History and Detail: one `StatePanel` template, skeletons for first load, 50% content while refreshing |
| D3 app bar | Fixed on Record, History, Detail (56dp, wordmark, channel badge) |
| D4 Detail has no nav or back | Fixed: back row and navigation |
| D5, D6 unfolded Record and navigation | Fixed on Record, History, Detail: 80dp rail, 320dp session pane with REC pill and Marker/Stop, feed on the right |
| D7 level meter | Fixed: live waveform in the REC pill |
| D8 history and new recording on one page | Fixed: Record shows pending sessions and the 5 most recent sessions under the new-session controls |
| D9 copilot as one text blob | Partly: sections are separate blocks, still a panel above the feed rather than events interleaved with the transcript (the live snapshot carries no per-event source time) |
| D10 status chips | Fixed: `InstChip` with the state word |
| L1, L2, L3 inner-display breaks | Fixed: History is list + detail on the unfolded display; Record uses the spec's panes |
| T1-T5, touch targets, L4, L5 | Not touched: Chat, Settings, Models, Agent, Dictionary, Onboarding are still Views (see below) |
| Contrast misses | Token-level, to be fixed in `lordmuffin/instrument` |

Transcript auto-scroll: the feed pins to the newest line while you follow it, pauses when you drag away, and shows "Jump to live" (`FollowLive`, unit-tested).

Still on Views, with their findings above as follow-ups: Chat, Settings, Models, STT models, Agent, Dictionary, Dictation history, Onboarding, Debug log. Their screenshot baselines are `LegacyLayoutsTest`.

## Plan

Fix order, as agreed: (1) map Instrument tokens into a Compose theme; (2) Record screen (live waveform, history and new recording on one page, transcript auto-scroll); (3) two-pane History / Detail on the unfolded display; (4) empty, error and loading states. UI only: recording, upload and sync code is untouched. Token-level contrast misses go to `lordmuffin/instrument`. Chat, Settings, Models, Agent and Dictionary stay on Views for now; their touch-target and max-width findings are listed above as follow-ups.
