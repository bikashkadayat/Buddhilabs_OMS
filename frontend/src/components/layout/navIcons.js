import {
  Activity, Archive, BadgeCheck, BarChart3, BriefcaseBusiness, Building2,
  CalendarCheck2, CalendarClock, CalendarDays, CalendarPlus2,
  CalendarRange, CircleCheckBig, CirclePlus, CircleUserRound,
  ClipboardCheck, ClipboardList, ClipboardPen, Clock4, FilePenLine,
  FilePlus2, FileSearch, FileStack, Fingerprint, FolderKanban, FolderOpen,
  Goal, History, HousePlus, Inbox, Layers, LayoutDashboard, ListTodo,
  ArrowLeftRight, MailOpen, Megaphone, Package2, PackageCheck, Search, Send,
  Settings2, ShieldCheck, SquareCheckBig,
  SquareKanban, TrendingUp, TriangleAlert, UserRoundCheck, UsersRound,
  Wallet, Warehouse, Wrench,
  Palette, Globe,
} from 'lucide-react';

/**
 * The one navigation icon registry.
 *
 * WHY A REGISTRY RATHER THAN AN IMPORT PER FILE
 * ---------------------------------------------
 * Navigation is drawn by five surfaces — the sidebar, the mobile tab bar, the
 * command palette, global search and Home's quick actions — and each one used
 * to carry its own hand-written SVG paths. The same destination was therefore
 * drawn several different ways, at whatever size and stroke that file happened
 * to use, and changing an icon meant finding every copy. `navConfig` names an
 * icon; every surface resolves the name here; the glyph is identical
 * everywhere by construction.
 *
 * Names stay strings in navConfig so that file remains plain data — it is read
 * by tests that have no business importing React components.
 *
 * CHOOSING A GLYPH (Phase OMS-NAVIGATION-PREMIUM-ICON-UPGRADE)
 * -----------------------------------------------------------
 * The first pass reached for the most literal icon in each case — a plain
 * `FileText` for Documents, a bare `Users` for people, `Package` for assets.
 * Literal is not the same as legible: at 18px a thin outline of a sheet of
 * paper carries almost no silhouette, so a column of them read as grey texture
 * rather than as distinct destinations.
 *
 * These are chosen for SILHOUETTE — a shape recognisable at a glance and
 * distinct from its neighbours in the same menu — which is why Documents is a
 * folder with structure in it rather than a page outline, and why the two
 * calendar-based leave entries differ in their interior marks rather than
 * their frame.
 */
export const NAV_ICONS = {
  // ---- workspace rail --------------------------------------------------
  // Specified as HousePlus in Phase OMS-NAVIGATION-EXECUTIVE-UI-UPGRADE.
  // NOTE: this is the same glyph as `wfh` below (work-from-home), so Home and
  // WFH now share a silhouette, and a house-with-a-plus conventionally means
  // "add a home" rather than "go home". Implemented as asked; `House` is the
  // one-word revert.
  home: HousePlus,
  queue: BriefcaseBusiness,
  drafts: ClipboardList,
  tasks: ListTodo,
  documents: FolderKanban,
  people: UsersRound,
  assets: Package2,
  reports: BarChart3,
  admin: Settings2,

  // ---- shapes shared by more than one module ---------------------------
  overview: LayoutDashboard,
  waiting: Clock4,
  archive: Archive,
  calendar: CalendarDays,
  team: UsersRound,
  building: Building2,
  folder: FolderOpen,
  inbox: Inbox,
  send: Send,

  // ---- memo ------------------------------------------------------------
  'memo-create': FilePlus2,
  'memo-drafts': FilePenLine,
  'memo-review': FileSearch,

  // ---- minute ----------------------------------------------------------
  'minute-create': ClipboardPen,
  'minute-mine': ClipboardList,

  // ---- circular --------------------------------------------------------
  'circular-ack': BadgeCheck,
  'circular-unread': MailOpen,
  'circular-create': Megaphone,

  // ---- task ------------------------------------------------------------
  'task-mine': CircleCheckBig,
  'task-team': UsersRound,
  'task-all': Layers,
  'task-board': SquareKanban,
  'task-review': SquareCheckBig,
  'task-overdue': TriangleAlert,
  insights: TrendingUp,

  // ---- appraisal -------------------------------------------------------
  'appraisal-mine': ClipboardCheck,
  goals: Goal,
  evidence: FileStack,
  'review-team': UserRoundCheck,
  cycles: CalendarClock,

  // ---- leave -----------------------------------------------------------
  'leave-apply': CalendarPlus2,
  'leave-mine': ClipboardList,
  'leave-records': CalendarRange,
  'leave-balance': Wallet,
  'leave-policy': ShieldCheck,

  // ---- people & attendance ---------------------------------------------
  attendance: CalendarCheck2,
  corrections: BadgeCheck,
  wfh: HousePlus,

  // ---- assets ----------------------------------------------------------
  requests: Inbox,
  maintenance: Wrench,
  register: Warehouse,
  assignment: ClipboardCheck,
  // Phase ASSET-CUSTODY-TRANSFER. Two-way arrows for a move between people; a
  // checked parcel for something handed back; a user-check for a leaver whose
  // assets are accounted for.
  transfer: ArrowLeftRight,
  assetReturn: PackageCheck,
  clearance: UserRoundCheck,

  // ---- reports & admin -------------------------------------------------
  history: History,
  biometric: Fingerprint,
  bulk: Layers,
  health: Activity,
  search: Search,

  // ---- white-label settings (Phase S9) ---------------------------------
  // A palette and a globe: both read at 18px, and neither is a cog -- the
  // two entries sit next to "Subscription & billing" under Admin, where a
  // third generic gear would make all three look like the same page.
  branding: Palette,
  domain: Globe,

  // ---- mobile tab bar only ---------------------------------------------
  create: CirclePlus,
  profile: CircleUserRound,
};

export default NAV_ICONS;
