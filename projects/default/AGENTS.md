# AGENTS.md

## Purpose

This project is for fast design work with coding agents. Keep it lightweight. Do not turn it into a heavy design app or framework unless the user explicitly asks for that.

Goal: turn short prompts into visible wireframes, concepts, flows, page previews, or lightweight prototypes.

Project-specific intent, design notes, and working rules belong here while the project is still small. Avoid extra strategy, decision, or concept files until they are actually useful.

The workspace should feel like a canvas: screens, variants, and components are placed visually first. Tweak panels are optional and should only appear when the user asks for them or when live parameters make comparison faster.

## Design Source

- Read `DESIGN.md` before doing UI work.
- Treat `DESIGN.md` as the design baseline for this project.
- If the user's request conflicts with `DESIGN.md`, follow the user and mention the deviation briefly.
- Ask for existing UI, design tokens, screenshots, brand guidelines, or product context when that would materially improve the design direction.
- Do not invent a design direction from scratch when useful reference material exists.

## Before The First Pixel

Before creating a new screen or direction, briefly decide the system for that design:

- What typeface and type scale?
- What color palette, with at most 2 to 3 main colors?
- What spacing unit?
- What visual vocabulary: cards, lines, space, split panes, canvas, tables, lists?

Apply those decisions consistently. Record them briefly in the file or in the agent response if they matter for the next iteration.

## Working Rules

- Prefer simple files over infrastructure.
- `canvas.jsx` is the board plan: project metadata, `DCSection`, `DCArtboard`, `DCPage`, and visible composition.
- Small one-screen sketches can stay directly in `canvas.jsx`.
- If there are multiple screens, larger components, or reusable variants, place them in `components/*.jsx` and use them from `canvas.jsx`.
- Component files export through `window`, for example: `window.LoginScreen = LoginScreen`.
- Avoid generic names like `const styles = {}` in component files. Use component-specific names.
- The shared canvas renderer lives outside this project folder in `../../core/` and should only be changed when the user asks to change shared infrastructure.
- Use plain HTML/CSS for static wireframes when that is enough.
- Use React/JSX in `canvas.jsx` and `components/*.jsx` for interaction, variants, or tweaks.
- No build step: no Vite, no npm project, no SvelteKit unless explicitly requested.
- Use Markdown for concepts, flows, product logic, and decisions.
- Use SVG only for simple diagrams or static sketches.
- Add new concept files only when they are genuinely useful.
- Chat history lives in `chats/`.
- Project assets belong in `assets/` when needed.

## Local Agent Bridge

- The canvas can include a left-side chat panel that starts local agents.
- Keep the bridge simple: local Python server, JSONL history, no database, no queue system unless it becomes necessary.
- Full chat history is stored in `chats/*.jsonl` and passed to the agent.
- The latest automatic Canvas QA result is stored in `chats/*.qa.json` and passed to the next agent prompt.
- CLI calls to `codex` and `claude` are allowed because these agents are expected to edit the same local project files.
- The browser does not directly mutate design files. It sends message, target file, and selector context to the bridge; the CLI agent edits files.
- Each project may have its own local `.git` repo. The bridge creates a baseline snapshot and commits successful agent edits automatically.
- Project-local git ignores `chats/`, `attachments/`, and `qa/` by default so versioning stays focused on design files.

## Canvas Model

Use these simple primitives:

- `Canvas`: a large zoomable surface for screens, variants, and components.
- `Page View`: one fullscreen page or app view without the canvas board. Set `window.DesignProject.view = "page"` and render `DCPage`.
- `DCSection`: a thin group for related screens or variants.
- `DCArtboard`: a stable viewport for one screen or state, with `id`, `label`, `width`, `height`, and optional position.
- `DCPage`: a fullscreen page in Page View. Use it for exactly one website, landing page, or app view that should be checked responsively.
- `ARTBOARD`: shared size presets from the renderer. Use `ARTBOARD.mobile` (390x844) and `ARTBOARD.desktop` (1280x820) when nothing more specific is needed.
- `dc-screen`: the root inside an artboard. It fills the artboard.
- `dc-screen-body`: the flexible content area inside a screen. It may scroll internally when content is genuinely longer.
- `Variant`: a named design direction inside an artboard.
- `Selector Mode`: a mode where clicks mark an element and provide a direct selector for agent edits.
- `Tweaks`: optional live controls for density, radius, hue, contrast, or mode.

