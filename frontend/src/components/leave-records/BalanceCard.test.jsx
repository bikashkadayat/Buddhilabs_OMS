import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BalanceCard from './BalanceCard';

describe('BalanceCard', () => {
  const base = { leaveType: 'Annual Leave', entitled: '18.00', used: '5.00', pending: '2.00', available: '11.00' };

  it('shows the available days prominently', () => {
    const { container } = render(<BalanceCard {...base} />);
    // The prominent figure is the dedicated available-number element.
    expect(container.querySelector('.lr-bc-available-num')).toHaveTextContent('11');
    expect(screen.getByText('days available')).toBeInTheDocument();
  });

  it('renders the legend with used / pending / available in full view', () => {
    render(<BalanceCard {...base} />);
    expect(screen.getByText(/Used/)).toBeInTheDocument();
    expect(screen.getByText(/Pending/)).toBeInTheDocument();
    expect(screen.getByText(/Available/)).toBeInTheDocument();
  });

  it('hides legend and details link in compact mode', () => {
    render(<BalanceCard {...base} compact />);
    expect(screen.queryByText('Details')).not.toBeInTheDocument();
  });

  it('opens a details modal when Details is clicked', () => {
    render(<BalanceCard {...base} carriedForward="1.00" />);
    fireEvent.click(screen.getByText('Details'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Carried forward')).toBeInTheDocument();
  });

  it('renders zero state without crashing', () => {
    render(<BalanceCard leaveType="Unpaid" entitled="0" used="0" pending="0" available="0" />);
    expect(screen.getByText('Unpaid')).toBeInTheDocument();
  });
});
