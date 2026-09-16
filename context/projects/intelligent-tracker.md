# Intelligent Tracker

## Goal
Internal project: a Canvas app on SharePoint (with Power Automate) that tracks projects, issues,
change requests and time logs in one place, so that every piece of work resolves to a work item
linked to a project ID and the scheduling engine can forecast delays.

## Phase
UI review and sign-off. Modules: Project Plan, Issues, Change Request, Time Logs. Build order:
Project Plan, then Issues, then CR, then Time Logs. Reference document:
Intelligent_Tracker_Solution_Design_Modules.docx (26 screens).

## Next milestone
Sign-off gate (as of 2026-09-16): Sreekumar signs off the UI for Issues, CR and Plan Tree in the
group chat once the review points are added; Ayush signs off the updated solution design from
Laxmikant. Development in Canvas starts only after both. No hard delivery date agreed yet; when
one is set, add it here.

## Risks
- Review points from Sreekumar keep arriving during UI walkthroughs; each one delays the gate.
- The solution design document and the SharePoint list configuration must stay in step with the DB changes.

## Key people
- Laxmikant: team lead; Project Plan module, Time Tracker, scheduling engine, SharePoint configuration, solution design document.
- Samuel: Issues module.
- Johan: Change Request module.
- Ashwini: PMO; Zoho tracking, review comments, sign-off collection.
- Sreekumar: internal customer; functional sign-off of the UI.
- Ayush: solution design sign-off.

## Glossary
- CR: change request. CR items (1.1, 1.2, ...) are sub-items of a CR.
- Plan tree: the project plan hierarchy (project, stage, milestone, task, sub-task).
- Work item: any task, issue task or CR item that the scheduling engine reads.
- Zoho: where the team logs their daily tasks.

## Recent decisions
_Rewritten by scripts/consolidate.py from memory/decisions.jsonl._