Use one screen root per artboard by default:

```jsx
<DCArtboard id="step-1" label="1 - Start" width={390} height={844}>
  <main className="dc-screen" data-agent-id="step-1.screen">
    <section className="dc-screen-body" data-agent-id="step-1.body">
      ...
    </section>
  </main>
</DCArtboard>
```

Use Page View for a single fullscreen page:

```jsx
window.DesignProject = {
  title: "Page Concept",
  view: "page",
  render({ DCPage }) {
    return (
      <DCPage id="page-concept">
        <PageConcept />
      </DCPage>
    );
  }
};
```

In Page View, the toolbar has a width control. Use it for quick responsive checks without creating separate artboards.

Important editable areas should have stable `data-agent-id` attributes. Selector Mode should prefer those IDs so users can point to a specific element.

The canvas is a renderer, not a visual editor. The agent edits JSX/HTML directly. Pan and zoom belong to the canvas wrapper; screen content should remain normally interactive. Wheel zoom should only apply on empty canvas space, not over a screen or interactive element.

## Layout Contract

- Treat `DCArtboard width/height` as a fixed viewport, not a suggestion.
- No screen content may overflow horizontally.
- A screen should intentionally use the artboard height: root `height: 100%`, with `grid-template-rows: auto minmax(0, 1fr) auto` or equivalent.
- For mobile flows, header, progress, and footer are fixed rows; the middle content uses `minmax(0, 1fr)` and scrolls only when there is genuine extra content.
- For desktop flows, the inner screen should use the full artboard height. Avoid a small top section with accidental empty space below.
- Avoid hard inner widths larger than the artboard. Use `width: 100%`, `max-width: 100%`, and `min-width: 0`.
- For lists and rows, use `minmax(0, 1fr)` instead of plain `1fr` when actions sit beside text.
- If the canvas shows a red `overflow x` or `overflow y` marker, the wireframe is not done. Fix density, wrapping, scroll areas, or artboard size.

## Automatic QA

- After the browser renders a project, it runs a lightweight QA check.
- Current checks: project load state, artboard count, artboard overflow, body horizontal overflow, blank artboards, and Page View horizontal overflow.
- A passing QA result means the visible artifact rendered and no obvious overflow was found.
- A warning means the next agent should fix the reported issue before continuing visual exploration.

## Design Rules

- Create real screens, not marketing landing pages, unless explicitly requested.
- Use realistic example content, not `Lorem ipsum`.
- Do not invent content, fake numbers, or icons just to fill space.
- Keep layouts quiet, clear, and easy to scan.
- No cards inside cards.
- No aggressive gradients, orbs, or purely atmospheric backgrounds.
- No decorative SVG illustrations when a clear placeholder or real asset would be better.
- Do not default to rounded containers with a colored left-border accent.
- Text must not overflow or cover UI elements.
- Controls need stable dimensions so layouts do not jump.
- Consider mobile and desktop at least roughly.
- Use CSS Grid/Flex with `gap`, `text-wrap: pretty`, and prefer `oklch()` for new colors.

## Agent Output

When building a new wireframe:

1. Work in the current project folder.
2. Use `canvas.jsx` for board composition.
3. Use `components/*.jsx` once there are multiple screens or larger components.
4. Show at least three variants for open visual-direction questions.
5. Check each artboard mentally: no horizontal overflow, no accidental empty space, clear scroll area when needed.

For variants:

- One direction should cleanly apply familiar patterns.
- One direction can be bolder with color or typography.
- One direction should test a different layout or interaction metaphor.
- Variants should explore different axes: density, color, hierarchy, motion, layout, or interaction.

## Non-Goals

- Not a full Figma replacement.
- No multiplayer canvas at the start.
- No complex custom file format.
- No backend unless there is a concrete need.
- No framework just because it is possible.
- No `src/` app scaffold before this becomes a real product.
