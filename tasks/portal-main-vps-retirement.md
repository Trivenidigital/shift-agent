# Portal — main-vps retirement tracker

**Status:** OPEN — soak window in progress.

**Context:** Portal migrated to srilu via `tools/deploy-portal.sh` on
2026-05-10 (PR for the migration). Per the slowly-decommission discipline
in `memory/project_srilu_canonical_state.md`, the old main-vps portal at
`http://46.62.206.192:8080/portal/` keeps running for ≥7 days post-srilu
deploy before retirement.

## Soak window

- **Srilu deploy date:** 2026-05-10
- **Earliest retirement date:** 2026-05-17 (7 days post-deploy)
- **Action gate:** before retirement, verify srilu portal has been operational
  for the entire soak window (no journald `failed` events on
  `triveni-portal.service`).

## Retirement steps (when the soak window passes)

1. Verify srilu portal stable: `ssh root@srilu-vps 'systemctl status triveni-portal.service'` shows `active` since the deploy date with no failed restarts.
2. Capture the main-vps portal content as a final backup tarball:
   `ssh root@46.62.206.192 'tar -czf /tmp/portal-final-backup-$(date +%Y%m%d).tgz /var/www/triveni/portal/' && scp it locally.`
3. Replace main-vps `/var/www/triveni/portal/index.html` with a tiny redirect stub:
   ```html
   <!doctype html>
   <meta http-equiv="refresh" content="0; url=http://89.167.116.187:8080/">
   <link rel="canonical" href="http://89.167.116.187:8080/">
   <p>Portal moved to <a href="http://89.167.116.187:8080/">http://89.167.116.187:8080/</a></p>
   ```
   This catches operators who bookmarked the old URL.
4. Hold the redirect stub on main-vps for an additional 30 days, then `systemctl stop nginx` (or remove the `triveni` vhost) entirely.
5. Open a follow-up PR `chore(portal): retire main-vps host` documenting the retirement + closing this tracker.

## Why a redirect stub instead of immediate shutdown

Operators who bookmarked the old URL would otherwise see a 404 / connection refused with no breadcrumb. A `meta http-equiv="refresh"` HTML stub bridges them to the new URL with one extra redirect.

## When this tracker can be deleted

After the follow-up PR retires main-vps + this PR is referenced in the retirement commit message. Close + delete the tracker file at that time.
