# Lessons

## 2026-05-13 — Hermes-first and deployed-state first

- Before live smoke interpretation or follow-on work, verify the active deployed Hermes mode/config and current code path. Do not infer from an older handoff.
- For this project, treat `cf-router`/Hermes substrate as the first explanation for inbound behavior. Only describe a dispatcher chain as healthy after audit evidence shows the deployed path actually reached it.
- Enforce Hermes-first before any custom code, plan, review, or bug-fix proposal: check repo primitives, deployed VPS Hermes skills, deployed VPS Hermes plugins, and the Hermes ecosystem before scoping net-new work.
- When saying "Hermes capabilities", include what is actually installed on the VPS under `/root/.hermes/skills` and `/root/.hermes/plugins`, not just source files in this checkout.
- Related Hermes ecosystem checks to remember: Self-Evolution Kit at `https://github.com/NousResearch/hermes-agent-self-evolution` and Awesome Hermes Agent at `https://github.com/0xNyk/awesome-hermes-agent`.
- For "no key / no token" Hermes work, perform current market research across installed Hermes skills/plugins, official Hermes skills, Awesome Hermes, vendor MCP servers, iPaaS MCP options, and manual-export fallbacks before labeling anything custom-only.
- Do not treat `employee` sender identity as "never a customer." Employees can submit legitimate catering inquiries for their own/family/friend events. Owner identity remains control-plane; employee identity can still enter customer-side catering when intent is clear.
- For active catering leads, do not require a follow-up message to independently satisfy the new-inquiry classifier. Weak menu/proposal/food/event signals can be enough to route to the existing lead's follow-up branch; otherwise status/menu follow-ups fall into the generic LLM.
- For SSH on Windows, always use the two-step redirect/read pattern; never rely on inline SSH stdout.

## 2026-05-14 — Production pilot posture

- First production pilot bundle is **Shift Agent + Catering Agent + Daily Brief Agent**. Do not start by adding a greenfield third agent; Daily Brief is the owner control tower that makes Shift/Catering operable.
- Production readiness must be enforced by a deterministic gate, not by memory. Use `/usr/local/bin/pilot-readiness-check --text` before saying a customer VPS is ready for the first three-agent pilot.
- Current `main-vps` readiness after deploy `deploy-20260514-170739-f4ce14db`: gateway active, WhatsApp bridge connected, timers active, roster valid, catering menu valid with 78 available items. Blocking rows are only `customer.name` and `customer.location_id` placeholders.
- For catering menu updates, verified owner OR verified employee may submit a menu image/PDF source, but only the owner may apply the extracted menu with the confirmation code. Preserve that split in future dispatcher/SKILL changes.
- Self-learning/evolution rule for production: live agents may learn state and memory (menus, LIDs, customer notes, lead history, roster facts) but must not mutate code, SKILLs, prompts, or deploy config in prod. Skill/code evolution goes through traces/evals, tests, review, PR, and tarball deploy.
- For a 3-4 day customer ask, treat this as a production pilot with explicit smoke evidence, not broad GA. Use `docs/runbooks/production-pilot-shift-catering-daily-brief.md` as the acceptance script.
- Commit before deploy whenever possible. The 2026-05-14 pilot gate was deployed from an uncommitted working tree, so the deploy tag uses the previous HEAD hash; future production deploys should commit first for traceability unless the user explicitly asks for an emergency hot deploy.
- When editing runtime YAML, account for schema defaults that may not be present in the file. If appending a list item such as `daily_brief.sections`, first materialize the full intended default list plus the new item; otherwise a default-backed config can be narrowed accidentally.
- For pydantic-backed production CLIs on the VPS, run through `/usr/local/lib/hermes-agent/venv/bin/python` or an equivalent Hermes-venv shebang. System Python can lack pydantic and make direct operator/systemd execution diverge from smoke tests.
