# Changelog

All notable changes to the NIF Office Management System are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/). Commit convention: [Conventional Commits](https://www.conventionalcommits.org/)
(see `docs/CONTRIBUTING.md`).

## [Unreleased]

### Changed
- **Homepage, human first.** The home page now reads in one order at every
  width: a greeting sentence ("You have 7 items that need attention today."
  with up to three urgency lines), four summary cards (Needs attention,
  Pending reviews, Due today, Completed), **My focus today** with
  verb-labelled actions sorted most urgent first, **Start something**
  shortcuts (Create task, memo, minute, Apply for leave, plus pills), a
  **My tasks** card with a completion ring and Open / Pending acceptance /
  Waiting review / Overdue, **Latest updates** grouped by day with like rows
  collapsed, and the attendance and notice strips last. UI only: no API,
  permission, workflow or calculation changed. Also fixes the UTC date line,
  the "B.S. null" case, the `hm-tile-label` / `.hm-tile-l` mismatch and the
  activity feed's cache key.
- **Homepage polish.** Same layout, refined finish: 8 px rhythm, one card
  recipe (soft tinted border, two-layer shadow, shared radius), compact KPI
  cards with a hover lift, soft chip colours by verb (overdue red, review
  orange, accept blue, start green) with a left accent on overdue rows,
  standardised 36 px buttons, lighter sidebar active and hover states, a
  64 px header with a restyled search field and bell badge, day-grouped feed
  typography, a 10 px ring track, shimmer skeletons for every home card, and
  focus rings throughout. Transitions and shimmer are off under
  `prefers-reduced-motion`.
- **Homepage enterprise refinement.** One five-hue status system in tokens
  (overdue red, review amber, pending purple, accepted blue, completed green)
  now drives the home chips, KPI tiles, task legend and the product-wide
  status badge map; borders fade to a 6 % tint with the shadow carrying the
  edge; every home card shares 20 px padding, a 16 px radius and 18 px icons;
  body text on the home page is 14.5 px at 1.5 line height, figures 30 px;
  columns 1.55 : 1 with a 28 px gap; 200 ms hover and lift; queue rows show
  reference, situation and requester.

### Added
- **Task autosave.** Create Task and Edit Task now keep a server-side draft
  through the Phase 111 draft store (`DocumentDraft.Kind.TASK`): 2.5 s after
  typing stops, every 30 s while typing, and on tab hide, with the IndexedDB
  copy as before. The form header shows **Saving… / Draft saved · 2 minutes
  ago**; returning to the form offers recovery; the Unfinished Work page lists
  task drafts. A draft keyed by an existing task is refused unless the caller
  may edit that task, so drafts follow task permissions.
- **Edit conflict detection.** The edit form sends `expected_updated_at`; a
  task saved by somebody else in between answers **409** with the current task,
  and the form shows *Task updated by another user* with Reload / Review
  changes.
- **Rich task description.** `Task.description_format` (`text` | `html`).
  New tasks are written by the rich editor with a starter template (Task Goal,
  Expected Deliverables, Success Criteria, Risks, Dependencies, Notes) and are
  sanitised on write by the shared allowlist, now in `common/html_sanitizer.py`
  (memos and circulars import it from there). Existing tasks stay plain text.
- **Subtasks.** `TaskSubtask`: title, notes, single assignee (must be on the
  task), due date, done state, ordering. Owners add, edit, assign, reorder and
  delete; assignees and owners complete. Comments and evidence can be threaded
  under a subtask (nullable `subtask` on `TaskComment` / `TaskAttachment`).
  Endpoints under `/tasks/{id}/subtasks/`.
- **Progress from subtasks.** With subtasks, `progress_percent` is derived
  (done / total) and the manual control is refused with 409; without them the
  checklist and manual rules are unchanged. A task cannot be submitted for
  review or closed while a subtask is open.
- **Notifications:** `TASK_SUBTASK_ASSIGNED`, `TASK_SUBTASK_COMPLETED` (creator;
  on shared work everybody when the last one closes), `TASK_SUBTASK_OVERDUE`
  (daily, in the reminder run) and `TASK_DRAFT_RECOVERABLE` (an hour-idle task
  draft, sent by the daily draft purge job).

### Added
- **"Mark all as read" in the notification bell.** The dropdown could only
  clear notifications one at a time, and only by opening each one — which
  navigates away, so clearing a backlog meant leaving the page repeatedly. The
  action sits in the panel header rather than among the items, because it acts
  on the whole list rather than on any one of them, and it appears only when
  something is unread: a control that is always visible but usually inert
  teaches people to stop seeing it.
- It invalidates the notifications page's list as well as the badge and the
  dropdown. Clearing the badge while the full page still showed unread rows
  would have left the two disagreeing on the same screen.

`markAllRead` already existed in the service and `POST
/notifications/mark-all-read/` already existed on the server; neither was
wired to anything.

### Added
- **A dedicated Nepali calendar at `/calendar`**, separate from the leave
  calendar. It shows Bikram Sambat months, festivals and public holidays — and
  fetches no leave data at all. Not hidden, not filtered: never requested.
- A "This month" list beneath the grid. Below 480px the cells show dots rather
  than names, so without it a month's festivals would be readable only one tap
  at a time.

### Changed
- The **Nepali Calendar** menu entry now points at `/calendar`.
  `/leaves/my-calendar` goes back to being the work calendar — the one that
  answers "when am I off, and what did I book" — and returns to the UNLISTED
  registry, reachable from Leave records and search.

### Why they are two pages
They answer different questions. Somebody opening a patro to check when Dashain
falls should not be shown their own annual leave beside it, and somebody
planning time off should not have to read past festivals to find their booking.
Pointing the menu at one page and labelling it as the other made the calendar
look like a leave screen with Nepali dates.

`nepaliCalendar.test.jsx` asserts the separation at the SOURCE — no leave hook,
no leave service, no `record` passed into a day cell. A rendering assertion
would pass merely because nothing happened to be on screen, which is the same
state a later "show my leave here too" toggle would produce.

### Removed
- The `/leaves/my-calendar → workspace` prefix override. It existed to stop the
  People & Attendance rail taking over when the menu pointed at a leave URL;
  giving the page its own top-level address removed the need for the patch
  rather than keeping it.

### Added
- **A Nepali calendar event layer, Hamro Patro style.** `CalendarEvent` holds
  festivals, jayantis, national days and observances, with a Devanagari name, an
  optional tithi and a public-holiday flag. `GET /leaves/calendar-events/` serves
  them for a date RANGE, and the calendar cells render them under the date — red
  and washed when the day is genuinely a day off, plain when it is only marked.
- `manage.py seed_calendar_events --year <year>` loads them from a fixture. It
  validates every entry BEFORE writing any of them, because a half-loaded
  calendar is worse than an unloaded one: it looks complete.

### Why this is a new table and not `Holiday`
`Holiday.date` is UNIQUE and every row in it is excluded from working-day
calculations. A Nepali calendar is neither one-per-day nor all-days-off — a busy
day carries a festival, a jayanti and an observance, and most of what it shows is
not a public holiday. Putting festivals into `Holiday` would have turned every
marked day into a non-working day and silently inflated everyone's leave balance.
The two tables are joined only when the calendar is drawn.

### Data
Seeded from the project's own `holidays_np_2026.json` — 8 entries, with
Devanagari names added. **No festival date here is computed or invented.** Most
Nepali festivals are lunar: their Gregorian date moves each year and is fixed by
panchanga. A festival on the wrong day in an HR system means somebody works a
public holiday, so `seed_calendar_events` only loads dates supplied by whoever
owns the organisation's calendar, and `tithi` is stored as given rather than
derived.

### Added
- **Both leave calendars are Bikram Sambat calendars.** My Leave Calendar and
  Team Calendar now run on Nepali months (Baishakh–Chaitra), Sunday-first, with
  the Devanagari day leading each cell and the Gregorian day beneath it in small
  type. The Gregorian date is kept deliberately: every other surface in this
  product — email, timestamps, exports — speaks AD, and a calendar you cannot
  cross-reference against them is one people stop trusting. The month heading
  carries the AD span it covers ("Bhadra 2083 · Aug–Sep 2026") for the same
  reason.
- BS primitives in `services/bsDate.js`: `bsMonthGrid`, `bsDaysInMonth`,
  `bsToAd`, `toBS`, `shiftBsMonth`, `bsMonthLabel`, `toNepaliNumeral`,
  `BS_WEEKDAYS`.

### Fixed
- **The grids started on Monday.** A Nepali calendar is Sunday-first, and
  Saturday — not Sunday — is Nepal's weekly holiday, so the old alignment put
  the rest day in the wrong column.
- **A BS month length is measured, never calculated.** Months run 29–32 days
  with no arithmetic rule; `bsDaysInMonth` walks down from 32 and returns the
  first day that both constructs *and* still reports the month asked for, so a
  converter that silently rolls day 32 into the next month cannot report 32.
- **Holidays no longer vanish at a year boundary.** The holiday endpoint is
  keyed by Gregorian year, and a BS month can straddle two of them — Poush runs
  across December into January. Asking for one year silently dropped every
  holiday on the far side; both years are fetched now.
- Leading and trailing cells are rendered as the neighbouring month's days,
  dimmed, rather than left blank: an empty cell and a real date look identical
  at a glance, and people do read the edges of a calendar.

### Fixed
- **Three of four workflow roles were offered a button labelled with their own
  role (Phase MEMO-WORKFLOW-UX-HARDENING).** The action button rendered
  `roleTypeLabel` for everyone except an approver, so a reviewer saw a button
  reading **"Reviewer"** beside one reading "Reject" — a noun and a verb, with
  nothing on screen saying the noun was how you pass the memo on. A memo parked
  at such a step is indistinguishable from a workflow that has stopped, which
  is what it was reported as. Buttons now read **Mark as Reviewed**,
  **Recommend**, **Support**, **Approve**.
- The confirmation dialog had the same problem — "Reviewer — confirm" told the
  reader who they are, which they knew, instead of what pressing the button
  does. It now leads with the verb and says where the memo goes next, with the
  final step saying plainly that approving issues and archives it.

### Added
- **A step guide above the action.** Which step of how many, what is being
  asked, and what to do before pressing — the panel previously opened straight
  into a button whose label was the reader's own role.
- `ROLE_ACTIONS` in `memoLabels.js` carries the title, instruction, verb and
  past tense for each role, with a verb (never a noun) as the fallback for a
  role the client does not yet know. `workflowVocabulary.test.js` fails if a
  button goes back to naming the reader's role.

Verified through the real UI across the whole chain — reviewer → recommender →
supporter → approver → archive — with the attachment visible, previewable
(200, 40 043 bytes) and downloadable at every stage, and the file intact on
disk afterwards.

### Fixed
- **Protected media carried no cache directive (Phase
  OMS-SIDEBAR-AVATAR-SYNC).** Browsers fell back to heuristic caching, so an
  avatar could be re-fetched on any remount and each of those spent the media
  throttle for nothing. The response now sends
  `Cache-Control: private, max-age=<remaining signature life>` — `private`
  because the URL is bound to one user and a shared proxy must never hold it,
  and the remaining life rather than a fixed number because caching past the
  signature would leave a readable copy after the link that authorised it had
  expired.

### Fixed
- **The login response handed out a URL guaranteed to 404 (Phase
  OMS-AVATAR-FINAL-FIX).** `users/token_serializers.py` returned
  `user.profile_photo.url` — the raw `/media/` path. The project deliberately
  does not serve that: the public catch-all was removed and files come only
  through `documents.protected_media`. Every other place this field is
  serialised already signed it, and `UserSerializer` even carries the comment
  "never a raw /media/ path". So on every login the client was given a broken
  image URL, and only recovered if something later re-read `/auth/user/`.
- **`Avatar` turned that transient failure into a permanent one.** Its `broken`
  flag was set once and never cleared, so an instance that had failed showed
  initials for the rest of its life — even after a valid URL arrived. That is
  why the header and the sidebar disagreed: same person, two identities,
  decided by which component mounted before the good URL landed. The flag now
  resets when `photo` changes, which also matters because the signed URLs
  expire after 300s, so a long-open tab recovers on the next refresh instead of
  degrading for the session.

**The previous phase's fix was real but incomplete.** Routing every surface
through `UserAvatar` was necessary — without it the sidebar could never have
shown a photo at all — but it could not help while the URL being shared was one
the server refuses. Verifying by navigating after login hid this: navigation
remounts the avatars with the good URL, which is exactly the state a real
session does not start in.

### Fixed
- **The same person had two identities on screen (Phase
  OMS-USER-AVATAR-CONSISTENCY).** The header rendered the signed-in user
  through `Avatar` — photo, with initials as fallback — while the sidebar
  footer drew its own `<span>{user.initials}</span>`. A user with a profile
  photo saw their face in the header and "DM" in the rail at the same time.
  Two further surfaces had the same defect: the `/me` hub, which is where the
  header avatar LINKS TO, so clicking your photo took you to a page showing
  your initials; and the mobile Profile tab, which showed a stock silhouette.
- The cause was three lines of duplicated wiring, not styling: every call site
  pulled `profile_photo`, `initials` and `color` out of `useAuth` by hand, and
  one of them did not. **`UserAvatar`** now owns that wiring — `Avatar` renders
  anyone (a leave applicant, a workflow actor), `UserAvatar` renders whoever is
  logged in — and a test asserts `user.profile_photo` is read in exactly one
  place and that nothing renders the current user as bare initials again.
- `.sb-foot-av` became a wrapper rather than the tile itself: the avatar may
  render as an `<img>`, and an image cannot contain the presence dot.

**Two of the four broken surfaces were found by the guard, not by the audit.**
The grep that opened this phase missed `ProfileWorkspace`, and the rule written
to prevent recurrence caught it immediately — which is the argument for writing
the rule rather than fixing the instances.

### Fixed
- **Search had two triggers on mobile (Phase MOBILE-NAVIGATION-CLEANUP).** The
  header magnifier and the Search tab in the bottom bar opened the same
  palette — two controls for one action, on the bar with the least room for
  either. The cause was two breakpoints that had drifted apart: the tab bar
  mounts from `useIsMobile` (max-width: 1023px), while the header button had a
  CSS rule of its own at max-width: 900px that only made it icon-only, so
  across 901–1023px both were fully visible. The header button is now gated on
  the same hook the tab bar mounts from, so the two cannot disagree again.
- **Ctrl/Cmd-K survived the removal.** The shortcut lived inside GlobalSearch,
  which the header no longer renders below 1024px, so deleting the duplicate
  button would quietly have taken the keyboard route with it. Most phones have
  no keyboard; a tablet in a keyboard case is squarely inside this breakpoint.
  Layout binds it directly on mobile instead.

The mobile header is now Menu, Logo, Notifications, Profile.
`singleSearchTrigger.test.jsx` asserts exactly one trigger on each side of the
breakpoint and fails if the duplicate returns.

### Changed
- **Sidebar redesigned to executive grade (Phase
  OMS-NAVIGATION-EXECUTIVE-UI-UPGRADE).** Rows are 48px, set as `min-height`
  rather than vertical padding so a wrapping label grows the row instead of
  breaking the column's rhythm; 14px icon gap on a 20px inset, which is what
  makes a column of glyphs read as a straight line rather than a ragged edge.
- **Two `.sb-item` rules had accumulated** — the original and the hover/active
  pass after it — so the row's geometry came from one and its transitions from
  the other, which is how `transition: all 0.2s ease` survived next to a
  considered transition list. They are one rule now, and a test fails if a
  second appears.
- **The active state is glass**: a gradient wash, an inset hairline along the
  top edge for the lit rim that makes it read as raised, an outer shadow for
  depth, and `backdrop-filter` (prefixed as well, or the glass drops on Safari
  — where it is most visible). The accent bar moved from `border-left` on the
  row to its own pseudo-element: as a border it made the active row 3px
  narrower than its neighbours whether or not it was active, and ran the full
  row height instead of aligning to the pill.
- **Section headers** get a fading rule above them instead of floating as a
  bare uppercase word in a gap; the first group in a rail suppresses it, having
  nothing above to divide from. The size stays `var(--fs-label)` — the type
  scale's smallest step — with the "smaller" coming from tracking and colour
  rather than an invented eleventh size.
- **The footer** separates with a rule that fades out before both edges rather
  than a full-width seam that read as the menu being cut off, and the identity
  block sits in it as a glass card with a hairline border.
- Home is `HousePlus` as specified. **It is the same glyph as WFH**, and a
  house-with-a-plus conventionally means "add a home"; `House` is the one-word
  revert if that reads wrong in use.

All transitions are 170–180ms on a single easing curve, and every one of them —
including the pill and icon transforms — is disabled under
`prefers-reduced-motion`. Touch viewports keep their existing taller rows
(52px), which clears the 48px floor rather than fighting it.

### Changed
- **Navigation icons upgraded to a premium set (Phase
  OMS-NAVIGATION-PREMIUM-ICON-UPGRADE).** The first pass reached for the most
  literal glyph in each case — a plain page for Documents, a bare outline for
  people, a box for assets. Literal is not the same as legible: at 18px and
  stroke 2 a column of thin outlines read as grey texture beside the labels
  rather than as distinct marks, which is what "placeholder-like" describes.
  Glyphs are now chosen for SILHOUETTE — recognisable at a glance and distinct
  from their neighbours in the same menu — so Documents is a folder with
  structure in it (`FolderKanban`) rather than a page outline, Work Queue is
  `BriefcaseBusiness`, Tasks is `ListTodo`, and the leave entries differ in
  their interior marks rather than their frame.
- **Weight raised to 22px at stroke 2.25**, from 18/2. The two move together:
  a heavier stroke on a small glyph closes its interior counters and turns it
  into a blob, so growing the box is what makes the extra weight legible.
  Resting opacity went 0.72 -> 0.82, judged against the rendered rail rather
  than picked from a scale.
- **Active state carries four agreeing cues** instead of a flat wash: a
  gradient pill, a solid accent bar, a brightened and glowing glyph, and a
  heavier label. Previously the only thing separating "where I am" from "where
  I could go" was a few percent of background luminance. The pill is a
  pseudo-element so the fade costs one composite rather than repainting the
  row, and everything is disabled under `prefers-reduced-motion`.
- **Profile card**: gradient avatar with a hairline ring in place of a flat
  blue square, and role and department as separate chips. They had been joined
  into one muted string ("Department Head · Administration") that truncated
  from the wrong end on a narrow rail; they are two different facts — what you
  may do, and where you sit — and the department chip is omitted entirely when
  unset rather than leaving a dangling separator.

### Changed
- **Navigation uses one icon system end to end (Phase
  OMS-NAVIGATION-ICON-CONSISTENCY).** Icons rendered only in the workspace
  rail — `{isWorkspace && <Icon …>}` — so a destination had an icon at
  workspace level and none one click deeper: 69 of 78 entries were text-only,
  and the rail appeared to lose its iconography the further in you went. Every
  entry across all ten module contexts now carries a Lucide glyph.
- **One registry replaces five private icon sources.** The sidebar, mobile tab
  bar, command palette, global search and Home quick actions each hand-wrote
  their own SVG paths, so the same destination was drawn several different ways
  — Home and Queue were different shapes in the rail and the tab bar, and Quick
  Actions drew at stroke 1.8 while everything else used 2. `navIcons.js` maps
  names to Lucide components and `NavIcon.jsx` fixes size and stroke in one
  place; `navConfig` stays plain data, naming icons as strings.
- The header menu button and sidebar close button now come from the same
  family as the navigation they control.

**A latent bug this surfaced:** the workspace rail still named icons from the
old vocabulary (`check`, `docs`, `box`, `cog`), none of which exist in the new
registry — every one of them was silently falling back to a default glyph. The
guard test caught it before it shipped; the names are corrected.

`navIcons.test.jsx` fails if any entry loses its icon, if a name does not
resolve, if a registry entry is not a real Lucide component, if a navigation
surface hand-rolls an SVG again, or if any surface overrides the shared stroke.

### Changed
- **Launcher tiles carry icons instead of letters (Phase
  OMS-ICON-STANDARDIZATION).** All 24 module tiles across the Documents,
  People & Attendance, Assets and Reports workspaces rendered one or two
  letters — M, Mi, C, D, A, L, W, Ap — which read as placeholder text. "Mi"
  said nothing the word "Minute" beside it did not, and modules sharing a first
  letter got arbitrary disambiguation ("As" for Assignment against "A" for
  Attendance). Each tile now takes a Lucide component, rendered at a fixed
  20px / stroke 2 in `ModuleLauncher` rather than at each call site, so
  twenty-four tiles cannot drift apart. `launcherIcons.test.jsx` fails if a
  letter returns, if a tile has no icon, if an icon name does not exist in
  lucide-react, or if a second icon library appears beside it.
- `.ml-ico` lost the `font-weight`/`font-size` that sized a letter; the accent
  background and `currentColor` are what the glyph needs.

**User avatars still show initials, deliberately.** A person's initials are not
a module abbreviation — replacing "BK" with a generic person icon would make
every avatar identical and lose the only thing distinguishing them at a glance.

### Fixed
- **Volunteers were paid an intern's leave entitlement (Phase
  LEAVE-POLICY-ENTERPRISE-IMPLEMENTATION).** Category D meant "Intern /
  Volunteer" and granted both 8 annual and 8 sick days. The organisation's
  policy grants an intern 4 and 5 and a volunteer 2 and 3, so every volunteer
  held four times the annual leave the policy allows and every intern twice it.
  Volunteers now resolve to a new **Category E**, and length of service never
  promotes either tier. Compensatory leave, which the policy gives to neither,
  was marked applicable for both and is now correctly Not Applicable.
