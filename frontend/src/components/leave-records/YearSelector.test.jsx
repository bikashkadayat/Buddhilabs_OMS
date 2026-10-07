import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import YearSelector from './YearSelector';

describe('YearSelector', () => {
  it('renders the current year as the selected value', () => {
    render(<YearSelector currentYear={2026} onChange={() => {}} />);
    expect(screen.getByLabelText('Select year')).toHaveValue('2026');
  });

  it('calls onChange with a numeric year on selection', () => {
    const onChange = vi.fn();
    render(<YearSelector currentYear={2026} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Select year'), { target: { value: '2025' } });
    expect(onChange).toHaveBeenCalledWith(2025);
    expect(typeof onChange.mock.calls[0][0]).toBe('number');
  });

  it('includes the current year even if outside the default range', () => {
    render(<YearSelector currentYear={2020} onChange={() => {}} minYear={2023} />);
    expect(screen.getByRole('option', { name: '2020' })).toBeInTheDocument();
  });
});
