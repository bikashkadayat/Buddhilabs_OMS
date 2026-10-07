/**
 * The Help Center's content, built in.
 *
 * IN THE PRODUCT, NOT ON A WEBSITE. Somebody stuck on a screen should get the
 * answer without leaving it. Every link points at a real page in this
 * workspace, and every article says only what the product actually does --
 * a help article that describes a button that isn't there is worse than none.
 *
 * `roles`: who sees it ('all', or any of maker/checker/approver/bod/admin).
 * `type`: tutorial (do something, step by step), faq (a question people ask
 * support), guide (understand something).
 */
export const HELP = [
  // --- Everyone ---------------------------------------------------------
  {
    slug: 'check-in', type: 'tutorial', roles: ['all'],
    title: 'Check in and check out',
    summary: 'Record your attendance from the top of your Home page.',
    steps: [
      'Open Home. Your attendance is the first thing on the page.',
      'Press Check in. Allow location access if your browser asks — your location is recorded with the check-in.',
      'At the end of the day, press Check out on the same spot.',
      'Your working time and where you checked in are shown under your greeting.',
    ],
    links: [{ label: 'Go to Home', to: '/' }, { label: 'My attendance', to: '/my-attendance' }],
    keywords: 'attendance punch in out clock location gps',
  },
  {
    slug: 'apply-leave', type: 'tutorial', roles: ['maker', 'checker', 'approver', 'bod'],
    title: 'Apply for leave',
    summary: 'Ask for time off; your department head and HR review it.',
    steps: [
      'Open Leave → Apply, or press Ctrl+K and type "apply".',
      'Choose the type of leave and the dates. Your remaining balance is shown before you send.',
      'Add a short reason and anyone covering for you, then submit.',
      'You’ll get a notification when it’s approved or rejected. Track it under Leave → My applications.',
    ],
    links: [{ label: 'Apply for leave', to: '/leave/apply' }, { label: 'My leave balance', to: '/leave/balance' }],
    keywords: 'holiday time off vacation sick request absence',
  },
  {
    slug: 'search', type: 'tutorial', roles: ['all'],
    title: 'Find anything with Ctrl+K',
    summary: 'One search box for people, tasks, leave, documents and pages.',
    steps: [
      'Press Ctrl+K (Cmd+K on a Mac), or the search box at the top. On a phone, tap Search in the bottom bar.',
      'Type at least two letters: a person’s name, a task number, a memo subject, or a page like "leave balance".',
      'Use the arrow keys and Enter, or tap a result. Some results can be approved right from the search.',
    ],
    links: [],
    keywords: 'search find command palette shortcut',
  },
  {
    slug: 'notifications', type: 'tutorial', roles: ['all'],
    title: 'Choose which notifications you get',
    summary: 'Turn in-app or email notifications on or off, by kind.',
    steps: [
      'Open the bell at the top, then See all — or your avatar → Preferences.',
      'On the Preferences tab, switch each kind of notification on or off for in-app and for email.',
      'Use the tabs on the notifications page to see only Approvals, Leave, Tasks and so on.',
    ],
    links: [{ label: 'Notification preferences', to: '/notifications?tab=prefs' }],
    keywords: 'email alerts bell unsubscribe preferences',
  },
  {
    slug: 'correction', type: 'tutorial', roles: ['maker', 'checker', 'approver', 'bod'],
    title: 'Fix a missed check-in',
    summary: 'Ask for an attendance correction when a day is wrong.',
    steps: [
      'Open People & Attendance → Corrections.',
      'Choose the day, say what the correct times were and why.',
      'Your department head reviews it, then HR. The day updates once it’s approved.',
    ],
    links: [{ label: 'Attendance corrections', to: '/workforce/corrections' }],
    keywords: 'forgot to check in missed punch wrong time absent correction',
  },
  {
    slug: 'create-task', type: 'tutorial', roles: ['all'],
    title: 'Create and assign a task',
    summary: 'Give work to someone, with a due date and a reviewer.',
    steps: [
      'Press Create task on Home (or Create → Create task on a phone).',
      'Give it a title, choose who does it and who reviews it, and set a due date. Start from a template to save typing.',
      'The assignee gets a notification and accepts it; you’ll see progress in Tasks.',
    ],
    links: [{ label: 'Create a task', to: '/tasks/create' }],
    keywords: 'task assign todo work job',
  },
  {
    slug: 'write-memo', type: 'tutorial', roles: ['maker', 'checker', 'approver', 'bod'],
    title: 'Write and send a memo',
    summary: 'Draft a memo and send it through its review steps.',
    steps: [
      'Open Documents → Memo → Create memo.',
      'Write the subject and body, and choose who reviews, recommends and approves it.',
      'Save as a draft at any time — drafts are kept under Unfinished Work — or submit it.',
    ],
    links: [{ label: 'Create a memo', to: '/memos/create' }],
    keywords: 'memo letter document draft approval',
  },
  // --- Managers -----------------------------------------------------------
  {
    slug: 'approve-leave', type: 'tutorial', roles: ['checker', 'approver', 'admin'],
    title: 'Approve or reject a leave request',
    summary: 'Decide requests from your team, in order.',
    steps: [
      'Requests waiting for you show on Home under Pending reviews, and in Leave → Review requests.',
      'Open a request to see the dates, the balance it uses, and who else is away.',
      'Approve, or Reject with a reason the employee will read. Department heads approve first; HR approves after.',
    ],
    links: [{ label: 'Review requests', to: '/leave/pending' }],
    keywords: 'approve reject leave manager department head hr',
  },
  {
    slug: 'review-task', type: 'tutorial', roles: ['checker', 'approver', 'admin'],
    title: 'Review a submitted task',
    summary: 'Accept finished work, or send it back with a comment.',
    steps: [
      'Tasks waiting on your review are in Tasks → Reviews, oldest first.',
      'Open one, check the work and any attachments.',
      'Approve it, or return it with a comment saying what to change.',
    ],
    links: [{ label: 'Review queue', to: '/tasks/review-queue' }],
    keywords: 'review approve task submitted',
  },
  {
    slug: 'team-today', type: 'guide', roles: ['checker', 'approver', 'admin'],
    title: 'See who’s in today',
    summary: 'Your team’s attendance at a glance, and in detail.',
    steps: [
      '“Your team today” on Home shows who is present, late, on leave or absent, and who isn’t in yet.',
      'Open the Team dashboard for trends, department totals and pending corrections.',
    ],
    links: [{ label: 'Team dashboard', to: '/workforce/team' }],
    keywords: 'team attendance present absent late manager',
  },
  // --- Administrators -------------------------------------------------------
  {
    slug: 'getting-started', type: 'guide', roles: ['admin'],
    title: 'Getting started: setting up your workspace',
    summary: 'The eight steps that make the workspace ready for your staff.',
    steps: [
      'Open Getting started to see each step and whether it’s done. Steps tick themselves as you complete them.',
      'Upload your logo, add your employees, set your attendance rules and department heads.',
      'Then invite your team, create a first task and approve a first leave request to see the whole flow.',
    ],
    links: [{ label: 'Getting started', to: '/getting-started' }],
    keywords: 'setup onboarding checklist first steps new',
  },
  {
    slug: 'add-user', type: 'tutorial', roles: ['admin'],
    title: 'Add an employee and give them their sign-in',
    summary: 'Create an account; they choose their own password at first sign-in.',
    steps: [
      'Open Administration → Users → Add user.',
      'Enter their name, email, role and department. Leave the password blank to generate one.',
      'Share the email and temporary password shown. They must change it the first time they sign in.',
      'If they forget it later, they can use “Forgot your password?” on the sign-in page — or you can reset it from the same list.',
    ],
    links: [{ label: 'Users', to: '/admin/users' }],
    keywords: 'add user employee staff account invite create',
  },
  {
    slug: 'departments', type: 'tutorial', roles: ['admin'],
    title: 'Set up departments and their heads',
    summary: 'Department heads approve leave and corrections for their people.',
    steps: [
      'Open Settings → Workspace setup → Departments.',
      'Create or rename departments, and choose a head for each.',
      'Put each employee in a department from their profile in Users.',
    ],
    links: [{ label: 'Departments', to: '/admin/leaves/departments' }],
    keywords: 'department head team structure manager',
  },
  {
    slug: 'attendance-rules', type: 'tutorial', roles: ['admin', 'approver'],
    title: 'Change office hours and late rules',
    summary: 'When people are expected in, and when a day counts as late or half.',
    steps: [
      'Open Settings → Workspace setup → Attendance rules.',
      'Set when the office starts, when a check-in counts as late or a half day, and the hours in a full day.',
      'Save. The rules apply to everyone without a more specific policy.',
    ],
    links: [{ label: 'Attendance rules', to: '/admin/attendance/policies' }],
    keywords: 'office hours late grace half day overtime shift policy',
  },
  {
    slug: 'branding', type: 'tutorial', roles: ['admin'],
    title: 'Add your logo and colours',
    summary: 'Your brand on the sign-in page, every screen, emails and documents.',
    steps: [
      'Open Settings → Branding.',
      'Upload your logo and choose your colours. Add a welcome line for your staff if you like.',
      'Changes appear for everyone straight away.',
    ],
    links: [{ label: 'Branding', to: '/settings/branding' }],
    keywords: 'logo colour color brand theme',
  },
  {
    slug: 'subscription', type: 'tutorial', roles: ['admin'],
    title: 'Renew or upgrade, and send a payment receipt',
    summary: 'Choose a plan, pay, upload the receipt — we confirm it.',
    steps: [
      'Open Settings → Subscription & billing and choose a plan. Your access doesn’t change while we confirm.',
      'Pay using one of the methods shown, quoting the reference.',
      'Upload a screenshot of the receipt with the transaction ID. You’ll be notified when it’s confirmed.',
    ],
    links: [{ label: 'Subscription', to: '/settings/subscription' }],
    keywords: 'pay payment renew upgrade plan billing invoice receipt esewa khalti bank',
  },
  // --- FAQs ---------------------------------------------------------------
  {
    slug: 'forgot-password', type: 'faq', roles: ['all'],
    title: 'I forgot my password',
    summary: 'Use “Forgot your password?” on the sign-in page.',
    steps: [
      'On the sign-in page, choose “Forgot your password?” and enter your work email.',
      'Open the email and choose a new password. The link works for an hour, once.',
      'No email? Check spam, then ask your administrator — they can reset it for you.',
    ],
    links: [],
    keywords: 'password reset forgot locked out cannot sign in login',
  },
  {
    slug: 'change-password', type: 'faq', roles: ['all'],
    title: 'How do I change my password?',
    summary: 'From your avatar → Security.',
    steps: ['Open your avatar at the top right → Security, then enter your current and new password.'],
    links: [{ label: 'Security', to: '/profile#security' }],
    keywords: 'change password security',
  },
  {
    slug: 'checkin-disabled', type: 'faq', roles: ['all'],
    title: 'Why can’t I check in?',
    summary: 'Usually location, a holiday, or biometric-only attendance.',
    steps: [
      'Location: allow your browser to use your location, then try again. On a desktop without GPS, check in from your phone.',
      'Holiday or leave: there’s nothing to check in to — Home says so.',
      'Biometric only: if Home says your attendance is recorded at the office device, use the device.',
    ],
    links: [{ label: 'Fix a missed check-in', to: '/help/correction' }],
    keywords: 'check in disabled grey location permission biometric device',
  },
  {
    slug: 'late-half-day', type: 'faq', roles: ['all'],
    title: 'Why was I marked late or half day?',
    summary: 'Your check-in time compared with your office’s attendance rules.',
    steps: [
      'Your organization sets when a check-in counts as late or as a half day.',
      'If the time recorded is wrong, ask for a correction.',
    ],
    links: [{ label: 'Ask for a correction', to: '/workforce/corrections' }],
    keywords: 'late half day absent marked wrong',
  },
  {
    slug: 'no-access', type: 'faq', roles: ['all'],
    title: 'It says I don’t have access',
    summary: 'Some pages are for managers, HR or administrators.',
    steps: ['What you can open depends on your role. If you need a page for your work, ask your administrator.'],
    links: [],
    keywords: 'access denied permission unauthorized forbidden role',
  },
  {
    slug: 'other-devices', type: 'faq', roles: ['all'],
    title: 'How do I sign out of other devices?',
    summary: 'Avatar → Sessions → Sign out everywhere else.',
    steps: ['Open your avatar → Sessions. You’ll see where you’re signed in, and can end every session but this one.'],
    links: [{ label: 'Sessions', to: '/profile#sessions' }],
    keywords: 'sign out logout devices sessions security',
  },
  {
    slug: 'payment-rejected', type: 'faq', roles: ['admin'],
    title: 'Our payment was rejected or needs more information',
    summary: 'The reason and what to do are on your subscription page.',
    steps: [
      'Open Settings → Subscription & billing. The reason we gave is shown above the receipt form, with the next step.',
      'Upload the receipt again with the details we asked for. Your access isn’t affected while we look.',
    ],
    links: [{ label: 'Subscription', to: '/settings/subscription' }],
    keywords: 'payment rejected declined more information receipt',
  },
  {
    slug: 'roles', type: 'guide', roles: ['all'],
    title: 'What each role can do',
    summary: 'Employee, Department Head, HR, Board and Administrator.',
    steps: [
      'Employee: attendance, leave, tasks and documents of your own.',
      'Department Head: everything an employee does, plus approving your department’s leave, corrections and task reviews.',
      'HR: the second approval on leave, and the organization’s attendance and people records.',
      'Board: reads organization-wide reports and approves department heads’ leave.',
      'Administrator: users, departments, policies, settings and billing.',
    ],
    links: [],
    keywords: 'role permission employee manager hr admin board',
  },
];

export const TYPE_LABEL = { tutorial: 'How to', faq: 'Question', guide: 'Guide' };

/** Articles this role may see. */
export const helpFor = (role) => HELP.filter((a) => a.roles.includes('all') || a.roles.includes(role));

/** Case-insensitive search over title, summary, steps and keywords. */
export const searchHelp = (query, role) => {
  const terms = (query || '').toLowerCase().split(/\s+/).filter(Boolean);
  const pool = helpFor(role);
  if (!terms.length) return pool;
  return pool
    .map((a) => {
      const hay = `${a.title} ${a.summary} ${a.keywords} ${a.steps.join(' ')}`.toLowerCase();
      const title = a.title.toLowerCase();
      if (!terms.every((t) => hay.includes(t))) return null;
      const score = terms.reduce((n, t) => n + (title.includes(t) ? 3 : 1), 0);
      return { a, score };
    })
    .filter(Boolean)
    .sort((x, y) => y.score - x.score)
    .map(({ a }) => a);
};

export const articleBySlug = (slug) => HELP.find((a) => a.slug === slug) || null;