- **An intern was told they had no maternity or paternity leave.** Both rows
  were `applicable=False`, which hides the entitlement; the policy says "as per
  organization policy" — real leave, arranged case by case. That is a third
  state, and neither existing field could express it: a 0-day allocation reads
  as "you get none". `EntitlementRule.by_arrangement` now carries it, the
  entitlement is shown with "Available by arrangement" instead of
  "Remaining: 0 / 0 days", and no balance row is generated because there is no
  yearly figure to hold.
- **The eligibility gate was hardcoded to a category.** `_code_applies_to_user`
  tested `!= LeaveCategory.D`, so every new category had to be remembered in
  two places — and Category E would silently have inherited maternity and
  paternity leave that volunteers do not get. It now reads the rule.

### Added
- **`manage.py apply_leave_policy`** re-applies the entitlement matrix and
  re-resolves every employee's category, reporting what changed. `--dry-run`
  shows the effect without writing; `--rebuild-balances` regenerates derived
  balances. The matrix literal in `category_engine` was previously a comment
  that nothing executed — the only code writing those rows was a migration with
  its own frozen copy — so the two could drift with nothing to notice. A test
  now asserts the database still matches it.
- **Leave Balance and Leave Policy pages**, and Leave Records in the leave menu.
  The first two existed only as widgets partway down the Overview page.

### Changed
- The leave module's navigation ceiling is 8 entries, raised from 7 for that
  module alone and recorded in `navConfig.test.js` where the rule requires such
  a decision to be made. A second test pins that no other module uses it.

**Existing leave records are not rewritten.** The migration corrects the
entitlement matrix and moves volunteers to Category E; leave already taken,
approved or recorded is history and is left exactly as it stands.
- **Attachment downloads were throttled at 20/min per IP address (Phase
  MEMO-P1-PRODUCTION-BLOCKERS).** `ProtectedMediaView` sets
  `authentication_classes = []` — a browser cannot put an Authorization header
  on a native `<img>`/`<a>`, which is why signed URLs exist — so DRF saw every
  attachment fetch as anonymous and applied the `anon` rate: a login-page
  ceiling, keyed by IP. Measured: 19 requests, then 429. One memo carrying ten
  quotations spent half the budget, and every user behind a shared office
  address drew on the same bucket, so downloads failed at random with nothing
  in the UI to explain it. The view now has its own `media` scope at 120/min.
  Verified in a real browser: 100 consecutive fetches, all 200.
