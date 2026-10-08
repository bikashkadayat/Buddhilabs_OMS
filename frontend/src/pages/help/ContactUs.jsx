import React from 'react';
import { Link } from 'react-router-dom';
import { LifeBuoy, ListChecks, BookOpen, Activity, Lightbulb } from 'lucide-react';

import { PLATFORM_NAME } from '../../config/platform';

/**
 * Contact Buddhi Labs. Deliberately no phone numbers or personal WhatsApp:
 * every request goes through a ticket, so it is tracked, answered by whoever
 * is on duty, and never lost in someone's personal messages.
 */
const ROUTES = [
  { to: '/help/contact', Icon: LifeBuoy, title: 'Create a ticket', body: 'Problems, questions, billing, domains — anything. The fastest way to reach us.' },
  { to: '/help/tickets', Icon: ListChecks, title: 'Follow a ticket', body: 'See our replies and answer them in one place.' },
  { to: '/help/contact?category=feature_request', Icon: Lightbulb, title: 'Suggest a feature', body: 'Tell us what would make your work easier.' },
  { to: '/help', Icon: BookOpen, title: 'Search the Knowledge Base', body: 'Most questions are answered in under a minute.' },
  { to: '/help/status', Icon: Activity, title: 'Check system status', body: 'See whether a problem is ours before you report it.' },
];

const ContactUs = () => (
  <div className="page hc sc">
    <h1 className="hc-h1">Contact {PLATFORM_NAME}</h1>
    <p className="hc-muted">
      Everything goes through the Support Center, so nothing gets lost and you always know where your request is.
      We reply within our support targets — critical issues within 4 hours.
    </p>
    <div className="br-hub">
      {ROUTES.map(({ to, Icon, title, body }) => (
        <Link className="br-hub-card" to={to} key={title}>
          <span className="br-hub-ic" aria-hidden="true"><Icon size={20} strokeWidth={2} /></span>
          <span className="br-hub-title">{title}</span>
          <span className="br-hub-body">{body}</span>
        </Link>
      ))}
    </div>
    <dl className="sc-targets">
      <dt>Critical</dt><dd>4 hours</dd>
      <dt>High</dt><dd>8 hours</dd>
      <dt>Medium</dt><dd>24 hours</dd>
      <dt>Low</dt><dd>72 hours</dd>
    </dl>
    <p className="hc-muted">Targets are for resolution and pause while we’re waiting for your reply.</p>
  </div>
);

export default ContactUs;
