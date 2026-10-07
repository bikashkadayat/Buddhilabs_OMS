import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import SaveIndicator from './SaveIndicator';
import DraftRecoveryDialog from './DraftRecoveryDialog';
import { STATUS } from '../../hooks/useDocumentDraft';

describe('SaveIndicator (Phase 111.8)', () => {
  it.each([
    [STATUS.SAVING, 'Saving…'],
    [STATUS.SAVED, 'Saved'],
    [STATUS.OFFLINE, 'Offline draft'],
    [STATUS.LOCAL_ONLY, 'Local only'],
  ])('renders %s as "%s"', (status, label) => {
    render(<SaveIndicator status={status} />);
    expect(screen.getByRole('status')).toHaveTextContent(label);
  });

  it('shows nothing before the first save', () => {
    /* "Saved" on an untouched form would be a lie. */
    const { container } = render(<SaveIndicator status={STATUS.IDLE} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says how long ago the last save was', () => {
    render(<SaveIndicator status={STATUS.SAVED}
      lastSavedAt={new Date(Date.now() - 2 * 60 * 1000)} />);
    expect(screen.getByRole('status')).toHaveTextContent('2 minutes ago');
  });

  it('does not show a stale timestamp while a save is in flight', () => {
    render(<SaveIndicator status={STATUS.SAVING}
      lastSavedAt={new Date(Date.now() - 5 * 60 * 1000)} />);
    expect(screen.getByRole('status')).not.toHaveTextContent('ago');
  });

  it('announces changes politely rather than interrupting', () => {
    render(<SaveIndicator status={STATUS.SAVED} />);
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('never renders a document workflow status', () => {
    /* The approved separation: this reports storage, not approval stage. */
    const { container } = render(<SaveIndicator status={STATUS.SAVED} />);
    expect(container.textContent.toLowerCase()).not.toMatch(
      /draft|submitted|approved|archived|under review/,
    );
  });
});

describe('DraftRecoveryDialog (Phase 111.5)', () => {
  const draft = {
    payload: {
      form: { subject: 'Server refresh approval' },
      sections: [{ title: 'Background', body: '<p>Two hundred words here</p>' }],
      matrixRows: [{ assignee_id: 'a' }, { assignee_id: 'b' }],
      attachments: [{ name: 'quote.pdf', size: 1000 }],
    },
    savedAt: new Date(Date.now() - 3 * 60 * 1000),
    source: 'local',
    device: 'Chrome on macOS',
    versions: [],
  };

  const setup = (overrides = {}) => {
    const handlers = {
      onRestore: vi.fn(), onContinue: vi.fn(), onDiscard: vi.fn(),
    };
    render(<DraftRecoveryDialog draft={draft} label="memo" {...handlers} {...overrides} />);
    return handlers;
  };

  it('offers exactly the three approved choices', () => {
    setup();
    expect(screen.getByRole('button', { name: 'Restore draft' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue editing' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Discard draft' })).toBeInTheDocument();
  });

  it('says when the draft was saved', () => {
    setup();
    expect(screen.getByRole('dialog')).toHaveTextContent('3 minutes ago');
  });

  it('describes what would be restored', () => {
    /* "You have an unsaved draft" alone leaves the user guessing whether
       accepting it overwrites something newer. */
    setup();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('Server refresh approval');
    expect(dialog).toHaveTextContent('4 words of content');
    expect(dialog).toHaveTextContent('2 persons in the workflow');
  });

  it('is honest that attachments must be re-added', () => {
    /* Blocker B3: File bytes cannot survive a process restart. */
    setup();
    expect(screen.getByRole('dialog')).toHaveTextContent('1 attachment to re-attach');
  });

  it('each button calls its own handler', () => {
    const h = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Restore draft' }));
    expect(h.onRestore).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Continue editing' }));
    expect(h.onContinue).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    expect(h.onDiscard).toHaveBeenCalled();
  });

  it('is a modal dialog for assistive technology', () => {
    setup();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });

  it('renders nothing when there is nothing to recover', () => {
    const { container } = render(<DraftRecoveryDialog draft={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