- **Nothing bounded an upload except the per-file 10 MB cap.** No limit on the
  number of files in a request, the number a memo could accumulate, or the
  total bytes — so ten individually-legal files made a 94 MB request that was
  accepted and stored, taking the worker from 420 MB to 1.39 GB resident.
  `config.uploads.validate_attachment_batch` now enforces, for memos, 10 files
  per request, 20 per memo and 50 MB per upload, checked before any single file
  is written so an oversized batch cannot leave a partial result. Each refusal
  names the limit and what was sent ("These 10 files total 94.4MB, over the
  50MB limit for one upload…") instead of "upload failed".

### Changed
- `DATA_UPLOAD_MAX_NUMBER_FILES` set to 50 as a global backstop, and the two
  `*_MAX_MEMORY_SIZE` settings written out at their existing defaults so the
  values are recorded decisions. **Neither of those bounds an attachment
  upload** — Django excludes file data from `DATA_UPLOAD_MAX_MEMORY_SIZE` and
  caps no total byte count anywhere — which is why the limits above are
  enforced in application code. A production deployment still needs
  `client_max_body_size` at the reverse proxy: the app can refuse an oversized
  request, but not before the body has been received.
- **"+ Add more" made every memo unsubmittable (Phase
  MEMO-ENTERPRISE-FINAL-HARDENING).** The control appended
  `{ title: '', body: '' }`, but `MemoSection.title` is a required column, so
  the sections write was rejected — *after* the memo row had been created. One
  session's server log holds 52 memo creations against 44 section rejections,
  and the database 29 attachment-less drafts, including eleven copies of one
  subject inside a single minute. Because sections are written before files,
  the attachment upload was never reached, which is why this presented as a
  broken attachment system: the uploads were never sent. A new block now
  arrives titled (`Section 3`), Submit and Save as draft are disabled while any
  title is blank, and the block itself says which one.
- **Field errors were reported as "Could not submit the memo for approval."**
  Both memo mutations read only `error.response.data.detail`, a key a field
  error does not carry. The body already said
  `{"sections":[{},{},{"title":["This field may not be blank."]}]}`; the author
  was shown none of it. `services/memoErrors.js` now renders that as
  "Section 3 title is required."
- **Every retry orphaned another draft.** Memo creation is the first of four
  writes, so a rejection downstream left the row behind and the next click made
  a new one. The form now reuses the memo it already created, re-sending the
  header as a PATCH so edits made while fixing the fault are kept.
- **Five clicks on Submit created five memos.** `busy` is derived from
  react-query state and only disables the button on the next render, so clicks
  dispatched in one event-loop turn all got through: against SQLite four of the
  five additionally returned 500 (`database is locked`, from the
  `select_for_update` in memo numbering); against PostgreSQL they would all
  have succeeded, leaving five real memos. A ref set synchronously at click
  time closes the window.
- **The memo file picker offered six types while the server accepted fifteen
  (Phase MEMO-END-TO-END-REAL-WORLD-VALIDATION).** Both upload inputs still
  declared `accept=".pdf,.doc,.docx,.xls,.xlsx,.csv"` after the server was
  widened, so the file dialog hid PNG, JPG, WEBP, TXT, PPT, PPTX and ZIP —
  types the API accepts. Uploading a PNG through the API worked; choosing one
  in the browser did not show it.
  - Found by driving the real application in headless Chrome. **No API test
    could have caught it**: `accept` is a browser hint that never reaches the
    server, so every request-level test passed while the user could not pick
    the file. A guard now compares both inputs against the server's list.
  - **Validated end to end through the real UI, not the test client.** Logged in
    at the form, filled the fields, set real files on the real input via
    `DOM.setFileInputFiles`, added an approver through the employee search and
    clicked Submit. The captured network trace shows the corrected order:
    `POST /memos/ 201 → /sections/ 200 → /attachments/ 201 → /matrix/ 200 →
    /send-for-review/ 200`.
    - One file, three files and five files (including PNG and CSV) all upload
      and submit.
    - The detail page renders "Attachments 5" with size, date and uploader per
      row; Preview returns `inline` and Download returns `attachment`, both 200.
    - The approver sees the memo, all five attachments, a working download and
      Approve/Reject; an employee not on the workflow sees nothing.
  - Confirmed live in the browser: the 10 MB limit text, and the submit button
    correctly disabled until an approver is added.


### Fixed
- **Memo submission wrote its own content after locking itself (Phase
  MEMO-V1.0-PRODUCTION-HARDENING).** The submit flow called
  `create-and-submit` and saved sections, attachments and matrix afterwards —
  but submitting LOCKS the memo, so every following write hit a locked
  document. Sections returned 403 twelve times in one session's log and the
  attachments queued behind them never ran, while the author got a success
  dialog and a memo with no body and no files.
  - The order is now create draft → sections → attachments → matrix → submit.
    The old comment defended the previous order as "atomic create + submit: a
    rejected matrix leaves no orphan draft". That trade is reversed
    deliberately: a rejected submit now leaves a DRAFT holding the author's
    work, which they can fix and resubmit, instead of discarding it to avoid
    leaving a row behind.
  - **A failed upload no longer reports success.** `saveChildren` caught the
    error, set a message and returned normally, so the mutation resolved and
    the success dialog appeared over the top of it. It rethrows now.
  - **The global `Content-Type: application/json` is gone from `api.js`.**
    Axios already sends that for object bodies, so it bought nothing, and it
    was silently applied to FormData — which is what broke memo and minute
    uploads. Verified after removal: JSON POST 201, bodiless GET 200, multipart
    upload 201.
  - **A malformed request body returned 500.** `_matrix_rows` called
    `request.data.get(...)` without checking the body was an object, so a bare
    JSON list raised AttributeError. Malformed input is the client's mistake and
    deserves a 400 saying so; an unhandled exception on request data is ours.
  - Two tests pinned the broken order and a third mock was missing
    `setSections` and `sendForReview` entirely — the page called `undefined()`
    as soon as the flow changed. All updated, with the reason recorded.
  - **End-to-end over real HTTP**: draft → 3 sections (200) → 3 attachments
    (201) → matrix (200) → submit (200), ending `draft_for_review` with 3
    sections, 3 attachments, 1 workflow step and 7 audit rows. Submitting
    raised MEMO_SUBMITTED to the author and MEMO_APPROVAL_REQUIRED to the
    approver, both with `sent` email-log rows. Sections, attachments and files
    all survive review, approval and archiving. 449 backend and 1,114 frontend
    tests pass.


### Fixed
- **Memo and minute attachment uploads never reached Django (Phase
  CRITICAL-MEMO-UPLOAD-FIX).** `services/api.js` creates the shared axios
  instance with a DEFAULT of `Content-Type: application/json`, and
  `memoService.uploadAttachments` posted FormData without overriding it. The
  multipart body went out labelled as JSON, the parser found no files, and every
  upload returned 400 in about ten milliseconds without reading a byte.
  - **`minuteService` had the identical defect**, and both carried comments
    asserting the opposite — minute's said "setting it by hand produces a
    request the server cannot parse". It does not: axios replaces the value with
    one carrying the correct boundary when the body is FormData. Circular and
    task uploads have always passed the header and have always worked, which is
    the evidence those comments were written against.
  - **No backend test could have caught this.** Django's test client builds its
    own multipart body and never touches axios, so every server-side attachment
    test passed against a feature that failed in every browser. The bug lived
    exactly in the gap between the two suites.
  - A source guard now fails if any service posts FormData without declaring
    multipart — verified by putting the bug back.
  - Proved over REAL HTTP against the running server, not the test client: the
    old shape returns **400 in 0.0126s**, matching the timings in the server log
    exactly; the fixed shape returns **201**. All seven requested types upload
    (pdf, docx, xlsx, csv, png, pptx, zip), the detail payload carries size,
    uploader, date, preview and download URLs, and a downloaded file is
    byte-identical to its source. 1,114 frontend tests pass.
  - The durable fix is to drop the JSON default from `api.js` and let axios
    infer per request. Not done here — it changes every request in the app — but
    it is what would remove the trap rather than guard it.


### Changed
- **Memo attachment limit raised from 2 MB to 10 MB (Phase
  MEMO-ATTACHMENT-SIZE-UPGRADE).** Memo was the only module below the shared
  project default while being the one carrying procurement quotations,
  contracts and subscription proposals — minutes allow 25 MB for board packs
  and tasks 15 MB for evidence. The pptx and zip types added the phase before
  were effectively unusable at 2 MB.
  - **The repo's own user guide already said "≤ 10 MB"** (`memo-guide.md`), so
    the code and the shipped documentation had been contradicting each other.
    Raising the code resolves that rather than creating a new gap. The EXTERNAL
    E-memo manual (p.6) still says 2 MB and is the one that needs correcting —
    flagged in the code, the service docstring and the manual test.
  - Seven user-facing statements of the old limit updated. `Profile.jsx`'s 2 MB
    was deliberately left alone: that is the profile-photo rule, a different
    limit that nobody asked to change.
  - Verified: the size ladder passes at 1, 3, 5, 8 and 10 MB and rejects at
    10.5 and 12 with "Attachment exceeds the 10MB limit" — a message derived
    from the constant, not hardcoded. All eleven file types upload at ~3 MB
    each, survive draft → review → approved → archived with files on disk, and
    a 7.25 MB attachment downloads byte-identical to what was stored.
    393 memo backend and 1,111 frontend tests pass.


### Added
- **Memo attachments are a first-class panel (Phase
  MEMO-ATTACHMENT-ENTERPRISE).** The backend already served everything a reader
  needs — size, uploader, upload date and a short-lived signed URL per file —
  and the detail page threw all of it away, rendering a bare list of links
  titled "Attachment". A reviewer could not tell a 40KB screenshot from a 2MB
  quotation, or who had attached it.
  - New `components/memo/AttachmentPanel.jsx`: count, filename, size, upload
    date and uploader per row, with separate **Preview** and **Download**
    actions and full-width buttons under a thumb. The legacy single-file field
    still renders, labelled as such — older memos carry one and dropping it
    would hide their only document.
  - **`preview_url` added to the serializer**: the same signed URL without the
    download disposition, so a reviewer can read a quotation without saving it
    first. Same TTL, same user binding; only Content-Disposition differs.
  - **Memo now accepts all fifteen requested types**, adding ppt, pptx and zip,
    with a MIME entry for legacy `.ppt`. **ZIP is a deliberate exception,
    flagged in the code**: every other type is verified by its magic bytes and
    an archive hides its contents from that check entirely, so what is allowed
    is the container, not what is inside it.
  - Links, not fetches — memo URLs are signed and carry their own credential,
    unlike task attachments which need the token. The panel's docstring records
    why the two modules differ, since that difference caused the task 401.
  - Verified: all ten of the brief's files upload, including Nepali filenames,
    a real ZIP and a legacy .ppt; attachments survive draft → review → approved
    → archived with files on disk at every step; the serializer exposes every
    field the UI needs and preview differs from download.


### Fixed
- **Task evidence downloads returned 401 (Phase
  CRITICAL-TASK-EVIDENCE-DOWNLOAD-BUG).** `AttachmentPanel` rendered the
  filename as `<a href={file.download_url}>`, and that URL is an authenticated,
  task-scoped view — tasks deliberately do not use the signed media URLs that
  memo, minute and circular use, because per-request authorisation is what makes
  their download log possible. A browser navigation carries no Authorization
  header, so every click downloaded a 401 body.
  - The filename is now a button that fetches through
    `taskService.downloadAttachment` with `responseType: 'blob'` and hands the
    response to the existing `saveBlob`. Failures are stated in the row rather
    than swallowed — a download that silently does nothing is the same
    experience as the bug.
  - **Full frontend audit:** five other sites use `href={…url}` or
    `window.open`, and all five are correct — memo, minute, circular and the
    analytics export all serve SIGNED, user-bound, 300-second URLs, and the two
    inventory PDFs already fetch a blob through the authenticated client first.
    Tasks were the only module affected.
  - **Two tests were pinning the bug in place.** They asserted the filename was
    a link with the right `href` — true, and useless, exactly the failure the
    downloads guard was written for after nine export buttons did the same. Both
    are inverted, with the history in their docstrings.
  - **The guard could not have caught this**, and now can. Its matcher fired on
    literal `/api/` hrefs and on `href={…Url(…)}` calls; a property like
    `file.download_url` is neither. A module-scoped rule was added instead of a
    cleverer pattern: from the source, a signed URL and an authenticated path
    are indistinguishable, so the honest guard is the one that knows which
    module serves authenticated paths. Verified to fail when the `<a href>` is
    put back.
  - Task attachments now accept the shared document policy too (csv, doc, xls,
    txt were missing), keeping zip, pptx and gif.
  - Verified per role against the live endpoint: creator 200, admin 200,
    unrelated department head 404, unrelated employee 404, **anonymous 401** —
    the last being the exact error users saw, confirming the diagnosis.
    719 backend and 1,111 frontend tests pass.


### Fixed
- **Search and Create overlays survived every route change (Phase
  CRITICAL-NAVIGATION-STATE-FIX).** `Layout` owned three overlay states and its
  route-change effect reset only the drawer, so the search palette and the
  create sheet stayed mounted after navigating. The bottom tabs are plain
  `NavLink`s with no `onClick`, so nothing else put them away; the palette locks
  body scroll while mounted, which is why a stuck overlay also froze the page.
  - The effect now resets all three, and is keyed on **`location.key`, not
    `location.pathname`** — tapping the tab you are already on leaves the
    pathname identical and only the key moves, so a pathname dependency strands
    the overlay in the commonest case there is.
  - **`GlobalSearch` had the same defect in its own state.** It owns a second
    palette, opened by the header magnifier — which is still on the bar below
    900px, just icon-only — and had no route awareness at all. Fixing `Layout`
    alone would have looked fixed while leaving that path broken.
  - No manual DOM cleanup was added. `CommandPalette` already restores
    `document.body.style.overflow` on unmount, so closing the state is the whole
    fix; reaching for `removeOverlay()` would have papered over the real cause.
  - Regression test added, and **verified against three variants**: it fails on
    the original code (all three cases), fails again on the plausible
    `location.pathname` fix (two of three), and passes only on the shipped one.
    It exercises both entry points, because two components owning two copies of
    "is search open" is what let one get fixed and the other not.


### Changed
- **One attachment policy for memo, minute and circular (Phase
  ATTACHMENT-POLICY-FINAL).** All eleven types — pdf, doc, docx, xls, xlsx,
  csv, txt, png, jpg, jpeg, webp — now upload to all three modules.
  - **Defined once**, as `config.uploads.DOCUMENT_ATTACHMENT_EXTENSIONS`, and
    imported by the three services. Three separate copies are what allowed the
    drift that caused the outage: memo listed types that could never upload,
    circulars silently inherited the narrower project default, and minutes
    rejected csv for no recorded reason.
  - **Minutes keep pptx and gif** as a deliberate superset. The shared policy is
    a floor, not a ceiling; board packs carry slide decks, and narrowing minutes
    to the eleven would have been a regression nobody asked for.
  - **`test_an_image_is_refused` became `test_an_image_is_accepted`.** It has now
    been inverted twice, so its docstring records the whole history rather than
    just the current assertion. **The code and the E-memo manual now disagree**:
    p.6 still says "accept only PDF, doc, docx, xls, xlsx, csv", and until that
    is updated anyone reading it will file this behaviour as a defect. That is a
    documentation task and it is flagged in the test, which is where the last
    person looked.
  - Verified: 13 of 13 test files upload to each of the three modules, including
    Nepali and 170-character filenames. Attachments survive draft → review →
    issue → broadcast → **archive** with files intact on disk at every step.
    Creator, issuer, recipient and department head all download the same file
    with the correct `application/pdf` content type. 1,257 backend tests pass.


### Fixed
- **Memo attachments: doc, xls and csv could never be uploaded (Phase
  CRITICAL-ATTACHMENT-SYSTEM-FIX).** Reproduced, root-caused and fixed.
  - **Root cause.** `config.uploads.validate_attachment` checks the sniffed MIME
    with `mimes.get(ext, set())`. An extension with no entry in
    `ALLOWED_ATTACHMENT_MIMES` therefore got an EMPTY expected set, so every
    file of that type failed the content check — always, by construction. Memo
    declared `doc`, `xls` and `csv` as allowed extensions and none of the three
    had a MIME entry, so all three were rejected with a message blaming the
    user's file: "File content ('text/csv') does not match the '.csv'
    extension."
  - **Fix, part one:** MIME entries for csv, txt, doc and xls, determined by
    measuring what libmagic actually reports rather than by guessing. That
    mattered — a multi-column CSV sniffs as `text/csv` but a single-column one
    sniffs as `text/plain`, and legacy .doc and .xls are indistinguishable OLE2
    containers reported as `application/x-ole-storage`.
  - **Fix, part two:** the class of bug is now impossible to reintroduce
    silently. An extension with no MIME entry is treated as a CONFIGURATION
    fault: logged loudly for operators and accepted on the extension allowlist
    that already passed, exactly as the missing-libmagic case beside it is
    handled. Refusing there kept punishing users for a server misconfiguration.
  - **Circulars** were validating against the project default set, which
    silently excluded csv, doc, xls and txt without anyone declaring it. They
    now declare `CIRCULAR_ATTACHMENT_EXTENSIONS` explicitly. **Minutes** gained
    doc, xls, csv and txt so the three document modules accept the same set.
  - **Memo was NOT widened.** Images and .txt were added and then reverted:
    `test_an_image_is_refused` encodes the E-memo manual's rule (p.6, "accept
    only PDF, doc, docx, xls, xlsx, csv") and was deliberately inverted when
    that rule was tightened. Fixing the bug restores that list; widening it is a
    policy decision for whoever owns the manual.
  - Verified: 13/13 file types on minute and circular, 9/9 permitted types on
    memo, including Nepali and 170-character filenames. All nine dangerous types
    rejected — including `sneaky.pdf.exe` and an EXE renamed `payload.pdf`,
    caught by the magic-byte check. 20 attachments on one memo. Attachments
    survive draft → review → issue → broadcast with files intact on disk.
    538 backend tests pass.


### Added
- **Command palette: Create task (Phase V1.1).** `/tasks/create` had a route
  and nothing pointing at it — no rail item, no palette action — so the only
  ways in were the dashboard button or typing the URL. It is in the palette
  now, and navConfig's reachability table records it, which is the check that
  should have caught the omission.
- **Notification centre: filter by module, and unread only.** Both are
  SERVER-side. The `is_read` parameter already existed and was simply unused by
  the page; `module` is new and filters on the `MODULE_` category prefix — the
  same rule `notifications.emails.module_for` uses, so the filter and the audit
  log cannot disagree about what "Leave" means. Filtering the fetched page in
  the browser instead would have quietly hidden anything on page two, which is
  worse than no filter: a person would conclude the notification never existed.
  Verified against the API: 34 rows unfiltered, 14 for Leave, 11 for Circular,
  27 unread, and the two compose.


### Added
- **Module and reference on every notification (Phase
  EMAIL-NOTIFICATION-ENTERPRISE).** The audit trail's mandatory field list
  asked for both; the log carried neither.
  - **Module is derived, not stored.** Categories are named `MODULE_EVENT`
    throughout, so `module_for()` reads the prefix and a new category is
    classified the moment it is added. A stored column would have been a second
    copy of what the category already says, and the copy is what goes stale.
  - **Reference is a real column** (`NotificationLog.reference`, migration
    `0015`), because the human document number — `CIR-2026-000001` and the like
    — is not derivable from anything already recorded. `object_id` remains the
    UUID; the two answer different questions in an audit.
  - **One wiring point, not seven.** `send_notification_email` reads
    `reference` from the email context, so any module that puts one there gets
    it logged automatically. Circulars now pass their circular number; leave
    continues to pass its UUID as `request_ref`, which the same line picks up.
  - **The shared email template carries the standard.** The header is the
    organisation over "Office Management System" on the brand navy, and every
    email — not only leave — now shows its module and reference above the body.
    Verified on a live send: organisation, product name, `#274095`, module
    line, reference, action button and portal link all present.
  - Validated across all seven workflows against real data: Leave 62/58,
    Task 46/44, Circular 38/38, Minute 18/18, Memo 4/4, Appraisal 3/3,
    Inventory 2/2 (in-app / email). 167 log rows, zero failures.
  - Still not claiming "delivered", per the brief: SMTP acceptance is recorded
    as `sent`, and only sent, failed and read are tracked.


### Fixed
- **Circular delivery-log rows were untraceable (Phase GO-LIVE-FINAL).** Found
  by running the circular lifecycle end to end for the first time. Every other
  module passes `object_id` to the dispatcher so a send can be traced back to
  the thing that caused it; circulars passed none, so their `NotificationLog`
  rows recorded that mail went out but not what it was about. One argument in
  `circulars.workflow._notify`, which `broadcast.py` imports, so it covers the
  broadcast and reminder paths too. The same lifecycle now produces 7 log rows
  tied to the circular instead of 7 orphans.

### Verified
- **Circular UAT passed.** Draft → send for review → issue → broadcast to the
  organisation: 7 in-app notifications, 7 emails, 5 audit rows, 0 failures, and
  `CIRCULAR_ISSUE_REQUIRED`, `CIRCULAR_ISSUED` and `CIRCULAR_BROADCAST` all
  raised. This was the highest-risk untested path in the product — circular
  broadcast reaches the whole organisation from one action.
- **Role permission matrix.** Twelve endpoints against maker, checker, approver
  and admin over the real API. The organisation-analytics boundary holds
  exactly as designed: 403 for maker and checker, 200 for approver and admin.
  Anonymous access is 401 everywhere. A maker's user list returns a directory —
  id, name, username, email, role — with no sensitive fields.


### Changed
- **Notification branding and the audit trail (Phase
  NOTIFICATION-PRODUCTION-POLISH).** Two real gaps; the rest of the brief was
  already built.
  - **Five email templates carried five different accent colours**, three of
    them arbitrary — indigo `#6366F1` on the generic template, `#2563EB` on
    memo and the weekly digest — and none of them the brand navy `#274095` the
    product actually uses. The arbitrary ones are now the brand colour. The
    SEMANTIC ones are deliberately untouched: the leave email takes its accent
    from the request's status, and the low-balance alert is amber because it is
    a warning. Those colours mean something; brand consistency is not a reason
    to make a rejection look like an approval.
  - **`NotificationLog` was never registered in the admin.** The model has been
    recording every send attempt all along — 129 rows were already there — but
    there was no way to look at it. An audit trail that cannot be inspected is
    not one. Now registered and READ-ONLY by construction: no add, change or
    delete, because the rows are evidence of what the system did and an
    editable row is evidence of nothing. The Notification admin gained
    `read_at`, a date hierarchy and idempotency-key search.
  - **"Delivered" is not tracked, and cannot honestly be.** The log records
    `sent` when SMTP accepts the message, which is not the same as delivery;
    real delivery confirmation needs provider webhooks and is a new
    integration. Sent, failed and read are all recorded today.
  - Verified already complete and unchanged: per-user per-category preferences
    with in-app and email toggles (model, `/notifications/preferences`
    endpoint, and the Preferences tab in the UI), and the shared dispatcher —
    leave, memo, minute, circular, task, appraisal and inventory all notify
    through it.

- **Leave notification emails: the request reference (Phase
  LEAVE-NOTIFICATION-PRODUCTION).** The leave workflow already sent both in-app
  and email notifications on submit, stage-1 approval, final approval and
  rejection — `notifications/dispatcher.py` does in-app plus email with
  idempotency keys, per-user preferences and a `NotificationLog` row per send.
  The one field the specification named that the email did not carry was the
  **Leave Request ID**, which is now a monospaced Reference row. A Leave has no
  human-readable number, so the reference is the UUID — which is what the URL
  carries and what support will be quoted.
  - Verified end to end rather than by reading: a submitted leave produced 4
    in-app notifications and 4 emails (department head as the actionable
    recipient; HR, Admin and the approver copied), every specified field
    present, and 4 `sent` rows in the delivery log. Stage-1 approval, final
    approval and rejection each produced their own emails to the employee and
    the record recipients.
  - **No "returned for revision" email exists because no such state exists.**
    `Leave.Status` is pending / pending_hr / approved / rejected. Adding the
    state is a workflow change, which this phase forbids.
  - Note for anyone testing this from a shell or a management command:
    `dispatch_email` runs in a daemon thread unless `NOTIFICATIONS_RUN_SYNC` is
    set, so a process that exits immediately drops the mail. A running server is
    unaffected.

- **Type is set from the scale and nowhere else (Phase PRODUCT-V1.0).**
  Frontend only.
  - **803 font sizes now come from tokens: zero literals remain.** 612 in
    `index.css` and 191 inline in JSX, plus the chart theme. `--fs-display`
    through `--fs-label` are the only way to size type in this product now.
  - **22 of those inline sizes were OFF-scale** — 18, 15, 12.5, 10, 22, 24px
    sitting in JSX style objects. "Zero off-scale" was reported twice in
    earlier phases and was twice true only of `index.css`, the same blind spot
    that hid Playfair Display in inline styles. Corrected to the nearest step.
  - **A guard now reads both.** It fails on any literal pixel font size
    anywhere in CSS or components, on scale or not — the scale is only
    enforceable if the tokens are the only way to set type. It immediately
    caught three sizes in the Recharts theme that the conversion had missed.
  - The chart theme's tick, tooltip and legend sizes are tokens too. Recharts
    spreads `tick` onto the SVG `<text>`, where presentation attributes are CSS
    declarations and `var()` resolves — verified against a rendered chart
    rather than assumed, since a wrong call there would silently blank the axis
    labels.
  - Verified already-complete and unchanged: profile entry points, terminology,
    the employee appraisal view (the 5-step tracker is already gated on
    `caps.is_subject`, so the 10-stage ladder never reaches the person being
    appraised), and the Home dashboard section list.

- **The type scale is fully adopted, and a font I had reported as removed was
  not (Phase UI-PRODUCTION-LAST-MILE).** Frontend only.
  - **Off-scale font declarations: 30 to ZERO.** The seven sizes in use are now
    exactly the specified scale. The largest part of this was one real
    inconsistency rather than drift: a statistic numeral — the big number on a
    card — was rendered at 22, 24, 25, 27, 30 and 40px depending on which
    module drew it. All are Display 28 now, except the compact mobile summary
    card at H1 20. The 18px titles became H2 16, and page titles Display 28.
  - **Playfair Display was still in twelve inline style objects.** Earlier
    phases removed it from `index.css` and reported the product clean; the
    guard test only ever read the stylesheet. Removing the family from the
    `@import` then made those twelve WORSE rather than better — they stopped
    resolving to Playfair and started resolving to the system serif, so
    headings that had looked deliberate began looking like a fallback. Found
    by reading a screenshot, not by a check.
    - A new guard now walks every `.js`/`.jsx` source, not just the CSS, and it
      was verified to fail on a reintroduction.
  - **`×` was still the close button in 15 files, 17 buttons.** U+00D7 sits
    below the sweep range the earlier icon audits used, so "zero typed marks"
    was true only of the range checked. All are Lucide `X` now; the multiplication
    signs in the editor's "Insert 3 × 3" are prose and were left alone, and
    Modal's docstring no longer refers to "the × button".

- **Design-system adoption, incrementally (Phase UI-CONSISTENCY).** Frontend
  only. Parts 1-6 of the brief were already in place and were re-verified: the
  sidebar identity block links, the profile hub carries six sections, the five
  priority font sizes are at zero, the typed marks are at zero, and the
  terminology is settled. The remaining work was Part 8.
  - **Every hand-rolled empty state is gone — seven pages, now zero.** The
    leave and inventory modules each drew their own: a `div.empty-state` with a
    `div.empty-icon` and a `div.empty-msg`, with the danger colour set by an
    inline style. All now use `EmptyState`, which is the `ui-empty` primitive
    the design-system layer already provided.
  - **The same 48px lock SVG was pasted into three leave pages** to say "you do
    not have permission". `EmptyState` gained a `denied` variant carrying the
    Lucide `Lock`, and the three copies became one line each. The variant is
    deliberately NEUTRAL rather than red: a refusal is not a fault, and
    colouring it like one tells people something has broken when nothing has.
  - **The empty-state vocabulary grew** to cover the situations those pages
    needed — no pending approvals, no assets assigned, no permission — so the
    wording is now shared rather than retyped per page.
  - Four dead icon imports removed on the way through; ESLint does not flag
    unused lucide imports, so they had been accumulating unseen.

- **Profile experience and the last of the type scale (Phase
  UX-PRODUCTION-FINAL-IMPLEMENTATION).** Frontend only.
  - **The sidebar identity block is a link.** Avatar, name and role are one
    target rather than three — three tab stops and three tap targets for one
    destination is worse than one, and people click whichever part their eye
    landed on.
    - This needed the guard in `sidebarFooter.test.jsx` NARROWED first. It
      asserted the footer held nothing clickable at all, as a proxy for "the
      classic-menu switch has not come back". It now pins what it was written
      to protect: exactly one link, to the profile, and nothing that alters
      navigation. Narrowed rather than deleted, because deleting it to make a
      change pass is how the switch would come back.
  - **The avatar and the rail now open the hub (`/me`), not the account form.**
    The hub carries attendance, leave, assets, drafts and now tasks and
    appraisal, and links on to `/profile` for account details. `/profile` alone
    is a form, which is not what somebody clicking their own name wants.
    navConfig's reachability table was corrected — it still named the header
    avatar as the way to `/profile`.
  - **Task Summary and Appraisal Status added to the hub**, from query keys the
    app already populates, so opening the page adds no request when either has
    been fetched elsewhere. Counts, not percentages.
  - **Typography: 63 off-scale declarations down to 30.** The small tail
    (9.5, 9, 8.5, 10px) folded onto the Label step; all of it sits in
    padding-based pills that grow rather than clip, confirmed by the overflow
    probe at 1440 and 360.
  - **Fixed en route:** `.pw-sub` carried `text-transform: capitalize`, added to
    tidy a raw attendance status. It applies to the whole class, so the
    sentences added beside it rendered as "No Tasks Assigned To You."
    Capitalisation is now scoped to the one value that needs it.

- **Production polish, Parts 2-5 implemented (Phase UI-PRODUCTION-V1).**
  Frontend only. No backend, workflow, permission or API change.
  - **Typography: 274 off-scale declarations down to 63**, and 29 distinct font
    sizes down to 19. Every size whose nearest step was unambiguous was
    collapsed onto the seven-step scale — the half-points (12.5, 11.5, 13.5,
    14.5, 10.5) which were copy-paste drift rather than decisions, plus 15, 17,
    19, 21 and 26, each within 2px of one step and no closer to any other. 211
    declarations moved.
    - **Deliberately left:** 8.5-10px, 18px, 22-40px. The small ones sit in
      fixed-size badges where a larger glyph would not fit; the large ones
      carry heading and display hierarchy. Moving those is a judgement per
      selector, not a mapping.
  - **Icons: the last 8 typed marks are gone.** EmptyState's whole variant set
    (a tick, a plus, an em dash and an exclamation mark set in the body face)
    is now Lucide, as are the memo approval tick, the queue resolution marks
    and two inventory and leave empty states. `SignatureCards` needed the
    sentence changed with the glyph: "Blocks marked ✓" became "Blocks marked
    **Verified**", because an icon has no reading and a screen reader was
    announcing the sentence with a hole in it.
  - **Terminology: one name per concept.** Objectives → Goals on the two table
    headers that still disagreed with every other surface; Development Plan →
    Growth Plan on the workflow stage label and the HR dashboard section; the
    appraisal rail's "My evidence" → "My work record", matching the page it
    opens. Display layer only — the payload keys are untouched.
  - **Empty states: a shared vocabulary in `services/emptyStates.js`.** One
    situation had four sentences across the work queue, memo, minute, task and
    appraisal modules — "Nothing is waiting on you", "...on you right now",
    "...on your action", "Nothing needs your action right now". All now read
    "Nothing is waiting on you." Eleven files, four situations unified.
    - The vocabulary carries its own rules: state the fact, never apologise or
      congratulate, and no "right now" or "at the moment" — every empty state
      is about now.

- **One zero-state row, not three (Phase DASHBOARD-ZERO-STATE-CLEANUP).**
  Frontend only. A zero-value card is neither information nor actionable, so it
  no longer renders at all; the work summary carries what needs doing and
  nothing else. When every figure is zero the strip becomes a single compact
  row — "You're all caught up. / No pending approvals, reviews or overdue
  work." — still a link into the queue, because an empty space cannot be told
  apart from a summary that failed to load.
  - This reverses Phase DASHBOARD-V3's rule, which gave each zero figure its
    own reassurance and so cost three rows to say one thing.
  - The tick is the `lucide-react` icon, not the emoji shown in the brief:
    Phases UX-PRODUCTION-POLISH and UI-FINAL both required no emoji anywhere.
  - The task strip's "No tasks assigned to you" moved onto the same row style,
    so the two clear states in the page no longer look like different features.
  - Nothing collapses while loading — every count is zero until the queue
    answers, and reassuring somebody prematurely then snapping out to four
    cards is worse than a moment of dashes.

- **Production polish (Phase UX-PRODUCTION-POLISH).** Frontend only; no module,
  workflow, page, API, permission or business logic touched.
  - **One typeface family, not three.** Playfair Display and Poppins removed —
    18 declarations across page titles, auth screens, modal headings, calendar
    toolbars, memo cards and attendance panels. Everything routes through a
    single `--font-sans` token (Inter, then Noto Sans Devanagari for the Nepali
    that appears mid-sentence, then the system stack), with `--font-deva` for
    Devanagari-first strings. The design-system guard that asserted the
    OPPOSITE — that headings kept Playfair, "a deliberate choice not an
    oversight" — is reversed rather than deleted, because the failure it guards
    against is the same in both directions.
  - **Emoji removed.** Four, all of them introduced by the dashboard zero-state
    work, replaced with the `lucide-react` icon already used product-wide. Two
    standalone tick glyphs went the same way. About ten more remain in memo,
    queue, inventory and leave — reported, not changed, because one of them is
    referred to by name in body text.
  - **The avatar navigates to the profile on every screen size.** It had
    briefly opened an account menu on mobile — a fix for a crowded 360px bar
    that solved the crowding and broke the expectation. Sign out was already on
    the profile page, so the menu duplicated a page that existed.
  - `.ref-no` declared `font-family: 'Inter', monospace`. Inter is not
    monospace, so the fallback never applied and the declaration did nothing;
    reference numbers now get `tabular-nums`, which is what they wanted.
  - Verified with 142 responsive checks at 1440, 1024, 768 and 390: no overlap,
    no clipping, no control under 44px.

- **Home dashboard, executive polish (Phase DASHBOARD-V4).** Frontend only.
  Sections 1-5, the caps, the renames and the large-screen rules were already
  in place; three things changed.
  - **The live attendance panel is off Home.** Three cards of live presence,
    device status and a punch feed — "Large Statistics" on the page the brief
    defines as an action centre. It also brought a WebSocket and a 20-second
    poll to a screen whose own note says it deliberately runs no polling loop.
    Nothing is lost: the same panel renders on the Leave dashboard, and
    /workforce/hr ("HR command centre" in the rail, same role gate) carries the
    presence figures, device rows and punch feed with a department summary.
  - **A zero is a text row, not a card.** The clear state kept the card ground,
    border, accent rule and shadow, so "nothing overdue" carried the same
    visual weight as a figure needing action. It now drops all of that and
    keeps only its grid cell, so the row does not reflow as figures come and go.
  - "Nothing waiting for review" became "No reviews waiting", matching the
    brief's wording, and the notices panel's "All" became "View all" so the
    three panel links on one screen no longer read three different ways.
  - Re-measured at 360x780: Work Summary 176-312, Quick Actions 328-557, queue
    from 572 — all three on the first screen. Wide view re-checked at 2560.

- **Home dashboard, approved structure implemented (Phase DASHBOARD-V3).**
  Frontend only. Sections 1-5, the renames, the caps and the large-screen rules
  were already in place from the preceding phases; four things actually changed.
  - **The team and organisation task blocks are gone from Home.** They carried
    Team Completion, Organisation Completion and Overdue Rate — department and
    organisation analytics on the page the brief defines as an Action Center,
    not an Analytics Center. Every figure still exists unchanged in Task
    Analytics, Insights and Reports, which is where those tiles already linked.
    **Task Reviews went with them**, and that is a deliberate conflict with
    Phase DASHBOARD-V2, which asked for it by name: it lived in the team block
    because the server only sends `pending_review` to somebody with a team, and
    Section 4 says My Tasks is ONLY the four personal figures. The review queue
    stays one click away at /tasks/review-queue.
  - **The zero rule is per figure again.** A zero no longer drops out silently
    under one blanket "Nothing waiting" line; each of the three figures where
    zero is the answer says so in its own words — "Nothing waiting for you",
    "Nothing overdue", "Nothing waiting for review" — as a one-line link rather
    than a large empty card. Drafts carries no zero label and still drops out,
    because an empty drafts folder is not news anybody came for.
  - **Quick Actions is above the queue on mobile again**, below it on desktop.
    The brief asks for both "directly below My Work Queue" and all three of
    Work Summary, Queue and Quick Actions on the first mobile screen, and makes
    the mobile placement conditional on that rule being met. It was not: with
    Quick Actions below, it began 360px past the fold. Measured after the move
    at 360x780 — Work Summary 176-312, Quick Actions 328-557, queue from 572.
    All three are on the first screen.
  - The queue's link is "View all", matching Recent Activity.

- **Home dashboard, final cleanup (Phase DASHBOARD-V2).** Frontend only. Most
  of this specification was already met by DASHBOARD-V1/LITE/MOBILE-V3; what
  follows is what actually changed, plus one requirement that could not be met
  as written.
  - **"Task Reviews" is back.** DASHBOARD-V1 deleted the "Pending Reviews" tile
    on the grounds that a reviewer's number reads 0 for almost everybody
    forever. That was the wrong fix: the right one was to scope it to
    reviewers, which the server already does — `pending_review` is only in the
    dashboard payload for somebody with a team. It now renders in the team
    block under the disambiguated name, for the same reason "My Late Tasks" is
    not "Overdue": the work summary's "Waiting For Review" counts every
    module's review queue, not tasks alone.
  - **Task summary is three tiles and a line.** Completed is the one figure
    there nobody acts on, and a fourth card gave it the same weight as three
    that are actionable. It is now "9 completed · 6 open", still a link to the
    completed list. Two counts, never a percentage — the endpoint defines no
    denominator, so a rate invented on the client would disagree with the
    Insights page. Verified: every `%` in the widget comes from the payload.
  - "Open" became "Open Tasks"; Recent activity's "All →" became "View all →".
  - **Quick Actions sits directly after My Work Queue on every screen.** The
    order and the mobile-first requirement cannot both hold on a 360px phone —
    measured at 360x780, the fold lands inside the SECOND queue card, so Quick
    Actions begins below it; an actionable queue card runs to about 250px once
    it carries Approve and Reject at full width. The trade was put to the
    author and the order was confirmed, so the order is what ships. The queue's
    three-row cap on mobile is what keeps the scroll to it short.
  - **The container is wider again.** 92vw/1760px still left about 350px of
    margin on each side of a 2560px screen. Now 95vw/2160px, rising to 2560px
    past 2600px. There is still a ceiling, because a 3000px line of body text
    cannot be read — but it is above the monitors people actually use.
  - **The work summary tiles lay out horizontally above 1600px.** Stretched
    across a wide row each tile was ~600px holding a single digit: the empty
    space had moved from the row into the cards, which is not an improvement.
    The number now leads, the label sits beside it and the call to action goes
    to the far edge.
  - Verified at 360x780 and 2560x1400, and against the repo's mobile gate (no
    new findings). Already in place from earlier phases and re-checked here:
    Upcoming Deadlines gone, work summary capped at four, queue capped at five,
    no appraisal card on Home, wide-screen container.

