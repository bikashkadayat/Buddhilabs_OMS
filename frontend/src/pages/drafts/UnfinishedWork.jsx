import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FileText, Trash2 } from 'lucide-react';

import { draftService } from '../../services/draftService';
import { Skeleton, EmptyState, ErrorState } from '../../components/leave-records/States';

/**
 * Unfinished Work (Phase 111C) — every autosaved draft the user has, across
 * Memo, Minute, Circular and Task.
 *
 * The recovery dialog only appears when someone happens to reopen the form they
 * were on. This page answers the other question: what was I in the middle of?
 * A user whose laptop died halfway through a memo does not necessarily remember
 * which of the three modules it was in.
 *
 * These are snapshots, not documents. A draft here has no memo number and no
 * workflow status, because nothing has been committed yet — which is exactly
 * why losing it used to be silent.
 */

const KIND_LABEL = { memo: 'Memo', minute: 'Minute', circular: 'Circular', task: 'Task' };

// 'new' means the document was never saved, so the only place to resume is the
// create form; anything else resumes on that document's edit page.
const resumePath = (row) => {
  const base = {
    memo: '/memos', minute: '/minutes', circular: '/circulars', task: '/tasks',
  }[row.kind];
  if (!base) return '/';
  return row.document_key === 'new' ? `${base}/create` : `${base}/${row.document_key}/edit`;
};

const when = (iso) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return 'less than a minute ago';
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

const UnfinishedWork = () => {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['drafts', 'mine'],
    queryFn: draftService.listDrafts,
  });

  const discard = useMutation({
    mutationFn: (row) => draftService.discardDraft(row.kind, row.document_key),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drafts', 'mine'] }),
  });

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Unfinished Work</h1>
          <p className="lr-page-sub">
            Documents you started but have not saved yet · autosaved for 90 days
          </p>
        </div>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState
          message="Nothing unfinished — everything you have started is saved."
          ctaLabel="Create Memo"
          ctaTo="/memos/create"
        />
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <ul className="draft-work-list">
          {rows.map((row) => (
            <li key={row.id} className="draft-work">
              <FileText size={17} aria-hidden="true" className="draft-work-icon" />
              <div className="draft-work-main">
                <span className="draft-work-title">
                  {row.title || `Untitled ${KIND_LABEL[row.kind] || row.kind}`}
                </span>
                <span className="draft-work-meta">
                  {KIND_LABEL[row.kind] || row.kind}
                  {' · last edited '}{when(row.saved_at)}
                  {row.device_label ? ` · ${row.device_label}` : ''}
                </span>
              </div>
              <button type="button" className="lr-btn lr-btn-primary draft-work-resume"
                onClick={() => navigate(resumePath(row))}>
                Resume
              </button>
              <button type="button" className="lr-btn lr-btn-ghost draft-work-discard"
                aria-label={`Discard ${row.title || 'untitled draft'}`}
                disabled={discard.isPending}
                onClick={() => discard.mutate(row)}>
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default UnfinishedWork;
