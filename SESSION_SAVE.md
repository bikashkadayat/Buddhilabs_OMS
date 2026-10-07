# NIF Leave System - Development Session Summary

**Date:** April 10, 2026
**Status:** Docker containers running

## Running Services

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| Database | PostgreSQL (internal) |

## Login Credentials

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@gmail.com | admin123 |
| Maker | maker@example.com | test123 |
| Checker | checker@example.com | test123 |
| Approver | approver@example.com | test123 |

## Docker Commands

```bash
# Stop containers
docker-compose down

# Start containers
docker-compose up -d

# View logs
docker-compose logs -f
```

## Key Files Modified

1. **Frontend:**
   - `src/pages/Login.jsx` - Added password visibility toggle
   - `src/pages/leave/ApplyLeave.jsx` - Added reporting manager dropdown
   - `src/pages/leave/Dashboard.jsx` - Role-based dashboards
   - `src/services/adminService.js` - Users API endpoint
   - `vite.config.js` - Docker support

2. **Backend:**
   - `users/views.py` - Added UserListView
   - `config/urls.py` - Added /api/v1/users/ endpoint
   - `config/settings.py` - Environment variables for database

3. **Docker:**
   - docker-compose.yml
   - Dockerfile.backend
   - Dockerfile.frontend

## To Continue Tomorrow

1. Start Docker:
   ```bash
   cd /home/nif/Documents/Leave-system
   docker-compose up -d
   ```

2. Access at http://localhost:5173