- **Mobile Home dashboard (Phase MOBILE-DASHBOARD-V3).** Frontend only; no
  backend, workflow, API or permission change.
  - **The mobile and desktop work summaries had drifted, and nothing noticed.**
    Phase DASHBOARD-V1 cut the summary from five figures to four, renamed
    "Overdue" to "Late To Act On" and folded approvals and acknowledgements
    into "Waiting For Me" — in `ActionTiles.jsx` only. `MobileActionCards.jsx`
    kept its own copy of the list, so for a full phase a phone showed five cards
    with the old labels while a laptop showed four with the new ones. The list
    now lives in `workSummary.js` and neither component owns it, and
    `workSummary.test.jsx` asks both about the same counts and compares the
    answers — a guard verified to fail on exactly that drift.
  - **The mobile summary is a 2x2 grid**, not five full-width rows at ~60px
    each. Zero figures drop out; when everything is clear the strip collapses
    to one "✅ Nothing waiting" line that is still a link, because an empty
    space cannot be told apart from a summary that failed to load. The desktop
    tiles collapse the same way — previously a clear day rendered "Nothing
    waiting for you" beside "Nothing overdue", two cards to say one thing.
  - **Quick Actions moved above My Day on mobile** and is a 2x2 grid there
    rather than a stack of four full-width cards; the "Start a new memo"
    description is dropped at that width, being the title said twice. My Day is
    variable height, so anything after it had no fixed position at all.
  - **The appraisal card was removed from Home.** It is a second rendering of a
    fact My Day already carries: a step that is genuinely your turn arrives from
    the `needs_me` scope, and the module's notifications carry the rest. The
    component is kept and annotated, not deleted; navConfig's reachability table
    was corrected, since four routes still named the Home card as a way in.
  - **My Day shows three rows on a phone, five on a desktop**, in condensed
    cards — the title keeps its 44px tap target and the action buttons keep
    their full width, the padding around them is what goes.
  - **The header lost a control.** At 360px it carried a hamburger, a 42px logo,
    a separator, two lines of branding, search, the bell, an avatar with name
    and email, and a Logout button. Logout moves into an account menu behind the
    avatar (Escape and outside-click close it); the logo drops to 28px. Search
    was already icon-only below 900px.
  - **Empty panels collapse.** Unread notices rendered a full card — heading,
    border, "All →" — to say there was nothing to read. Now one quiet row that
    still links on. Recent activity shows three rows on a phone.
  - Verified in headless Chrome at 360x780 with the repo's own mobile gate,
    which caught one regression this phase introduced: the condensed My Day
    title had been set to a 40px tap target against a 44px minimum. Condensing
    now takes the padding around the controls, never the controls. Remaining
    gate output is unchanged (`mobile-profile` renders thin in the harness, as
    it did before).
  - The attendance strip drops below the work on a phone. It answers "am I
    marked present", which is worth having and is never why somebody opened
    Home.

