import { describe, expect, it } from 'vitest';

import {
  canonicalPath, canonicalUrl, shareEmail, shareTargets, shareText,
} from './shareLinks';

const record = {
  kind: 'Task',
  title: 'Website Deployment',
  reference: 'NIFN-TSK-2083-0002',
  url: 'https://oms.nif.org.np/tasks/NIFN-TSK-2083-0002',
};

describe('deep links', () => {
  it('addresses a record by its reference, not its id', () => {
    expect(canonicalPath('tasks', 'NIFN-TSK-2083-0002', 'uuid-1'))
      .toBe('/tasks/NIFN-TSK-2083-0002');
  });

  it('falls back to the id when no reference has been issued', () => {
    expect(canonicalPath('tasks', '', 'uuid-1')).toBe('/tasks/uuid-1');
    expect(canonicalPath('tasks', null, 'uuid-1')).toBe('/tasks/uuid-1');
  });

  it('builds an absolute url against the current origin', () => {
    expect(canonicalUrl('/tasks/X', 'https://oms.nif.org.np'))
      .toBe('https://oms.nif.org.np/tasks/X');
    // A path handed over without its leading slash still produces one url.
    expect(canonicalUrl('tasks/X', 'https://oms.nif.org.np'))
      .toBe('https://oms.nif.org.np/tasks/X');
  });

  it('puts the reference in the share text, not only the link', () => {
    const text = shareText(record);
    expect(text).toContain('Task: Website Deployment');
    expect(text).toContain('NIFN-TSK-2083-0002');
    expect(text).toContain(record.url);
  });

  it('names the reference in the email subject', () => {
    const { subject, body } = shareEmail(record);
    expect(subject).toBe('Task Review Required – NIFN-TSK-2083-0002');
    expect(body).toContain('Website Deployment');
    expect(body).toContain(record.url);
  });

  it('encodes every target so a title with & or # survives', () => {
    const awkward = { ...record, title: 'Deploy R&D #2 100% done' };
    const { email, whatsapp, teams } = shareTargets(awkward);
    for (const href of [email, whatsapp, teams]) {
      // The raw characters must not appear unencoded after the query starts:
      // a bare & would truncate the body at the client.
      expect(href.split('?')[1]).not.toContain(' ');
      expect(href.split('?')[1]).not.toContain('R&D');
    }
    expect(decodeURIComponent(whatsapp)).toContain('Deploy R&D #2 100% done');
  });

  it('points each target at the service it claims to', () => {
    const t = shareTargets(record);
    expect(t.email.startsWith('mailto:?')).toBe(true);
    expect(t.whatsapp.startsWith('https://wa.me/?text=')).toBe(true);
    expect(t.teams.startsWith('https://teams.microsoft.com/share?')).toBe(true);
    expect(decodeURIComponent(t.teams)).toContain(record.url);
  });

  it('is not task-specific: a memo shares the same way', () => {
    const memo = { kind: 'Memo', title: 'Budget revision',
      reference: 'NIFN-MEMO-2083-0011',
      url: 'https://oms.nif.org.np/memos/NIFN-MEMO-2083-0011' };
    expect(shareEmail(memo).subject)
      .toBe('Memo Review Required – NIFN-MEMO-2083-0011');
    expect(shareText(memo)).toContain('Memo: Budget revision');
  });
});
