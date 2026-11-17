# Intelligent Tracker

## Goal
Internal project: a Canvas app on SharePoint (with Power Automate) that tracks projects, issues,
change requests and time logs in one place, so that every piece of work resolves to a work item
linked to a project ID and the scheduling engine can forecast delays.

## Phase
UI review/sign-off continues alongside build work: Samuel is building the Issues create
functionality, Johan is building the CR create functionality, and Laxmikant is building plan-tree
checklist items and add-dependency screens. Build order remains Project Plan, then Issues, then
CR, then Time Logs. Reference document: Intelligent_Tracker_Solution_Design_Modules.docx
(26 screens). Note: standing decision is that no development happens until Sreekumar signs off the
UI and Ayush signs off the design; current build activity on Issues/CR should be checked against
that gate.

## Next milestone
Two demo checkpoints are now committed (Ashwini confirmed and shared with Sreekumar,
2026-09-25): Issues + CR demo on 2026-09-30, Plan Tree demo on 2026-10-05. Full sign-off gate
still requires Sreekumar's UI sign-off (Issues, CR, Plan Tree) in the group chat and Ayush's
sign-off of the updated solution design from Laxmikant before Canvas development is considered
formally unblocked.

## Risks
- The first-demo-date ask was repeated by Sreekumar after slipping 2+ days; now resolved with dates set, but shows the pattern of repeated asks escalating to P1 (`2026-09-21-intelligent-tracker-03`).
- Several P2 items (stage validation, 'my tasks' filter, UAT plan share) are now multiple days overdue and should be watched for further escalation per the slip rule.
- Johan's CR time-block work is P1 and blocked on the Time Logs data structure being finalized.
- Sreekumar questioned the value of having a PMO role if the team doesn't follow a plan (open thread, possible process review).
- Review points from Sreekumar keep arriving during UI walkthroughs; each one can delay the gate.
- The solution design document and SharePoint list configuration must stay in step with DB changes.

## Key people
- Laxmikant: team lead; Project Plan module (now building checklist/dependency screens), Time Tracker, scheduling engine, SharePoint configuration, solution design document.
- Samuel: Issues module; building create functionality; owns the new rework-checkbox logic.
- Johan: Change Request module; building create functionality; CR time-block work blocked on Time Logs.
- Ashwini: PMO; Zoho tracking, review comments, sign-off collection; now also publishing a weekly Monday plan and demo-date commitments.
- Sreekumar: internal customer; functional sign-off of the UI; raised PMO accountability question.
- Ayush: solution design sign-off.

## Glossary
- CR: change request. CR items (1.1, 1.2, ...) are sub-items of a CR.
- Plan tree: the project plan hierarchy (project, stage, milestone, task, sub-task).
- Work item: any task, issue task or CR item that the scheduling engine reads.
- Zoho: where the team logs their daily tasks.
- Rework checkbox: on a task created under an issue, defaults to checked (assumed rework); TL or PM can uncheck it when the issue stems from another module or external dependency.

## Recent decisions
- 2026-09-22 `D-2026-09-22-01`: The rework checkbox on a task defaults to checked (assumed rework); the TL or project manager can uncheck it for a specific task when the issue stems from another module or external dependency. (by Sreekumar)
- 2026-09-22 `D-2026-09-22-02`: An issue can be raised without a task, but a task must be created before that issue can actually be fixed or closed. (by Sreekumar)
- 2026-09-22 `D-2026-09-22-03`: Going forward, PMO will publish a weekly plan every Monday showing planned activities for the week, instead of reporting only after completion. (by Sreekumar)