- **Home dashboard width and zero-states (Phase DASHBOARD-LITE).** Three fixes,
  two of them defects the previous phase introduced and a screenshot exposed.
  - **`.hm-tiles` still declared `repeat(5, 1fr)`** after the work summary went
    from five tiles to four — and to three once the zero rule collapsed one. The
    empty space on the right of that row was the grid holding a column open for
    a tile that no longer existed. `.hm-grid` had the same fault: three columns
    for the two panels left after Upcoming Deadlines was deleted. Both are now
    `auto-fit`, so the row follows the number of cards rather than a number
    written when they were first counted.
  - **`.page` was `max-width: 1400px`**, which leaves roughly half a 2560px
    screen empty and far more on a 4K one. Now `min(92vw, 1760px)` rising to
    2000px past 2200px, with padding on a `clamp` so the gutters stay
    proportional. Deliberately not 100%: text at 3000px is unreadable and a
    table that wide makes the eye travel further than it can track.
  - **The zero rule now covers the task strip.** An HR user with no tasks was
    shown four cards reading 0 — four large zero cards, which is exactly what
    the rule exists to remove. One line, and the way through kept.
- **Home dashboard: ~22 cards to ~12 (Phase DASHBOARD-V1).** Frontend only, six
  files, nothing removed that is not reachable elsewhere.
  - **Two cards said "Overdue" and disagreed.** The work summary counted queue
    items waiting on you (0); the task strip counted your own tasks past their
    date (1). Both correct, both called the same thing, side by side on one
    screen — which reads as a defect whichever number you look at first. Now
    **Late To Act On** and **My Late Tasks**. Same for Reviews → **Waiting For
    Review** and **Task Reviews**.
  - **Upcoming Deadlines deleted.** It filtered the SAME queue array My Day
    renders and showed the same rows a screen further down — the page repeating
    itself.
  - **Zero tiles collapse to a line.** Thirteen of the twenty-two cards read 0
    for an employee with three things to do. Waiting For Me and Late To Act On
    stay visible at zero because their zero is the answer somebody came for —
    the rest become "✅ Nothing overdue".
  - **Quick Actions moved above the task and appraisal strips.** The mobile
    criterion is seeing the summary, the queue and what you can create without
    scrolling; below two more tile strips, Quick Actions sat ~1,000px down.
    Trimming cards could not fix that — only the order could.
  - **No client-derived percentages.** The Completed tile shows
    "3 completed · 4 open" rather than a rate: the endpoint defines no
    denominator, and one invented here would disagree with the Insights page.
  - **Appraisal card 4 tiles → 3**, and the first no longer reads "My Review /
    My Review". With the step as its value the card duplicated its own label
    for anybody actually at that step — the likeliest of the five. It shows the
    cycle; the Review Due card carries whose turn it is.


### Changed
- **Task analytics made findable (Phase T5.1).** No new endpoint, KPI or
  calculation — the analytics engine already served every figure below. What
  changed is that people can now reach it.
  - **Insights is on the Tasks rail.** The visual dashboard existed on
    `/tasks/analytics` and was listed nowhere: reachable only *"via Task reports
    page · search"*. It carried 16 charts nobody could find.
  - **It took Workload's place rather than being added.** The rail is capped at
    seven per module and was full. Workload is one view of one thing; Insights
    carries health, trends, department comparison and the executive summary —
    and now links through to Workload, so the deeper page sits behind the
    broader one. Raising the cap is how a rail becomes the 76-link sidebar this
    navigation replaced.
  - **A status donut on the Task dashboard**, built from the SAME payload the
    tiles read — no second request and no arithmetic, so the chart cannot
    disagree with the numbers beside it. Zero-value slices are dropped rather
    than drawn as a label pointing at nothing, and the tiles stay: they are
    scoped links people navigate by, and a chart is not a substitute for a way
    through.
  - **Three charts on the Workload page**, which had 50 tiles and no chart
    despite being the page about distribution. Assignee and department come
    from the payload it already fetched; reviewer from the analytics endpoint
    that already served it, failing silently so a refused feed costs its own
    card and not the page.
  - **Open and overdue only on the assignee chart — never completed.**
    "Completed per person" on a workload chart is a ranking of people; open work
    is load, not output.
  - **Column counts above the Kanban board**, from the board payload the columns
    already render from. Empty columns keep their zero: "nothing in review" is
    information, and dropping it would make it look like "no review column".
    No ARIA list roles — the board below is already a list of the same six
    columns, and a second one announces every column twice.
  - **"Department Ranking" → "Department Comparison".** Sorting unchanged. The
    table compares departments, which is allowed; the word *Ranking* beside a
    fairness rule this strict was the wrong one.


### Changed
- **Appraisal now speaks to employees, not to HR (Phase APM-UX).** Frontend
  only: no model, workflow, RBAC, evidence, report, permission or audit change.
  - **Ten stages become five, for the person being appraised.** *My Goals · My
    Review · Manager Review · Final Feedback · My Growth Plan*. A reviewer still
    sees the full ladder — knowing a record sits at Review Committee rather than
    Supervisor Review is their job; it is not the employee's, and making them
    learn it was the complexity this phase removes.
  - **Grouped by `stage_index`, never by the stage NAME.** The dashboard sends
    `stage` as a display label, so grouping on it would mean a reworded stage
    silently regrouped somebody's appraisal — the same class of mistake as
    deciding an appraisal is finished by matching the string "Closed". A test
    asserts the position map and the status map agree for all ten stages.
  - **The committee is folded away, not hidden.** Its comment stays in the
    employee's Feedback, attributed. An employee whose appraisal was read by a
    committee has a legitimate interest in knowing that; what goes is the demand
    they learn the word *calibration*.
  - Objective→**Goal**, Target→**What success looks like**,
    Achievement→**What happened**, `Weight: 40`→**"40% of your year"**. The 100%
    rule is unchanged and now reads as an instruction: *"Your goals add up to
    85% — add 15% more before you send them to your manager."*
  - **My Evidence → My Work Record.** Sources with no provider say **"Not
    currently tracked"** and never a zero: an absence rendered as a zero reads
    as *"you did none of this"*, a claim the system has no basis for and the
    person has no way to correct. Three figures lead; the other four sit behind
    *Show all figures* and are **never removed** — an employee's own page must
    not show less than their reviewer can see.
  - **Self assessment: five sections → four.** *Support Needed* replaces
    *Lessons Learned* and *Comments* — it asks what the organisation owes the
    person rather than what they got wrong. The retired headings still PARSE,
    and any record that has them renders them editable: dropping them on save
    would delete somebody's words as a side effect of a UI change they never
    asked for.
  - **Supervisor feedback: one empty box → four prompts.** Strengths · Areas To
    Improve · Overall Feedback · **How We Can Help**. Composed into the existing
    `supervisor_comments` field. The last prompt is the one that changes the
    character of a review: the other three describe the person, that one commits
    the organisation to something.
  - Development Plan → **Growth Plan**; *Not Considered* → **Not Yet
    Considered**; Home card 6 tiles → 4, with progress **weighted by each goal's
    share of the year** so an easy 5% goal cannot flatter the headline.
  - **Phase APM-UX.1 — the language sweep finished the job.** The first pass
    changed only the screens it happened to edit, which left the employee's own
    LANDING PAGE untouched: `MyAppraisal` still said "Objective" throughout and,
    worse, still rendered the full ten-stage ladder — the one page most likely
    to be somebody's first encounter with appraisal opened on the thing this
    phase exists to remove. `EvidencePanel`, `FeedbackPanel` and
    `CompetencyRatings` were the same story. A source-level sweep across every
    appraisal screen now separates employee-facing text from reviewer text and
    reports zero remaining jargon on the former; the latter keeps its
    vocabulary deliberately, because a reviewer needs to know a record is at
    Review Committee rather than Supervisor Review.
  - **"Calibration" left the employee rail.** It was ungated and opened an empty
    page for everybody who was not on a committee — a word they should never
    have had to learn attached to a link that never worked.


### Fixed
- **Report downloads were completely broken in Task and Appraisal (Phase FIX).**
  Nine export buttons across five pages were plain
  `<a href="/api/v1/.../?export=csv">` links. This app authenticates with a JWT
  held in JavaScript, not a session cookie, so a link navigation carries **no
  Authorization header**: every one of them hit the API unauthenticated, got a
  401, and the browser rendered that JSON as a blank-looking page or saved it as
  the "file". Blank page, no file, no response, broken download — every symptom
  came from that one line.
  - The bug's origin is a comment that shipped with the helper it describes:
    *"Export URLs, for plain links — the browser downloads them with the session
    it already has."* There is no session.
  - **Every older module was already correct.** Memo, minute, circular,
    inventory, leave, attendance, workforce and the report builder all fetch
    through the authenticated client and hand the response to `saveBlob`. Only
    the two newest modules diverged.
  - Replaced with `components/common/ExportButtons.jsx` so the rule lives in
    ONE place — five copies of the fix would rot the same way five copies of
    the bug did. `reportCsvUrl` / `reportPdfUrl` are gone rather than left
    around to be reused.
  - **A refused export now says so in place**, with the server's message. The
    old behaviour navigated away and showed raw JSON, which is why a permission
    error was reported as "blank page" rather than as a permission error.
