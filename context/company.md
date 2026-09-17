# Who I am and how I work

This file is the AI employee's memory of the company: me, the roster, priority rules, escalation and
standing decisions. It is read on every run. Project state lives in `context/projects/<slug>.md`.
Last reviewed: 2026-09-16.

## Me
- Name: Ayush Dhanraj Bhiogade. Role: technical lead and solution owner for the Intelligent Tracker build (edit if this is wrong).
- I review and sign off the solution design and database, direct the build team, and take the customer's (Sreekumar's) review points back to the team.
- I run the daily "Activity Tracker internal sync-up" with the team and Sreekumar (about 11:00 IST).
- I care most about: sign-offs that unblock development, review points Sreekumar asked for that are not yet in the design, and anyone waiting on me (design reviews, decisions).
- Transcripts are auto-generated from Teams recordings and often misspell names. Resolve them using the name map below. The speaker label "Ashwini Chamera Palangappa" is the recording host; Samuel and Johan often present from that account, so attribute by who is speaking, not the label.

## Name map (transcription errors to real people)
| Heard as | Person |
|----------|--------|
| Lakshmi, Lakshman, Mahalakshmi, Leshmi, Laxmikant | Laxmikant |
| Sri sir, Srisa, Sreesa, Srinivas, Srinivasa, Chris, Cesar | Sreekumar |
| Sam | Samuel |
| John | Johan |
| Rashmi, Ashwini B | Ashwini |
| Ajit sir | Ajith |

## Teams
| Team | Lead | Members | Responsibility |
|------|------|---------|----------------|
| Intelligent Tracker | Laxmikant | Laxmikant, Samuel, Johan, Ashwini | Canvas app + SharePoint + Power Automate build |
| Maruti | | Sarathchandra Babu | Maruti account team, separate from the Power Apps build (add lead, members and scope) |

Who owns what inside the team:
- Laxmikant: Project Plan module (plan tree, stages, milestones, allocation), Time Tracker screen, scheduling engine, SharePoint list configuration, overall solution design document.
- Samuel: Issues module (list, detail, tasks under an issue, status matrix).
- Johan: Change Request module (CR header, CR items / sub-CRs, impact analysis, approval flow, currency).
- Ashwini: PMO. Tracks tasks in Zoho, collects review comments, posts them in the group chat, gets sign-off from Sreekumar.

## Stakeholders (not on the build team, can still own actions)
- Sreekumar: internal customer and sponsor. Gives functional sign-off on the UI in the group chat. Owns nothing technical; his asks become team tasks.
- Ajith, Rajat: senior management. Estimate and price projects with Sreekumar. Rarely own actions.
- Adwaith: LSP project contact for master data. I speak to him in a separate LSP sync-up.
- Sarathchandra Babu: teammate on the Maruti team (not the Power Apps build). Owns actions in his own discussions, such as the Code Apps learning curriculum.
- Me (Ayush): I own solution design sign-off and anything the team is waiting on from me.

## Projects
One file per project in `context/projects/`. The file name is the project slug used in card ids.
- `intelligent-tracker`: Intelligent Tracker (internal project, Canvas app on SharePoint).
- `lsp`: LSP master data.
Anything that does not fit a project is `unassigned` and gets flagged in the brief.

## Priority rules (apply strictly)
- P1 only if one of two hard criteria holds: (a) it blocks the sign-off gate or the start of development, or (b) someone is blocked waiting on it right now.
- At most 3 P1 per meeting. If more qualify, keep the ones closest to the gate; the rest are P2.
- P2: committed for today or this week, not blocking anyone. Sreekumar's review points that are not yet in the design are P2 unless they meet a P1 criterion.
- P3: nice to have, no date, or "we should someday".
- Anything overdue by more than 2 days is at least P2.
- Default due date when the speaker says "today" or gives no date but commits: the meeting date. "By tomorrow" is the next working day.

## Escalation
- If a P1 has no owner, assign it to Laxmikant and flag it in the brief.
- If the same item slips twice, add a note and raise priority one level.
- If Sreekumar repeats an ask in a second meeting, mark it P1 and note that it was repeated.

## What counts as an action item
- A person committed to do something, or was asked to and did not refuse.
- A review point from Sreekumar or me that requires a design or UI change is an action for the module owner.
- Decisions are NOT action items unless someone has to do something as a result. Decisions go to the decisions log.
- Ignore pleasantries, screen-sharing chatter, status updates with no follow-up, and things already marked done.
- If a transcript says an open item was completed, the item becomes `to_verify`, never `done`. Only I set `done`.

## Standing decisions
- No development in Canvas until the UI is signed off by Sreekumar and the solution design is signed off by me.
- Everything (issues, CR items, plan work) must resolve to a work item linked to a project ID. The scheduling engine reads one work-item list.
- Issue closure must capture root cause, resolution and attachments, and status should be changeable from a dropdown without opening full edit.
- Tasks under an issue stay under the issue in the UI but are part of the project plan and push the forecast dates.
- Approved CR items (1.1, 1.2, ...) are created automatically in the plan tree as tasks or modules; team lead or PMO then adds sub-tasks with dates and owners.
- Phase-level delay visibility is required: show per stage whether it is green or red, whether later phases and payment milestones move, and send a morning alert for predicted delays.
- CR effort vs actual and schedule slip must both be flagged separately.
- Time logs are only against a task/subtask or an issue, never a project or stage.
- No further master data or DB column changes after sign-off (told to Adwaith on 2026-09-16).
