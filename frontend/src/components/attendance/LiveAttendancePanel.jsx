import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { attendanceService } from '../../services/attendanceService';
import { useAttendanceStream } from '../../hooks/useAttendanceStream';
import useAutoRefresh from '../../hooks/useAutoRefresh';
import LivePresenceCard from './LivePresenceCard';
import DeviceStatusCard from './DeviceStatusCard';
import RecentPunchesFeed from './RecentPunchesFeed';

/**
 * Container for the live attendance widgets.
 *
 * One query feeds all three cards, so the counts, the device list and the punch
 * feed can never disagree with each other. A WebSocket event is treated purely
 * as a signal to re-read that query — the REST response stays the single source
 * of truth, which is why a dropped message cannot corrupt the view and why the
 * polling fallback needs no separate code path.
 */

const POLL_MS = 20000;

const LiveAttendancePanel = () => {
  const { data, refetch, isLoading, isError } = useQuery({
    queryKey: ['attendance', 'dashboard'],
    queryFn: attendanceService.dashboard,
  });

  const { connected } = useAttendanceStream({ onEvent: () => refetch() });

  // Interval polling runs only while the socket is down; focus/visibility
  // refresh stays on either way. See useAutoRefresh.
  useAutoRefresh(refetch, connected ? 0 : POLL_MS);

  if (isLoading) return <div className="table-card att-card">Loading live attendance…</div>;
  if (isError) return null;  // never block the rest of the dashboard

  return (
    <div className="att-live-grid">
      <LivePresenceCard data={data} connected={connected} />
      <DeviceStatusCard devices={data?.devices} />
      <RecentPunchesFeed punches={data?.recent_punches} connected={connected} />
    </div>
  );
};

export default LiveAttendancePanel;
