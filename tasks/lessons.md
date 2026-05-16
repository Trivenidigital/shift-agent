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

## 2026-05-15 - Flyer routing and cf-router precedence

- When adding a new WhatsApp agent, verify pre-gateway plugins before trusting the dispatcher matrix. `cf-router` can skip the LLM and dispatcher entirely, so new intent families need explicit bypass/priority guards there too.
- Food/event/festival words inside a flyer brief are not Catering intent when an explicit flyer/design keyword or active flyer project exists. Guard the deterministic Catering F7 branch before weak follow-up suppression, especially when the sender has an old active catering lead.
- Do not stop at "cf-router returns None" for live WhatsApp readiness. A new agent must also prove the next gateway/dispatcher validation boundary accepts the real inbound sender block and can invoke the target skill without fail-closed UX.
- When running production smoke probes as root against state files normally owned by `shift-agent`, immediately verify ownership/mode or chown the agent state directory. Root-created JSON state with `0600` can pass root probes and then fail real WhatsApp traffic with `PermissionError`.
- Flyer workflow smoke is not product-quality proof. A deterministic/Pillow renderer can validate state, delivery, and PDF packaging, but customer readiness requires a real image-generation path and a visual quality gate before calling the agent production-ready.
- Flyer Studio must optimize for one-shot quality and credit discipline. Do not default to three image concepts with expensive models; generate one best design, ask for `APPROVE` or changes, and export final formats from the selected image without spending image credits again.
- Long-running Flyer image generation must send an immediate WhatsApp acknowledgement before the model call. Customer-visible responsiveness is part of correctness; no silent multi-minute waits after pressing send.
- Flyer revisions are not notes-only. Any revision must invalidate the current selected design/final assets, apply high-confidence structured field edits (date/time/etc.), regenerate a design, and block `APPROVE` until the revised design is ready.
- Flyer menu/promo requests are not always one-time events. Breakfast menus, weekend specials, and recurring offers may omit a date legitimately; parse location/address/phone/menu details, allow recurring schedules without fake dates, and pass the full menu/pricing text into the image prompt.
- Active Flyer projects must not swallow explicit new flyer work. If a sender has an old awaiting-approval project, route "need/create flyer..." and media-backed menu/template edits as new projects, not as revisions to stale state.
- Uploaded flyer/template images need project-level reference assets when the sender is not onboarded yet. Customer brand storage alone is not enough, because unauthenticated/pending senders may not have an active customer profile for the renderer to read.
- Menu and price-list flyers can be complete without event date/time/venue. For product lists with prices and a contact number, generate the flyer instead of waiting for event fields that do not exist.
- Do not synthesize E.164 phone numbers from `@lid` values in Flyer routing. If `identify-sender` cannot resolve a phone/LID mapping, fail closed rather than creating account, quota, or project state under a fabricated identity.
- For money-adjacent onboarding, make payment references immutable history, not just "latest scalar on customer." A reused Stripe/Razorpay/manual reference must remain blocked even after plan changes.
- Account scripts that read Pydantic schemas must run through the Hermes venv in cf-router and smoke/deploy paths. Direct shebang execution can accidentally use system Python and fail only in production.
- Saved logo/template replacement is an account-admin action. Authorized flyer requesters can request flyers, but permanent brand-kit changes need the business WhatsApp/onboarding admin or explicit owner role.
- Flyer Studio marketing must be business-category neutral unless targeting a specific vertical campaign. User examples like item swaps or price edits describe a revision pattern, not restaurant-only copy. Lead with universal owner pain: lost time, slow approvals, repeated revisions, and getting finished marketing assets without design software.
- Keep Hermes as an internal platform name only. Customer-facing Flyer Studio marketing, WhatsApp outreach, flyers, onboarding language, and sales conversations must not mention Hermes.
- For Flyer Studio outbound campaigns, do not put the whole pitch in the WhatsApp text body. Send a strong visual flyer first; keep the message short with only the calls to action such as "Start Free Trial" and "Act Now! Save Time and Money" plus links.
- Flyer Studio campaign creative must be broadly useful across restaurants, salons, tutors, realtors, temples, service businesses, and local retailers. Avoid product-specific examples like Dosa/Idly/Poori/Parota unless the campaign is explicitly restaurant-specific.
- For Flyer Studio marketing flyers, keep the "100+ businesses" social-proof badge unless the user explicitly asks to remove it.
- For customer-facing Flyer Studio outreach, do not expose raw URLs when a true WhatsApp button/CTA/link mechanism is available. Preferred visible CTAs are "Start Free Trial" and "Act Now! Save Time and Money."
- Flyer Studio campaign CTA labels must not share the same backend action. "Start Free Trial" should prefill a ready-to-create trial opener, while "Act Now! Save Time and Money" should prefill onboarding/setup intent so routing and customer expectations stay distinct.
- Flyer Studio WhatsApp campaign buttons should be reply/action buttons, not URL CTA buttons, when the user expects one-tap intent inside the chat. URL buttons open browser/dialog UI; they do not send or type an inbound message to the agent.
