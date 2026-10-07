import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import PageHeader from '../components/common/PageHeader';
import StatTile from '../components/common/StatTile';
import StatusBadge from '../components/common/StatusBadge';
import EmptyState from '../components/common/EmptyState';
import Skeleton from '../components/common/Skeleton';
import DataTable from '../components/common/DataTable';
import { FormRow, FormActions, FormSection } from '../components/common/Form';
import StatCard from '../components/common/StatCard';
import Badge from '../components/common/Badge';

/**
 * The design system (Phase D).
 *
 * Two halves: the components behave, and the STYLESHEET stays consistent. The
 * second is the one that decays silently - twenty-nine font sizes accumulated
 * one reasonable-looking decision at a time, and only a test notices the
 * thirtieth.
 */

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, '..', 'index.css'), 'utf8');
const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('typography consistency', () => {
  it('defines the seven-size scale as tokens', () => {
    for (const t of ['--fs-display', '--fs-h1', '--fs-h2', '--fs-body', '--fs-sm', '--fs-meta', '--fs-label']) {
      expect(css, `${t} missing`).toContain(`${t}:`);
    }
  });

  it('defines the 4px spacing scale as tokens', () => {
    for (const t of ['--sp-1', '--sp-2', '--sp-3', '--sp-4', '--sp-6', '--sp-8', '--sp-12']) {
      expect(css, `${t} missing`).toContain(`${t}:`);
    }
  });

  it('uses ONLY scale tokens for font-size in the ui- layer', () => {
    // New components must not reintroduce literals. Older per-module classes
    // keep theirs until their file is next edited - that is the migration
    // policy, and this test is what stops the new layer joining them.
    //
    // The namespace is `ui-`, not `ds-`: `ds-` was already Department
    // Statistics, and the first draft of this layer silently restyled the admin
    // dashboard. This test is what caught it.
    const dsRules = [...css.matchAll(/\.ui-[^{]*\{([^}]*)\}/g)].map((m) => m[1]);
    const literals = dsRules
      .flatMap((body) => [...body.matchAll(/font-size: *([0-9.]+px)/g)].map((m) => m[1]));
    expect(literals).toEqual([]);
  });

  it('has retired Playfair Display from every statistic numeral', () => {
    // Headings keep it; figures do not. A display serif in a stat tile was the
    // clearest "styled ad hoc" signal in the old UI.
    const numeralSelectors = [
      '.s-num, .bc-used', '.lr-bc-available-num', '.lr-side-stat b', '.lr-kpi-value',
      '.memo-tile-value', '.memo-card-tile-value', '.att-stat-val', '.hm-sum-n',
    ];
    for (const sel of numeralSelectors) {
      const rule = new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`);
      const body = css.match(rule)?.[1] ?? '';
      expect(body, `${sel} uses a decorative face`).not.toMatch(/Playfair Display|Poppins/);
      expect(body, `${sel} needs tabular-nums`).toContain('tabular-nums');
    }
  });

  it('sizes every font from the scale, in CSS and in components alike', () => {
    // ADDED (Phase PRODUCT-V1.0). "Zero off-scale sizes" was reported twice
    // and was twice true only of index.css — 22 off-scale values were sitting
    // in inline style objects in JSX the whole time, exactly as Playfair had
    // been. Same blind spot, same class of wrong claim.
    //
    // So this reads BOTH. A literal pixel font size anywhere is a failure now,
    // on scale or not: the scale is only enforceable if the tokens are the
    // only way to set type.
    const root = join(dirname(fileURLToPath(import.meta.url)), '..');
    const offenders = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === 'node_modules') continue;
        const full = join(dir, entry.name);
        if (entry.isDirectory()) { walk(full); continue; }
        if (!/\.jsx?$/.test(entry.name) || entry.name.includes('.test.')) continue;
        const body = readFileSync(full, 'utf8');
        const hits = body.match(/fontSize: *'?[0-9.]+/g);
        if (hits) offenders.push(`${full.slice(root.length + 1)}: ${hits.join(', ')}`);
      }
    };
    walk(root);
    expect(offenders).toEqual([]);

    // And the stylesheet, minus the :root block that defines the tokens.
    const afterRoot = css.slice(css.indexOf('}', css.indexOf(':root {')));
    expect(afterRoot.match(/font-size: *[0-9.]+px/g)).toBeNull();
  });

  it('has retired every decorative face from the component sources too', () => {
    // ADDED (Phase UI-PRODUCTION-LAST-MILE), because the guard below reads
    // index.css and nothing else — and Playfair Display survived in twelve
    // INLINE style objects across the leave module for several phases while
    // that guard reported clean. Removing the family from the @import then
    // made it worse rather than better: those declarations stopped resolving
    // to Playfair and started resolving to the system serif, so headings that
    // had looked deliberate started looking like a fallback.
    const root = join(dirname(fileURLToPath(import.meta.url)), '..');
    const offenders = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === 'node_modules') continue;
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.jsx?$/.test(entry.name) && !entry.name.includes('.test.')
                 && /Playfair Display|Poppins/.test(readFileSync(full, 'utf8'))) {
          offenders.push(full.slice(root.length + 1));
        }
      }
    };
    walk(root);
    expect(offenders).toEqual([]);
  });

  it('has retired every decorative face, product-wide', () => {
    // This guard used to assert the OPPOSITE — that headings kept Playfair
    // Display, "a deliberate choice not an oversight". Phase
    // UX-PRODUCTION-POLISH reversed the decision: a display serif on page
    // titles plus Poppins on the auth screens gave one product three voices,
    // and two of them read as a template rather than a house style.
    //
    // Turned around rather than deleted, because the failure it guards against
    // is the same one in both directions: a face creeping in on one screen and
    // nowhere else.
    expect(css).not.toContain("'Playfair Display'");
    expect(css).not.toContain('"Playfair Display"');
    expect(css).not.toContain("'Poppins'");
    // And the families are not re-imported behind the product's back.
    const imported = css.match(/@import url\('([^']+)'\)/)?.[1] ?? '';
    expect(imported).not.toMatch(/Playfair Display|Poppins/);
    expect(imported).toMatch(/Inter/);
    expect(imported).toMatch(/Noto\+Sans\+Devanagari/);
  });

  it('routes every family through one token', () => {
    // One declaration to change, so a fourth face cannot be introduced by
    // editing a single component's stylesheet.
    expect(css).toMatch(/--font-sans:[^;]*Inter/);
    expect(css).toMatch(/--font-deva:[^;]*Noto Sans Devanagari/);
  });

  it('darkens --text-muted to clear 4.5:1 on BOTH grounds it sits on', () => {
    // The blueprint proposed #64748b, which passes on white (4.76) and fails on
    // --bg-main #f0f4f8 (4.31) - and muted text sits on that ground constantly.
    expect(css).not.toMatch(/--text-muted: #94a3b8/);
    expect(css).not.toMatch(/--text-muted: #64748b/);
    const value = css.match(/--text-muted: (#[0-9a-f]{6})/i)?.[1];
    expect(value).toBeTruthy();

    const lum = (h) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
      const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const ratio = (a, b) => {
      const [x, y] = [lum(a), lum(b)];
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    expect(ratio(value, '#ffffff')).toBeGreaterThanOrEqual(4.5);
    expect(ratio(value, '#f0f4f8')).toBeGreaterThanOrEqual(4.5);
  });
});

describe('StatTile — every number is a door', () => {
  it('renders as a link when it has a destination', () => {
    wrap(<StatTile value={3} label="Approvals waiting" to="/queue" />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/queue');
    expect(link).toHaveTextContent('3');
    expect(link).toHaveTextContent('Open →');
  });

  it('renders as a plain figure — with no false affordance — when it has none', () => {
    const { container } = wrap(<StatTile value={0} label="Nothing" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(container.querySelector('.ui-tile.is-static')).toBeInTheDocument();
  });

  it('carries a tone for urgency', () => {
    const { container } = wrap(<StatTile value={2} label="Overdue" to="/queue" tone="alert" />);
    expect(container.querySelector('.ui-tile.is-alert')).toBeInTheDocument();
  });
});

describe('StatusBadge — colour is never the only signal', () => {
  it.each([
    ['draft', 'Draft'], ['pending', 'Pending'], ['approved', 'Approved'],
    ['rejected', 'Rejected'], ['acknowledged', 'Acknowledged'],
    ['archived', 'Archived'], ['overdue', 'Overdue'],
  ])('renders %s with the word "%s"', (status, word) => {
    wrap(<StatusBadge status={status} />);
    expect(screen.getByText(word)).toBeInTheDocument();
  });

  it('normalises casing and separators', () => {
    wrap(<StatusBadge status="Under-Review" />);
    expect(screen.getByText('In review')).toBeInTheDocument();
  });

  it('falls back readably for a status it does not know', () => {
    // The old Badge fell through to the raw value, so a new workflow state
    // rendered as `pending_acknowledgement` on screen.
    wrap(<StatusBadge status="some_new_state" />);
    expect(screen.getByText('some_new_state')).toBeInTheDocument();
  });

  it('keeps record type in a separate family from status', () => {
    const { container } = wrap(<StatusBadge type="memo" />);
    expect(container.querySelector('.ui-type-memo')).toBeInTheDocument();
    // A type must never carry a status class, or a violet MEMO reads as a state.
    expect(container.querySelector('.ui-badge')).toBeNull();
  });
});

describe('EmptyState — an absence with a reason', () => {
  it('says why it is empty and offers the way on', () => {
    wrap(<EmptyState variant="first" title="No memos yet" body="Create one and it appears here."
      action={{ to: '/memos/create', label: 'Create memo' }} />);
    expect(screen.getByRole('heading', { name: 'No memos yet' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Create memo' })).toHaveAttribute('href', '/memos/create');
  });

  it('offers a retry callback for the error variant', () => {
    const onAction = vi.fn();
    wrap(<EmptyState variant="error" title="Could not load" onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onAction).toHaveBeenCalled();
  });

  it('distinguishes cleared from never-had-any', () => {
    const { container, unmount } = wrap(<EmptyState variant="cleared" title="All done" />);
    expect(container.querySelector('.ui-empty-ic.is-cleared')).toBeInTheDocument();
    unmount();
    const second = wrap(<EmptyState variant="first" title="Nothing yet" />);
    expect(second.container.querySelector('.ui-empty-ic.is-first')).toBeInTheDocument();
  });
});

describe('Skeleton — replaces the "Loading…" strings', () => {
  it('hides the placeholder bars from assistive tech and announces once', () => {
    const { container } = wrap(<Skeleton rows={4} />);
    expect(container.querySelectorAll('.ui-sk')).toHaveLength(4);
    expect(container.querySelector('.ui-sk-wrap')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Loading');
  });
});

describe('DataTable', () => {
  const columns = [
    { key: 'subject', header: 'Subject' },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
  ];

  it('renders headers as scoped column headers', () => {
    wrap(<DataTable columns={columns} rows={[{ id: 1, subject: 'Budget', status: 'approved' }]} />);
    expect(screen.getByRole('columnheader', { name: 'Subject' })).toHaveAttribute('scope', 'col');
    expect(screen.getByText('Budget')).toBeInTheDocument();
    expect(screen.getByText('Approved')).toBeInTheDocument();
  });

  it('handles its own loading state so callers cannot forget to', () => {
    const { container } = wrap(<DataTable columns={columns} rows={[]} loading />);
    expect(container.querySelector('.ui-sk')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('handles its own empty state', () => {
    wrap(<DataTable columns={columns} rows={[]} empty={{ variant: 'cleared', title: 'Nothing here' }} />);
    expect(screen.getByRole('heading', { name: 'Nothing here' })).toBeInTheDocument();
  });

  it('scrolls inside itself rather than pushing the page sideways', () => {
    const { container } = wrap(<DataTable columns={columns} rows={[{ id: 1 }]} />);
    expect(container.querySelector('.ui-table-wrap')).toBeInTheDocument();
  });
});

describe('Form — the label wiring callers get wrong', () => {
  it('binds label, hint and error to the control', () => {
    wrap(
      <FormRow label="Subject" hint="Keep it short" error="Subject is required" required>
        {(props) => <input {...props} />}
      </FormRow>,
    );
    const input = screen.getByLabelText(/Subject/);
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const describedBy = input.getAttribute('aria-describedby').split(' ');
    const described = describedBy.map((id) => document.getElementById(id).textContent);
    expect(described).toContain('Keep it short');
    expect(described).toContain('Subject is required');
    expect(screen.getByRole('alert')).toHaveTextContent('Subject is required');
  });

  it('marks a required field for assistive tech, not only with an asterisk', () => {
    wrap(<FormRow label="Subject" required>{(props) => <input {...props} />}</FormRow>);
    expect(screen.getByLabelText(/required/)).toBeInTheDocument();
  });

  it('accepts plain children for simple cases', () => {
    wrap(<FormRow label="Notes" htmlFor="notes"><textarea id="notes" /></FormRow>);
    expect(screen.getByLabelText('Notes')).toBeInTheDocument();
  });

  it('renders sections and actions', () => {
    const { container } = wrap(
      <FormSection title="Details" description="About the memo">
        <FormActions><button type="button">Save</button></FormActions>
      </FormSection>,
    );
    expect(screen.getByRole('heading', { name: 'Details' })).toBeInTheDocument();
    expect(container.querySelector('.ui-factions.is-end')).toBeInTheDocument();
  });
});

describe('deprecated adapters keep their call sites working', () => {
  it('StatCard renders through StatTile', () => {
    const { container } = wrap(<StatCard title="Pending" value={4} subtitle="this week" />);
    expect(container.querySelector('.ui-tile')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
  });

  it('Badge renders through StatusBadge, inheriting the fuller status map', () => {
    const { container } = wrap(<Badge status="archived" />);
    expect(container.querySelector('.ui-badge')).toBeInTheDocument();
    expect(screen.getByText('Archived')).toBeInTheDocument();
  });
});
