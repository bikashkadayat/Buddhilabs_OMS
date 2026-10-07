import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, FileText } from 'lucide-react';
import { inventoryService } from '../../services/inventoryService';
import AssetHistory from '../../components/inventory/AssetHistory';
import CustodyRecord from '../../components/inventory/CustodyRecord';
import AssetLifecycle from '../../components/inventory/AssetLifecycle';
import OwnerHistory from '../../components/inventory/OwnerHistory';
import EmptyState from '../../components/common/EmptyState';

const Row = ({ k, v }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, padding: '7px 0', borderBottom: '1px solid var(--border-light)' }}>
    <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>{k}</span>
    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, textAlign: 'right', wordBreak: 'break-word' }}>{v || '—'}</span>
  </div>
);

const Card = ({ title, children, action }) => (
  <div style={{ background: 'var(--white)', border: '1px solid var(--border)', borderRadius: 'var(--r2)', padding: 20, boxShadow: 'var(--shadow)' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
      <div style={{ fontWeight: 700, fontSize: 'var(--fs-body)', color: 'var(--brand-blue)', textTransform: 'uppercase', letterSpacing: '.4px' }}>{title}</div>
      {action}
    </div>
    {children}
  </div>
);

const InventoryItemDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [item, setItem] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [it, hist] = await Promise.all([inventoryService.item(id), inventoryService.assignments(id)]);
      setItem(it); setHistory(hist);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load item.');
    } finally { setLoading(false); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="page"><div style={{ padding: 24 }}>Loading item…</div></div>;
  if (error || !item) return (
    <div className="page">
      <EmptyState variant="error" title={error || 'Item not found.'}
        onAction={load} actionLabel="Retry" />
    </div>
  );

  const specs = [
    ['Brand', item.brand], ['Model', item.model], ['CPU', item.cpu], ['RAM', item.ram],
    ['Storage', [item.storage_size, item.storage_type].filter(Boolean).join(' ')], ['GPU', item.gpu],
    ['Screen', item.screen_size], ['OS', item.os], ['MAC', item.mac_address], ['IP', item.ip_address],
    ['Accessories', item.accessories],
  ].filter(([, v]) => v);
  const genericSpecs = Object.entries(item.specifications || {});

  return (
    <div className="page">
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate('/inventory')}><ArrowLeft size={18} /></button>
            Inventory
          </div>
          <div className="pg-title">{item.name}</div>
          <div className="pg-desc">{item.asset_code} · {item.asset_type_display} · {item.category_name || 'Uncategorised'}</div>
        </div>
        {/* 'assigned' = held in office, 'out' = held and taken out. (There is no
            'approved' item status — that belongs to a take-out request, not an item.) */}
        {['assigned', 'out'].includes(item.status) && item.current_holder && (
          <div className="pg-head-right">
            <button className="btn btn-primary" onClick={() => inventoryService.assignmentReceipt(item.id)}><FileText size={16} /> Handover Receipt</button>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        <Card title="Overview">
          <Row k="Status" v={item.status_display} />
          <Row k="Condition" v={item.condition_display} />
          <Row k="Serial No." v={item.serial_number} />
          <Row k="Department" v={item.department_name} />
          <Row k="Vendor" v={item.vendor} />
          <Row k="Purchase Date" v={item.purchase_date ? `${item.purchase_date_bs || ''} BS · ${item.purchase_date}` : ''} />
          <Row k="Purchase Cost" v={item.purchase_cost} />
          {/* Where it physically is — distinct from Department, which is whose it is. */}
          <Row k="Location" v={item.location} />
        </Card>

        {(item.photo || item.document) && (
          <Card title="Photo &amp; Documents">
            {item.photo && (
              <img src={item.photo} alt={`${item.name}`}
                style={{ maxWidth: '100%', borderRadius: 8, marginBottom: 10 }} />
            )}
            {item.document && (
              <a href={item.document} target="_blank" rel="noreferrer">
                {item.document_name || 'Purchase document'}
              </a>
            )}
          </Card>
        )}

        <Card title="Current Holder">
          {item.current_holder ? (
            <>
              <Row k="Employee" v={item.current_holder} />
              <Row k="Since" v={item.assigned_date ? `${item.assigned_date_bs || ''} BS · ${item.assigned_date}` : '—'} />
            </>
          ) : <div style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', padding: '8px 0' }}>Not currently assigned.</div>}
        </Card>

        {(specs.length > 0 || genericSpecs.length > 0) && (
          <Card title="Specifications">
            {specs.map(([k, v]) => <Row key={k} k={k} v={v} />)}
            {genericSpecs.map(([k, v]) => <Row key={k} k={k} v={String(v)} />)}
          </Card>
        )}
      </div>

      {/* Phase ASSET-LIFECYCLE-DISPOSAL. What it is worth, what cover it has and
          where it has been - all derived on the server. This replaced the
          Warranty and Disposal cards, which showed the stored fields rather
          than the position derived from them. */}
      <div style={{ marginTop: 16 }}>
        <AssetLifecycle itemId={id} />
      </div>

      {/* Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD. Who has held it, in order,
          as sentences - shares the custody request with the panel below. */}
      <div style={{ marginTop: 16 }}>
        <OwnerHistory itemId={id} />
      </div>

      {/* Phase ASSET-CUSTODY-TRANSFER. Custody status, the transfer, maintenance
          and request histories, and signed document links in one record. */}
      <div style={{ marginTop: 16 }}>
        <CustodyRecord itemId={id} />
      </div>

      <div style={{ marginTop: 16 }}>
        <Card title="Assignment History">
          {history.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>No assignment history.</div>
          ) : (
            <div className="resp-table-wrap">
              <table className="resp-table">
                <thead><tr><th>Holder</th><th>Type</th><th>From</th><th>Condition</th><th>Returned</th><th>Return Cond.</th></tr></thead>
                <tbody>
                  {history.map(h => (
                    <tr key={h.id}>
                      <td data-label="Holder">{h.assigned_to_name}{h.is_active && <span style={{ marginLeft: 6, fontSize: 'var(--fs-label)', color: 'var(--brand-blue)' }}>(active)</span>}</td>
                      <td data-label="Type">{h.is_handover ? 'Handover' : 'Assign'}</td>
                      <td data-label="From">{h.assigned_date_bs || h.assigned_date || '—'}</td>
                      <td data-label="Condition">{h.handover_condition_display || '—'}</td>
                      <td data-label="Returned">{h.returned_at ? new Date(h.returned_at).toLocaleDateString('en-GB') : '—'}</td>
                      <td data-label="Return Cond.">{h.return_condition_display || '—'}{h.return_remarks ? ` · ${h.return_remarks}` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Phase 70.11. The custody table above answers "who has held this"; this
            answers "what has happened to it" - every lifecycle transition, written
            by the engine at the moment the status changed. Renders nothing for a
            reader the endpoint refuses rather than showing an empty panel. */}
        <AssetHistory itemId={id} />
      </div>
    </div>
  );
};

export default InventoryItemDetail;