- **Blob error bodies were unreadable.** With `responseType: 'blob'` the *error*
  body is a Blob too, so `err.response.data.detail` is undefined and the user is
  told nothing. Read back as text — via `FileReader` where
  `Blob.prototype.text()` is missing, which is Safari before 14, older Edge, and
  jsdom, which is how it was caught.
- **Task and appraisal CSVs had no UTF-8 BOM.** Excel on Windows does not sniff
  encodings: without a byte-order mark it reads a CSV as the system codepage, so
  a Nepali name arrived as mojibake in the application these are most often
  opened in. `analytics/exports.py` and `reports/workforce_reports.py` have
  emitted `utf-8-sig` since they were written — with a comment saying why. The
  two newest writers were the ones that diverged. Both now emit the BOM and
  declare `charset=utf-8`.

### Added
- **Export tests that assert the file, not the button.** 57 backend tests
  covering all 13 task reports and all 6 appraisal reports in both formats:
  real `%PDF` headers and `%%EOF` trailers, the BOM, Devanagari surviving the
  round trip, an embedded comma not shifting the columns, CSV headers matching
  the JSON columns, `nosniff`, 401 for anonymous callers, and per-role scoping
  on the export path.
  - Plus `test/downloads.guard.test.js`, a source-level guard that fails on any
    `<a href>` pointing at the API and on any file-fetching service method
    missing `responseType`. **Verified against the original bug**: reintroducing
    one link makes it fail and names the file.
  - The three frontend tests that covered these buttons **passed throughout**
    the period the downloads were broken, because they asserted a link existed
    with the right href — which was true, and useless. Rewritten to click the
    button and assert the authenticated request.

### Added
- **Appraisal notifications (Phase RELEASE).** The module could run a complete
  ten-stage cycle and tell nobody: an employee discovered their self-assessment
  was due by visiting the page. Eleven categories, a signal seam mirroring the
  task module's, and receivers connected once in `AppConfig.ready()`.
  - **One rule decides every recipient:** tell the person whose turn it has just
    become, and the person who was waiting on the answer. Nobody else — and
    that deliberately excludes HR from per-appraisal traffic. HR runs a hundred
    of these; a notification per stage per person is a hundred a week, and the
    predictable result is a filter rule that hides the category including the
    ones HR does need. HR's view is the cycle dashboard, which is a pull.
  - **A return carries the reviewer's reason verbatim.** That reason is the only
    part the person receiving it back will read, and paraphrasing it loses the
    specifics they need to act on. Keyed on the audit row rather than a
    timestamp, so two returns seconds apart are correctly two messages.
  - **A failing receiver cannot roll back a transition** — the opposite of the
    rule for the audit trail, which *is* allowed to fail one: a state change
    with no record is an evidence gap, a missed notification is an
    inconvenience.
- **Appraisal deadline reminders.** The cycle stored three advisory deadlines
  and nothing read them, so the dates were decoration. `send_appraisal_reminders`
  nudges whoever owns a stage at 7 days, 3 days and on the day — **then stops**,
  because a reminder arriving every morning is one people filter, and the filter
  catches the last one too. It never escalates to a manager's manager: appraisal
  is a conversation between two people, and lateness belongs on HR's dashboard
  where a human decides whether it matters. Registered in `CRON_JOBS` and the
  crontab; 34 tests.

### Fixed
- **`reviewer_backlog` was not a count.** `Task.Meta.ordering` is
  `["-created_at"]`, and a `.values().annotate()` that does not clear it groups
  by `department_name, created_at` — **one group per task**. The dict collecting
  the rows then kept whichever the database returned last, so the figure was
  "was the last-ordered task in this department under review": 0 or 1 by luck,
  and different on SQLite and PostgreSQL because the two order equal timestamps
  differently. `average_resolution_days` had the identical shape and was the
  last row's value rather than a mean. Both fixed with an explicit `.order_by()`
  and pinned by regression tests that use three tasks and two durations — with
  one of each, a broken implementation looks right half the time.
  `workload_by_department` escaped the bug only because it happens to end in an
  `.order_by()`; relying on that by accident is what made this hard to see.
- **Four task-evidence tests failed on the first of every month.** The snapshot
  command correctly writes a closing monthly row on the 1st (and quarterly in
  Jan/Apr/Jul/Oct, annual in January) alongside the daily one; the tests fetched
  it with a bare `.get()` and raised `MultipleObjectsReturned`. The product
  behaviour was never wrong — the suite would have gone red on 1 January with
  four rows, on a day nobody is around to diagnose it. Scoped to the cadence
  under test.

- **Appraisal production hardening audit (Phase APM-FINAL.1).** Closes the
  measurement gaps the previous audit recorded, with numbers rather than claims.
  - **`appraisal/tests/test_scale.py`** — the query-count coverage appraisal did
    not have while tasks and analytics did. Eight surfaces measured at 50 and
    250 appraisals; every one is **constant**. The HR dashboard — the only
    surface that reads every appraisal in the organisation — holds at 42
    queries, and an employee's own record costs 12 whatever the organisation
    grew to.
  - **`appraisal/tests/test_apmfinal1_audit.py`** — 17 adversarial checks for
    evidence and access. Confirms all seven declared sources are reported with
    their availability (six say *not collected*, which is a different claim from
    zero), that re-citing the same window does **not** duplicate a citation
    while two objectives citing it separately correctly do not collapse, that a
    citation stays frozen when the underlying tasks are deleted, and that
    `evidence_link` contains no `aggregate`/`annotate`/`Count`/`Avg`/`Sum` — it
    cites the contract and computes nothing of its own.
  - **Access boundaries pinned by test**: a department head is *not* entitled to
    an appraisal in their own department they do not supervise; being a
    supervisor somewhere grants nothing anywhere else; the evidence endpoint
    follows the appraisal rather than the person; and reports run over the
    caller's visible set, so an outsider's report is empty rather than filtered.
  - **Four appraisal UAT suites (42 cases) in `docs/UAT.md`**, each with
    negative cases, plus four additional sign-off criteria specific to this
    module — no score or ranking anywhere, every percentage with its
    denominator, every list of people ordered by name, and "not considered"
    never rendered as a negative. A defect against any of those is Critical.

### Changed
- **`CanManageCycles` now documents why reads are open.** Any authenticated user
  can read an appraisal cycle and only HR can change one. That asymmetry was
  correct but unwritten, so an auditor had to guess whether it was a decision or
  an oversight: a cycle carries the round's period and its three deadlines,
  which is exactly what somebody needs to know when their own self-assessment is
  due — gating it to HR would hide the deadlines from the people they apply to.
  Behaviour unchanged; a test now pins both halves.

- **Goal Approval stage (Phase APM-FINAL).** The appraisal ladder becomes ten
  stages, with **Goal Approval** between Goal Setting and Mid-Year Review. It
  closes the delta the APM-00 freeze recorded: "the employee has drafted
  objectives" and "the supervisor has accepted them as the basis for the year"
  are different facts, and running them together meant an employee could not
  tell agreed objectives from ones still under discussion — while the moment of
  acceptance, the one somebody is actually held to, left no trace of its own.
  - **Objectives now have their own lifecycle: Draft → Approved → Locked.**
    Derived by the workflow from the appraisal's stage, never set by hand — two
    places deciding whether an objective is locked is two places that can
    disagree about what somebody agreed to. The badge is what settles "we never
    actually agreed that".
  - **Submitting is the employee's act; approving is not.** Objectives are the
    employee's to *propose*, and a process where only a manager can put them
    forward is one where they are handed down rather than agreed. The 100%
    weight gate moved to submission, so the person told about an incomplete set
    is the one who can still fix it, at the moment they try to hand it over.
  - **Goals are frozen at Goal Approval.** A set that can be edited while it is
    being approved is a set nobody can be held to. Rework goes back through a
    return, which is visible in the audit trail — unlike a quiet edit underneath
    the person reading them.
  - **Returning to Goal Setting undoes the approval** and clears
    `goals_agreed_at`. Left as Approved, the employee is asked to rewrite a set
    the record still claims was agreed, and whichever version is quoted later,
    one of the two is wrong. A return from a *later* stage leaves locked goals
    locked: it is not an invitation to rewrite what the year was measured
    against.
  - **Stage totals are now derived, not written.** Every "stage 4 of 9" on
    screen reads `TOTAL_STAGES` from the ladder. Inserting this stage left four
    stale literals behind, which is exactly why the constant exists.
- **Appraisal audit trail (Part 4).** Three new actions — `goals_submitted`,
  `goals_locked` and `promotion_recorded`. The last carries the value it moved
  **from and to**: folded into a generic "Updated: promotion_readiness", the
  most consequential field in the record was indistinguishable from a typo fix,
  and the question asked afterwards is always "when did this become Development
  Required, and who changed it". Editing only the rationale is correctly *not*
  a status change.
- **End-to-end UAT (Part 5).** One appraisal walked through all ten stages by
  employee, supervisor, committee and HR **through the HTTP API**. Every step
  asserts both that the right person can act and that the wrong one cannot —
  a workflow that lets the correct actor through is only half of what matters.

- **Documentation for go-live (Phase FINAL).** An audit of `docs/` found the two
  newest and largest modules effectively undocumented — Task appeared in 3 files
  and Appraisal in 1, against 5–17 for every older module. Four guides close
  that, and the README gains a documentation index so they are reachable:
  - `docs/user-manual/task-guide.md` — the 9-state workflow, who may assign,
    reminders and escalation, and what the personal task record is *not*.
  - `docs/user-manual/appraisal-guide.md` — the 9 stages, the 100% weight rule,
    the four promotion-readiness states, the four training kinds, and why
    competency levels are words rather than numbers.
  - `docs/ADMIN_GUIDE.md` — roles and what they actually mean, deactivating a
    leaver without orphaning records, the 20 scheduled jobs and which are
    critical, opening an appraisal cycle, and the five configuration mistakes
    that bite.
  - `docs/DISASTER_RECOVERY.md` — consolidates procedures that existed but were
    scattered across BACKUP.md and RUNBOOK.md, and adds what was missing: stated
    RPO/RTO objectives, a decision table for telling an outage from a disaster,
    and a quarterly drill. The nightly `backup_verify` proves the artifact is
    restorable; it does not prove a person can bring the system back, that the
    encryption key is where they think it is, or that media and database line
    up. Only the drill does.

- **Appraisal user experience (Phase APM-03b).** The complete frontend for the
  APM-02 backend: an employee's own appraisal, a supervisor's team reviews, a
  committee calibration queue and HR's cycle dashboard — 8 screens, 11
  components, 100 tests. Work Queue and Home now carry appraisal, and the
  disabled "Appraisal" placeholder on People & Attendance leads somewhere.
  - **One record page for every role.** Employee, supervisor, committee and HR
    all read `/appraisals/:id`. Two pages rendering the same appraisal would be
    two places a permission can be got wrong, and only one of them would be
    tested the day somebody changes a rule. What differs is which controls
    appear, and that comes from the server's `capabilities` block — never
    re-derived here from the caller's role. A screen with **no** controls is a
    correct screen, and a test asserts it renders.
  - **The 100% rule is shown here and enforced there.** The running total and
    the shortfall are always stated, but the form does not refuse a fourth
    objective that overshoots: objectives are written in whatever order they
    come to mind, and a form that blocks the fourth until the first three are
    re-weighted makes people do arithmetic before they may finish a thought.
    `workflow.agree_goals` is the gate, and it is the only thing that can be.
  - **Evidence is consumed, never recomputed — and the client does no
    arithmetic at all.** Not a percentage, not a total, not an average. Every
    figure arrives from `/appraisals/{id}/evidence/` with its denominator and
    definition, and both are rendered: "75%" over four tasks and over four
    hundred are not the same claim. The employee CHOOSES what is cited, with a
    reason, which is what makes it evidence somebody offered rather than a
    measurement taken of them.
  - **No colour encodes a judgement about a person.** No red-to-green ramp for
    competency levels, no tone for a promotion readiness value — a five-step
    colour scale is a score with the digits filed off and would be read as one
    across a table of ten rows. Every list of people keeps the server's
    alphabetical order and offers no sort control, because sorting colleagues
    by any appraisal attribute produces a ranking whatever the column is called.
  - **Appraisal is reached from People & Attendance, not from a tenth top-level
    entry.** The workspace rail is capped at nine links per role and a module
    rail at seven; both were full. Those caps are a budget for what somebody can
    hold at a glance rather than numbers to raise whenever a module ships, so
    Appraisal takes the route the HR command centre already takes — an overview
    tile, recorded in `UNLISTED`, indexed for search.
  - **No inline action on an appraisal queue row.** Every other Work Queue
    source offers approve/reject in place; this one opens the record. An
    appraisal step means reading somebody's written year and adding to it, and a
    one-click approval is exactly the rubber-stamp the module exists to prevent.
  - **Accessibility is asserted, not claimed.** 10 tests pin that every control
    has an accessible name, every table a caption, every field a label, and that
    nothing depends on colour: completed stages say "completed" in words,
    weight shortfalls are stated in a live region, and revealing a form moves
    focus into it — pressing "Add objective" removes the button, so a keyboard
    user was otherwise dropped back at the top of the document.
  - **My Goals and My Evidence as their own pages.** The record is where
    objectives are *agreed* — a long sitting, once or twice a year. These are
    where they are *checked*, in twenty seconds, bookmarkable.
  - **My Evidence groups by all seven declared sources, including the six with
    no provider.** Rendering the empty ones is the point: somebody appraised on
    task activity alone needs to know their memo and minute work is ABSENT
    rather than ZERO. "No evidence from Leave" and "Leave evidence is not
    collected on this system" are completely different sentences to have quoted
    at you in a review, and only one of them is true. Evidence is labelled
    **Used** (cited, frozen with its read date), **Suggested** (available, not
    yet cited) or **not collected**. Citing is deliberately not offered here —
    it happens on the record, against an objective, with a reason.
  - **The self assessment has the five sections the specification names** —
    Achievements, Challenges, Lessons Learned, Future Goals, Comments —
    composed into the existing single text field as markdown headings, so the
    stored value stays readable in the record, the PDF and the audit log rather
    than becoming JSON that prints as punctuation. Every section is optional:
    forced to fill "Challenges", somebody invents one, and the invented answer
    is what ends up in the meeting. Text written before the form had sections is
    preserved and shown as editable — 11 tests pin that no input loses a word.
    Saving is separate from submitting, because one button doing both would make
    every half-finished draft a submission.
  - **Review delays on the HR dashboard, ordered by the wait.** The one ordering
    in this module that ranks *records* rather than people, and it names who each
    delayed appraisal is with — a delay nobody owns is a delay nobody clears.
    Computed server-side so the CSV and PDF exports cannot disagree with the
    screen.
  - **Completed Reviews for supervisors, Review History for the committee.** A
    supervisor is asked about last cycle far more often than this one. The
    committee's comments are reached through each record rather than inlined in
    the history list — list rows are shared with HR's organisation-wide view, so
    carrying review prose on them would put every committee comment in the
    system into one response.

