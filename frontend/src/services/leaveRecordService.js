import api from './api';

/**
 * Phase 6 - Enterprise Leave Records service.
 *
 * NOTE ON URLS: the Phase 6 brief assumed some endpoints under /leaves/*, but
 * the Phase 4 backend actually mounts several of them at the top level. This
 * service targets the REAL backend routes:
 *   my-history        GET /leaves/my-history/?year=
 *   balances          GET /leave-balances/?year=          (brief said /leaves/balances/)
 *   calendar          GET /leaves/calendar/?start=&end=&user_id=
 *   weekly summaries  GET /weekly-summaries/?year=&user_id= (brief said /leaves/weekly-summaries/)
 *   monthly summaries GET /monthly-summaries/?year=&user_id=
 *   leave types       GET /leave-types/                    (brief said /leaves/types/)
 *   holidays          GET /holidays/?year=                 (brief said /leaves/holidays/)
 *   team attendance   GET /leaves/team-attendance/?department=&month=YYYY-MM
 *
 * ModelViewSet endpoints are paginated ({ count, results }); APIView endpoints
 * (my-history, calendar, team-attendance) return a plain object/array. The
 * `unwrap` helper below normalizes both.
 */

/** @param {import('axios').AxiosResponse} res */
const unwrapList = (res) =>
  Array.isArray(res.data) ? res.data : (res.data?.results ?? []);

export const leaveRecordService = {
  /**
   * @typedef {Object} LeaveBalance
   * @property {string} leave_type_code
   * @property {string} leave_type_name
   * @property {string} entitled_days
   * @property {string} carried_forward_days
   * @property {string} used_days
   * @property {string} pending_days
   * @property {string} available_days
   *
   * @typedef {Object} MonthlySummary
   * @property {number} year @property {number} month
   * @property {string} total_leave_days @property {Object<string,string>} by_type
   * @property {string} approved_days @property {string} pending_days
   * @property {number} working_days @property {string} attendance_percentage
   *
   * @typedef {Object} LeaveHistory
   * @property {{id:string,full_name:string,email:string,role:string,department:string}} user
   * @property {number} year
   * @property {LeaveBalance[]} balances
   * @property {Object[]} recent_leaves
   * @property {MonthlySummary[]} monthly_summaries
   *
   * @param {number} year
   * @returns {Promise<LeaveHistory>}
   */
  getMyHistory: async (year) => {
    const res = await api.get('/leaves/my-history/', { params: { year } });
    return res.data;
  },

  /**
   * @param {number} year
   * @returns {Promise<LeaveBalance[]>}
   */
  getMyBalances: async (year) => {
    const res = await api.get('/leave-balances/', { params: { year } });
    return unwrapList(res);
  },

  /**
   * @typedef {Object} LeaveDayRecord
   * @property {string} date
   * @property {'full'|'first_half'|'second_half'} day_portion
   * @property {string} portion_days
   * @property {string} leave_type_code
   * @property {string} display_color
   * @property {'pending'|'approved'|'rejected'|'cancelled'} status
   * @property {boolean} is_holiday @property {boolean} is_weekend
   *
   * @param {string} startDate ISO yyyy-mm-dd
   * @param {string} endDate   ISO yyyy-mm-dd
   * @param {string} [userId]
   * @returns {Promise<LeaveDayRecord[]>}
   */
  getCalendar: async (startDate, endDate, userId) => {
    const res = await api.get('/leaves/calendar/', {
      params: { start: startDate, end: endDate, user_id: userId },
    });
    return Array.isArray(res.data) ? res.data : (res.data?.results ?? []);
  },

  /**
   * @param {number} year @param {string} [userId]
   * @returns {Promise<Object[]>} weekly summaries
   */
  getWeeklySummaries: async (year, userId) => {
    const res = await api.get('/weekly-summaries/', { params: { year, user_id: userId } });
    return unwrapList(res);
  },

  /**
   * @param {number} year @param {string} [userId]
   * @returns {Promise<MonthlySummary[]>}
   */
  getMonthlySummaries: async (year, userId) => {
    const res = await api.get('/monthly-summaries/', { params: { year, user_id: userId } });
    return unwrapList(res);
  },

  /**
   * @typedef {Object} LeaveType
   * @property {string} code @property {string} name
   * @property {string} default_days_per_year @property {string} display_color
   * @returns {Promise<LeaveType[]>}
   */
  // Self-scoped weekly/monthly leave summary PDF (NIF letterhead). Returns the
  // full axios blob response for saveBlob().
  reportPdf: (period, year) =>
    api.get(`/leaves/my-report/${period}/`, { params: { year }, responseType: 'blob' }),

  getLeaveTypes: async () => {
    const res = await api.get('/leave-types/');
    return unwrapList(res);
  },

  /**
   * @param {number} year
   * @returns {Promise<{date:string,name:string,holiday_type:string}[]>}
   */
  getHolidays: async (year) => {
    const res = await api.get('/holidays/', { params: { year } });
    return unwrapList(res);
  },

  /**
   * Festivals and observances for a date RANGE — the Nepali calendar's events.
   *
   * A range rather than a year because a Bikram Sambat month can straddle two
   * Gregorian years, and a year-keyed query drops everything past the boundary.
   *
   * @param {string} start YYYY-MM-DD
   * @param {string} end   YYYY-MM-DD
   * @returns {Promise<{date:string,display_name:string,tithi:string,
   *                    event_type:string,is_public_holiday:boolean}[]>}
   */
  getCalendarEvents: async (start, end) => {
    const res = await api.get('/leaves/calendar-events/', { params: { start, end } });
    return unwrapList(res);
  },

  /**
   * @param {string} dept department code
   * @param {string} month YYYY-MM
   * @returns {Promise<{department:string,year:number,month:number,team:Object[]}>}
   */
  getTeamAttendance: async (dept, month) => {
    const res = await api.get('/leaves/team-attendance/', {
      params: { department: dept, month },
    });
    return res.data;
  },
};

export default leaveRecordService;
