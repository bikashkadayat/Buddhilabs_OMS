import React, { useMemo, useState } from 'react';
import { ErrorState, Skeleton } from '../leave-records/States';
import ExportMenu from './ExportMenu';
import PeriodPicker from './PeriodPicker';

/**
 * The shell all nine analytics pages render inside.
 *
 * It owns the four things every page needs and none of them should reimplement:
 * the period filter state, the loading and error states, the "as of" stamp, and
 * the export menu. A page below this line is just charts.
 *
 * Filter state lives here rather than in the URL deliberately: these dashboards
 * are read, adjusted and read again in one sitting, and a query string that
 * changes on every dropdown makes the browser back button useless for actually
 * going back.
 */
const AnalyticsPage = ({
  title, description, query, defaults = {}, children, departments = [],
  showGranularity = true, showCompare = true, orgAnalyst = false,
  exportTypes, showExport = true,
}) => {
  const [filters, setFilters] = useState(defaults);
  const params = useMemo(
    () => Object.fromEntries(
      Object.entries(filters).filter(([, value]) => value !== undefined && value !== '')),
    [filters],
  );

  const { data, isLoading, isError, error, refetch, isFetching } = query(params);

  return (
    <div className="wf-page an-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">{title}</h1>
          {description && <p className="wf-sub">{description}</p>}
        </div>
        {showExport && (
          <ExportMenu params={params} orgAnalyst={orgAnalyst} only={exportTypes} />
        )}
      </header>

      <PeriodPicker
        value={filters}
        onChange={setFilters}
        showGranularity={showGranularity}
        showCompare={showCompare}
        departments={departments}
        generatedAt={data?.generated_at}
        cached={data?.cached}
        truncated={data?.window?.truncated}
      />

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {data && !isError && (
        <div className={isFetching ? 'an-body an-body-loading' : 'an-body'}>
          {children({ payload: data.data, window: data.window, scope: data.scope,
                      params })}
        </div>
      )}
    </div>
  );
};

/** Scope line shown under a page title: who these numbers cover. */
export const ScopeNote = ({ scope, window }) => {
  if (!scope) return null;
  const where = scope.level === 'organization'
    ? 'the whole organisation'
    : (scope.department || 'your department');
  return (
    <p className="an-scope-note">
      Covering {scope.headcount} employees across {where}
      {window && ` · ${window.from} to ${window.to}`}
      {scope.warnings?.includes('department_not_in_scope')
        && ' · the requested department is outside your scope, so your own is shown'}
    </p>
  );
};

export default AnalyticsPage;
