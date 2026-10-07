import React from 'react';

/**
 * Everything a person needs to confirm they have the right asset before they act
 * on it (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD).
 *
 * Ten facts, in the order the brief lists them. Two of them - serial number and
 * warranty expiry - were missing from the earlier picker summary, and they are
 * the two that actually settle "is this the laptop in front of me": the serial is
 * printed on the device, and the warranty date is what the store checks before
 * accepting a damaged asset back.
 *
 * Pure display of one record. It takes the asset object as returned by the
 * register API and derives nothing, so it cannot describe a different asset from
 * the one it was handed.
 */
const Fact = ({ label, value, testId }) => (
  <div data-testid={testId}>
    <dt>{label}</dt>
    <dd>{value || '—'}</dd>
  </div>
);

const withBs = (ad, bs) => (ad ? `${ad}${bs ? ` · ${bs} BS` : ''}` : '');

const AssetInfoCard = ({ asset, title = 'Asset information' }) => {
  if (!asset) return null;
  return (
    <section className="inv-info-card" aria-label={title} data-testid="asset-info-card">
      <h4 className="memo-panel-title">{title}</h4>
      <dl className="memo-doc-meta">
        <Fact label="Asset code" value={asset.asset_code} />
        <Fact label="Asset name" value={asset.name} />
        <Fact label="Category" value={asset.category_name} />
        <Fact label="Serial number" value={asset.serial_number} />
        {/* Whoever holds it now. An asset in the store has nobody accountable
            for it, and "In store" is a truer answer than a blank. */}
        <Fact label="Current owner" value={asset.current_holder || 'In store'} />
        <Fact label="Department" value={asset.department_name} />
        <Fact label="Condition" value={asset.condition_display} />
        <Fact label="Purchase date" value={withBs(asset.purchase_date, asset.purchase_date_bs)} />
        <Fact label="Warranty expiry"
          value={withBs(asset.warranty_expiry, asset.warranty_expiry_bs)} />
        <Fact label="Status" value={asset.status_display} />
      </dl>
    </section>
  );
};

export default AssetInfoCard;
