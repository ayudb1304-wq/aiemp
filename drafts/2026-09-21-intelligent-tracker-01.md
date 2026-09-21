# Review: Date validation for inserting stages in Plan Tree

_Draft for `2026-09-21-intelligent-tracker-01` (Laxmikant: Verify and add date-based validation for inserting a new stage above or between existing stages in the plan tree.). Written 2026-09-21 from 2026-09-21-activity-tracker-sync.docx. Not sent anywhere; copy what you need._

Hi Laxmikant,

From today's sync-up: Sreekumar raised a case in the Plan Tree where a new stage is inserted above or between existing stages (e.g., adding a stage after "Requirements" but positioned above it). We need to check that this doesn't break date sequencing.

Action: Please add validation so that when a new stage is inserted, its start/close dates are checked against the neighboring stages' start and close dates \u2014 it can't be based solely on "this date" or the "close date" in isolation. Make sure inserting a stage above or between existing stages doesn't create conflicting or overlapping stage dates.

Let me know once this is verified/added so we can close this out for sign-off.

Thanks,
Ayush
