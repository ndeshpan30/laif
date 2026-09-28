# UI_UX.md — Design System: "Ledger" (Notion × Newsprint × Bullet Journal)

## 1. Design Philosophy

The interface should feel like writing in a well-kept bullet journal that happens to be alive: **calm, dotted-grid paper** underneath a **clean Notion-like structural minimalism**, with the **editorial confidence of the Newsprint system** — sharp corners, strong typographic hierarchy, visible structure — but softened enough for daily conversational use and fully theme-aware (light/dark).

Core tenets:
- **The main screen is purely conversational.** No dashboard, no charts, no buttons to hunt for — just a chat surface over a dot-grid background, exactly like opening a journal to write.
- **The dashboard is secondary** ("The Ledger") — a dedicated page for stats, charts, and the historical log of everything accomplished, reached by navigation, never the landing experience.
- **Zero border radius, everywhere.** No soft shadows, no blur, no rounded corners — sharp rectangular structure, in the spirit of both Notion's clean blocks and Newsprint's stark geometry.
- **Everything trackable gets a chart.** Every tracker the user defines should have a corresponding visual (area/line chart with a dotted background) on the Ledger page.

## 2. Design Tokens

### Colors — Light Mode
- Background / Paper: `#F9F9F7`
- Foreground / Ink: `#111111`
- Muted / Divider: `#E5E5E0`
- Accent (sparing use only — warnings, streak highlights): `#CC0000`
- Dot-grid dot color: `rgba(17,17,17,0.08–0.10)`

### Colors — Dark Mode
- Background / Slate Board: `#191919`
- Foreground / Chalk: `#EBEBEB`
- Muted / Divider: `#333333`
- Accent: same `#CC0000`, used identically sparingly
- Dot-grid dot color: `rgba(235,235,235,0.08)`

Implement as CSS variables switched by a `.dark` class (via `next-themes` or equivalent), never as hardcoded hex in components.

### Typography
- **Serif (headlines / Ledger page titles)**: `'Playfair Display', serif` — used only on the dashboard, never in the chat.
- **Body / chat bubbles**: `'Inter', sans-serif` — clean, legible, conversational.
- **Monospace (stats, timestamps, tracker labels, metadata)**: `'JetBrains Mono', monospace` — gives the "ledger entry" feel to dates and numbers.
- Uppercase + `tracking-widest` + monospace for all section labels, metadata, and navigation — a Newsprint signature carried through.

### Radius & Borders
- Border radius: `0px` everywhere, no exceptions.
- Standard border: `1px solid` in the current mode's ink/muted color.
- Heavy dividers (major section breaks, Ledger header): `border-b-4`.
- Collapsed grid borders in multi-column layouts (share borders between adjacent cells — `border-r` on all but the last column, `border-b` on all but the last row).

### Shadows
- No soft drop shadows anywhere.
- Hover state on interactive cards: hard offset shadow `4px 4px 0px 0px <ink-color>` with a `-2px, -2px` translate — a "lifted paper cutout" effect, not a blur.

## 3. The Dot-Grid Texture (Signature Element)

Applied globally to `body` as the base bullet-journal paper feel, theme-aware via CSS variables:

```css
:root {
  --bg-paper: #F9F9F7;
  --text-ink: #111111;
  --border-line: #E5E5E0;
  --dot-color: rgba(17, 17, 17, 0.1);
}
.dark {
  --bg-paper: #191919;
  --text-ink: #EBEBEB;
  --border-line: #333333;
  --dot-color: rgba(235, 235, 235, 0.08);
}
body {
  background-color: var(--bg-paper);
  color: var(--text-ink);
  background-image: radial-gradient(var(--dot-color) 1px, transparent 1px);
  background-size: 16px 16px;
}
* { border-radius: 0px !important; }
```

Charts reuse the exact same dot pattern internally (via an SVG `<pattern>` in `<defs>`) so the chart background visually matches the page background — the chart looks like it's drawn directly onto the journal paper.

## 4. Screen 1 — Main Conversational Page (Primary Landing)

This is the **only** screen a user sees by default. It is deliberately empty of anything except:
- A scrollable message thread (AI messages left-aligned with a subtle bordered card; user messages right-aligned, filled with the ink/chalk color and inverted text) sitting directly over the dot-grid background — no card chrome around the whole page, just the messages themselves as bordered blocks.
- A single input bar, pinned to the bottom, with only a bottom border (no full box) — `border-b-2`, transparent background, monospace placeholder text, subtle background tint on focus.
- No visible navigation chrome beyond a minimal way to reach the Ledger (e.g., a small top-right icon/link) — the point is zero cognitive overhead on landing.

Message styling:
- AI message: `border border-[var(--border-line)]`, background = paper color, label above it in uppercase monospace ("AGENT").
- User message: filled with `var(--text-ink)` background and inverted (paper-colored) text, label ("YOU") right-aligned above it.

Voice input (if enabled) surfaces as a mic icon inline with the input bar; when active, show a minimal waveform or pulsing dot — no modal, no separate screen.

## 5. Screen 2 — The Ledger (Dashboard / History Page)

Reached via navigation from the main screen, never the default view. This is where every tracked thing gets a chart, and the full historical log lives.