- **Performance Appraisal module (Phase APM-02).** The specification's nine-stage
  workflow, ten competencies, goals with enforced weights, development and
  training plans — consuming the Phase T6 evidence layer rather than rebuilding
  it. No other module was modified.
  - **The line this module does not cross.** The spec asks for `CompetencyRating`
    and "promotion readiness" while forbidding scoring and ranking. Those are only
    in conflict if the line is drawn in the wrong place, so it is drawn here:
    **a PERSON may form a judgement and record it with reasons; the SYSTEM may
    never form one, aggregate one, or order people by one.** A supervisor rating
    somebody "Exceeds Expectations" with a justification is the process working;
    an `overall_score`, an average of competency levels, or any list of people
    sorted by a metric is the process being replaced by a machine that cannot be
    argued with. 30 tests in `test_fairness.py` assert the absence.
  - **Competency levels are WORDS, not 1-5 integers.** Stored numerically,
    somebody averages them within a month and produces exactly the composite this
    module may not have. Words stay ordered and comparable by a human while making
    that average a deliberate act. Every rating requires a comment — a level with
    no reasoning is a number in disguise.
  - **Self and supervisor ratings COEXIST**, neither overwriting the other. The
    gap between them is usually the most useful thing in the conversation.
  - **Evidence is cited, never recomputed.** `EvidenceReference` stores what the
    task module said, with provenance and capture time. `evidence_link.py` imports
    the CONTRACT and never `tasks`, contains no `aggregate()` or `annotate()`, and
    a test asserts both. Citations are frozen: a test deletes every task and
    confirms the cited figures do not move, because the figure in the discussion
    and the figure in the record must still match months later.
  - **Appraisal visibility is tighter than anywhere else in the system.**
    Membership is explicit — named supervisor, named committee — never inferred
    from a department. A department head is *not* entitled to the appraisal of
    everybody in their department, only of those they actually supervise, and a
    test pins that distinction.
  - **Stage ownership is per FIELD, not per request:** a supervisor may PATCH at
    their own stage and still cannot rewrite the employee's self-assessment in the
    same call. Nobody signs off their own appraisal, even with org scope.
  - Going backwards is a first-class transition with a mandatory reason — real
    appraisals go back, and a process that cannot will either be approved
    dishonestly or leave the system for email where nothing is recorded.
  - HR dashboards report PROCESS state. Promotion readiness and succession list
    only what a human recorded, with their rationale, alphabetically — and someone
    with no recommendation is **absent** rather than marked unready, because "not
    considered" and "not ready" are different claims.
  - 149 tests: workflow (28), RBAC (32), evidence integration (14), fairness (30),
    dashboards and reports (45).

- **Task-to-appraisal evidence layer (Phase T6).** A central evidence registry,
  standardised task evidence, periodic snapshots, a personal performance record
  and a manager evidence view. **No appraisal was built** — no cycle, no
  workflow, no form, no rating, no score, no ranking.
  - **A contract-only `evidence` app.** Schema dataclasses and a registry:
    no models, no migrations, no views, and no import of any business module —
    all four asserted by tests. It declares all SEVEN eventual sources (task,
    memo, minute, circular, attendance, leave, inventory) with only task
    implemented, and `available()` reports the six as *unavailable* rather than
    empty. "This person has no leave evidence" and "leave is not connected" are
    very different claims to make about somebody.
    - The dependency points from `tasks` to the contract and never back, which
      is what lets the six un-integrated modules stay untouched. A registry
      living inside `tasks` would have made every other module depend on the
      task module in order to publish evidence.
  - **Standardised metrics (T6.2).** The nine named metrics in the shared shape,
    with units, definitions and — for every ratio — the count it was taken over.
    A contract test refuses a percentage with no denominator: "75%" over four
    tasks and over four hundred are not the same claim.
  - **Periodic snapshots (T6.3).** Daily, monthly, quarterly and annual series
    that COEXIST rather than deriving from one another — summing dailies would
    double-count a task that stayed open across days. Closing snapshots are
    written on the *first* of a period and cover the whole preceding one;
    written on the last day they would omit whatever happened after the job ran
    that evening, and nobody would know which hours were missing. Still
    write-once: history is never corrected, only added to.
  - **Low-volume guard (T6.8)**, closing the T5.5 audit finding. Departments
    already carried the flag; people did not. Somebody with two tasks showing
    "50% completion" is noise presented as a metric, and it is the likeliest way
    this data gets misused about a person. The flag travels *with* the evidence —
    in the payload, on the person's own page, on the manager's table and in the
    CSV — so it cannot be left behind when the numbers are emailed on.
  - **Versioned contract (T6.6)** at `evidence/contract/`, describing its own
    keys, units, definitions, period types, live sources and guarantees, so a
    consumer discovers the schema instead of hard-coding a list it cannot tell
    has changed.
  - **Manager evidence view (T6.5)** and **personal performance record (T6.4)**.
    Both ordered by name with no sort control anywhere — a table sorted by
    completion rate hands a manager a judgement they did not make and cannot see
    the basis of. The person sees their own low-volume warning too, before
    anybody quotes the numbers at them.
  - Three evidence exports (T6.7) in CSV and PDF, including a task-contribution
    report that is the row-level record behind every aggregate: an aggregate
    nobody can drill into is one nobody can check.

- **Task analytics and performance intelligence (Phase T5).** Executive,
  department, employee, reviewer and health dashboards; a KPI registry; weekly,
  monthly and quarterly trends; and a read-only appraisal evidence layer. No
  other module was touched.
  - **The guardrail is the design, not a convention.** T5's instruction is
    explicit — display metrics, do not score, do not rank employees, do not
    evaluate — and the absence is ASSERTED rather than assumed. There is no
    composite in the KPI registry, no score-shaped key anywhere in an employee
    payload, and employee and reviewer rows come back ordered BY NAME with no
    sort control on the page. A list sorted by completion rate is a ranking
    whatever the header says, and the person at the bottom of it will be asked
    about it. Departments ARE ranked, because a department is a unit of work
    with a head accountable for it — the distinction is the whole design, and
    both halves are tested.
  - **KPI registry (`tasks/kpi.py`).** Each metric defined once — formula, unit,
    direction, and the sentence it is read by — and the definition is SHIPPED to
    the client and rendered beside the number. "Completion %" appears on five
    surfaces; computed at each it would be five definitions, and the first time
    two disagreed on screen nobody could say which was right.
  - **Honest denominators throughout.** On-time excludes tasks that never had a
    due date rather than counting them as on time; drafts are excluded from every
    total; work finished late is Completed, never Overdue; rework is counted from
    the TIMELINE, so a task returned twice and then approved still counts.
  - **Trends** keep every empty bucket — a line that skips its quiet weeks is a
    lie about the shape of the curve — and say which bucket is still running,
    because `overdue` and `blocked` are states rather than events and mean
    something different in the current one.
  - **Evidence layer (`tasks/evidence.py`).** Read-only, and structurally not an
    appraisal: no score, no weighting, no comparison to anybody. Every percentage
    travels with its numerator and denominator. A future appraisal module may
    weigh these numbers — deciding how is that module's accountability, and it
    must not find the decision already made for it here.
  - **`EmployeeTaskEvidenceSnapshot`**, written once per employee per day and
    never rewritten. Appraisal happens months later, by which time the underlying
    tasks have moved; a figure recomputed in December against March's work is not
    March's figure. Registered as a non-critical heartbeat and backfillable.
  - **A person can read their own record** at `/tasks/my-record`, which states
    what it is and is not before it shows a single number. A record you cannot
    inspect is one you cannot correct.
  - Three more reports (Task Health, Task Trend, Executive Summary) appended to
    the registry so existing slugs cannot shift — a slug is in bookmarks and
    saved exports.

- **Task operations and organisational integration (Phase T4).** Tasks stop
  being a module you visit and become work that finds you: they appear in the
  Work Queue, on Home, in the bell, and they chase themselves when nobody
  acts. No other module was touched.
  - **Work Queue (T4.1).** Tasks are the FIRST source, because they are the only
    one that is the person's own work rather than a decision about somebody
    else's. **One request, not six**: the six named situations are all slices of
    `?scope=needs_me`, which the server already computes — six sources would
    have meant six chances to show the same task twice under different labels.
    Only Accept is offered inline; returning work needs a written reason the API
    enforces and a queue row has nowhere to type one, so review opens the task.
  - **Home widgets (T4.2), role-aware by PAYLOAD not by role check.** Which
    blocks render is decided by which keys the dashboard sent. Re-deriving the
    role client-side is how a widget ends up asking for a number the server
    never sends and showing a permanent dash. A failure renders nothing rather
    than costing the rest of the page.
  - **Notification delivery (T4.3), on one path.** The T3 seam said the choice of
    which path to retire belonged to the phase that built delivery; this is that
    phase. The inline `notify_user` calls T1/T2 made from inside the workflow
    engine moved to `tasks/receivers.py` — same categories, same recipients, same
    wording — so each of the six events has exactly one delivery path and a
    notification failure cannot touch a transition. Three notifications
    (acceptance, clarification, cancellation) are NOT among the six and are still
    sent directly; that is stated at the call sites rather than papered over.
  - **Reminder engine (T4.4).** 7 / 3 / 1 days before, on the day, and every day
    overdue, plus a review nudge measured from SUBMISSION — a review bottleneck
    is invisible on a due-date report, which is why it gets its own reminder.
    Every send is guarded by a `(task, kind, day)` unique row, so the scheduler
    can run twice, be re-run by hand, or overlap itself and nobody is chased
    twice in a day.
  - **Escalation engine (T4.5).** Supervisor at 3 days, HR at 7, management at
    14 — all settings, not constants in a loop. Rungs are CUMULATIVE, so
    visibility accumulates rather than moving up and going quiet, and a rung with
    nobody to tell is skipped rather than widened to everybody, which is how
    escalations stop being read. Registered as a CRITICAL heartbeat job: its
    failure mode is silence.
  - **Review management (T4.6).** A queue aged from submission, and a
    per-reviewer backlog — because "fourteen awaiting review" is a number, while
    "eleven of them with one person, oldest three weeks" is something somebody
    can act on this afternoon.
  - **Task groups (T4.7).** A template raises ONE TASK PER CHECKLIST SECTION.
    A group is a label and a creation event, not a parent task — a parent would
    need a status, and "is Website Launch in progress when three of five are?"
    is a rule nobody should have to learn.
  - **Bulk operations (T4.8)** with partial success REPORTED, not hidden: every
    task is checked individually against the same guards a single request uses,
    and the response names what was refused and why. Archiving is filing, not
    cancelling — folding the two together would quietly delete finished work
    from every completion metric — and an archived task keeps every door except
    the default list.
  - Calendar lenses (T4.9), search by reviewer and template (T4.10), completion
    trend (T4.11), and a Template Usage report (T4.12) counted over tasks rather
    than the template's own counter, which is scoped to nothing.

- **Task workspace (Phase T3).** Board, calendar, workload, overdue centre,
  dashboards, reports and search — a visualisation and productivity layer over
  the data T1 and T2 already record. **No migrations**: nothing here needed a
  column, which is the check that it is computed at the right time. No workflow,
  permission or existing API was changed.
  - **Board (Part 1).** Six columns — Backlog, Assigned, In Progress, Review,
    Completed, On Hold — with the status-to-column mapping living in
    `tasks/board.py` and SERVED to the client, so it exists once. Two copies of
    a mapping is two things that can disagree, and a task claimed by neither is
    shown nowhere, which reads as data loss. A test asserts every status is
    either placed in a column or explicitly excluded, so a tenth status cannot
    fall off silently. Cancelled is the one exclusion, and stays in List and
    search. **Read-only by design**: a drag cannot ask a reviewer why they are
    returning work, and would move tasks into states the engine refuses.
  - **List and filters (Part 2).** Reviewer and Created By columns; filters for
    status, department, assignee, reviewer, priority and date range. The
    options come from the caller's OWN visible tasks, not the staff directory —
    a filter listing departments somebody has no tasks in is a list of dead
    ends, and one built from the directory leaks the shape of the organisation.
  - **Calendar (Part 3).** Day / Week / Month, with the window resolved
    server-side so an unbounded range cannot be requested and the week starts on
    Sunday everywhere. Each task gets exactly ONE kind — a cell showing a task as
    both completed and upcoming is unreadable — coloured red / orange / green /
    blue and labelled in words, because this page gets printed.
  - **Workload (Part 4).** Three perspectives from one endpoint, chosen by the
    server and named in the payload. Employees see their own four numbers;
    managers additionally see their team; HR additionally sees departments and
    utilisation. `busiest_quarter_share_percent` is deliberately NOT presented as
    "% utilised": nothing here knows anybody's hours, so that number would be
    invented. Open work nobody is carrying is reported beside the load, because
    that is the gap a utilisation view usually hides.
  - **Overdue centre (Part 5)** and **role dashboards (Part 6)**, including
    average completion time measured from ASSIGNMENT rather than creation — a
    task that sat in somebody's drafts for a fortnight must not be counted
    against the assignee — and reported as null, never 0.0, when nothing has
    completed.
  - **Six reports with CSV and PDF export (Part 7).** All share one
    {columns, rows, summary} envelope, so one table renders them all and a
    seventh appears with no UI change. The Reviewer Performance report counts
    returns from the TIMELINE rather than the current state, because a task sent
    back twice and then approved would otherwise show no returns at all — and the
    rework rate is the point of the report.
  - **Search (Part 8).** Tasks are a source in the existing enterprise search,
    surfacing number, title, assignee, priority and status. No redesign.
  - **Event hooks only (Part 9).** Six signals — TASK_ASSIGNED,
    TASK_REVIEW_REQUIRED, TASK_COMPLETED, TASK_OVERDUE, TASK_RETURNED,
    TASK_BLOCKED — emitted at the points they occur with NOTHING connected and
    nothing delivered, plus a `detect_overdue_tasks` command for the one event
    with no transition behind it. `send_robust` throughout: a listener that
    fails must never roll back the transition that triggered it, which is the
    opposite of the rule for the audit trail and deliberately so.

