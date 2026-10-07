import React, { useEffect, useState } from 'react';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import EmptyState from '../../components/common/EmptyState';
import StatusBadge from '../../components/common/StatusBadge';
import { platformService, money } from '../../services/platformService';

/**
 * Part 1: the Plans page — the catalogue every subscription action picks from.
 *
 * READ-ONLY, AND THAT IS THE DESIGN. A plan's price is a `PlanPrice` row that
 * is immutable and superseded by insertion rather than edited, because a
 * subscription points at the price it was sold at and a renewal must not be
 * able to rewrite history. So "change the price" is an operation on the
 * catalogue, not a form field on this table, and it is not in this phase's
 * brief. What the page is for is answering the question an operator actually
 * has before assigning a plan: what does this one include, what does it cost
 * today, and is it still on sale.
 *
 * `price` is null when a plan has no active price for today's date — shown as
 * "not priced" rather than as free, because the two are very different and
 * the plan picker refuses to sell the former.
 */
const PlatformPlans = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.plans()
      .then((response) => {
        if (!alive) return;
        setRows(response.data);
        setError(null);
      })
      .catch(() => { if (alive) setError('The plan catalogue could not be loaded.'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);

  const interval = (months) => {
    if (months === 1) return 'Monthly';
    if (months === 3) return 'Quarterly';
    if (months === 6) return 'Half-yearly';
    if (months === 12) return 'Annual';
    return `${months} months`;
  };

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Plans"
        description="The catalogue subscriptions are assigned from. Prices are versioned, not edited."
      />
      {error ? (
        <EmptyState
          variant="error"
          title="Could not load the catalogue"
          body={error}
          onAction={() => { setLoading(true); setReload((n) => n + 1); }}
          actionLabel="Retry"
        />
      ) : (
        <DataTable
          columns={[
            { key: 'name', header: 'Plan' },
            { key: 'code', header: 'Code' },
            { key: 'interval_months', header: 'Billed',
              render: (row) => interval(row.interval_months) },
            { key: 'price', header: 'Price', align: 'right',
              render: (row) => (row.price
                ? money(row.price.amount_minor, row.price.currency)
                : 'not priced') },
            { key: 'included_seats', header: 'Seats included', align: 'right',
              render: (row) => (row.included_seats == null
                ? 'unlimited' : row.included_seats) },
            { key: 'trial_days', header: 'Trial', align: 'right',
              render: (row) => `${row.trial_days} d` },
            { key: 'grace_days', header: 'Grace', align: 'right',
              render: (row) => `${row.grace_days} d` },
            { key: 'billing_mode', header: 'Billing' },
            { key: 'is_active', header: 'On sale',
              render: (row) => (
                <StatusBadge status={row.is_active ? 'active' : 'retired'} />
              ) },
          ]}
          rows={rows}
          loading={loading}
          caption="Subscription plans"
          empty={{
            variant: 'first',
            title: 'No plans',
            body: 'Nothing can be sold until a plan exists with an active price.',
          }}
        />
      )}
    </div>
  );
};

export default PlatformPlans;
