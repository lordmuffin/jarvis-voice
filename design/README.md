# Jarvis Voice design system

Tokens and usage rules for every Jarvis Voice UI. Extracted from the Android app (`android/app/src/main/res`); `tokens.json` holds the values, this file says how to use them. The browsable version, with live component previews, is the [Jarvis Voice design system artifact](https://claude.ai/artifact/WvwABBJrY2E6VgqVC8wX1F).

Token names match the Android resources (`jv_bg` is `@color/jv_bg`). Lengths are dp/sp on Android and px on the web.

Jarvis Voice is a private, on-device voice assistant: a floating pill that turns speech into text in any app, a voice chat with local models, and background agent tasks. The interface is a quiet dark slate with one electric teal. It should feel like an instrument that is always on and never in the way.

## Content fundamentals

- **Plain and short.** The welcome line is the model: “Dictate anywhere. No cloud required.” One idea per sentence, no exclamation marks.
- **Talk to the person as “you”; call the product “Jarvis”.** “Jarvis needs these to show up when you need it.” The background agent is named Kai in prompts (“What should Kai work on?”).
- **Say why, in their terms.** Permission reasons and setting descriptions name the benefit: “Required to capture your voice”, “Show in tray when dictation is copied to clipboard”.
- **Casing.** Buttons and screen titles are Title Case (“Get Started”, “Start Talking”, “Pick Vault Folder”). Primary and row buttons render ALL CAPS (AppCompat); compact buttons stay as written (“Chats”, “New”). Section labels are uppercase via style, written normally (“Notifications”). Helper text is sentence case with a full stop.
- **Status lines are terse and specific.** “Saved to today's note.” “Nothing to save.” “Recording failed. Try again.” Errors say what failed: “Failed: <reason>”.
- **Glyphs over words where the meaning is universal.** ▼ / ▾ for expand and menus, ✓ for done, ✕ for delete, ↩ Reply, ⎘ Copy, ▶ Run. Emoji appear only as icons: the bottom nav and the mode pills (🎙 Voice, ⌨️ Type). Never decorate prose with emoji.
- **Numbers.** Empty stats show “—”. Token counts are abbreviated “842 tkns”, “12.4k tkns”. Rates are “wpm”, lowercase.

## Visual foundations

### Color

- The app is **dark only**. Build every screen on `jv_bg`, raise content one step to `jv_surface` (rows, panels, nav, cards), and raise controls on it to `jv_surface2` (secondary buttons, chips, the AI bubble, dividers). Never go lighter than `jv_surface2` for a fill.
- `jv_accent` (teal) is the only brand hue. Use it for the one primary action, the user's chat bubble, selection outlines, progress, live values (wpm), links, and the idle dot. Text on teal is `jv_on_accent` or `jv_bg`, never white.
- `jv_waveform` is reserved for the recording bars, so listening reads differently from idle.
- State colors mean state only: `jv_success` (overlay running, permission granted, note saved), `jv_warning` (task running, context getting full), `jv_error` (failed, stopped, full). Always pair them with a word or glyph.
- Glass: the floating pill and chat pills use `jv_glass_bg` with a `jv_glass_border` hairline so they sit over other apps.
- `jv_violet` is defined but unused. Do not introduce it without a reason.

### Type

- One family: **Roboto** (the Android system face, `sans`), and the system monospace (`mono`) for tokens, URLs and logs. No display face; hierarchy comes from size and weight.
- The scale is small and dense: `meta` (11px) is the most used size. Use `display` only for the wordmark, `title` for onboarding steps, `stat` / `stat-lg` for numbers, `body-lg` for setting titles, `chat` for messages, `body` for transcripts, `caption` and `meta` for supporting text, `label` for section headers, `micro` for captions under numbers and nav labels, `tag` for WHISPER / ENHANCED.
- Bold (700) marks titles, task names, permission titles and numbers. Everything else is regular. Uppercase always gets tracking: `label` 0.08em, `tag` 0.1em.
- Line spacing is Android's default except in chat bubbles (1.35).

### Spacing and layout

- Values are dp on Android, px here. The common steps are `space-4`, `space-8`, `space-12` and `space-16`; 12 is the default inner padding of rows and panels, 16 of cards and screen sides.
- Lists are stacks of square `jv_surface` blocks separated by a thin gap of `jv_bg` (`space-4` in History, `space-6` for tasks). They are not cards.
- Settings is one long column: `label` heading, content, 1px `jv_surface2` rule, `space-20`, next heading.
- Controls come in three heights: `size-control-sm` (32), `size-control-md` (36), `size-control-lg` (52). Bottom nav is `size-nav` (64).

### Shape

- Square is the default (`radius-0`): rows, panels, task cards, chips, step dots.
- Round means “touch me” or “alive”: pills at `radius-24`, chat bubbles at `radius-18` with a `radius-4` tail toward the speaker, dots and FABs at `radius-full`.
- Onboarding cards and text fields use `radius-12`; their icon tiles `radius-10`. AppCompat buttons keep `radius-2`.

### Borders, elevation, transparency

- Borders are hairlines (`border-hairline`, 1px) in `jv_glass_border`, or `border-active` (1.5px) in `jv_accent` / `jv_stop_ring` for the selected pill. Rings (logo, check circle) are `border-ring` (2px) teal.
- Elevation is used twice: the overlay pill (12dp) and the bottom nav (8dp). Everything else is flat and separated by tone.

### Motion

- **Breathe:** the idle dot scales 1 → 1.4 and fades .85 → 1 over 1400ms, reversing forever. It is the product's heartbeat; use it only for “ready”.
- **Waveform:** three bars pulse 20% → 100% → 20%, bar *i* over 600 + 120·*i* ms with an 80·*i* ms delay, linear.
- **Processing:** an indeterminate teal spinner. No other ambient motion.
- Honour reduced motion by holding the static state.

### Focus and accessibility

- Text pairs pass 4.5:1: `jv_text` and `jv_text2` on all three surfaces, teal and state colors on `jv_bg` and `jv_surface2`.
- Known misses, kept exact from the app: `jv_glass_border` is 1.5:1 as a control outline; `jv_dot_inactive` is 1.8:1 (width carries the meaning); white on `jv_fab_stop` is 4.2:1 (glyphs only, 24px+); `jv_violet` is 2.9:1 as text.
- Web focus is a solid 2px `jv_accent` outline with a 2px offset (13:1 on `jv_bg`).

## Iconography

- Two Material-style vector icons ship in the app: `res/drawable/ic_mic.xml` and `ic_settings.xml`. They are drawn white and tinted `jv_accent` inside 44px icon tiles.
- Navigation and mode icons are emoji at 20px: 🎙 Record, 💬 Chat, 📋 History, ⚙️ Settings, 📦 Models, 🤖 Agent. Keep this set and order. The active tab is shown by the label color, since emoji ignore tint.
- Small Unicode glyphs act as inline icons: ▼ ▾ ▲ ✓ ✕ ↩ ⎘ ▶ ■.
- The launcher mark (`res/mipmap-*/ic_launcher*.png`) is a cyan waveform that breaks into a branching node graph on `jv_bg`. Use the PNGs as given; do not redraw it.

## Platforms

- **Android** is the source of every value here (`android/app/src/main/res`).
- **macOS** (Jarvis Live) has no UI yet. Start it from these tokens and components.
- The legacy web capture page (`src/jarvis_voice/static/capture.html`) uses its own red/green palette. It is bugfix-only; do not copy its colors.

## Gaps

- Fonts: no font files ship in the repository. Web builds use Roboto and Roboto Mono from Google Fonts as stand-ins for Android's system faces.
- Shadows: Android elevation (8dp, 12dp) is not tokenised; the artifact's components approximate it.
- Components: the 15 components (OverlayPill, Waveform, Button, Pill, ChatBubble, ContextMeter, TaskCard, SessionRow, PermissionCard, StepDots, SectionLabel, SettingRow, TextField, StatRow, BottomNav) are documented with live previews in the artifact, as web recreations of the Android layouts. They are not in this repository.
- Colors that exist only as literals in code (`jv_on_accent`, `jv_waveform`, `jv_stop_*`, `jv_fab_stop`, `jv_disabled_fill`, `jv_danger_fill`, `jv_success_tint*`, `jv_dot_inactive`) are named here but are not yet in `colors.xml`.
- The artifact holds only the xxxhdpi launcher PNGs; the repository keeps every density under `res/mipmap-*`.
