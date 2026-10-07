import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import SettingsIndex from './Index';

/**
 * Phase S9. The hub exists for a navigation reason as much as a product one:
 * `navConfig.test.js` holds a density budget and says in so many words that
 * another Administration entry means merging something, not raising it. So
 * what matters is that all three account pages are reachable FROM HERE --
 * if one is dropped, it becomes unreachable rather than merely unlisted.
 */
describe('the organisation settings hub', () => {
  it('reaches all three account pages', () => {
    render(<MemoryRouter><SettingsIndex /></MemoryRouter>);
    const links = screen.getAllByRole('link')
      .map((a) => a.getAttribute('href'));
    expect(links).toEqual(expect.arrayContaining([
      '/settings/subscription', '/settings/branding', '/settings/domains',
    ]));
  });

  it('says what each one is for, so the titles are not three nouns', () => {
    render(<MemoryRouter><SettingsIndex /></MemoryRouter>);
    expect(screen.getByText(/when it renews/i)).toBeInTheDocument();
    expect(screen.getByText(/across the whole workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/your own web address/i)).toBeInTheDocument();
  });
});
