import React from 'react';
import { Link } from 'react-router-dom';
import {
  CreditCard, Globe, Palette, ScrollText, Tags, CalendarDays, Building2, Clock3, Fingerprint,
} from 'lucide-react';
import PageHeader from '../../components/common/PageHeader';

/**
 * Settings → Organisation. A hub, and it exists for a navigation reason.
 *
 * Phase S8 added "Subscription & billing" to the Administration rail and
 * Phase S9 wanted to add "Branding" and "Custom domain" beside it. That
 * takes one module to ten links, and `navConfig.test.js` holds a density
 * budget with an explicit instruction attached: if another entry is
 * proposed, "the honest fix is to merge or demote something, not to raise
 * this again."
 *
 * So the three merge. They belong together on their own terms as well --
 * every one of them is about the ACCOUNT rather than about the people in
 * it, which is a different question from anything else under
 * Administration, and it is how a customer thinks about them: "our
 * subscription, our logo, our address".
 */
const CARDS = [
  { to: '/settings/subscription', Icon: CreditCard,
    title: 'Subscription & billing',
    body: 'Your plan, what it costs, when it renews, and how to pay.' },
  { to: '/settings/branding', Icon: Palette,
    title: 'Branding',
    body: 'Your logo, colours and wording, across the whole workspace.' },
  { to: '/settings/domains', Icon: Globe,
    title: 'Custom domain',
    body: 'Sign in at your own web address instead of ours.' },
];

// Configuration an administrator touches a few times a year. It lived in the
// Administration rail beside the pages used every day; here it is one click
// from the rail's "Settings" and out of the way the rest of the time.
const SETUP = [
  { to: '/admin/leaves/policies', Icon: ScrollText,
    title: 'Leave policies', body: 'How many days each kind of leave gives, and who qualifies.' },
  { to: '/admin/leaves/leave-types', Icon: Tags,
    title: 'Leave types', body: 'Annual, sick, unpaid and any others you offer.' },
  { to: '/admin/attendance/policies', Icon: Clock3,
    title: 'Attendance rules', body: 'Office hours, grace period, shifts and overtime.' },
  { to: '/settings/attendance', Icon: Fingerprint,
    title: 'Attendance & biometric devices',
    body: 'App or device attendance, and the fingerprint devices you connect.' },
  { to: '/admin/leaves/holidays', Icon: CalendarDays,
    title: 'Holidays & events', body: 'Public holidays and the dates your organization closes.' },
  { to: '/admin/leaves/departments', Icon: Building2,
    title: 'Departments', body: 'Your teams and who heads each one.' },
];

const Hub = ({ cards }) => (
  <div className="br-hub">
    {cards.map(({ to, Icon, title, body }) => (
      <Link className="br-hub-card" to={to} key={to}>
        <span className="br-hub-ic" aria-hidden="true">
          <Icon size={20} strokeWidth={2} />
        </span>
        <span className="br-hub-title">{title}</span>
        <span className="br-hub-body">{body}</span>
      </Link>
    ))}
  </div>
);

const SettingsIndex = () => (
  <div className="page">
    <PageHeader
      title="Settings"
      description="Your account, your brand, and how your workspace is set up."
    />
    <h2 className="br-hub-h">Your account</h2>
    <Hub cards={CARDS} />
    <h2 className="br-hub-h">Workspace setup</h2>
    <Hub cards={SETUP} />
  </div>
);

export default SettingsIndex;
