import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the axios instance so we control the response shape.
vi.mock('./api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import api from './api';
import { leaveService } from './leaveService';

const leaf = (id, status = 'pending') => ({
  id, status, user_name: 'Maker User', leave_type: 'annual',
  start_date: '2026-06-15', end_date: '2026-06-16', reason: 'r',
  created_at: '2026-06-01T00:00:00Z',
});

describe('leaveService.getAll — pagination unwrap', () => {
  beforeEach(() => vi.clearAllMocks());

  it('handles a DRF paginated response ({count, results})', async () => {
    api.get.mockResolvedValue({ data: { count: 3, next: null, previous: null, results: [leaf('1'), leaf('2'), leaf('3')] } });
    const { data } = await leaveService.getAll();
    expect(data).toHaveLength(3);
    expect(data[0].id).toBe('1');
    expect(data[0].type).toBe('Annual Leave'); // mapped
  });

  it('handles a legacy bare-array response (backward compat)', async () => {
    api.get.mockResolvedValue({ data: [leaf('1')] });
    const { data } = await leaveService.getAll();
    expect(data).toHaveLength(1);
  });

  it('handles an empty paginated response', async () => {
    api.get.mockResolvedValue({ data: { count: 0, next: null, previous: null, results: [] } });
    const { data } = await leaveService.getAll();
    expect(data).toEqual([]);
  });

  it('does not throw on the paginated shape (the original bug)', async () => {
    api.get.mockResolvedValue({ data: { count: 1, results: [leaf('x')] } });
    await expect(leaveService.getAll()).resolves.toBeDefined();
  });
});
