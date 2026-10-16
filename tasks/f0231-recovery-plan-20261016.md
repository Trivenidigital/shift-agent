# F0231 recovery and six-item flyer polish
**Drift-check tag:** extends-Hermes
**New primitives introduced:** none.

## Hermes-first analysis
| Domain | Hermes skill found? | Decision |
|---|---|---|
| OCR | Existing visual_qa._vision_text and model transport | Require literal visible transcription and test repeated same-hash observations |
| Composition | Existing premium_poster_v1 compositor and background model | Adjust existing shared-price short-menu layout |
| Recovery | Existing generate-flyer-concepts, update-flyer-project, manual queue | Recover original project with history retained |
| Delivery | Existing actions.send_flyer_concept_previews + safe_io | Resolve verified recipient and preserve preview/approval binding |
In-tree capabilities fully cover the work; no new subsystem or community integration is proposed.

## Governance bootstrap
Universal docs/governance/engineering-directive.md v1.2.0 blob31d7706d42b5a9477ce236f4dcb3841223b74a6f.
Registry docs/governance/project-registry.yaml v1.0.0 blob207ad4183e4b0fd37b74eeec29e7dc215546c4cd.
Flyer directive v1.0.0 blobfe45ce8094eb666057e8393be1e989ec64f7c324; repo-meta v1.1.0 blobeecd75a7bf500579b85a67ba7f748bf6bb39fc20.
Shared directive read v1.1.0 blob9c6ce25607d59ac3dc3918ab4c908c77a65bde5f. Shared runtime source changes not planned; consume existing safe send/identity controls.
Architecture exception none. User explicitly authorizes repair, deployment and verified WhatsApp preview delivery; genuine final approval remains with human.

## Capability Reuse Map — flyer-studio
- Requested outcome: faithful OCR, professional six-item shared-price flyer, recovered F0231 preview delivered pending human approval.
- Affected projects: flyer-studio.
- Applicable directives: universal, registry, Flyer directive above.
- Existing platform/model capabilities reused: image generation, existing vision OCR.
- Existing deterministic kernels reused: locked facts, strict QA, PPv1 layout, manifests, revision/version, manual queue, send validation.
- Existing stores/workflows reused: original F0231/revisions/assets, existing generation/recovery/SLA workflow.
- Thin adapters: improved OCR contract and bounded layout sizing; use supported recovery controls.
- Custom runtime code genuinely unavoidable: corrections within current functions only.
- New subsystem: none.
- Evidence existing capabilities were insufficient: two distinct render artifacts rejected for OCR spelling normalization; PPv1 heading/badge/panel obscure hero and waste space.
- Architecture exception: none.
- Shared-platform impact: no new transport/router; existing preview-send helper consumed with explicit project-to-recipient verification.
- Other agents affected: none by Flyer source changes; review unreleased main PR805 owner-yield range before deployment.
- Vertical E2E proof: both historical artifact hashes verified; repeated real OCR; visual four-format inspection; same-project supported retry; real preview outbound ID and persisted reply binding; no fabricated final approval.

## Capability Reuse Map — repo-meta
- Requested outcome: current evidence/checklist.
- Affected projects: repo-meta.
- Applicable directives: universal and repo-meta.
- Existing platform/model capabilities reused: none needed for records.
- Existing deterministic kernels reused: architecture governance checker.
- Existing stores/workflows reused: tasks plan/todo/lessons.
- Thin adapters: none. Custom runtime code genuinely unavoidable: none.
- New subsystem: none. Evidence existing capabilities were insufficient: prior isolated tests did not prove recovery delivery.
- Architecture exception: none. Shared-platform impact: none. Other agents affected: none.
- Vertical E2E proof: documented evidence matches actual runtime artifacts and send results.

## Runtime assumptions and findings
- Live2543afdf; origin/main ae4f06ab includes PR805 owner-yield revision repair, not yet live. Review full release range.
- F0231 remains manual_edit_required v1, no preview IDs or approval. Primary95b53d4a… quarantine and deterministic0512d7d6… current assets are distinct; both QA sidecars match their artifacts and report the same spelling discrepancy. Preserve all originals before regenerating.
- Original supplied spelling Mysoore Pak is preserved for this explicitly scoped historical recovery. Automatic correction of new intake remains deployed; this repair must demonstrate faithful OCR of the original locked artwork.
- SLA timer active and serviceUser shift-agent; owner escalation delivered23:15; actual customer update bridge accepted23:35 outbound3EB0282B1EB3A12A4DBFE9. Thus follow-up is queued/actionable, not guaranteed automatic repair. No watchdog code change required by this incident.
- Recipient must be verified project/customer phone+17329837841 and originalLID201975216009469@lid. Never send to bot+918522041562 or unverified contacts.

## Checklist
- [x] Trace both historical artifacts and actual SLA path.
- [ ] Repair literal OCR contract; repeat same-hash OCR on typo and canonical images without expected-name priming.
- [ ] Polish actual six-item shared-price layout and inspect preview/four exports.
- [ ] Independent review, full tests/CI, canonical deploy and installed-code proof.
- [ ] Recover F0231 through supported controls and send verified WhatsApp preview; persist outbound ID and await human approval.


## Included main release range — PR805
Reviewed full live2543afdf..ae4f06ab: cf-router hooks and router tests only. Existing owner-authority, pending-confirmation, edit-signal, catering classifier and approval-code grammar are reused. Owner-only unmatched Catering questions and declined code decisions yield to existing Hermes routing. Flyer edits, customer paths and pending revisions remain guarded; no preview/QA/send bypass. Catering directive v1.0.0 blob858bd9f29d591e023b895ff5cbd2f3804f146ead and shared directive above apply. Per-agent impact: Catering owner queries reach existing assistant; Flyer retains explicit edit handling; Shift/Expense paths unchanged. No new runtime subsystem, config, schema or architecture exception. Independent source review completed; actual owner handset yield remains a distinct post-release acceptance check.

## Reviewed release and live residual — 2026-10-16 00:31 UTC
PR806 merged86c4e0d7 and deployed deploy-20261016-002607-86c4e0d7. Full gate f45bee27:10,571 passed47skipped in795.06s; all8CIchecks passed; canonical merged artifact497057586a0b2f6d573c5c9898591a34b1d6b710cf3b571366b5ee65ec989d71. Installed source matches exact Git blobs under both Python runtimes/serviceUID999; gateway active, bridge connected, queue0. Local CRLF hashes were rejected and replaced with authoritative Git-blob hashes before installed proof.
Actual supported F0231 retry remained manual_edit_required: artifact95c670f6b15dee3dbd72f628dbd84cef50deefd05359fc1a49e52bfc6851e31d prints Mysoore Pak, but primary OCR flagged its normalized bullet row as BOTH near-name and heuristic brand. Conservative policy refused reread; no preview was sent. Original and first-retry histories/artifacts retained in separate snapshots. Midnight log rotation moved original project audit to /var/log/shift-agent-archive/decisions.log-20261016; the existing resolver on that archive agrees with current customer primary LID. No recipient-control relaxation.
Follow-up within existing QA: allow neutral verification only when a heuristic brand flag refers to exactly the same observed near-item candidate; explicit identities, known source brands, organization suffixes, unrelated brand/price/phone/duplicate/garbling blockers remain closed. All strict secondary gates remain authoritative. No fact-matching tolerance or dish aliases.
Actual fresh photo also exposed PPv1 director's obsolete upper/lower-band prompt, unlike the already improved generic image prompt. Reuse existing continuous-photo guidance in that director, inspect a fresh real image and all actual exports, then re-run release gates. No new renderer or masking subsystem.
