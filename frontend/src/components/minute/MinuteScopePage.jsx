import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, FileSpreadsheet, Plus } from 'lucide-react';

import { minuteService } from '../../services/minuteService';
import MinuteTable from './MinuteTable';
import { Skeleton, ErrorState, EmptyState } from '../leave-records/States';

/**
 * One page, bound to a server-side scope.
 *
 * Every sidebar menu renders this with a different `scope` and column set, so a menu's
 * definition lives in exactly one place (minutes.views._apply_scope) and adding a menu is
 * a route plus a scope name — not another list implementation with its own filtering
 * bugs. The column sets differ because the question each menu answers differs: an
 * acknowledgement queue needs "how many have responded", the archive needs the
 * reference number and the date it was filed.
 *
 * @param {{ scope:string, title:string, subtitle:string, columns:string[],
 *           emptyMessage:string, allowCreate?:boolean, showStatusFilter?:boolean,
 *           refetchInterval?:number }} props
 */
const MinuteScopePage = ({
  scope, title, subtitle, columns, emptyMessage,
  allowCreate = false, showStatusFilter = false, refetchInterval,
}) => {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const params = { scope };
  if (search.trim()) params.search = search.trim();
  if (statusFilter) params.status = statusFilter;

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['minutes', scope, search, statusFilter],
    queryFn: () => minuteService.getMinutes(params),
    refetchInterval,
  });

  const { data: taxonomy } = useQuery({
    queryKey: ['minutes', 'taxonomy'],
    queryFn: minuteService.getTaxonomy,
    // The taxonomy changes when an administrator edits it, not per navigation.
    staleTime: 10 * 60 * 1000,
    enabled: showStatusFilter,
  });

  const items = data?.results ?? data ?? [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">{title}</h1>
          <p className="lr-page-sub">{subtitle}</p>
        </div>
        {allowCreate && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => navigate('/minutes/create')}>
            <Plus size={14} /> Create Minute
          </button>
        )}
      </div>

      <div className="memo-filters">
        <label className="lr-field">
          <span className="sr-only">Search minutes</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by minute number, subject or reference…"
            aria-label="Search minutes" />
        </label>
        {showStatusFilter && (
          <label className="lr-field">
            <span className="sr-only">Filter by status</span>
            <select value={statusFilter} aria-label="Filter by status"
              onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {/* The server's own choice list; the fallback is the manual's four
                  states, so the filter still works if the taxonomy call is in
                  flight. */}
              {(taxonomy?.statuses || [
                { code: 'draft', label: 'Draft' },
                { code: 'draft_for_review', label: 'Draft For Review' },
                { code: 'pending_acknowledgement', label: 'Pending Acknowledgement' },
                { code: 'archived', label: 'Archived' },
              ]).map((status) => (
                <option key={status.code} value={status.code}>{status.label}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {isLoading && <Skeleton rows={5} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && items.length === 0 && (
        // ctaTo={null} suppresses the shared component's default CTA, which is
        // "Apply for leave" — correct where it was written, wrong on a minute page.
        <EmptyState
          message={emptyMessage}
          ctaTo={allowCreate ? '/minutes/create' : null}
          ctaLabel="Create Minute"
        />
      )}
      {!isLoading && !isError && items.length > 0 && (
        <MinuteTable items={items} columns={columns} />
      )}
    </div>
  );
};

export default MinuteScopePage;
