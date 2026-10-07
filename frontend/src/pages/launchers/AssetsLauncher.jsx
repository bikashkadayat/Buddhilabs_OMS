import React from 'react';
import {
  ArrowLeftRight, Boxes, ClipboardCheck, Eye, Inbox, Package, PackageCheck, Send,
  Trash2, UserRoundCheck, Wrench,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import ModuleLauncher from '../../components/layout/ModuleLauncher';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';
// assetLifecycle, not the neighbouring inventoryService export: `dashboard`
// lives on the former. Reaching for the wrong object made `queryFn` undefined,
// so this query never ran and every tile on the launcher showed "—" instead of
// a count. React Query said so on the console and nothing else did.
import { assetLifecycle } from '../../services/inventoryService';

/**
 * Assets launcher (Phase E).
 *
 * /inventory/dashboard/ returns TWO shapes: an organisation view with `counts`
 * for anyone who may see the whole register, and a personal view with no
 * `counts` key at all for everybody else - deliberately, so an Employee gets
 * their own picture rather than a 403. Reading data.counts.* unguarded would
 * throw for most of the organisation, so every read here branches on `scope`.
 */
const AssetsLauncher = () => {
  const { role } = useAuth();
  const { data } = useQuery({
    queryKey: ['inventory', 'dashboard'],
    queryFn: assetLifecycle.dashboard,
    staleTime: 60_000,
    retry: false,
  });

  const org = data?.scope === 'organisation';
  const c = org ? data.counts : null;
  const mine = !org ? data : null;

  // Cards follow the ROUTE's own role gates (App.jsx), so no card leads to
  // "Unauthorized". Three did, for every employee.
  const approves = ['checker', 'approver', 'admin'].includes(role);
  const readsRegister = can(role, 'assetRegisterRead');

  const cards = [
    // Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD. Offered only to somebody the
    // server says may browse the whole register - a card that led an employee to
    // a refusal would be a dead end on the module's front page.
    ...(data?.roles?.can_browse_register ? [{
      key: 'visibility', title: 'Asset Visibility', icon: Eye, accent: '#1f5f8b',
      to: '/inventory/visibility',
      counts: org ? [['assets on the books', c?.total_assets]] : undefined,
      blurb: 'Every department, every owner — read-only',
    }] : []),
    {
      key: 'mine', title: 'My assets', icon: Package, accent: '#274095', to: '/inventory/my-assets',
      counts: [['in your care', mine?.assigned?.length ?? c?.assigned]],
      action: { label: 'View', to: '/inventory/my-assets' },
    },
    {
      key: 'requests', title: 'Requests', icon: Inbox, accent: '#0f7f8b', to: '/inventory/requests',
      counts: [['open', org ? c?.pending_requests : (mine?.requests?.length ?? 0)]],
      action: { label: 'Request an asset', to: '/inventory/requests' },
    },
    // Approvers get the approval queue; everybody else gets THEIR OWN
    // take-out requests -- which had no link anywhere, while this card sent
    // employees to an approval page their role cannot open.
    {
      key: 'takeouts', title: 'Take-outs', icon: Send, accent: '#a35b06',
      to: approves ? '/inventory/approvals' : '/inventory/my-requests',
      counts: [['overdue returns', org ? c?.overdue_returns : (mine?.take_outs?.length ?? 0)]],
    },
    {
      key: 'maintenance', title: 'Maintenance', icon: Wrench, accent: '#4c3a9e', to: '/inventory/maintenance',
      counts: org ? [['open tickets', c?.open_maintenance_tickets]] : undefined,
      blurb: org ? null : 'Report a fault with an asset',
    },
    ...(approves ? [{
      key: 'assignment', title: 'Assignment', icon: ClipboardCheck, accent: '#3c4a6e', to: '/inventory/assignment',
      blurb: 'Assign and reclaim devices',
    }] : []),
    // Phase ASSET-CUSTODY-TRANSFER.
    {
      key: 'transfers', title: 'Transfers', icon: ArrowLeftRight, accent: '#315d9c',
      to: '/inventory/transfers',
      counts: org ? [['awaiting approval', c?.pending_transfers]] : undefined,
      blurb: org ? null : 'Transfers of assets you give or receive',
    },
    {
      key: 'returns', title: 'Returns', icon: PackageCheck, accent: '#5b6b2e',
      to: '/inventory/returns',
      counts: org ? [['awaiting the store', c?.assets_awaiting_return]] : undefined,
      blurb: org ? null : 'Give an asset back to the store',
    },
    {
      key: 'clearance', title: 'Exit clearance', icon: UserRoundCheck, accent: '#8a3b52',
      to: '/inventory/exit-clearance',
      counts: org ? [['held by former staff', c?.assets_held_by_inactive_employees]] : undefined,
      blurb: org ? null : 'Whether any asset is still in your name',
    },
    // Phase ASSET-LIFECYCLE-DISPOSAL. Store-keeping, not something an
    // individual employee does: shown to the people who run the register.
    ...(readsRegister ? [{
      key: 'disposals', title: 'Disposal', icon: Trash2, accent: '#8d4a2f',
      to: '/inventory/disposals',
      counts: org ? [['awaiting approval', c?.pending_disposals]] : undefined,
      blurb: org ? null : 'Assets being taken off the books',
    }, {
      key: 'register', title: 'Register', icon: Boxes, accent: '#126b4e', to: '/inventory',
      counts: org ? [['assets on the books', c?.total_assets]] : undefined,
      blurb: org ? null : 'The full asset register',
    }] : []),
  ];

  return (
    <ModuleLauncher
      title="Assets"
      description="What the organisation owns, and who is holding it."
      cards={cards}
    />
  );
};

export default AssetsLauncher;
