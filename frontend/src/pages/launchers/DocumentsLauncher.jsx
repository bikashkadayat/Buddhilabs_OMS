import React from 'react';
import { ClipboardList, ClipboardPen, FileText, Megaphone } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import ModuleLauncher from '../../components/layout/ModuleLauncher';
import { memoService } from '../../services/memoService';
import { minuteService } from '../../services/minuteService';
import { circularService } from '../../services/circularService';
import { draftService } from '../../services/draftService';

/**
 * Documents launcher (Phase E / blueprint §05).
 *
 * Counts come from the four /dashboard/ endpoints the sidebar already polls, on
 * the same query keys - so opening this page issues no new requests when the
 * rail has already populated the cache.
 */
const DocumentsLauncher = () => {
  const opts = { staleTime: 60_000, retry: false };
  const { data: memo } = useQuery({ queryKey: ['memos', 'dashboard'], queryFn: memoService.getDashboard, ...opts });
  const { data: minute } = useQuery({ queryKey: ['minutes', 'dashboard'], queryFn: minuteService.getDashboard, ...opts });
  const { data: circular } = useQuery({ queryKey: ['circulars', 'dashboard'], queryFn: circularService.getDashboard, ...opts });
  const { data: drafts } = useQuery({ queryKey: ['drafts', 'list'], queryFn: draftService.listDrafts, ...opts });

  const draftCount = Array.isArray(drafts) ? drafts.length : (drafts?.results?.length ?? 0);

  const cards = [
    {
      key: 'memo', title: 'Memo', icon: FileText, accent: '#4c3a9e', to: '/memos',
      counts: [
        ['waiting on you', memo?.pending_actions],
        ['drafts', memo?.drafts],
        ['in inbox', memo?.inbox],
      ],
      action: { label: 'Create memo', to: '/memos/create' },
    },
    {
      key: 'minute', title: 'Minute', icon: ClipboardPen, accent: '#3c4a6e', to: '/minutes',
      counts: [
        ['needs action', minute?.needs_my_action],
        ['my drafts', minute?.my_drafts],
      ],
      action: { label: 'Create minute', to: '/minutes/create' },
    },
    {
      key: 'circular', title: 'Circular', icon: Megaphone, accent: '#a35b06', to: '/circulars',
      counts: [
        ['to acknowledge', circular?.pending_acknowledgement],
        ['unread', circular?.unread],
        ['drafts', circular?.drafts],
      ],
      action: { label: 'Create circular', to: '/circulars/create' },
    },
    {
      key: 'drafts', title: 'Unfinished drafts', icon: ClipboardList, accent: '#126b4e', to: '/drafts',
      counts: [['autosaved, across all three', draftCount]],
      action: { label: 'Resume', to: '/drafts' },
    },
  ];

  return (
    <ModuleLauncher
      title="Documents"
      description="Choose a module. Its own menu replaces this rail while you are inside it."
      cards={cards}
    />
  );
};

export default DocumentsLauncher;