### Layout
- Header: large serif title ("The Ledger."), a `border-b-4` heavy divider beneath it, and a small monospace metadata line underneath (e.g., "Vol. 1 | Life Telemetry & Schedule Adherence") — a nod to Newsprint's editorial masthead, kept purely as flavor, not literal newspaper content.
- Main content area: 12-column grid, asymmetric split (e.g., 8/4), with a shared collapsed border (`border-r` on the left column only on desktop, full-width stacked on mobile).
- **Left column (majority width)**: charts. One area/line chart per active tracker (sleep, stress, mood, custom trackers), each rendered on the dot-grid pattern with a soft fill gradient in the ink/chalk color, monospace axis labels, and a bordered container with the hard-shadow hover effect.
- Below the primary chart(s): a row of small stat cards (habit streak count, schedule-drift percentage, etc.) — plain bordered boxes, one inverted (dark-filled) card per row to create a visual accent, per Newsprint's "invert one section" rule.
- **Right column (minority width)**: the running historical log — a reverse-chronological list of everything accomplished (tasks completed, habits logged, notes), each entry showing its BuJo-style glyph (`X` for completed task, `·` for a logged habit, `-` for a note) next to a one-line description and a monospace timestamp, separated by dashed dividers.

### Chart Component Spec

Built with Recharts `AreaChart`, matching the reference implementation the user specified — dotted background, single continuous area with a soft fill, minimal axis chrome:

```tsx
<div className="border border-[var(--border-line)] bg-[var(--bg-paper)] p-4">
  <h3 className="mb-4 font-mono text-xs uppercase tracking-widest">Tracker Name</h3>
  <ResponsiveContainer width="100%" height="100%">
    <AreaChart margin={{ top: 8, right: 8, bottom: 0, left: 8 }} data={data}>
      <defs>
        <pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="2" cy="2" r="1" fill="var(--dot-color)" />
        </pattern>
        <linearGradient id="fillGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%" stopColor="var(--text-ink)" stopOpacity={0.3} />
          <stop offset="95%" stopColor="var(--text-ink)" stopOpacity={0} />
        </linearGradient>
      </defs>
      <rect width="100%" height="100%" fill="url(#dots)" />
      <XAxis dataKey="date" axisLine={false} tickLine={false}
             tick={{ fill: 'var(--text-ink)', fontSize: 12, fontFamily: 'JetBrains Mono' }} dy={10} />
      <Tooltip contentStyle={{ backgroundColor: 'var(--bg-paper)', borderColor: 'var(--border-line)', borderRadius: '0px' }} />
      <Area type="monotone" dataKey="value" stroke="var(--text-ink)" fillOpacity={1}
            fill="url(#fillGradient)" strokeWidth={2} />
    </AreaChart>
  </ResponsiveContainer>
</div>
```

- Every user-defined tracker (via `tracker_definitions`) automatically gets one of these cards on the Ledger — no manual dashboard configuration required.
- Charts must render correctly and legibly in both light and dark mode via the CSS variables, never hardcoded colors.

## 6. Components

### Buttons
- Primary: solid ink/chalk background, inverted text; on hover, inverts fully (background becomes paper color, border appears).
- Secondary (outline): transparent background, `border` in ink color; fills solid on hover.
- Ghost: no border, subtle muted background on hover only.
- All buttons: sharp corners, uppercase label, `tracking-widest`, minimum 44×44px touch target.

### Cards
- `border` in ink/muted color, paper background, `p-6` padding, sharp corners.
- Hover: hard offset shadow (`4px 4px 0px 0px var(--text-ink)`) + `-2px,-2px` translate.

### Inputs
- Bottom-border only (`border-b-2`), transparent background, monospace font.
- Focus: subtle background tint, no ring/glow — consistent with the flat, no-blur philosophy.

### Icons
- `lucide-react`, `stroke-1` or `stroke-width={1.5}`, colored to match the current text/ink color; inverted (paper-colored) inside filled/dark sections.

## 7. Dark/Light Mode Toggle

- Implemented via `next-themes`, a simple icon toggle (sun/moon) placed unobtrusively — likely alongside the Ledger navigation link, never on the main conversational screen's primary focal area.
- All tokens above are CSS variables so the entire dot-grid, chart, and component set flips instantly with zero component-level conditional logic.

## 8. Responsive Behavior

- Mobile: the Ledger's 12-column grid collapses to a single column; `border-r` dividers are dropped, `border-b` dividers are kept between stacked sections.
- The conversational main page is mobile-first by default — it is already a single vertical column of messages plus a pinned input bar, so no adaptation is needed beyond standard safe-area padding for PWA installs.
- Typography scales down one step on mobile (serif headline `text-5xl` → not larger; chat text stays constant for legibility).

## 9. Motion

- Fast, mechanical transitions only (`transition-all duration-200 ease-out`) — no bouncy/organic easing, consistent with the flat, structural aesthetic.
- New chat messages: simple fade/slide-in, no elaborate animation.
- Chart data updates (after a re-solve): smooth transition of the area path, not a hard redraw — this is the one place a slightly softer motion is appropriate, since it visually communicates "the schedule just changed."

## 10. Accessibility

- Contrast: ink-on-paper and chalk-on-slate both exceed AAA contrast ratios in their respective modes.
- Focus states: visible `focus-visible` ring in ink color, 2px offset, keyboard-only.
- Semantic HTML throughout (`header`, `nav`, `main`, `section`); all icon-only buttons carry `aria-label`.
- Minimum 44×44px touch targets for all interactive elements, especially on the mobile input bar and Ledger nav.
