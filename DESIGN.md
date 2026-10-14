# Panel design

The panel follows Impeccable's Operate guidance: familiar controls, precise hierarchy,
state visibility, restrained color, responsive structure, and accessible keyboard interaction.

- Warm off-white navigation and a light content surface suit a local daytime utility.
- Deep green marks the current navigation item, primary actions, and positive states.
  Amber and red distinguish pending/error states with accompanying text.
- One UI sans-serif family carries headings, controls, and tables; monospace is limited
  to candidate codes, task identifiers, and logs. Numeric data uses tabular numerals.
- Four routes: workspace, task history, reference catalog, connection/settings.
  The workspace puts task creation and current execution beside one another, followed
  by a results/log view. Settings use labeled groups, not nested cards or modals.
- Every data value comes from the backend. Empty and first-run states explain the next
  action; reference prices are explicitly historical and unverified.
- Small authored SVG icons share a consistent stroke. Controls have visible focus,
  disabled/loading states, at least 44px target heights, and reduced-motion support.
- On small screens navigation becomes a compact top row, forms stack, and tables
  scroll inside their own region without forcing horizontal page overflow.
