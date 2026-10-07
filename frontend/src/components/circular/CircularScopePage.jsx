import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';

import { circularService } from '../../services/circularService';
import CircularTable from './CircularTable';
import { Skeleton, ErrorState, EmptyState } from '../leave-records/States';

/**
 * One page, bound to a server-side scope.
 *
 * Every sidebar menu renders this with a different `scope` and column set, so a
 * menu's definition lives in exactly one place (circulars.views._apply_scope) and
 * adding a menu is a route plus a scope name - not another list implementation with
 * its own filtering bugs.
 *
 * The status filter reads the SERVER's status list rather than a hardcoded one. The
 * minute module's audit found the opposite: its taxonomy endpoint sent no statuses,
 * so its filter fell back to a hardcoded list that omitted `cancelled` entirely and
 * those records could not be filtered for at all.
 */
const CircularScopePage = ({
  scope, title, subtitle, columns, emptyMessage,
  allowCreate = false, showFilters = true,
}) => {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [classificationFilter, setClassificationFilter] = useState('');

  const params = { scope };
  if (search.trim()) params.search = search.trim();
  if (statusFilter) params.status = statusFilter;
  if (classificationFilter) params.classification = classificationFilter;

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['circulars', scope, search, statusFilter, classificationFilter],
    queryFn: () => circularService.getCirculars(params),
  });

  const { data: taxonomy } = useQuery({
    queryKey: ['circulars', 'taxonomy'],
    queryFn: circularService.getTaxonomy,
    // The taxonomy changes when an administrator edits it, not per navigation.
    staleTime: 10 * 60 * 1000,
    enabled: showFilters,
  });

  const items = data?.results ?? data?.items ?? data ?? [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">{title}</h1>
          <p className="lr-page-sub">{subtitle}</p>
        </div>
        {allowCreate && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => navigate('/circulars/create')}>
            <Plus size={14} /> Create Circular
          </button>
        )}
      </div>

      {showFilters && (
        <div className="memo-filters">
          <label className="lr-field">
            <span className="sr-only">Search circulars</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by circular number, subject or reference…"
              aria-label="Search circulars" />
          </label>
          <label className="lr-field">
            <span className="sr-only">Filter by status</span>
            <select value={statusFilter} aria-label="Filter by status"
              onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {(taxonomy?.statuses || []).map((row) => (
                <option key={row.code} value={row.code}>{row.label}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span className="sr-only">Filter by classification</span>
            <select value={classificationFilter} aria-label="Filter by classification"
              onChange={(e) => setClassificationFilter(e.target.value)}>
              <option value="">All classifications</option>
              {(taxonomy?.classifications || []).map((row) => (
                <option key={row.code} value={row.code}>{row.label}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {isLoading && <Skeleton rows={5} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && items.length === 0 && (
        // ctaTo={null} suppresses the shared component's default CTA, which is
        // "Apply for leave" - correct where it was written, wrong here.
        <EmptyState message={emptyMessage}
          ctaTo={allowCreate ? '/circulars/create' : null}
          ctaLabel="Create Circular" />
      )}
      {!isLoading && !isError && items.length > 0 && (
        <CircularTable items={items} columns={columns} />
      )}
    </div>
  );
};

export default CircularScopePage;
