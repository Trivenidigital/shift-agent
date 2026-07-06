# Phase D — Google Business Profile + Instagram API scoping (PAPER-ONLY)

**Drift-check tag:** `extends-Hermes` — the GBP posting leg (PR-D4) adds a NEW
external-write boundary (OAuth token store + one POST wrapper + public image
host) on top of unmodified Hermes primitives (approval gesture, JSON+flock
sidecar, audit chain, WhatsApp delivery). It does not fight any Hermes
convention; it consumes an external service Hermes does not own. Per CLAUDE.md,
external WRITE APIs are genuine net-new engineering — but the
`mcp/native-mcp` community-MCP check is a MANDATORY gate before any custom
OAuth/post LOC.

**Status:** PAPER-ONLY (2026-07-06). Hard gates honored: NO Google Cloud
project created, NO access request filed, NO OAuth consent, NO API calls, NO
external accounts. This document is the to-verify checklist and account-model
decision input that GATES PR-D4; nothing here is executed. Knowledge-cutoff
caveat applies throughout (quotas, endpoints, and review timelines move — every
"verify at D4" line below is a live re-check, not a settled fact).

Companion docs: `tasks/phase-d-flyer-to-gbp-spec.md` (merged #564, the feature
spec + offline prototype), `tasks/phase-d-d3-d4-design.md` (the D3/D4 build
design that consumes this scoping's account decision).

## Hermes-first capability checklist

Run against the per-step model. The GBP posting leg is dominated by net-new
external-write work; the messaging/approval/audit substrate around it is all
Hermes.

| Step | Tag | Note |
|---|---|---|
| Owner grants posting access (manager-role or OAuth consent) | `[net-new]` | external account action; no Hermes primitive; friction map is the deliverable below |
| Store the OAuth token / refresh token | `[net-new]` thin | new sidecar; storage substrate (JSON + flock + `atomic_write_json`) IS Hermes |
| Compose the post body from locked facts | `[Hermes-adjacent]` | the deterministic composer shipped offline (#564); this doc adds nothing to it |
| Host the flyer image at a public URL (GBP `media.sourceUrl` needs a reachable URL) | `[net-new]` | GBP will not accept a WhatsApp media handle; a public image host is required — see §1.3 |
| POST `localPosts.create` | `[net-new]` | the external write; `mcp/native-mcp` gate first |
| YES-per-post approval before any post | `[Hermes]` | approval-alias + quoted-reply gesture (actions.py:1699-1709) |
| Audit each post attempt/result | `[Hermes]` | `ndjson_append` chokepoint + additive `LogEntry` variants |
| Alert on post failure at the write site (§12b) | `[Hermes-adjacent]` | Telegram alerter deployed; net-new is the one call site |

### Ecosystem check

| Domain | Hermes / ecosystem skill found? | Decision |
|---|---|---|
| GBP post publishing | none — 4-source audit `tasks/skills-roadmap.md` has zero GBP/Google-Business/social-posting entries | build in a gated future phase (PR-D4); `mcp/native-mcp` community-MCP check MANDATORY before any custom OAuth/POST LOC |
| Public image hosting for `media.sourceUrl` | Hermes delivers media over WhatsApp but exposes no public HTTPS asset URL | net-new (a per-VPS static host or object store); scope in D4 |
| Instagram publishing | none | do NOT build — see §5; caption stays paste-ready text indefinitely |
| OAuth token storage | yes — `safe_io.atomic_write_json` + `fcntl.flock` sidecar pattern (deployed, e.g. `quote_echo_pending.json`) | mirror it |
| Approval / audit / delivery | yes — approval gesture, `LogEntry` union, `send_flyer_text` chokepoint | reuse (detailed in the D3/D4 design doc) |

**Verdict:** the entire messaging/approval/audit substrate is Hermes. The
genuinely new engineering in D4 is (a) the OAuth-on-behalf-of-owner leg, (b) a
public image host for `media.sourceUrl`, and (c) one `localPosts.create`
wrapper with §12b failure alerting — all gated on the account-model decision in
§3 and the `mcp/native-mcp` check.

---

## 1. GBP posting API surface (verify at D4 — cutoff caveat)

### 1.1 Endpoint & resource — posts live on the LEGACY v4 API

- Local posts are created on the **legacy Google My Business API v4.9**:
  `POST https://mybusiness.googleapis.com/v4/accounts/{accountId}/locations/{locationId}/localPosts`.
  Confirmed still active and documented as of 2026 (create / delete / get /
  list / patch / reportInsights).
- **The newer split Business Profile APIs do NOT cover local posts.** Business
  Information / Account Management / Q&A / Performance were split out of the v4
  monolith, but `localPosts` was never migrated — it remains on the v4 host.
  This is the single most common integration confusion (403s from hitting the
  wrong host); D4 must target `mybusiness.googleapis.com/v4`, not
  `mybusinessbusinessinformation.googleapis.com`.
- **Business Profile Performance API is metrics-only** (`getDailyMetricsTimeSeries`
  etc.) — relevant LATER for "your post got N views" reporting, never for
  posting.

### 1.2 `LocalPost` fields we use

- `summary` — the post body text. **Character cap: the v4 reference does not
  state a hard number in the pages fetched; the long-documented Business
  Profile post limit is 1,500 characters.** The prototype conservatively
  enforces `GBP_POST_MAX_CHARS = 1500`; D4 must confirm the live API's actual
  reject behavior and keep the guard at or below it.
- `topicType` (required enum): `STANDARD` | `EVENT` | `OFFER` | `ALERT`. Our
  draft is a `STANDARD` post (a "what's on" update). OFFER/EVENT carry extra
  required sub-objects (offer coupon/redemption/terms; event title + schedule)
  and are a possible later enrichment — NOT v0.
- `callToAction { actionType, url }` — `actionType` ∈ `{ CALL, ORDER, SHOP,
  LEARN_MORE, SIGN_UP, BOOK }`. `CALL` uses the profile's own phone and leaves
  `url` unset — the natural default (our drafts already end with "Call
  <phone>"); anything with a `url` requires a real destination we do not have,
  so v0 uses `CALL` or omits the CTA.
- `media[]` — a `MediaItem` where **`sourceUrl` is the ONLY supported data
  field** for a local-post media item. See §1.3 — this is a hard constraint.
- `event.recurrenceInfo` (RecurrenceInfo: daily/weekly/monthly) — new in 2026;
  a recurring weekly-special poster could use it later. Not v0.
- `scheduledTime` — optional future publish time. v0 posts immediately on YES.

### 1.3 Media constraint — GBP needs a PUBLIC image URL (net-new work)

`media.sourceUrl` must be a **publicly reachable HTTPS URL** to the image;
GBP fetches it server-side. Our flyer finals live at
`/opt/shift-agent/state/flyer/finals/*.png` (private, per-VPS). Therefore D4
needs a **public image host**: either (a) publish the chosen final
(`final_instagram_post`, the square asset) to a public HTTPS path on the
per-VPS box or an object store, with an unguessable path + short TTL, or (b)
the owner attaches the photo by hand in the GBP composer while our draft
supplies only the body text. **Option (b) is the zero-new-infra fallback and
matches the current spec's "the owner pastes text + attaches the already-
delivered image" story** — D4 can ship posting the BODY via API while the photo
stays a manual attach, deferring the public-host build until there's demand for
fully-hands-off posting. Flagged as a D4 design fork, not decided here.

---

## 2. OAuth, access request, and quota ladder (verify at D4)

- **Scope:** `https://www.googleapis.com/auth/business.manage` — a single
  scope covering all Business Profile base URLs. It is a **sensitive/restricted
  scope**, so the OAuth consent screen requires Google app verification before
  production use (brand review + possibly a security assessment). That
  verification is a SEPARATE clock from the API access request below.
- **Enabling ≠ access.** Turning the API on in the GCP console gives the
  project **0 QPM** — "enabled but throttled to zero." A separate, manual,
  one-time **Basic API Access request** (business details + GCP project number +
  use case) must be approved before any quota is granted. `0 QPM` in the quota
  console == "not yet approved."
- **Quota ladder:** approved projects default to **300 QPM** across most
  Business Profile APIs, with a tighter **10 edits/min per profile** on
  Business Information writes. localPosts create sits comfortably inside this
  for our fleet (a handful of posts/day). Quota increases are a separate,
  non-automatic request. **Note the per-API footgun:** approval is per-API — a
  project approved for the Business Profile write APIs can still show
  Account Management API quota = 0, which blocks `accounts.list`; D4 must
  request access for EVERY API in the call path (Account Management for
  discovery + My Business v4 for posting).
- **Timeline is variable.** Google explicitly says not to promise a fixed
  approval date. Profiles should be active + complete for 60+ days before
  requesting. **Implication:** the access request is the long pole for D4 and
  should be filed EARLY (see §4 BSP interaction), decoupled from D2/D3.
- **Service account is NOT an option.** GBP requires user-context OAuth — the
  acting identity must be a human Google account that owns or manages the
  location. There is no service-account / domain-wide-delegation path to a
  consumer Business Profile. This is *why* both ownership flows in §3 route
  through a real Google identity, never a service account. (Verify at D4 — but
  this is a long-standing platform limitation, not expected to change.)

---

## 3. The two ownership flows (the operator decision that gates PR-D4)

Because a service account is impossible (§2), SOMETHING must hold a
user-context `business.manage` token for each customer's location. Two models:

### (a) Hisaku-managed profile — manager/admin access (RECOMMENDED)

- The business **owner keeps primary ownership** and adds a **Hisaku Google
  identity as a *manager*** of their Business Profile — a single UI action in
  the GBP dashboard ("add a manager"), or an `accounts.admins.create`
  invitation the owner accepts. Confirmed model: *"If a 3P partner is added as
  a manager of the GBP by the merchant … the 3P partner need not use merchant
  credentials to use GBP APIs to edit and access GBP data."*
- **One Google identity, one GCP project, one OAuth consent posts for the whole
  fleet.** No per-owner OAuth browser journey. At scale this is the standard
  agency model: register a GBP **Organization account**, use **user groups +
  business/location groups** to manage many client locations under one identity.
- **TOS / risk assessment:**
  - Hisaku holds **standing write access to every managed customer's live,
    public profile under one identity** — a real blast radius (a posting bug
    could post to every managed profile at once). Mitigations belong in D4:
    YES-per-post approval (never silent auto-post), a per-post idempotency key,
    a kill switch, §12b failure alerting, and a §12a freshness watchdog on any
    post-queue table.
  - Manager access is a **trust concession** the owner grants once. It is
    revocable by the owner at any time (removing the manager). D4 must treat a
    revoked/expired grant as a fail-closed "cannot post — tell the owner,"
    never a silent drop.
  - Google's TOS require the acting party to genuinely manage profiles they or
    their clients own — which Hisaku does (it operates the agent for them).
    Aligns with the per-customer-VPS "we run it for you" posture.

### (b) Customer-owned OAuth — per-owner consent (FALLBACK)

- Each owner runs the **OAuth consent flow themselves**, granting Hisaku's app
  a `business.manage` token scoped to their own profile.
- **Friction map (why this is heavier over WhatsApp):**
  1. Deliver a consent LINK over WhatsApp → owner opens a **browser** →
     **Google login** (the owner must be signed into the Google account that
     owns the profile — many SMB owners don't know which account that is) →
     **restricted-scope consent screen** with scary "this app wants to manage
     your business" warnings → redirect back → **capture the auth code** on a
     redirect endpoint we must host and secure.
  2. **Per-owner token lifecycle:** store + refresh + detect revocation for
     every customer; a refresh failure is a per-owner outage.
  3. The consent screen only works AFTER Google app verification of the
     sensitive scope (§2) — same gate as (a), but now every owner sees it.
- **Upside:** lower blast radius (each token scoped to one owner; the owner
  sees exactly what they granted and can revoke in their own Google security
  page), and Hisaku holds no standing write access it didn't individually
  receive.
- **When to prefer (b):** an owner who refuses manager access, or a posture
  where Hisaku deliberately wants zero standing write authority.

### Recommendation

**Default to (a) manager-access; keep (b) as the per-customer opt-out.** For a
fleet of single-tenant VPS SMBs that Hisaku already operates, manager-access
matches the operating model, avoids the per-owner browser OAuth journey that is
genuinely painful to complete over WhatsApp, and lets one approved GCP project +
one consent cover the fleet. The manager grant is a single, familiar owner
action ("add my marketing person as a manager"). Flow (b) is the fallback for
owners who decline manager access. Either way, the acting credential is a real
Google identity, never a service account, and every post is YES-approved.

---

## 4. BSP-timeline interaction

Two independent external-approval clocks run in parallel and must not be
conflated:

- **Meta / WhatsApp BSP paperwork** (~2–4 week Meta clock per project memory) —
  gates the messaging channel Hisaku already uses.
- **Google GBP Basic API Access request** (§2) — variable timeline, gates ONLY
  the D4 posting leg.

Neither clock gates Phase D's non-API legs. **PR-D2 (offer emission) and PR-D3
(YES → draft-as-text) ship with ZERO external accounts** — they are pure
WhatsApp + local composition. Only **PR-D4** needs the GBP approval.

**Sequencing recommendation:** file the GBP Basic API Access request EARLY,
in parallel with BSP paperwork, because its timeline is the least predictable
and it is D4's long pole — but do NOT block D2/D3 on it. When SriniY next does
BSP paperwork, filing the GBP access request in the same sitting costs little
and starts the slowest clock. The Google OAuth app verification of the
sensitive `business.manage` scope is a third clock behind the access request;
start it as soon as the GCP project exists.

---

## 5. Instagram — do NOT build an API phase

Instagram content publishing is materially heavier than GBP and stays
paste-ready text indefinitely:

- Requires a **Facebook Business account + a linked Facebook Page + an
  Instagram Professional (Business/Creator) account + a Meta developer app**.
  Personal IG accounts cannot publish via API at all.
- **App review is mandatory for production**, with a **screencast per
  permission** — `instagram_business_content_publish` (publish) +
  `instagram_business_basic` (profile). **2–4 weeks per submission.** A profile
  connected to a Page requiring **Page Publishing Authorization (PPA)** cannot
  publish until PPA is complete.
- Two-step publish (`POST /{ig-user-id}/media` container →
  `/{ig-user-id}/media_publish`), 50 posts / 24h.

**Decision (unchanged from #564 spec):** the Instagram caption ships as
paste-ready TEXT the owner pastes; no IG API phase is scheduled. The account +
FB-Page + per-permission app-review burden violates the no-new-accounts posture
and buys little over a paste — the owner already posts to IG by hand.

---

## 6. Money / silent-failure discipline for PR-D4 (carry into the build)

Posting to a live public profile is an automated action that changes
operator-relevant external state, so the §12b / §12a disciplines apply:

- **No silent auto-post.** Every post is YES-per-post approved (the existing
  approval gesture); wrapper copy never claims "posted" (completion-verb
  discipline, `lint_no_unverified_completion`) until the API returns success.
- **§12b — alert at the write site.** A `localPosts.create` failure (auth
  expired / quota / 4xx) fires an operator alert at the exact call site, plain
  text (`parse_mode=None`), with `*_alert_dispatched` + `*_alert_delivered`
  structured logs around it.
- **§12a — watchdog any queue table.** If D4 introduces a post-queue or
  post-outcome table, it ships with a freshness SLO + watchdog in the SAME PR.
- **Idempotency.** A per-(project, location) idempotency key so a retry never
  double-posts to a live profile.
- **Revocation = fail-closed.** Expired/revoked manager access or token →
  "cannot post, owner action needed," never a silent skip.

## 7. To-verify-at-D4 checklist (knowledge-cutoff live re-checks)

1. `localPosts.create` still on `mybusiness.googleapis.com/v4` and not sunset
   (check the deprecation-schedule page at request time).
2. Exact `summary` character cap from a live 4xx probe (guard ≤ documented
   1,500).
3. `media.sourceUrl` public-URL requirement + accepted formats/dimensions/size
   (drives the §1.3 public-host vs manual-attach fork).
4. Current default quota after approval (assume 300 QPM; confirm) + which APIs
   in the call path each need their own access grant.
5. Service-account impossibility still holds (assume yes).
6. Manager-access (`accounts.admins.create` invite) still grants API posting
   rights without per-owner OAuth (assume yes; this is the §3(a) linchpin).
7. Google OAuth app-verification requirements/timeline for the sensitive
   `business.manage` scope.
