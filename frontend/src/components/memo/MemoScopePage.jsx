import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, Plus, RefreshCw, X } from 'lucide-react';
import { memoService } from '../../services/memoService';
import { saveBlob } from '../../services/documentService';
import MemoTable from './MemoTable';
import { FILTERABLE_STATUSES, MEMO_TYPES, memoStatusLabel } from './memoLabels';
import Toast from '../admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../leave-records/States';

const PAGE_SIZE = 50; // must match DRF's PAGE_SIZE so the pager count is right

const BLANK = {
  search: '', status: '', memo_type: '', department_name: '',
  created_from: '', created_to: '',
};

/**
 * One page component behind every memo menu.
 *
 * Each menu is a `scope` the server defines and filters, so a menu cannot drift
 * from its definition and the client never re-derives access rules. Filtering,
 * searching and pagination are all server-side — the original pages filtered a
 * single 50-row page in the browser, which silently under-reported every list and
 * count once a user could see more than one page.
 *
 * @param {{ scope:string, title:string, subtitle:string, columns?:string[],
 *           allowExport?:boolean, allowCreate?:boolean, emptyMessage?:string,
 *           refetchInterval?:number, hideStatusFilter?:boolean,
 *           showDepartmentFilter?:boolean, showDateFilter?:boolean }} props
 */
const MemoScopePage = ({
  scope,
  title,
  subtitle,
  columns,
  allowExport = false,
  allowCreate = false,
  emptyMessage = 'Nothing here yet.',
  refetchInterval,
  hideStatusFilter = false,
  showDepartmentFilter = false,
  showDateFilter = false,
}) => {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState(BLANK);
  const [toast, setToast] = useState(null);
  const [exporting, setExporting] = useState(false);

  // Department options come from the memo charts endpoint rather than the admin
  // department list: that list is admin-gated, so a department head opening this
  // page would get a 403 and an empty dropdown. This source is already scoped to
  // what the caller may see, needs no extra permission, and lists only
  // departments that actually have memos in it. Fetched only where the filter is
  // rendered, so the other menus do not pull data they never use.
  const { data: charts } = useQuery({
    queryKey: ['memos', 'department-options'],
    queryFn: () => memoService.getDashboardCharts(),
    enabled: showDepartmentFilter,
    staleTime: 5 * 60_000,
  });
  const departments = (charts?.by_department ?? [])
    .map((row) => row.label)
    .filter((label) => label && label !== 'Unassigned');

  const params = {
    scope,
    ...(filters.search.trim() ? { search: filters.search.trim() } : {}),
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.memo_type ? { memo_type: filters.memo_type } : {}),
    ...(filters.department_name ? { department_name: filters.department_name } : {}),
    ...(filters.created_from ? { created_from: filters.created_from } : {}),
    ...(filters.created_to ? { created_to: filters.created_to } : {}),
  };

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['memos', scope, params, page],
    queryFn: () => memoService.listMemos(params, page),
    refetchInterval,
    // Keep the previous page visible while the next loads, so paging through the
    // archive does not flash an empty table between requests.
    placeholderData: (previous) => previous,
  });

  const items = data?.items ?? [];
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isFiltered = Object.entries(filters).some(([, value]) => value !== '');

  const set = (key) => (event) => {
    setFilters((prev) => ({ ...prev, [key]: event.target.value }));
    setPage(1);
  };
  const clearFilters = () => { setFilters(BLANK); setPage(1); };

  const exportXlsx = async () => {
    setExporting(true);
    try {
      saveBlob(await memoService.exportMemos(params), `${scope}-memos.xlsx`);
    } catch {
      setToast({ message: 'Export failed. Please try again.', tone: 'error' });
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h2>{title}</h2>
          <div className="lr-page-sub">
            {subtitle}
            {!isLoading && !isError && ` · ${count} ${count === 1 ? 'memo' : 'memos'}`}
          </div>
        </div>
        <div className="memo-head-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={() => refetch()}
            aria-label="Refresh list" disabled={isFetching}>
            <RefreshCw size={14} className={isFetching ? 'lr-spin' : ''} /> Refresh
          </button>
          {allowExport && (
            <button type="button" className="lr-btn" onClick={exportXlsx} disabled={exporting || !count}>
              <Download size={14} /> {exporting ? 'Exporting…' : 'Export Excel'}
            </button>
          )}
          {allowCreate && (
            <button type="button" className="lr-btn lr-btn-primary" onClick={() => navigate('/memos/create')}>
              <Plus size={14} /> Create Memo
            </button>
          )}
        </div>
      </div>

      <div className="lr-filter-bar memo-filter-bar">
        <input type="search" placeholder="Search number, subject or author"
          value={filters.search} onChange={set('search')} aria-label="Search memos" />
        {!hideStatusFilter && (
          <select value={filters.status} onChange={set('status')} aria-label="Filter by status">
            <option value="">All statuses</option>
            {FILTERABLE_STATUSES.map((s) => <option key={s} value={s}>{memoStatusLabel(s)}</option>)}
          </select>
        )}
        <select value={filters.memo_type} onChange={set('memo_type')} aria-label="Filter by type">
          <option value="">All types</option>
          {MEMO_TYPES.map((t) => (
            <option key={t.code} value={t.code}>{t.label}</option>
          ))}
        </select>
        {showDepartmentFilter && (
          <select value={filters.department_name} onChange={set('department_name')}
            aria-label="Filter by department">
            <option value="">All departments</option>
            {departments.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        )}
        {showDateFilter && (
          <>
            <label className="memo-date-filter">
              <span>From</span>
              <input type="date" value={filters.created_from} onChange={set('created_from')}
                aria-label="Created from" max={filters.created_to || undefined} />
            </label>
            <label className="memo-date-filter">
              <span>To</span>
              <input type="date" value={filters.created_to} onChange={set('created_to')}
                aria-label="Created to" min={filters.created_from || undefined} />
            </label>
          </>
        )}
        {isFiltered && (
          <button type="button" className="lr-btn lr-btn-ghost" onClick={clearFilters}>
            <X size={13} /> Clear
          </button>
        )}
      </div>

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && items.length === 0 && (
        /* ctaTo is passed explicitly, and as null rather than undefined: EmptyState
           is shared with the leave module and defaults its CTA to "Apply for leave"
           -> /leave/apply. Omitting the prop (or passing undefined, which also
           triggers a default parameter) puts that button on an empty memo menu. */
        <EmptyState
          message={isFiltered ? 'No memos match these filters.' : emptyMessage}
          ctaLabel="Create Memo"
          ctaTo={allowCreate && !isFiltered ? '/memos/create' : null}
        />
      )}
      {!isLoading && !isError && items.length > 0 && (
        <>
          <MemoTable items={items} columns={columns} />
          {totalPages > 1 && (
            <div className="memo-pager">
              <button type="button" className="lr-btn lr-btn-ghost" disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}>Previous</button>
              <span className="lr-page-sub">Page {page} of {totalPages}</span>
              <button type="button" className="lr-btn lr-btn-ghost" disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          )}
        </>
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default MemoScopePage;