- **Task execution layer (Phase T2).** Tasks stop being records and become
  workspaces: the work is done inside them, and the evidence that it was done
  stays attached to it. Built entirely on Phase T1 — no workflow was redesigned,
  no RBAC rule was moved, and both endpoints whose payloads grew kept their
  existing response shape rather than breaking callers for tidiness.
  - **Checklist groups (T2.1).** Sections with their own tally, alongside
    top-level lines — groups are optional, so every T1 checklist keeps working
    and a four-line list is not forced under a synthetic "General" heading. Ticks
    are carried on the TEXT, so reorganising a list, or moving a line into a
    section, never silently un-ticks completed work.
  - **Progress is derived, until somebody overrules it (T2.2).** With a checklist
    present, ticking a box IS the progress update. Reporting a figure by hand
    sets `progress_is_auto=False` and the checklist stops overwriting it — a
    number a person typed and a tick then discarded would make the control
    pointless. The UI offers 0/25/50/75/100; the API still accepts any 0–100 so
    a derived 33% displays faithfully.
  - **Threaded comments, mentions and edited markers (T2.3).** Replies are one
    level deep by design. T1 forbade editing outright; T2 allows it for the
    AUTHOR ALONE, stamps `edited_at`, shows "edited" in words, and writes a
    timeline row carrying the previous text — silent editing destroys the record,
    and forbidding it just moves the correction into a second comment nobody
    reads. Mentions are resolved ids, not names parsed out of plain text, and one
    naming a person who cannot read the task is dropped rather than notifying
    them about something they cannot open.
  - **ZIP, links, withdrawal history and a download audit (T2.4/T2.5).** ZIP is
    opted into by the task module only — an archive hides its contents from the
    magic-byte check, so allowing it here does not widen memos or attendance
    corrections. Evidence may be a LINK (same row, `kind` says which). Removal is
    soft, because "attachment history" means the record survives the removal.
    Every download is logged to its own table, deliberately NOT to the activity
    timeline: a row per download would bury the eleven events that matter. The
    log is owner-only — an assignee reading it would learn who has been checking
    up on them.
  - **Evidence and Attachments are separate sections (T2.7).** A reviewer opening
    a task wants what was PRODUCED, not the brief it was produced against.
  - **Board view and richer cards (T2.8).** Cards carry title, assignee, due
    date, priority, progress, and comment/attachment/evidence counts — annotated
    on the queryset, so a 50-card board is one query, not a hundred and fifty. A
    zero count renders nothing rather than "0". The board is READ-ONLY BY DESIGN:
    a drag cannot ask a reviewer why they are sending work back, and would move
    tasks into states the engine refuses.
  - **Templates (T2.9).** "Website Launch", "Audit Review", "Monthly Report", "HR
    Onboarding". Applying one COPIES its checklist rather than linking to it — a
    living parent would rewrite the checklist of every onboarding in flight the
    moment somebody edited the template, invalidating ticks already made. A
    template carries no people and no date, only `default_due_in_days`. Retired,
    never deleted, so tasks raised from it keep a name.
  - 84 new backend tests (218 for the module) and 53 new frontend tests (107),
    including a query-count suite that fails on an N+1 in the list, the detail
    page, the comment thread or a grouped checklist.

- **Task Management foundation (Phase T1).** A new `tasks` app: assigned work
  with an accountable owner, a reviewer, evidence and a verifiable trail —
  numbered `NIFN-TSK-2083-0001` off a locked per-BS-year counter.
  - **The module is deliberately self-contained.** It imports no other business
    module (memo, minute, circular, leave, attendance, inventory, reports,
    analytics) and none imports it, so it can be removed by deleting the
    directory and three registration lines. `tasks/tests/test_independence.py`
    parses the source and fails on a crossing in either direction — including
    from the test fixtures, which have no exemption. The one cross-app model
    reference, `Task.department -> leaves.Department`, is a lazy string resolved
    through the app registry rather than an import.
  - Nine statuses on one ladder — Draft, Assigned, Accepted, In Progress, Under
    Review, Completed, Closed — with Blocked and Cancelled outside it.
    `tasks/workflow.py` is the only thing that may assign to `status`; every
    transition is atomic and writes its timeline row in the same transaction, so
    a rolled-back transition cannot leave a trail claiming it happened.
  - "Request Clarification" is a recorded question, not a tenth status: the
    workflow diagram routes it back to Assign Task, and inventing a state that
    behaves identically to Assigned would make every list, filter and permission
    check learn it for nothing.
  - Multi-assignee, selected by EMPLOYEE SEARCH rather than by department. The
    task moves on the first acceptance — waiting for the last would let one
    unavailable person block work others have begun — while per-person stamps
    still record exactly who agreed and when. Reassignment preserves acceptances
    that were not the point of the change.
  - RBAC in one place (`tasks/permissions.py`), exposed to the client as
    `capabilities` flags so the UI renders its action bar from the server's
    answer. A department head is confined to the departments they head plus their
    own; HR and Admin are organisation-wide; an Employee may not create a task
    and may not reach the employee picker. The person who did the work can never
    close it, whatever their role.
  - One dashboard whose role sections are ADDITIVE — an HR officer is also an
    employee with tasks of their own. Every count is computed over the caller's
    own visible set, so a tile can never advertise a task they cannot open.
  - Frontend: dashboard, create/edit form with the employee picker, and a detail
    page carrying Task Information, Checklist, Attachments & Evidence, Comments,
    Activity Timeline and Progress. Seven module menus, each a route plus a
    server-side scope name.
  - Evidence is served only through the task's own gate (`GET
    /api/v1/tasks/<id>/attachments/<id>/download/`), validated on size,
    extension and magic bytes by the shared `config.uploads` validator.
  - 128 backend tests and 30 frontend tests. Two guard tests were widened
    deliberately and are annotated in place: the workspace rail cap (8 -> 9, for
    Admin only, because Tasks is a new top-level module) and the capability
    parity baseline (a recorded `ADDED_SINCE` map, so a changed or removed
    capability still fails).

- **Autosave for Memo, Minute and Circular (Phase 111).** All three modules held
  the whole document in React state and wrote nothing until the user pressed a
  button, so a refresh, crash, power cut, disconnect or session expiry lost
  everything. One shared engine (`useDocumentDraft`) now snapshots each form to
  IndexedDB on every change and to the server five seconds after typing stops,
  every thirty seconds regardless, and whenever the tab is hidden.
  - **Workflow statuses are unchanged.** No "Auto Saved Draft" state was added:
    Memo keeps its eight statuses, Minute its four, Circular its nine. Autosave
    state (`saving` / `saved` / `offline` / `local-only`) lives on the snapshot
    and in the indicator, never on the document. Guard tests fail if that blurs.
  - New `drafts` app: one generic `DocumentDraft` per (kind, document, user),
    plus `DocumentDraftVersion` retaining the last 10 milestones. The payload is
    opaque and unvalidated, which is what lets a half-typed document be stored —
    `Memo.subject`, `Circular.subject`/`body` and `Minute.meeting_time` are all
    `blank=False`, so autosave through the real serializers would 400 for
    exactly the users who need it most.
  - Recovery dialog (Restore / Continue editing / Discard) that says what and
    when; whichever of the local and server copies is newer wins.
  - Offline queue that syncs on reconnect; "Unfinished Work" listing drafts
    across all three modules; draft history with non-destructive restore.
  - Exit protection on refresh, tab close and in-app links.
  - Autosave writes NO audit rows — `MemoViewSet.update` writes an `AuditLog`
    entry and a user-visible timeline step per edit, and a five-second cadence
    would put hundreds on one document. Frequency is a counter on the draft row;
    only create/restore/recover/discard/submit are logged.
  - Confidential memos are never written to local storage, only to the
    authenticated server snapshot. Local copies are namespaced per user and
    wiped on logout.
  - `purge_expired_drafts` removes snapshots untouched for 90 days, wired into
    `deploy/crontab` and the heartbeat registry.


### Changed
- **`Appraisal.promotion_recommended` (boolean) → `promotion_readiness` (three
  words plus a blank).** The boolean forced a supervisor to answer yes or no to
  a question whose honest answer is usually "yes, once X", and it made "never
  considered" indistinguishable from "not ready". Now **Ready / Ready With
  Development / Development Required**, with `""` meaning the question was never
  reached — a load-bearing absence: the reports omit those people rather than
  listing them as a no, and the per-answer counts cover the three recorded
  answers only, because a "Not Considered: 42" tile beside the real answers
  reads as a fourth verdict on 42 people. The rationale is now mandatory in all
  three directions; the one that most needs it is Development Required, since a
  no with no stated development is a verdict nobody can act on or appeal.
  Migration 0003 carries the data across (add, copy, remove, one migration) and
  is reversible but lossy by design — going back maps Ready With Development to
  `True`, because losing the nuance is recoverable and inverting the verdict is
  not.
- **`TrainingPlan.kind`, plus a nullable `mentor`.** Training, Certification,
  Mentorship and On-the-job. Everything was previously "training", so a
  mentoring pairing could only be expressed by writing the word in the title and
  HR's list of training needs silently included items nobody had to fund — the
  total was quoted as a training spend. One field on one model rather than four
  models: the shape is identical, and splitting it would triple the queries
  behind HR's training screen for no analytical gain.
- **Work Queue tags for `task` and `appraisal`.** Both were falling through to
  the raw lowercase type string with no chip colour, which read as a rendering
  fault beside six properly styled tags. `task` was a pre-existing gap from
  Phase T4.

### Fixed
- **Every Memo menu except Dashboard and Create Memo rendered a blank page.**
  The PDF-fidelity rebuild changed `MEMO_TYPES` in `memoLabels.js` from an array
  of strings to an array of `{code, label}` objects, but `MemoScopePage`'s type
  filter still ran `t.charAt(0).toUpperCase() + t.slice(1)` over the entries.
  That threw `TypeError: t.charAt is not a function` during render, and because
  `MemoScopePage` is the single component behind Draft Memo, Department Memo,
  Draft For Review Memo, Archived Memo and My Pending Actions, one crash blanked
  all five at once while the two pages that do not use it kept working. Routes,
  sidebar paths, permission guards and the API were all correct and unchanged.
- **Empty Memo menus offered an "Apply for leave" button pointing at
  `/leave/apply`.** `EmptyState` is shared with the leave module and defaults
  `ctaTo` to the leave form; the memo rebuild stopped passing the prop, so the
  default took over on every memo menu that cannot create. Memo now passes
  `ctaTo` explicitly (as `null`, not `undefined` — `undefined` re-triggers the
  default parameter), matching Minute and Circular.
- Dropped the retired `priority` column from the memo scope pages, the memo
  dashboard and the `MemoTable` test fixture; the column was removed from
  `MemoTable` in the rebuild and the call sites were still requesting it.
- `scripts/generate_sample_pdfs.py` still built its sample memo from the retired
  `title`/`body`/`MemoType.FINANCIAL`/`SENSITIVE_MEMO_TYPES` API and could not run.

### Security
- **Closed unauthenticated media serving (Critical).** Removed the public
  `/media/<path>` catch-all that streamed every upload without auth. All
  user files (memo attachments/vouchers, profile photos, report files) are now
  served only via short-lived HMAC-**signed, expiring URLs** (`documents.protected_media`),
  header-free so they work in `<img>`/`<a>`. Added regression tests.
- **Idle auto-logout** (default 20 min, configurable `VITE_IDLE_TIMEOUT_MIN`): a
  30s warning modal, activity resets the timer, on timeout the refresh token is
  blacklisted and the user is returned to login; multi-tab consistent.

### Changed
- **Memo module brought to the E-memo manual (PDF 1:1).** The workflow was already
  right — the approving chain, the recommender-only extension rule, noted members,
  unavailability forwarding, the approver swap, department self-assign and the
  archive all matched — so this is a form-and-document change. `Memo Type` becomes
  the manual's single GENERAL/CONFIDENTIAL/DRAFT dropdown, absorbing the separate
  `classification` column; `title` folds into `subject` and `priority` is retired,
  neither being on the manual's form. Adds **To**, **From unit / sub-unit**, **CC
  departments** (an access grant on an archived GENERAL memo, not decoration), and
  **content blocks** — Background and Recommendation seeded on create, with the
  manual's "+ Add more" and per-block removal. A **DRAFT memo keeps no log**, as
  p.4 requires. Attachments tighten to the manual's 2 MB and
  PDF/doc/docx/xls/xlsx/csv and gain its **File Name** box. The five unavailability
  labels are now the document's, verbatim. Four features that existed on the server
  but had no way in from the screen — mark-unavailable, add-new-approver, add-noted-
  member and add-view-access — are now on the memo page, each gated on its own
  capability flag. Migration `0015` carries existing rows across before dropping
  any column. OTP remains excluded.
- **Minute module rebuilt to the E-minute manual (PDF 1:1).** The manual describes no
  approver: a draft offers exactly *Submit for Draft Review* and *Submit for
  Acknowledge*, and the minute archives itself "once acknowledged by all present
  members". The four-role approval chain that had been reused from the memo module is
  therefore retired, along with the registers the manual never mentions (decisions,
  resolutions, action items, traceability, SLA escalation). The lifecycle is now
  `Draft → [Draft Review with the FRO] → Pending Acknowledgement → Archived`; members
  are recorded in the manual's three groups (Present / Absent / Invitee) and only those
  **present** are asked to acknowledge. Adds the FRO field and the Reference No. search
  that seeds a new minute from a previous one (p.5). The OTP challenge before
  acknowledging (p.9) is deliberately **not** implemented, per instruction — a test
  pins its absence. Migration `0008` carries existing data across (status remap,
  background+analysis → agenda body, participant role → attendance) before dropping
  anything; `0009` seeds DEPARTMENT / BRANCH / MANCOM / OTHERS. The minute PDF and
  signature sheet were rebuilt to the manual's layout. Analysis of all three manuals
  against the codebase is in `docs/SANIMA_PDF_MAPPING.md`.
- **Minute module simplified.** The approval matrix (add rows, pick a workflow role
  per row, drag into rank order, satisfy three structural rules) is replaced by
  "who approves this minute?" — one required **Approver** plus optional
  **Reviewers**; the chain is built reviewers-first/approver-last, so it can no
  longer be assembled in a shape the server rejects. The minute page opens with a
  **next-step banner** (what state it is in, whether anything is wanted from you,
  and the button that does it), a single approval-progress list in place of the
  tracker + matrix table + signature cards, and folds the audit-grade material into
  a "Full record" disclosure. Create Minute shows six fields with sub type,
  priority, reference, To/CC and analysis behind "More options". Sidebar: eleven
  minute menus → five, on two new server scopes (`needs_me`, `mine`) and a
  `needs_my_action` count; the old routes still resolve. Dashboard: 23 tiles and
  five charts → six personal tiles, with the organisation-wide figures behind a
  disclosure. No capability, export or record was removed.

### Added
- **CI/CD** (GitHub Actions): backend `pytest`+coverage, `ruff`, migration-drift
  check, `pip-audit`; frontend `vitest`+`eslint`+`vite build`+`npm audit`; secret
  scan (gitleaks); Conventional-Commits gate on PRs.
- **Automated encrypted off-host DB backups** (`backend/deploy/backup.sh`): daily
  `pg_dump` → gzip → AES-256, off-host copy (S3/rsync), retention rotation, plus a
  tested restore runbook (`backend/deploy/restore.sh`, `docs/BACKUP.md`).
- **Daily notification reconciliation** cron so the bell count can never drift
  from the actionable Pending Approvals queue.
- Contributor guide + Conventional-Commits config (`docs/CONTRIBUTING.md`,
  `commitlint.config.js`), this CHANGELOG.

### Fixed
- `react-hooks/rules-of-hooks` violation on the Asset Assignment page (a `useMemo`
  after an early return).

<!-- Backfill note: commits prior to this entry used placeholder messages
     ("your message"). History from here forward follows Conventional Commits. -->

## Earlier (pre-CHANGELOG)
Leave/memo/inventory/attendance modules, category-based leave engine, PDF
generation with the NIF letterhead, notifications, favicon set, login polish,
approval-queue ⇄ notification single-source resolver, attendance registration-date
absent floor, and inventory RBAC — delivered incrementally (see git history).
