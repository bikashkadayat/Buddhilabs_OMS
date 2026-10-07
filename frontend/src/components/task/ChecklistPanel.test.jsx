/**
 * Phase T2.1 — the checklist panel.
 *
 * The claims worth pinning: sections render with their own tally, a tick reports
 * the value that was actually clicked, and the editor cannot produce a section
 * with no lines in it (which would render as an empty heading forever).
 */
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ChecklistPanel from './ChecklistPanel';

const item = (id, text, done = false) => ({
  id, text, is_done: done, group: null, position: 0,
  done_by_name: done ? 'Employee Person' : '',
  done_at: done ? '2026-09-01T09:00:00Z' : null,
});

const group = (id, title, items) => ({
  id, title, position: 0, items,
  done_count: items.filter((i) => i.is_done).length,
  total_count: items.length,
});

const renderPanel = (props = {}) => {
  const onTick = vi.fn();
  const onSave = vi.fn();
  render(
    <ChecklistPanel
      items={[]} groups={[]} canTick canManage busy={false}
      onTick={onTick} onSave={onSave} {...props}
    />,
  );
  return { onTick, onSave };
};

describe('checklist panel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says so when there is no checklist', () => {
    renderPanel();
    expect(screen.getByText('No checklist on this task.')).toBeInTheDocument();
  });

  it('renders top-level lines', () => {
    renderPanel({ items: [item('a', 'Collect Data'), item('b', 'Draft Report')] });
    expect(screen.getByText('Collect Data')).toBeInTheDocument();
    expect(screen.getByText('Draft Report')).toBeInTheDocument();
  });

  it('renders a section with its own tally', () => {
    renderPanel({
      groups: [group('g1', 'Preparation',
        [item('a', 'Collect Data', true), item('b', 'Draft Report')])],
    });
    expect(screen.getByText('Preparation')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
  });

  it('collapses a section without losing it', () => {
    renderPanel({
      groups: [group('g1', 'Preparation', [item('a', 'Collect Data')])],
    });
    const head = screen.getByRole('button', { name: /Preparation/ });
    expect(screen.getByText('Collect Data')).toBeInTheDocument();
    fireEvent.click(head);
    expect(screen.queryByText('Collect Data')).toBeNull();
    fireEvent.click(head);
    expect(screen.getByText('Collect Data')).toBeInTheDocument();
  });

  it('reports the value actually clicked, not the value before the click', () => {
    /* This is a controlled checkbox: React resets it as soon as the handler
       returns, so a value read later reports the opposite of what was clicked
       and ticking a box silently un-ticks it. */
    const { onTick } = renderPanel({ items: [item('a', 'Collect Data')] });
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onTick).toHaveBeenCalledWith('a', true);
  });

  it('un-ticks a completed line', () => {
    const { onTick } = renderPanel({ items: [item('a', 'Collect Data', true)] });
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onTick).toHaveBeenCalledWith('a', false);
  });

  it('disables every box when the server has not granted a tick', () => {
    renderPanel({
      canTick: false,
      items: [item('a', 'Collect Data')],
      groups: [group('g1', 'Prep', [item('b', 'Review')])],
    });
    for (const box of screen.getAllByRole('checkbox')) {
      expect(box).toBeDisabled();
    }
  });

  it('offers no editor to somebody who may not define the checklist', () => {
    renderPanel({ canManage: false, items: [item('a', 'Collect Data')] });
    expect(screen.queryByRole('button', { name: /edit checklist/i })).toBeNull();
  });

  it('saves lines and sections in one request', () => {
    const { onSave } = renderPanel({ items: [item('a', 'Kick-off')] });
    fireEvent.click(screen.getByRole('button', { name: /edit checklist/i }));

    fireEvent.click(screen.getByRole('button', { name: /add section/i }));
    fireEvent.change(screen.getByLabelText(/section 1 title/i),
      { target: { value: 'Drafting' } });
    fireEvent.click(screen.getByRole('button', { name: /add line to drafting/i }));
    fireEvent.change(screen.getByLabelText(/drafting line 1/i),
      { target: { value: 'Write' } });

    fireEvent.click(screen.getByRole('button', { name: /save checklist/i }));
    expect(onSave).toHaveBeenCalledWith({
      items: ['Kick-off'],
      groups: [{ title: 'Drafting', items: ['Write'] }],
    });
  });

  it('drops a section with no lines rather than saving an empty heading', () => {
    const { onSave } = renderPanel({ items: [item('a', 'Kick-off')] });
    fireEvent.click(screen.getByRole('button', { name: /edit checklist/i }));
    fireEvent.click(screen.getByRole('button', { name: /add section/i }));
    fireEvent.click(screen.getByRole('button', { name: /save checklist/i }));
    expect(onSave).toHaveBeenCalledWith({ items: ['Kick-off'], groups: [] });
  });

  it('adds a line on Enter without submitting anything', () => {
    const { onSave } = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /add a checklist/i }));
    const field = screen.getByLabelText(/new checklist line/i);
    fireEvent.change(field, { target: { value: 'Collect Data' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/checklist line 1/i)).toHaveValue('Collect Data');
  });

  it('shows who completed a line and when', () => {
    renderPanel({ items: [item('a', 'Collect Data', true)] });
    expect(screen.getByText(/Employee Person/)).toBeInTheDocument();
  });

  it('marks a fully completed section as complete', () => {
    renderPanel({
      groups: [group('g1', 'Done', [item('a', 'One', true), item('b', 'Two', true)])],
    });
    const head = screen.getByRole('button', { name: /Done/ });
    expect(within(head).getByText('2/2').className).toContain('is-complete');
  });
});
