import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import SignatureCards from './SignatureCards';

/**
 * Phase 26 redesigned this section from profile cards to certification blocks, so
 * the assertions moved with it: the avatar-initials and status-line expectations
 * are gone (neither is rendered any more) and the stamp band is asserted instead.
 */
const block = (overrides) => ({
  key: 'recommender', heading: 'Recommended By', name: 'Sunita DeptHead',
  designation: 'Head of ICT', department: 'ICT Department',
  at: '2026-08-12T09:30:00Z', status_label: 'Approved', stamp: 'Recommended',
  state: 'done', remarks: '', initials: 'SD', verified: true,
  verification_id: 'E5D7-3A09-6D2B', ...overrides,
});

const BLOCKS = [
  block({ key: 'created', heading: 'Created By', name: 'Deepak Employee',
    designation: 'Programme Officer', status_label: 'Created', stamp: 'Created',
    initials: 'DE', verification_id: 'D3F1-CA05-03EC' }),
  block({ remarks: 'Recommended for approval.' }),
  block({ key: 'supporter', heading: 'Supported By', name: 'Manoj Support',
    state: 'active', status_label: 'Awaiting Action', stamp: 'Awaiting Action',
    at: null, verified: false, initials: 'MS', verification_id: '' }),
  block({ key: 'approver', heading: 'Approved By', name: 'Rajesh HR',
    state: 'pending', status_label: 'Pending', stamp: 'Pending', at: null,
    verified: false, initials: 'RH', verification_id: '' }),
];

describe('SignatureCards', () => {
  it('renders one block per entry, in hierarchy order', () => {
    render(<SignatureCards blocks={BLOCKS} />);
    const headings = screen.getAllByText(/By$/).map((n) => n.textContent);
    expect(headings).toEqual(['Created By', 'Recommended By', 'Supported By', 'Approved By']);
  });

  it('shows name, designation, department and date on each block', () => {
    render(<SignatureCards blocks={[BLOCKS[1]]} />);
    expect(screen.getByText('Sunita DeptHead')).toBeInTheDocument();
    expect(screen.getByText('Head of ICT')).toBeInTheDocument();
    // One block -> one column, so there is room for the department line.
    expect(screen.getByText('ICT Department')).toBeInTheDocument();
  });

  /**
   * Phase 30. Three steps must sit in one row of three, four in one row of four,
   * and five or more wrap without leaving a row holding a lone block. The column
   * count drives a CSS custom property, so it is asserted there.
   */
  it.each([[1, 1], [2, 2], [3, 3], [4, 4], [5, 3], [6, 3], [7, 4], [8, 4], [9, 3]])(
    'lays %i blocks out in %i columns', (count, columns) => {
      const many = Array.from({ length: count }, (_, i) => block({ key: `k${i}` }));
      const { container } = render(<SignatureCards blocks={many} />);
      expect(container.querySelector('.memo-cert-grid').style.getPropertyValue('--cert-cols'))
        .toBe(String(columns));
    },
  );

  it('drops the department line once the row is four across', () => {
    // "Department only if space permits" - four columns is too narrow for it.
    const four = Array.from({ length: 4 }, (_, i) => block({ key: `k${i}` }));
    render(<SignatureCards blocks={four} />);
    expect(screen.queryByText('ICT Department')).toBeNull();
    expect(screen.getAllByText('Head of ICT')).toHaveLength(4);
  });

  it('states the authorisation wording once, not under every block', () => {
    const { container } = render(<SignatureCards blocks={BLOCKS} />);
    expect(screen.getAllByText(/no wet signature is required/)).toHaveLength(1);
    // Two of the four blocks are signed, so two compact marks — and the wording
    // itself appears only in the footnote, never inside a block. The mark is a
    // Lucide icon rather than a typed ✓ (Phase UI-PRODUCTION-V1), so this
    // matches the word, which is what a reader and a screen reader both get.
    // Counted as ELEMENTS, not by text: the footnote now says "Blocks marked
    // Verified", so a text match finds three and the badge count is what this
    // test is actually about.
    expect(container.querySelectorAll('.memo-cert-verify')).toHaveLength(2);
  });

  it('omits the footnote entirely when nothing is signed yet', () => {
    render(<SignatureCards blocks={[BLOCKS[2], BLOCKS[3]]} />);
    expect(screen.queryByText(/no wet signature is required/)).toBeNull();
  });

  /**
   * The point of the redesign. A signed recommender's stamp must read
   * RECOMMENDED — its status_label is "Approved", which is right for a status
   * column and wrong for a stamp, and rendering the latter was the defect.
   */
  it('stamps each block with its own certification word, not "Approved" for all', () => {
    const { container } = render(<SignatureCards blocks={BLOCKS} />);
    const stamps = [...container.querySelectorAll('.memo-cert-stamp')].map((n) => n.textContent);
    expect(stamps).toEqual(['Created', 'Recommended', 'Awaiting Action', 'Pending']);
  });

  it('draws no avatar circles', () => {
    const { container } = render(<SignatureCards blocks={BLOCKS} />);
    expect(container.querySelector('.memo-sig-avatar')).toBeNull();
    // The initials are still in the payload; they must not be rendered.
    expect(screen.queryByText('SD')).toBeNull();
  });

  it('shows the verification badge and ID only where the person actually signed', () => {
    render(<SignatureCards blocks={BLOCKS} />);
    expect(screen.getAllByText('Not yet signed')).toHaveLength(2);
    expect(screen.getByText('D3F1-CA05-03EC')).toBeInTheDocument();
    expect(screen.getByText('E5D7-3A09-6D2B')).toBeInTheDocument();
    // The two unsigned blocks carry no ID at all.
    expect(document.querySelectorAll('.memo-cert-vid')).toHaveLength(2);
  });

  it('renders remarks when the signer left them', () => {
    render(<SignatureCards blocks={BLOCKS} />);
    expect(screen.getByText('“Recommended for approval.”')).toBeInTheDocument();
  });

  it('shows a dash rather than an invented date for an unsigned block', () => {
    render(<SignatureCards blocks={[BLOCKS[3]]} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('tones each block by state', () => {
    const { container } = render(<SignatureCards blocks={BLOCKS} />);
    const cards = container.querySelectorAll('.memo-cert');
    expect(cards[0].className).toContain('is-done');
    expect(cards[2].className).toContain('is-pending');
    expect(cards[3].className).toContain('is-pending');
  });

  it('marks a rejection distinctly and withholds verification', () => {
    render(<SignatureCards blocks={[block({
      state: 'rejected', status_label: 'Rejected', stamp: 'Rejected',
      verified: false, verification_id: '',
    })]} />);
    expect(document.querySelector('.memo-cert').className).toContain('is-rejected');
    expect(screen.getByText('Rejected')).toBeInTheDocument();
    expect(screen.getByText('Not yet signed')).toBeInTheDocument();
  });

  it('falls back to the status label if a block predates the stamp field', () => {
    // Backward compatibility: a cached payload from before Phase 26 has no
    // `stamp`, and an empty stamp band would look like a rendering failure.
    render(<SignatureCards blocks={[block({ stamp: undefined })]} />);
    expect(document.querySelector('.memo-cert-stamp').textContent).toBe('Approved');
  });

  it('renders nothing when there are no blocks', () => {
    const { container } = render(<SignatureCards blocks={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
