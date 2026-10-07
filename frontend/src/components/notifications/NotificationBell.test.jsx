import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/notificationService', () => ({
  notificationService: {
    unreadCount: vi.fn(),
    list: vi.fn(() => Promise.resolve([])),
    markRead: vi.fn(),
  },
}));

import { notificationService } from '../../services/notificationService';
import NotificationBell from './NotificationBell';

const renderBell = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><NotificationBell /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('NotificationBell', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the unread badge when there are unread notifications', async () => {
    notificationService.unreadCount.mockResolvedValue(3);
    renderBell();
    await waitFor(() => expect(screen.getByText('3')).toBeInTheDocument());
    expect(screen.getByLabelText(/3 unread/)).toBeInTheDocument();
  });

  it('shows no badge when there are zero unread', async () => {
    notificationService.unreadCount.mockResolvedValue(0);
    renderBell();
    await waitFor(() => expect(screen.getByLabelText('Notifications')).toBeInTheDocument());
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('caps the badge at 9+', async () => {
    notificationService.unreadCount.mockResolvedValue(25);
    renderBell();
    await waitFor(() => expect(screen.getByText('9+')).toBeInTheDocument());
  });
});
