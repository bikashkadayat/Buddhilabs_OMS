import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import platformService from '../../services/platformService';

/**
 * Phase S9 Part 3, operator side: every custom domain on the platform.
 *
 * THE COLUMN THAT MAKES THIS PAGE WORTH HAVING IS "Checks". A customer who
 * has pressed "Check now" eleven times and is still pending has misread
 * their DNS panel and will not work it out alone -- and from inside a single
 * tenant's page that is invisible. Sorted by last activity, so the ones
 * somebody is wrestling with right now are at the top.
 *
 * IT ALSO STATES WHETHER DNS RESOLUTION IS AVAILABLE AT ALL. Without a
 * resolver installed, every check answers "we could not look", and an
 * operator who does not know that will tell a customer to wait for
 * propagation that has already happened.
 */
const PlatformDomains = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    platformService.domains()
      .then((response) => { if (alive) setData(response.data); })
      .catch(() => { if (alive) setError('The domain list could not be loaded.'); });
    return () => { alive = false; };
  }, []);

  if (error) {
    return (
      <div className="page">
        <PageHeader title="Custom domains" />
        <EmptyState variant="error" title="Not available" body={error} />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="page">
        <PageHeader title="Custom domains" />
        <Skeleton rows={6} />
      </div>
    );
  }

  const counts = data.counts || {};
  const stuck = (data.domains || []).filter(
    (row) => row.status !== 'active' && row.check_count >= 3).length;

  return (
    <div className="page">
      <PageHeader
        title="Custom domains"
        description={`${counts.active || 0} serving · ${counts.pending || 0} awaiting DNS`
          + ` · ${counts.failed || 0} failing`}
      />

      {!data.dns_available && (
        <div className="br-alert br-alert-bad" role="alert">
          This deployment cannot perform DNS lookups, so no domain can be
          verified. Install dnspython and restart. Until then every check
          answers “we could not look” — do not tell customers their records
          are wrong.
        </div>
      )}

      {stuck > 0 && (
        <div className="br-alert br-alert-ok" role="status">
          {stuck} {stuck === 1 ? 'domain has' : 'domains have'} been checked
          three or more times without succeeding. Those customers are stuck
          and are unlikely to resolve it without help.
        </div>
      )}

      {(data.domains || []).length === 0 ? (
        <EmptyState
          variant="first"
          title="No custom domains yet"
          body="Every tenant is reachable on its own subdomain. Claims will appear here."
        />
      ) : (
        <DataTable
          columns={[
            { key: 'hostname', header: 'Hostname',
              render: (row) => <code>{row.hostname}</code> },
            { key: 'organization', header: 'Tenant',
              render: (row) => (
                <Link to={`/platform/organizations/${row.slug}`}>
                  {row.organization}
                </Link>
              ) },
            { key: 'status', header: 'State',
              render: (row) => row.status_display },
            { key: 'method', header: 'Proof',
              render: (row) => row.method.toUpperCase() },
            { key: 'check_count', header: 'Checks',
              render: (row) => row.check_count },
            { key: 'last_checked_at', header: 'Last checked',
              render: (row) => (row.last_checked_at
                ? new Date(row.last_checked_at).toLocaleString()
                : 'never') },
            { key: 'last_error', header: 'Why not',
              render: (row) => row.last_error || '—' },
          ]}
          rows={data.domains}
          rowKey={(row) => row.hostname}
        />
      )}
    </div>
  );
};

export default PlatformDomains;
