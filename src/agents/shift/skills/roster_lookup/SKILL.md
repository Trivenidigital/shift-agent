---
name: roster_lookup
description: Always invoke this skill whenever you need to know who is scheduled to work on a specific date at the business, what role they cover, their contact info, or their language capabilities. Also invoke when finding coverage candidates for an absent employee — filter by can_cover_roles AND not-already-scheduled-that-day. Reads /opt/shift-agent/roster.json directly; never invent employee names or data not in the file.
---

# Roster Lookup

The roster lives at `/opt/shift-agent/roster.json`. Read it directly. Never invent data.

## When to invoke

- To identify who's scheduled on a given date + what role
- To find coverage candidates for an absent employee
- To look up an employee's contact info (phone, name, languages)
- To check can_cover_roles for a proposed replacement

## Schema reminder

```json
{
  "location": {"id": "loc_xxx_01", "name": "...", "timezone": "America/New_York"},
  "employees": [
    {
      "id": "e001", "name": "Ravi Kumar", "nickname": "Ravi",
      "role": "cashier", "phone": "+19045550101",
      "languages": ["en", "te", "hi"],
      "can_cover_roles": ["cashier", "floor"],
      "status": "active",  // or "inactive" or "terminated" — skip non-active
      "phone_history": [],
      "restrictions": null
    }
  ],
  "schedule": {
    "2026-04-29": [
      {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"}
    ]
  }
}
```

## Coverage-finding logic

When looking for coverage on `<date>` for `<role>` for employee `<absent_id>`:

1. Filter `employees` where:
   - `status == "active"` (skip inactive/terminated)
   - `id != absent_id` (can't cover yourself)
   - `role` appears in `can_cover_roles`
   - NOT already scheduled on `<date>` (check `schedule[<date>]` — no entry with their `employee_id`)
2. Rank candidates by:
   - Language match with absent employee (prefer shared languages for customer continuity)
   - Role familiarity (primary `role == <role>` > secondary via `can_cover_roles`)
3. Return top 3.

If fewer than expected exist, return exactly what exists. **Never** pad the list.

## Restrictions

If an employee has `restrictions.no_work_days` containing the weekday of `<date>`, exclude them even if they can_cover the role.

## Data integrity rules

- If `roster.json` cannot be loaded or has schema violations, do NOT fabricate a response. Return an error to the caller: "roster load failed — please handle manually."
- Never modify the file from within this skill. Roster edits are the owner's responsibility via the runbook (edit → `shift-agent-fsck` validates on next nightly run).
- If an `employee_id` referenced in `schedule` is not present in `employees`, flag it as schema violation.

## Output format

Return structured JSON or clear markdown lists. Don't prose around the data unless the caller is asking for a summary.
