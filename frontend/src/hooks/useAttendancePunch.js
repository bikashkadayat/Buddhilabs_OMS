import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  attendanceService, getCurrentLocation, classifyFix,
} from '../services/attendanceService';
import { describeApiError } from '../services/apiErrors';

/**
 * Today's attendance and the check-in / check-out action, in one place.
 *
 * Shared by the home page hero and the attendance widget, so the two can
 * never disagree about whether somebody is checked in -- they read the same
 * `['attendance', 'today']` query and run the same mutation.
 *
 * THE COORDINATES ARE NEVER TYPED. They come from `navigator.geolocation` or
 * not at all, which is what makes the pin evidence rather than a claim. The
 * server decides whether a check-in without one is acceptable.
 */
export const useAttendancePunch = () => {
  const qc = useQueryClient();
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState('');

  const today = useQuery({
    queryKey: ['attendance', 'today'],
    queryFn: attendanceService.today,
    staleTime: 60_000,
    retry: false,
  });

  const punch = useMutation({
    mutationFn: async (kind) => {
      setLocating(true);
      let coords = {};
      try {
        const fix = await getCurrentLocation();
        if (fix) {
          coords = {
            latitude: fix.latitude,
            longitude: fix.longitude,
            accuracy: fix.accuracy,
            location_source: classifyFix(fix),
          };
        }
      } finally {
        setLocating(false);
      }
      return kind === 'in'
        ? attendanceService.checkIn(coords)
        : attendanceService.checkOut(coords);
    },
    onSuccess: () => {
      setError('');
      qc.invalidateQueries({ queryKey: ['attendance', 'today'] });
    },
    onError: (e, kind) => setError(describeApiError(e, kind === 'in'
      ? "We couldn't record your check-in. Please try again."
      : "We couldn't record your check-out. Please try again.")),
  });

  return {
    today: today.data,
    isLoading: today.isLoading,
    isError: today.isError,
    punch: punch.mutate,
    pending: punch.isPending,
    pendingKind: punch.variables,
    locating,
    error,
  };
};

export default useAttendancePunch;
