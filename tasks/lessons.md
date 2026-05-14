# Lessons

## 2026-05-13 — Hermes-first and deployed-state first

- Before live smoke interpretation or follow-on work, verify the active deployed Hermes mode/config and current code path. Do not infer from an older handoff.
- For this project, treat `cf-router`/Hermes substrate as the first explanation for inbound behavior. Only describe a dispatcher chain as healthy after audit evidence shows the deployed path actually reached it.
- Enforce Hermes-first before any custom code, plan, review, or bug-fix proposal: check repo primitives, deployed VPS Hermes skills, deployed VPS Hermes plugins, and the Hermes ecosystem before scoping net-new work.
- When saying "Hermes capabilities", include what is actually installed on the VPS under `/root/.hermes/skills` and `/root/.hermes/plugins`, not just source files in this checkout.
- Related Hermes ecosystem checks to remember: Self-Evolution Kit at `https://github.com/NousResearch/hermes-agent-self-evolution` and Awesome Hermes Agent at `https://github.com/0xNyk/awesome-hermes-agent`.
- Do not treat `employee` sender identity as "never a customer." Employees can submit legitimate catering inquiries for their own/family/friend events. Owner identity remains control-plane; employee identity can still enter customer-side catering when intent is clear.
- For active catering leads, do not require a follow-up message to independently satisfy the new-inquiry classifier. Weak menu/proposal/food/event signals can be enough to route to the existing lead's follow-up branch; otherwise status/menu follow-ups fall into the generic LLM.
- For SSH on Windows, always use the two-step redirect/read pattern; never rely on inline SSH stdout.
