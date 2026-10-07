import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { inventoryService } from '../../services/inventoryService';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { EMPTY } from '../../services/emptyStates';

const MyAssignedAssets = () => {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setRows(await inventoryService.myAssets()); }
    catch (e) { setError(e?.response?.data?.detail || e.message || 'Failed to load your assets.'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(load, 30000);

  return (
    <div className="page">
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb"><button className="pg-back" aria-label="Back" onClick={() => navigate('/inventory')}><ArrowLeft size={18} /></button>Inventory</div>
          <div className="pg-title">My Assigned Assets</div>
          <div className="pg-desc">Office items currently assigned to you (read-only)</div>
        </div>
      </div>

      <div className="table-card">
        <div className="tc-top"><span className="tc-title">Assets ({rows.length})</span></div>
        {loading ? (
          <Skeleton rows={3} label="Loading assets" />
        ) : error ? (
          <div style={{ padding: 32, textAlign: 'center' }}><div style={{ color: 'var(--danger,#b91c1c)', marginBottom: 12 }}>{error}</div><button className="btn btn-primary" onClick={load}>Retry</button></div>
        ) : rows.length === 0 ? (
          <EmptyState variant="first" title={EMPTY.noAssetsAssigned} />
        ) : (
          <div className="resp-table-wrap">
            <table className="resp-table">
              <thead><tr><th>Asset Code</th><th>Item</th><th>Specification</th><th>Category</th><th>Assigned Since</th><th>Condition</th></tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td data-label="Asset Code"><strong>{r.item_code}</strong></td>
                    <td data-label="Item">{r.item_name}<div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.asset_type}</div></td>
                    <td data-label="Specification">{r.spec || '—'}{r.accessories ? <div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>Accessories: {r.accessories}</div> : null}</td>
                    <td data-label="Category">{r.category_name || '—'}</td>
                    <td data-label="Assigned Since">{r.assigned_date_bs ? `${r.assigned_date_bs} BS · ${r.assigned_date}` : (r.assigned_date || '—')}</td>
                    <td data-label="Condition">{r.handover_condition_display || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default MyAssignedAssets;
