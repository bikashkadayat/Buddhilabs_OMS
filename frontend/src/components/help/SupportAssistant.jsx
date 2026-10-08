import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Sparkles, BookOpen, History, AlertTriangle, CheckCircle2, Lightbulb, Wrench } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { rankHelp } from '../../services/helpContent';
import { supportService } from '../../services/supportService';

/**
 * "Before you send": the in-app support assistant.
 *
 * As the customer describes the problem it looks in four places -- the
 * Knowledge Base and FAQ (here in the browser), known issues the platform
 * team has written up for everyone (with the workaround, and how many
 * customers it was resolved for), the customer's own past tickets and the
 * fixes we gave, and any live incident -- and leads with the single most
 * likely answer. It never writes an answer of its own: every suggestion is
 * an article, a written-up workaround, a real earlier reply, or a notice.
 *
 * Measured: "shown" is counted once per draft and "That solved it" once,
 * so the console reports a deflection RATE, not just a count.
 */
const SupportAssistant = ({ subject, message, category, onSolved }) => {
  const { role } = useAuth();
  const [found, setFound] = useState({ similar_tickets: [], known_issues: [], known_solutions: [], possible_solution: null });
  const shown = useRef(false);
  const text = `${subject} ${message}`.trim();

  const articles = useMemo(() => (text.length < 4 ? [] : rankHelp(text, role)), [text, role]);

  useEffect(() => {
    if (text.length < 8) return undefined;
    const timer = setTimeout(() => {
      supportService.assist(text, category).then((d) => setFound({
        similar_tickets: [], known_issues: [], known_solutions: [], possible_solution: null, ...d,
      })).catch(() => {});
    }, 500);
    return () => clearTimeout(timer);
  }, [text, category]);

  const long = text.length >= 8;
  const tickets = long ? found.similar_tickets : [];
  const solutions = long ? found.known_solutions || [] : [];
  const issues = found.known_issues;
  // The answer to try first: the server's (a known issue's workaround or a
  // fix from this customer's own history), else the best article.
  const best = (long && found.possible_solution) || (articles[0] ? {
    source: 'article', id: articles[0].slug, title: articles[0].title, text: articles[0].summary,
  } : null);
  const anything = articles.length || tickets.length || issues.length || solutions.length;

  useEffect(() => {
    if (anything && !shown.current) {
      shown.current = true;
      supportService.assistShown();
    }
  }, [anything]);

  if (!anything) return null;
  const sourceOf = (b) => (b.source === 'known_issue' ? `issue:${b.id}` : `${b.source}:${b.id}`);

  return (
    <aside className="sa" aria-label="Suggestions before you send">
      <p className="sa-head"><Sparkles size={15} aria-hidden="true" /> Before you send — these may solve it:</p>
      {issues.map((i) => (
        <p key={i.message} className="sa-issue" role="status">
          <AlertTriangle size={14} aria-hidden="true" /> <strong>Known issue · {i.component}:</strong> {i.message}
        </p>
      ))}
      {best && (
        <div className="sa-best">
          <p className="sa-best-head"><Lightbulb size={14} aria-hidden="true" /> Possible solution</p>
          <p className="sa-best-title">
            {best.source === 'article' ? <Link to={`/help/${best.id}`}>{best.title}</Link>
              : best.source === 'ticket' ? <Link to={`/help/tickets/${best.id}`}>{best.title}</Link> : <strong>{best.title}</strong>}
          </p>
          {best.text && <p className="sa-best-text">{best.text}</p>}
        </div>
      )}
      {solutions.filter((x) => !(best?.source === 'known_issue' && best.id === x.id)).length > 0 && (
        <ul className="sa-list" aria-label="Resolved similar cases">
          {solutions.filter((x) => !(best?.source === 'known_issue' && best.id === x.id)).map((x) => (
            <li key={x.id}>
              <Wrench size={14} aria-hidden="true" /> <strong>{x.title}</strong>
              <small className="hc-muted"> — {x.state_display}{x.resolved_for ? ` · resolved for ${x.resolved_for} customer${x.resolved_for === 1 ? '' : 's'}` : ''}</small>
              {x.workaround && <blockquote className="sa-answer">{x.workaround}</blockquote>}
            </li>
          ))}
        </ul>
      )}
      {best?.source === 'known_issue' && (solutions[0]?.resolved_for || 0) > 0 && (
        <p className="hc-muted sa-note">The same problem was resolved for {solutions[0].resolved_for} other customer{solutions[0].resolved_for === 1 ? '' : 's'}.</p>
      )}
      {articles.filter((a) => !(best?.source === 'article' && best.id === a.slug)).length > 0 && (
        <ul className="sa-list" aria-label="Related articles">
          {articles.filter((a) => !(best?.source === 'article' && best.id === a.slug)).map((a) => (
            <li key={a.slug}><BookOpen size={14} aria-hidden="true" /> <Link to={`/help/${a.slug}`}>{a.title}</Link>
              <small className="hc-muted"> — {a.summary}</small></li>
          ))}
        </ul>
      )}
      {tickets.length > 0 && (
        <ul className="sa-list" aria-label="Your earlier tickets">
          {tickets.map((t) => (
            <li key={t.id}>
              <History size={14} aria-hidden="true" />{' '}
              <Link to={`/help/tickets/${t.id}`}>{t.reference}: {t.subject || 'An earlier ticket'}</Link>
              <small className="hc-muted"> — {t.is_open ? `still ${t.status_display.toLowerCase()}; follow it instead of opening another` : t.status_display}</small>
              {t.answer && !(best?.source === 'ticket' && best.id === t.id) && <blockquote className="sa-answer">What fixed it: {t.answer}</blockquote>}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onSolved(best ? sourceOf(best) : 'create-ticket')}>
        <CheckCircle2 size={14} aria-hidden="true" /> That solved it — no ticket needed
      </button>
    </aside>
  );
};

export default SupportAssistant;
