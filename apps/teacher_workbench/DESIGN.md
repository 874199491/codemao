# Teacher Workbench visual identity

## Register

Calm operational control room: reliable enough for data writeback, warm enough for daily teaching work. The interface should feel like a well-kept teacher's desk, not a generic analytics dashboard.

## Palette

- Ink: `#17211D`
- Canvas: `#F4F6F1`
- Paper: `#FFFFFF`
- Deep green: `#132B27`
- Action green: `#1F8A5A`
- Soft green: `#E6F3EB`
- Signal yellow: `#F3C84B`
- Error red: `#C6534B`

## Typography

- Human voice and Chinese UI: `"Microsoft YaHei UI", "PingFang SC", sans-serif`
- Data voice: `"Cascadia Mono", Consolas, monospace`
- Use the mono face only for IDs, percentages, timestamps and workflow numbers.
- Headlines use weight 900; supporting copy uses weight 400. Numeric columns use tabular figures.

## Motion

- HyperFrames composition id: `teacher-workbench`.
- One deterministic GSAP entrance timeline, registered in `window.__timelines`.
- Sequence: sidebar, header, overview, metrics, operations, activity.
- Entrances use transforms and opacity only, between 0.38 and 0.72 seconds with `power3.out`.
- No infinite movement. Metric progress bars may animate once after live data arrives.
- Respect `prefers-reduced-motion`; content must remain fully visible without animation.

## Layout

- Fixed navigation rail on desktop, compact rail on medium screens, content-first mobile layout.
- Four live metric cards remain directly below the overview.
- The four thread-backed operations are the only primary action cards.
- Week context is calculated from the 2026-07-24 cohort start and shown in the hero and health panel.
- Confirmation copy must state the exact DingTalk write scope and preserved manual fields.

## Avoid

- Neon gradients, excessive glow, glass panels that reduce readability.
- Decorative motion on controls while a workflow is running.
- Generic “today's todo” or “pending feedback” cards not backed by the requested workflow.
- Hard-coded W1 labels in update actions; the current target week must be explicit.
- Hiding errors behind friendly status text; logs remain visible and copyable.
