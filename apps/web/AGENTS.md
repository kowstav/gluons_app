<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Owner app conventions

- `src/app/globals.css` is the design system. Its header explains the colour rule: blue is an owner decision, green is confirmed by the system or a readback, amber is waiting, red is stopped, and a dashed grey outline means nothing external happened. Use the tokens; do not hard-code colours.
- Shared pieces live in `src/components/ui.tsx` (`Field`, `Tabs`, `DataView`, `Diff`, `Receipt`, `useConfirm`). Show server records with `DataView` rather than raw JSON, and ask with `useConfirm` before anything that cannot be taken back. Reducing access (revoke, pause, emergency stop) stays one click.
- `console.tsx` is the shell and route table. Screens live in `src/components/pages/`, plus `operations.tsx` and `setup-room.tsx`.
- Keep one `role="status"` message per screen, give every control a label, and keep hints in `Field` so they do not become part of the control's name.
- Only the server's `verified` flag earns the verified stamp. Environment and measured/estimated labels come from the API, never from the client.
- Fonts are self-hosted from `src/fonts/` (SIL OFL 1.1). Do not add a font CDN.
- Design direction follows Anthropic's `frontend-design` skill; the quality checklist is Vercel's Web Interface Guidelines.
- Browser tests need the pinned Chromium build. Where only another build exists, set `GLUONS_E2E_CHROMIUM` to its executable.
