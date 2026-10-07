import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('./api', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
import api from './api';
import { memoService } from './memoService';

describe('memoService', () => {
  beforeEach(() => vi.clearAllMocks());

  it('listMemos unwraps a paginated response', async () => {
    api.get.mockResolvedValue({ data: { count: 2, next: null, previous: null, results: [{ id: '1' }, { id: '2' }] } });
    const res = await memoService.listMemos({ status: 'draft' }, 2);
    expect(api.get).toHaveBeenCalledWith('/memos/', { params: { status: 'draft', page: 2 } });
    expect(res.items).toHaveLength(2);
    expect(res.count).toBe(2);
  });

  it('createMemo returns id + memo_number', async () => {
    api.post.mockResolvedValue({ data: { id: 'm1', memo_number: 'NIFN-GEN-2026-0001', status: 'draft' } });
    const memo = await memoService.createMemo({ title: 'T' });
    expect(api.post).toHaveBeenCalledWith('/memos/', { title: 'T' });
    expect(memo).toMatchObject({ id: 'm1', memo_number: 'NIFN-GEN-2026-0001' });
  });

  it('createMemo with an attachment uses the service multipart path (M5)', async () => {
    api.post.mockResolvedValue({ data: { id: 'm1' } });
    const fd = new FormData();
    fd.append('title', 'T');
    fd.append('attachment', new Blob(['x']), 'f.pdf');
    await memoService.createMemo(fd);
    expect(api.post).toHaveBeenCalledWith('/memos/', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
  });

  it('createAndSubmit posts to the atomic endpoint (M2)', async () => {
    api.post.mockResolvedValue({ data: { id: 'm1', status: 'submitted' } });
    await memoService.createAndSubmit({ title: 'T', override_reviewer_id: 'c1' });
    expect(api.post).toHaveBeenCalledWith('/memos/create-and-submit/', { title: 'T', override_reviewer_id: 'c1' });
  });

  it('sendForReview posts the matrix to the send-for-review action', async () => {
    api.post.mockResolvedValue({ data: {} });
    const workflow = [
      { assignee_id: 'c1', role_type: 'reviewer' },
      { assignee_id: 'a1', role_type: 'approver' },
    ];
    await memoService.sendForReview('m1', { workflow, remarks: 'please review' });
    expect(api.post).toHaveBeenCalledWith('/memos/m1/send-for-review/', {
      workflow, remarks: 'please review',
    });
  });

  it('setMatrix posts the workflow rows in chain order', async () => {
    api.post.mockResolvedValue({ data: {} });
    const workflow = [{ assignee_id: 'c1', role_type: 'supporter' }];
    await memoService.setMatrix('m1', workflow);
    expect(api.post).toHaveBeenCalledWith('/memos/m1/matrix/', { workflow });
  });

  it('actOnMemo posts one decision, not a per-role verb', async () => {
    // One endpoint for all four role types: the server reads the active step's
    // role, so a client cannot approve on a supporter's step.
    api.post.mockResolvedValue({ data: {} });
    await memoService.actOnMemo('m1', { decision: 'proceed', remarks: 'checked' });
    expect(api.post).toHaveBeenCalledWith('/memos/m1/act/', {
      decision: 'proceed', remarks: 'checked',
    });

    await memoService.actOnMemo('m1', { decision: 'reject', remarks: 'not acceptable' });
    expect(api.post).toHaveBeenCalledWith('/memos/m1/act/', {
      decision: 'reject', remarks: 'not acceptable',
    });
  });

  it('archive and withdraw hit their own actions', async () => {
    api.post.mockResolvedValue({ data: {} });
    await memoService.archiveMemo('m1');
    expect(api.post).toHaveBeenCalledWith('/memos/m1/archive/', {});
    await memoService.withdrawMemo('m1', { remarks: 'superseded' });
    expect(api.post).toHaveBeenCalledWith('/memos/m1/cancel/', { remarks: 'superseded' });
  });

  it('exposes no legacy two-slot workflow calls', () => {
    // The endpoints behind these were removed with the legacy engine; leaving the
    // client methods behind would only produce 404s at runtime.
    for (const name of ['submitMemo', 'reviewMemo', 'approveMemo', 'rejectMemo',
      'returnMemo', 'cancelMemo', 'getAvailableCheckers', 'getAvailableApprovers']) {
      expect(memoService[name]).toBeUndefined();
    }
  });

  it('listTemplates unwraps paginated templates', async () => {
    api.get.mockResolvedValue({ data: { count: 1, results: [{ id: 't1', name: 'HR Notice' }] } });
    const t = await memoService.listTemplates();
    expect(api.get).toHaveBeenCalledWith('/memo-templates/');
    expect(t).toHaveLength(1);
  });
});
