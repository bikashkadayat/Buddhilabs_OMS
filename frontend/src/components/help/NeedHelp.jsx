import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LifeBuoy } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { categoryOf, helpFor } from '../../services/helpContent';

/**
 * "Need help?" on a major page: the two or three articles for this topic,
 * and one button that opens a ticket with the right category and THIS page
 * attached -- so "it doesn't work" arrives already saying where.
 *
 * `topic` is a Knowledge Base category; `category` is the ticket type.
 */
const NeedHelp = ({ topic, category = 'other', limit = 3 }) => {
  const { role } = useAuth();
  const { pathname } = useLocation();
  const articles = helpFor(role).filter((a) => categoryOf(a.slug) === topic).slice(0, limit);
  const ticket = `/help/contact?category=${category}&from=${encodeURIComponent(pathname)}`;
  return (
    <aside className="nh" aria-label="Need help?">
      <LifeBuoy size={16} aria-hidden="true" className="nh-ic" />
      <span className="nh-q">Need help?</span>
      {articles.map((a) => (
        <Link key={a.slug} className="nh-link" to={`/help/${a.slug}`}>{a.title}</Link>
      ))}
      <Link className="nh-cta" to={ticket}>Open support</Link>
    </aside>
  );
};

/**
 * Which pages get the box, and with what. Longest prefix wins. Kept here,
 * beside the component, so "every major page has Need help?" is one list.
 */
const PAGES = [
  ['/my-attendance', 'attendance', 'attendance'],
  ['/attendance', 'attendance', 'attendance'],
  ['/settings/attendance', 'attendance', 'attendance'],
  ['/admin/attendance', 'attendance', 'attendance'],
  ['/workforce', 'attendance', 'attendance'],
  ['/leave', 'leave', 'leave'],
  ['/leaves', 'leave', 'leave'],
  ['/tasks', 'tasks', 'task'],
  ['/settings/subscription', 'billing', 'billing'],
  ['/settings/domains', 'domains', 'domain'],
  ['/memos', 'documents', 'other'],
  ['/minutes', 'documents', 'other'],
  ['/circulars', 'documents', 'other'],
  ['/documents', 'documents', 'other'],
];

export const pageHelp = (pathname = '') => { // eslint-disable-line react-refresh/only-export-components
  const hit = PAGES.filter(([p]) => pathname === p || pathname.startsWith(`${p}/`) || pathname.startsWith(p))
    .sort((a, b) => b[0].length - a[0].length)[0];
  return hit ? { topic: hit[1], category: hit[2] } : null;
};

/** Rendered once by the workspace Layout, under every major page. */
export const PageNeedHelp = () => {
  const { pathname } = useLocation();
  const match = pageHelp(pathname);
  return match ? <NeedHelp topic={match.topic} category={match.category} /> : null;
};

export default NeedHelp;
