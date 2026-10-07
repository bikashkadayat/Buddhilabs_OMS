import React, { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Login from './pages/Login';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
// Phase S7: public self-service workspace creation. Eagerly imported
// like Login, because both are reached by somebody with no session and
// a lazy chunk on a cold visit is a spinner before the first paint.
import Register from './pages/Register';
// Phase S8: the customer's own subscription page. Lazy, because most
// users are not administrators and will never open it.
const SettingsSubscription = lazy(
  () => import('./pages/settings/Subscription'));
// Phase S9: the customer's own branding and custom domain. Lazy for the
// same reason -- an administrator visits these twice a year.
const SettingsIndex = lazy(() => import('./pages/settings/Index'));
const AttendanceRules = lazy(() => import('./pages/admin/AttendanceRules'));
const SettingsBranding = lazy(() => import('./pages/settings/Branding'));
const SettingsDomains = lazy(() => import('./pages/settings/Domains'));
import VerifyEmail from './pages/VerifyEmail';
// Phase 203. `/` is now a real page rather than a redirect. RoleLanding.jsx is
// retained unimported for one release: restoring it as the index element is the
// entire rollback for Phase A.
import Home from './pages/Home';
import WorkQueue from './pages/WorkQueue';
import Profile from './pages/Profile';
import RequireAuth from './components/common/RequireAuth';
// Phase S6. The Platform Admin Console: a separate application in the same
// bundle, reached only by platform staff and (once TENANCY_ENABLED is on)
// served only on a platform host. Lazy, because no tenant user will ever load
// one byte of it.
import RequirePlatform from './components/common/RequirePlatform';

// Leave Pages (core, kept eager for a fast first paint)
import LeaveDashboard from './pages/leave/Dashboard';
import ApplyLeave from './pages/leave/ApplyLeave';
import MyApplications from './pages/leave/MyApplications';
import LeaveBalance from './pages/leave/LeaveBalance';
import LeavePolicy from './pages/leave/LeavePolicy';
import PendingApprovals from './pages/leave/PendingApprovals';
import TeamCalendar from './pages/leave/TeamCalendar';

// Phase 6 - Enterprise Leave Records pages
import MyLeaveHistory from './pages/leave/records/MyLeaveHistory';
import MyLeaveCalendar from './pages/leave/records/MyLeaveCalendar';
import NepaliCalendar from './pages/calendar/NepaliCalendar';
import TeamAttendance from './pages/leave/records/TeamAttendance';

// Phase 9 - Notifications
import NotificationsPage from './pages/NotificationsPage';

// Phase 2.5 - Auth / User Management
import FirstLoginPasswordChange from './pages/FirstLoginPasswordChange';
import Unauthorized from './pages/Unauthorized';
import Skeleton from './components/common/Skeleton';

// L2: code-split the heavy/less-frequent route groups. recharts (reports +
// analytics), the admin consoles, and the TipTap-backed memo pages load on
// demand instead of bloating the initial bundle.
const WeeklyReport = lazy(() => import('./pages/leave/records/WeeklyReport'));
const MonthlyReport = lazy(() => import('./pages/leave/records/MonthlyReport'));

const EmployeeList = lazy(() => import('./pages/admin/leaves/EmployeeList'));
const EmployeeDetail = lazy(() => import('./pages/admin/leaves/EmployeeDetail'));
const PolicyManagement = lazy(() => import('./pages/admin/leaves/PolicyManagement'));
const CalendarManagement = lazy(() => import('./pages/admin/leaves/CalendarManagement'));
const DepartmentManagement = lazy(() => import('./pages/admin/leaves/DepartmentManagement'));
const LeaveTypeManagement = lazy(() => import('./pages/admin/leaves/LeaveTypeManagement'));
const BulkActions = lazy(() => import('./pages/admin/leaves/BulkActions'));
const AttendanceReports = lazy(() => import('./pages/attendance/AttendanceReports'));
const AttendanceRecords = lazy(() => import('./pages/attendance/AttendanceRecords'));
const BiometricAttendance = lazy(() => import('./pages/attendance/BiometricAttendance'));
const MyAttendance = lazy(() => import('./pages/attendance/MyAttendance'));
const UserManagement = lazy(() => import('./pages/admin/users/UserManagement'));

// Phase 9.1 - Workforce management. Route-level code splitting: none of these
// touch the initial bundle, and the recharts-backed dashboards load on demand.
const WorkforcePortal = lazy(() => import('./pages/workforce/Portal'));
const WorkforceCorrections = lazy(() => import('./pages/workforce/Corrections'));
const WorkforceTeam = lazy(() => import('./pages/workforce/TeamDashboard'));
const WorkforceHR = lazy(() => import('./pages/workforce/CommandCenter'));
const WorkforceWFH = lazy(() => import('./pages/workforce/WFH'));
const WorkforceCompOff = lazy(() => import('./pages/workforce/CompOff'));
const WorkforceConflicts = lazy(() => import('./pages/workforce/Conflicts'));
const WorkforceReports = lazy(() => import('./pages/workforce/Reports'));

// Phase 10 - Executive analytics. Route-level code splitting again: nine
// recharts-backed dashboards must not touch the initial bundle.
const AnalyticsExecutive = lazy(() => import('./pages/analytics/Executive'));
const ExecutiveDashboard = lazy(() => import('./pages/executive/ExecutiveDashboard'));
const AnalyticsHR = lazy(() => import('./pages/analytics/HRKpi'));
const AnalyticsManagement = lazy(() => import('./pages/analytics/Management'));
const AnalyticsAttendance = lazy(() => import('./pages/analytics/AttendanceTrends'));
const AnalyticsDepartments = lazy(() => import('./pages/analytics/Departments'));
const AnalyticsLeave = lazy(() => import('./pages/analytics/LeaveAnalytics'));
const AnalyticsWfh = lazy(() => import('./pages/analytics/WfhAnalytics'));
const AnalyticsCompOff = lazy(() => import('./pages/analytics/CompOffAnalytics'));
const AnalyticsDevices = lazy(() => import('./pages/analytics/DeviceAnalytics'));

// Phase 11 - System monitoring (HR/Admin only).
const SystemHealth = lazy(() => import('./pages/monitoring/SystemHealth'));

const ReportsHub = lazy(() => import('./pages/reports/ReportsHub'));
const ReportBuilder = lazy(() => import('./pages/reports/ReportBuilder'));
const ReportHistory = lazy(() => import('./pages/reports/ReportHistory'));

const InventoryList = lazy(() => import('./pages/inventory/InventoryList'));
const InventoryItemDetail = lazy(() => import('./pages/inventory/InventoryItemDetail'));
const AssetAssignment = lazy(() => import('./pages/inventory/AssetAssignment'));
const MyAssignedAssets = lazy(() => import('./pages/inventory/MyAssignedAssets'));
const MyTakeOutRequests = lazy(() => import('./pages/inventory/MyTakeOutRequests'));
const TakeOutApprovals = lazy(() => import('./pages/inventory/TakeOutApprovals'));
// Phase 70 - asset lifecycle management.
const InventoryDashboard = lazy(() => import('./pages/inventory/InventoryDashboard'));
const AssetRequests = lazy(() => import('./pages/inventory/AssetRequests'));
const MaintenanceTickets = lazy(() => import('./pages/inventory/MaintenanceTickets'));
const InventoryReports = lazy(() => import('./pages/inventory/InventoryReports'));
const AssetTransfers = lazy(() => import('./pages/inventory/AssetTransfers'));
const AssetReturns = lazy(() => import('./pages/inventory/AssetReturns'));
const ExitClearance = lazy(() => import('./pages/inventory/ExitClearance'));
const AssetDisposals = lazy(() => import('./pages/inventory/AssetDisposals'));
const AssetVisibility = lazy(() => import('./pages/inventory/AssetVisibility'));

// Phase E launchers. Mounted at NEW paths only - /reports keeps ReportsHub, so
// nothing existing is replaced.
const ProfileWorkspace = lazy(() => import('./pages/ProfileWorkspace'));
const DocumentsLauncher = lazy(() => import('./pages/launchers/DocumentsLauncher'));
const PeopleLauncher = lazy(() => import('./pages/launchers/PeopleLauncher'));
const AssetsLauncher = lazy(() => import('./pages/launchers/AssetsLauncher'));
const ReportsLauncher = lazy(() => import('./pages/launchers/ReportsLauncher'));

// Phase S6 - the Platform Admin Console. Code-split as one group: an operator
// opens all four screens in a session and a tenant user opens none of them.
const PlatformLayout = lazy(() => import('./components/platform/PlatformLayout'));
const PlatformDashboard = lazy(() => import('./pages/platform/Dashboard'));
const PlatformOrganizations = lazy(() => import('./pages/platform/Organizations'));
const PlatformOrganizationDetail = lazy(() => import('./pages/platform/OrganizationDetail'));
const PlatformAuditTrail = lazy(() => import('./pages/platform/AuditTrail'));
const PlatformPayments = lazy(() => import('./pages/platform/Payments'));
const PlatformPaymentMethods = lazy(() => import('./pages/platform/PaymentMethods'));
const PlatformCustomerHealth = lazy(() => import('./pages/platform/CustomerHealth'));
const PlatformSupport = lazy(() => import('./pages/platform/SupportInbox'));
const HelpCenter = lazy(() => import('./pages/help/HelpCenter'));
const HelpArticle = lazy(() => import('./pages/help/HelpArticle'));
const HelpSupport = lazy(() => import('./pages/help/Support'));
const GettingStarted = lazy(() => import('./pages/GettingStarted'));
const PlatformPlans = lazy(() => import('./pages/platform/Plans'));
const PlatformSubscriptions = lazy(() => import('./pages/platform/Subscriptions'));
const PlatformUsage = lazy(() => import('./pages/platform/Usage'));
const PlatformHealth = lazy(() => import('./pages/platform/Health'));
const PlatformDomains = lazy(() => import('./pages/platform/Domains'));
const PlatformProfile = lazy(() => import('./pages/platform/Profile'));
const PlatformSettings = lazy(() => import('./pages/platform/Settings'));
const PlatformLaunchReadiness = lazy(
  () => import('./pages/platform/LaunchReadiness'));

const MemoDashboard = lazy(() => import('./pages/memo/MemoDashboard'));
const CreateMemo = lazy(() => import('./pages/memo/CreateMemo'));
// Phase 111C: autosaved drafts across all three document modules.
const UnfinishedWork = lazy(() => import('./pages/drafts/UnfinishedWork'));
const EditMemo = lazy(() => import('./pages/memo/EditMemo'));
const MemoDetail = lazy(() => import('./pages/memo/MemoDetail'));
// The Phase 3 menus are one component bound to different server-side scopes, so
// they share a single chunk rather than ten near-identical ones.
const memoScopes = () => import('./pages/memo/scopePages');
const AllMemos = lazy(() => memoScopes().then((m) => ({ default: m.AllMemos })));
const DraftMemos = lazy(() => memoScopes().then((m) => ({ default: m.DraftMemos })));
const DraftForReview = lazy(() => memoScopes().then((m) => ({ default: m.DraftForReview })));
const DepartmentMemos = lazy(() => memoScopes().then((m) => ({ default: m.DepartmentMemos })));
const MyPendingActions = lazy(() => memoScopes().then((m) => ({ default: m.MyPendingActions })));
const MemoInbox = lazy(() => memoScopes().then((m) => ({ default: m.MemoInbox })));
const MemoOutbox = lazy(() => memoScopes().then((m) => ({ default: m.MemoOutbox })));
const ApprovedMemos = lazy(() => memoScopes().then((m) => ({ default: m.ApprovedMemos })));
const ArchivedMemos = lazy(() => memoScopes().then((m) => ({ default: m.ArchivedMemos })));
const RejectedMemos = lazy(() => memoScopes().then((m) => ({ default: m.RejectedMemos })));
// --- Minutes (Phases 31-41) ---------------------------------------------------
// Same shape as the memo module above: the eight sidebar menus are one component
// bound to different server-side scopes, so they share a single chunk.
const MinuteDashboard = lazy(() => import('./pages/minute/MinuteDashboard'));
const MinuteForm = lazy(() => import('./pages/minute/MinuteForm'));
const MinuteDetail = lazy(() => import('./pages/minute/MinuteDetail'));
const minuteScopes = () => import('./pages/minute/scopePages');

// --- Circulars (Phase 50) -----------------------------------------------------
const CircularDashboard = lazy(() => import('./pages/circular/CircularDashboard'));
const CircularForm = lazy(() => import('./pages/circular/CircularForm'));
const CircularDetail = lazy(() => import('./pages/circular/CircularDetail'));
const circularScopes = () => import('./pages/circular/scopePages');
const AllCirculars = lazy(() => circularScopes().then((m) => ({ default: m.AllCirculars })));
const DraftCirculars = lazy(() => circularScopes().then((m) => ({ default: m.DraftCirculars })));
const AssignedCirculars = lazy(() => circularScopes().then((m) => ({ default: m.AssignedCirculars })));
const UnderReviewCirculars = lazy(() => circularScopes().then((m) => ({ default: m.UnderReviewCirculars })));
const ReadyForIssue = lazy(() => circularScopes().then((m) => ({ default: m.ReadyForIssue })));
const ReadyForBroadcast = lazy(() => circularScopes().then((m) => ({ default: m.ReadyForBroadcast })));
const BroadcastedCirculars = lazy(() => circularScopes().then((m) => ({ default: m.BroadcastedCirculars })));
const MyCircularAcknowledgements = lazy(() => circularScopes().then((m) => ({ default: m.MyAcknowledgements })));
const UnreadCirculars = lazy(() => circularScopes().then((m) => ({ default: m.UnreadCirculars })));
const ArchivedCirculars = lazy(() => circularScopes().then((m) => ({ default: m.ArchivedCirculars })));
// --- Tasks (Phase T1) ---------------------------------------------------------
// Same shape as the memo and minute modules above: the module's menus are one
// component bound to different server-side scopes, so they share a single chunk
// rather than nine near-identical ones.
const TaskDashboard = lazy(() => import('./pages/task/TaskDashboard'));
const TaskForm = lazy(() => import('./pages/task/TaskForm'));
const TaskDetail = lazy(() => import('./pages/task/TaskDetail'));
const TaskTemplates = lazy(() => import('./pages/task/TaskTemplates'));
// Phase T3 workspace views.
const TaskCalendar = lazy(() => import('./pages/task/TaskCalendar'));
const TaskWorkload = lazy(() => import('./pages/task/TaskWorkload'));
const TaskOverdueScreen = lazy(() => import('./pages/task/TaskOverdue'));
const TaskReports = lazy(() => import('./pages/task/TaskReports'));
const TaskReviewQueue = lazy(() => import('./pages/task/TaskReviewQueue'));
// Phase T5 — performance intelligence.
const TaskAnalytics = lazy(() => import('./pages/task/TaskAnalytics'));
const TaskEvidence = lazy(() => import('./pages/task/TaskEvidence'));
const TaskTeamEvidence = lazy(() => import('./pages/task/TaskTeamEvidence'));
const taskScopes = () => import('./pages/task/scopePages');
const TaskNeedsMe = lazy(() => taskScopes().then((m) => ({ default: m.NeedsMyAction })));
const MyTasks = lazy(() => taskScopes().then((m) => ({ default: m.MyTasks })));
const AssignedByMe = lazy(() => taskScopes().then((m) => ({ default: m.AssignedByMe })));
const TeamTasks = lazy(() => taskScopes().then((m) => ({ default: m.TeamTasks })));
const TasksDueToday = lazy(() => taskScopes().then((m) => ({ default: m.DueToday })));
const OverdueTasks = lazy(() => taskScopes().then((m) => ({ default: m.OverdueTasks })));
const CompletedTasks = lazy(() => taskScopes().then((m) => ({ default: m.CompletedTasks })));
const AllTasks = lazy(() => taskScopes().then((m) => ({ default: m.AllTasks })));
const TaskBoardPage = lazy(() => taskScopes().then((m) => ({ default: m.TaskBoardPage })));
const DraftTasks = lazy(() => taskScopes().then((m) => ({ default: m.DraftTasks })));

// --- Appraisal (Phase APM-03b) ------------------------------------------------
// Four role workspaces plus one shared record page. The RECORD is deliberately
// one route for every role: two pages rendering the same appraisal is two places
// a permission can be got wrong, and only one of them would be tested the day
// somebody changes a rule. Which controls appear comes from the server's
// `capabilities` block.
const MyAppraisal = lazy(() => import('./pages/appraisal/MyAppraisal'));
const MyGoals = lazy(() => import('./pages/appraisal/MyGoals'));
const MyEvidence = lazy(() => import('./pages/appraisal/MyEvidence'));
const AppraisalDetail = lazy(() => import('./pages/appraisal/AppraisalDetail'));
const TeamAppraisals = lazy(() => import('./pages/appraisal/TeamAppraisals'));
const CommitteeQueue = lazy(() => import('./pages/appraisal/CommitteeQueue'));
const HRAppraisalDashboard = lazy(() => import('./pages/appraisal/HRAppraisalDashboard'));
const AppraisalCycles = lazy(() => import('./pages/appraisal/AppraisalCycles'));
const AppraisalReports = lazy(() => import('./pages/appraisal/AppraisalReports'));
const NewAppraisal = lazy(() => import('./pages/appraisal/NewAppraisal'));

const AllMinutes = lazy(() => minuteScopes().then((m) => ({ default: m.AllMinutes })));
const NeedsMyAction = lazy(() => minuteScopes().then((m) => ({ default: m.NeedsMyAction })));
const MyMinutes = lazy(() => minuteScopes().then((m) => ({ default: m.MyMinutes })));
const DraftMinutes = lazy(() => minuteScopes().then((m) => ({ default: m.DraftMinutes })));
const MinuteDraftForReview = lazy(() => minuteScopes().then((m) => ({ default: m.DraftForReview })));
const AssignedInitiated = lazy(() => minuteScopes().then((m) => ({ default: m.AssignedInitiated })));
const AssignedInvolvement = lazy(() => minuteScopes().then((m) => ({ default: m.AssignedInvolvement })));
const UnderProcessMinutes = lazy(() => minuteScopes().then((m) => ({ default: m.UnderProcessMinutes })));
const MyPendingAcknowledgements = lazy(() => minuteScopes().then((m) => ({ default: m.MyPendingAcknowledgements })));
const MinutesAcknowledged = lazy(() => minuteScopes().then((m) => ({ default: m.MinutesAcknowledged })));
const ArchivedMinutes = lazy(() => minuteScopes().then((m) => ({ default: m.ArchivedMinutes })));


function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<div className="page"><Skeleton rows={4} height={16} label="Loading page" /></div>}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        {/* Phase S7. Outside RequireAuth by necessity: the whole point
            is that nobody has an account yet. */}
        <Route path="/register" element={<Register />} />
        <Route path="/verify-email" element={<VerifyEmail />} />
        {/* First-login password change: protected but standalone (no chrome). */}
        <Route path="/auth/first-login-change-password" element={<RequireAuth><FirstLoginPasswordChange /></RequireAuth>} />
        {/* THE PLATFORM CONSOLE, AND IT IS NOT UNDER "/".
            Mounted as a sibling of the product rather than a page inside it,
            because it has its own chrome, its own guard and its own audience.
            Nesting it under the tenant Layout would have put a customer's
            sidebar around the screen that suspends that customer. */}
        <Route path="/platform" element={<RequirePlatform><PlatformLayout /></RequirePlatform>}>
          <Route index element={<PlatformDashboard />} />
          <Route path="organizations" element={<PlatformOrganizations />} />
          <Route path="organizations/:slug" element={<PlatformOrganizationDetail />} />
          <Route path="subscriptions" element={<PlatformSubscriptions />} />
          <Route path="plans" element={<PlatformPlans />} />
          <Route path="payments" element={<PlatformPayments />} />
          <Route path="payment-methods" element={<PlatformPaymentMethods />} />
          <Route path="customer-health" element={<PlatformCustomerHealth />} />
          <Route path="support" element={<PlatformSupport />} />
          <Route path="usage" element={<PlatformUsage />} />
          <Route path="health" element={<PlatformHealth />} />
            <Route path="domains" element={<PlatformDomains />} />
            <Route path="profile" element={<PlatformProfile />} />
            <Route path="settings" element={<PlatformSettings />} />
          <Route path="launch" element={<PlatformLaunchReadiness />} />
          <Route path="audit" element={<PlatformAuditTrail />} />
        </Route>
        <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<Home />} />
          {/* Phase 203. The union of every "waiting on me" list. Not role-gated:
              each source endpoint is already scoped server-side, so the page
              shows exactly what the user may see and nothing more. */}
          <Route path="queue" element={<WorkQueue />} />
          {/* Phase E. Launchers are additive: every route they link to already
              existed and still resolves on its own. */}
          {/* Phase F. The Profile tab's hub. /profile is untouched and linked
              from here - nothing is replaced. */}
          <Route path="me" element={<ProfileWorkspace />} />
          <Route path="documents" element={<DocumentsLauncher />} />
          <Route path="people" element={<PeopleLauncher />} />
          <Route path="assets" element={<AssetsLauncher />} />
          <Route path="reports/overview" element={<ReportsLauncher />} />
          <Route path="unauthorized" element={<Unauthorized />} />
          <Route path="admin/users" element={<RequireAuth allowedRoles={['admin']}><UserManagement /></RequireAuth>} />
          {/* Phase S8. Admin-only, matching the server: the portal refuses
              anybody who is not an administrator of this organization, and a
              route an employee can open only to be told "not for you" is a
              worse answer than a route they never see. */}
          {/* Customer success: help, support and setup, inside the workspace. */}
          <Route path="help" element={<HelpCenter />} />
          <Route path="help/contact" element={<HelpSupport />} />
          <Route path="help/:slug" element={<HelpArticle />} />
          <Route path="getting-started" element={<GettingStarted />} />
          <Route path="settings" element={<RequireAuth allowedRoles={['admin']}><SettingsIndex /></RequireAuth>} />
          {/* The setup checklist has always linked here; there was no page. */}
          <Route path="admin/attendance/policies" element={<RequireAuth allowedRoles={['approver', 'admin']}><AttendanceRules /></RequireAuth>} />
          <Route path="settings/subscription" element={<RequireAuth allowedRoles={['admin']}><SettingsSubscription /></RequireAuth>} />
          <Route path="settings/branding" element={<RequireAuth allowedRoles={['admin']}><SettingsBranding /></RequireAuth>} />
          <Route path="settings/domains" element={<RequireAuth allowedRoles={['admin']}><SettingsDomains /></RequireAuth>} />
          <Route path="admin/attendance-reports" element={<RequireAuth allowedRoles={['approver', 'bod', 'admin']}><AttendanceReports /></RequireAuth>} />
          <Route path="attendance/records" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AttendanceRecords /></RequireAuth>} />
          <Route path="admin/biometric-attendance" element={<RequireAuth allowedRoles={['admin']}><BiometricAttendance /></RequireAuth>} />
          <Route path="my-attendance" element={<MyAttendance />} />
          <Route path="profile" element={<Profile />} />
          <Route path="leave" element={<LeaveDashboard />} />
          <Route path="leave/apply" element={<RequireAuth allowedRoles={['maker', 'checker', 'approver', 'bod']}><ApplyLeave /></RequireAuth>} />
          <Route path="leave/my-applications" element={<MyApplications />} />
          <Route path="leave/balance" element={<LeaveBalance />} />
          <Route path="leave/policy" element={<LeavePolicy />} />
          <Route path="leave/pending" element={<PendingApprovals />} />
          <Route path="leave/calendar" element={<TeamCalendar />} />

          {/* Phase 6 - Enterprise Leave Records */}
          <Route path="leaves/my-history" element={<MyLeaveHistory />} />
          <Route path="leaves/my-calendar" element={<MyLeaveCalendar />} />
          {/* Its own top-level URL: the Nepali calendar is not a leave screen. */}
          <Route path="calendar" element={<NepaliCalendar />} />
          <Route path="leaves/weekly-report" element={<WeeklyReport />} />
          <Route path="leaves/monthly-report" element={<MonthlyReport />} />
          <Route path="leaves/team-attendance" element={<TeamAttendance />} />

          {/* Phase 7 - HR/Admin */}
          <Route path="admin/leaves/employees" element={<RequireAuth allowedRoles={['admin']}><EmployeeList /></RequireAuth>} />
          <Route path="admin/leaves/employees/:id" element={<RequireAuth allowedRoles={['admin']}><EmployeeDetail /></RequireAuth>} />
          <Route path="admin/leaves/policies" element={<RequireAuth allowedRoles={['admin']}><PolicyManagement /></RequireAuth>} />
          <Route path="admin/leaves/holidays" element={<RequireAuth allowedRoles={['admin']}><CalendarManagement /></RequireAuth>} />
          <Route path="admin/leaves/calendar-events" element={<RequireAuth allowedRoles={['admin']}><CalendarManagement initialTab="events" /></RequireAuth>} />
          {/* Admin-only, like every other admin page. It was guarded server-side
              alone (IsAdminOrSuperuser), which refused the data but let anybody
              reach a page of error states. */}
          <Route path="admin/leaves/departments" element={<RequireAuth allowedRoles={['admin']}><DepartmentManagement /></RequireAuth>} />
          <Route path="admin/leaves/leave-types" element={<RequireAuth allowedRoles={['admin']}><LeaveTypeManagement /></RequireAuth>} />
          <Route path="admin/leaves/bulk-actions" element={<RequireAuth allowedRoles={['admin']}><BulkActions /></RequireAuth>} />

          {/* Phase 8 - Reports & Analytics */}
          <Route path="reports" element={<ReportsHub />} />
          <Route path="reports/build/:type" element={<ReportBuilder />} />
          <Route path="reports/history" element={<ReportHistory />} />
          {/* MERGED into /analytics/leave. This page's API is admin-only, so HR and
              department heads who reached it got an error page; the leave analytics
              page serves every role that may see leave figures. */}
          <Route path="admin/analytics" element={<Navigate to="/analytics/leave" replace />} />

          {/* Phase 9.1 - Workforce management. Guards mirror the server-side
              scoping; the API is the real boundary, these just avoid showing a
              page that would only 403. */}
          <Route path="workforce" element={<WorkforcePortal />} />
          <Route path="workforce/corrections" element={<WorkforceCorrections />} />
          <Route path="workforce/team" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><WorkforceTeam /></RequireAuth>} />
          <Route path="workforce/hr" element={<RequireAuth allowedRoles={['approver', 'admin']}><WorkforceHR /></RequireAuth>} />
          <Route path="workforce/wfh" element={<WorkforceWFH />} />
          <Route path="workforce/comp-off" element={<WorkforceCompOff />} />
          <Route path="workforce/conflicts" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><WorkforceConflicts /></RequireAuth>} />
          <Route path="workforce/reports" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><WorkforceReports /></RequireAuth>} />

          {/* Phase 10 - Executive analytics. Employees have none; the
              executive, HR and device pages are HR/Admin. As in Phase 9.1
              these guards only avoid rendering a page that would 403 — the
              API is the security boundary, and it scopes every response. */}
          {/* Phase BOD-ROLE-EXECUTIVE-GOVERNANCE: the Board's read-only dashboard. */}
          <Route path="executive" element={<RequireAuth allowedRoles={['approver', 'bod', 'admin']}><ExecutiveDashboard /></RequireAuth>} />
          <Route path="analytics/executive" element={<RequireAuth allowedRoles={['approver', 'bod', 'admin']}><AnalyticsExecutive /></RequireAuth>} />
          <Route path="analytics/hr" element={<RequireAuth allowedRoles={['approver', 'bod', 'admin']}><AnalyticsHR /></RequireAuth>} />
          <Route path="analytics/devices" element={<RequireAuth allowedRoles={['approver', 'admin']}><AnalyticsDevices /></RequireAuth>} />
          <Route path="analytics/management" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsManagement /></RequireAuth>} />
          <Route path="analytics/attendance" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsAttendance /></RequireAuth>} />
          <Route path="analytics/departments" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsDepartments /></RequireAuth>} />
          <Route path="analytics/leave" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsLeave /></RequireAuth>} />
          <Route path="analytics/wfh" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsWfh /></RequireAuth>} />
          <Route path="analytics/comp-off" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><AnalyticsCompOff /></RequireAuth>} />

          {/* Phase 11 - System monitoring. Infrastructure health, backup
              state and login-failure counts: HR/Admin only, since a
              department head has no action to take on any of it. */}
          <Route path="monitoring" element={<RequireAuth allowedRoles={['approver', 'admin']}><SystemHealth /></RequireAuth>} />

          {/* Phase 9 - Notifications */}
          <Route path="notifications" element={<NotificationsPage />} />

          {/* Inventory Management */}
          <Route path="inventory" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><InventoryList /></RequireAuth>} />
          <Route path="inventory/items/:id" element={<RequireAuth allowedRoles={['checker', 'approver', 'bod', 'admin']}><InventoryItemDetail /></RequireAuth>} />
          <Route path="inventory/assignment" element={<RequireAuth allowedRoles={['checker', 'approver', 'admin']}><AssetAssignment /></RequireAuth>} />
          <Route path="inventory/my-assets" element={<MyAssignedAssets />} />
          <Route path="inventory/my-requests" element={<RequireAuth allowedRoles={['maker', 'checker', 'approver', 'admin']}><MyTakeOutRequests /></RequireAuth>} />
          <Route path="inventory/approvals" element={<RequireAuth allowedRoles={['checker', 'approver', 'admin']}><TakeOutApprovals /></RequireAuth>} />

          {/* Phase 70. These are NOT role-gated in the router, unlike the older
              inventory routes above: the server decides what each user sees, and
              the dashboard deliberately returns a personal view rather than a 403
              for an employee. Gating here would lock people out of their own
              assets, which is the opposite of what the endpoint does. */}
          <Route path="inventory/dashboard" element={<InventoryDashboard />} />
          <Route path="inventory/requests" element={<AssetRequests />} />
          <Route path="inventory/maintenance" element={<MaintenanceTickets />} />
          <Route path="inventory/reports" element={<InventoryReports />} />
          <Route path="inventory/reports/:name" element={<InventoryReports />} />
          {/* Phase ASSET-CUSTODY-TRANSFER. No role gate: every page scopes its own
              data on the server - an employee sees only transfers about them, only
              returns they raised, only their own clearance. */}
          <Route path="inventory/transfers" element={<AssetTransfers />} />
          <Route path="inventory/returns" element={<AssetReturns />} />
          <Route path="inventory/exit-clearance" element={<ExitClearance />} />
          <Route path="inventory/disposals" element={<AssetDisposals />} />
          {/* Ungated like its neighbours: the server refuses readers who may not
              browse the register, and the page says why rather than 403ing. */}
          <Route path="inventory/visibility" element={<AssetVisibility />} />

          {/* Tasks (Phase T1). Specific paths must precede /tasks/:id or
              "create" would be read as a task id. Not role-gated in the router:
              the API scopes every response, and creation is refused server-side
              for an Employee — a route guard here would additionally hide the
              lists an Employee is entitled to. */}
          <Route path="tasks" element={<TaskDashboard />} />
          <Route path="tasks/create" element={<TaskForm />} />
          <Route path="tasks/needs-me" element={<TaskNeedsMe />} />
          <Route path="tasks/mine" element={<MyTasks />} />
          <Route path="tasks/assigned-by-me" element={<AssignedByMe />} />
          <Route path="tasks/team" element={<TeamTasks />} />
          <Route path="tasks/due-today" element={<TasksDueToday />} />
          <Route path="tasks/overdue" element={<OverdueTasks />} />
          <Route path="tasks/completed" element={<CompletedTasks />} />
          <Route path="tasks/all" element={<AllTasks />} />
          <Route path="tasks/board" element={<TaskBoardPage />} />
          <Route path="tasks/drafts" element={<DraftTasks />} />
          {/* MERGED into the review queue: the same under-review tasks from the
              same scoped query, which the queue also ages and groups by reviewer.
              Kept as a redirect so bookmarks and old links still land. */}
          <Route path="tasks/pending-review" element={<Navigate to="/tasks/review-queue" replace />} />
          {/* Phase T2.9. Before /tasks/:id, or "templates" reads as a task id. */}
          <Route path="tasks/templates" element={<TaskTemplates />} />
          {/* Phase T3 workspace views. All before /tasks/:id, or each
              name would be read as a task id. Not role-gated: the API
              scopes every response and refuses the workload view
              itself, so a guard here would only hide pages an
              employee is entitled to. */}
          <Route path="tasks/calendar" element={<TaskCalendar />} />
          <Route path="tasks/workload" element={<TaskWorkload />} />
          <Route path="tasks/overdue-screen" element={<TaskOverdueScreen />} />
          <Route path="tasks/reports" element={<TaskReports />} />
          <Route path="tasks/review-queue" element={<TaskReviewQueue />} />
          <Route path="tasks/analytics" element={<TaskAnalytics />} />
          <Route path="tasks/my-record" element={<TaskEvidence />} />
          <Route path="tasks/team-evidence" element={<TaskTeamEvidence />} />
          <Route path="tasks/:id/edit" element={<TaskForm />} />
          <Route path="tasks/:id" element={<TaskDetail />} />

          {/* Appraisal (Phase APM-03b). Every named path precedes
              /appraisals/:id, or "cycles" would be read as an appraisal id.
              The HR dashboard is /appraisals/hr rather than
              /appraisals/cycle: one letter apart from /cycles is a URL
              people mistype and a route pair nobody can tell apart in a
              log. It also mirrors /workforce/hr, which is the same idea.
              Not role-gated in the router, for the same reason tasks are not:
              the API scopes every response and refuses every write an employee
              may not make, so a guard here would only hide the pages somebody
              IS entitled to — and an employee is entitled to their own
              appraisal. A page whose data the caller cannot see renders its own
              "nothing here" state instead. */}
          <Route path="appraisals" element={<MyAppraisal />} />
          <Route path="appraisals/goals" element={<MyGoals />} />
          <Route path="appraisals/evidence" element={<MyEvidence />} />
          <Route path="appraisals/new" element={<NewAppraisal />} />
          <Route path="appraisals/team" element={<TeamAppraisals />} />
          <Route path="appraisals/committee" element={<CommitteeQueue />} />
          <Route path="appraisals/hr" element={<HRAppraisalDashboard />} />
          <Route path="appraisals/cycles" element={<AppraisalCycles />} />
          <Route path="appraisals/reports" element={<AppraisalReports />} />
          <Route path="appraisals/:id" element={<AppraisalDetail />} />

          {/* Memos. Specific paths must precede /memos/:id or "drafts" would be
              read as a memo id. Access is scoped by the API per user, so these
              routes are not role-gated: a menu simply returns nothing when the
              user has nothing in it. */}
          <Route path="drafts" element={<UnfinishedWork />} />

          <Route path="memos" element={<MemoDashboard />} />
          <Route path="memos/all" element={<AllMemos />} />
          <Route path="memos/create" element={<CreateMemo />} />
          {/* /memos/my and /memos/outbox render the same view; the dashboard links to the former. */}
          <Route path="memos/my" element={<MemoOutbox />} />
          <Route path="memos/drafts" element={<DraftMemos />} />
          <Route path="memos/draft-for-review" element={<DraftForReview />} />
          <Route path="memos/department" element={<DepartmentMemos />} />
          <Route path="memos/pending" element={<MyPendingActions />} />
          <Route path="memos/inbox" element={<MemoInbox />} />
          <Route path="memos/outbox" element={<MemoOutbox />} />
          <Route path="memos/approved" element={<ApprovedMemos />} />
          <Route path="memos/archived" element={<ArchivedMemos />} />
          <Route path="memos/rejected" element={<RejectedMemos />} />
          <Route path="memos/:id/edit" element={<EditMemo />} />
          <Route path="memos/:id" element={<MemoDetail />} />

          {/* Minutes. Specific paths precede /minutes/:id, or "drafts" would be
              swallowed by the detail route. Menu definitions live server-side; each
              route below is just a scope name. */}
          <Route path="minutes" element={<MinuteDashboard />} />
          <Route path="minutes/needs-me" element={<NeedsMyAction />} />
          <Route path="minutes/mine" element={<MyMinutes />} />
          <Route path="minutes/all" element={<AllMinutes />} />
          <Route path="minutes/create" element={<MinuteForm mode="create" />} />
          {/* The narrower queues below are no longer in the menu, but stay routed:
              existing links and bookmarks must not 404. */}
          <Route path="minutes/drafts" element={<DraftMinutes />} />
          <Route path="minutes/draft-for-review" element={<MinuteDraftForReview />} />
          <Route path="minutes/initiated" element={<AssignedInitiated />} />
          <Route path="minutes/involvement" element={<AssignedInvolvement />} />
          <Route path="minutes/under-process" element={<UnderProcessMinutes />} />
          <Route path="minutes/my-acknowledgements" element={<MyPendingAcknowledgements />} />
          <Route path="minutes/acknowledged" element={<MinutesAcknowledged />} />
          <Route path="minutes/archived" element={<ArchivedMinutes />} />
          <Route path="minutes/:id/edit" element={<MinuteForm mode="edit" />} />
          <Route path="minutes/:id" element={<MinuteDetail />} />

          {/* Circulars. Specific paths precede /circulars/:id, or "drafts" would
              be read as a circular id. Access is scoped by the API per user, so
              these routes are not role-gated: a menu simply returns nothing when
              the user has nothing in it. */}
          <Route path="circulars" element={<CircularDashboard />} />
          <Route path="circulars/all" element={<AllCirculars />} />
          <Route path="circulars/create" element={<CircularForm mode="create" />} />
          <Route path="circulars/drafts" element={<DraftCirculars />} />
          <Route path="circulars/assigned" element={<AssignedCirculars />} />
          <Route path="circulars/under-review" element={<UnderReviewCirculars />} />
          <Route path="circulars/ready-for-issue" element={<ReadyForIssue />} />
          <Route path="circulars/ready-for-broadcast" element={<ReadyForBroadcast />} />
          <Route path="circulars/broadcasted" element={<BroadcastedCirculars />} />
          <Route path="circulars/my-acknowledgements" element={<MyCircularAcknowledgements />} />
          <Route path="circulars/unread" element={<UnreadCirculars />} />
          <Route path="circulars/archived" element={<ArchivedCirculars />} />
          <Route path="circulars/:id/edit" element={<CircularForm mode="edit" />} />
          <Route path="circulars/:id" element={<CircularDetail />} />

        </Route>
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
}

export default App;
