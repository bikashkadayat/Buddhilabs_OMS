# NIF Leave Management System - Docker Setup

A full-stack leave management application with Django backend, React frontend, and PostgreSQL database.

## Quick Start

```bash
# Build and start all containers
docker-compose up --build

# Or run in background
docker-compose up -d --build
```

## Services

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | http://localhost:5173 | React + Vite |
| Backend | http://localhost:8000 | Django REST API |
| Database | localhost:5432 | PostgreSQL |

## Default Credentials

- **Email:** `admin@gmail.com`
- **Password:** `admin123`

(You can also use: maker@example.com, checker@example.com, approver@example.com with password "test123")

## Commands

```bash
# View logs
docker-compose logs -f

# Stop all containers
docker-compose down

# Restart a specific service
docker-compose restart backend

# Rebuild
docker-compose build --no-cache
```

## Project Structure

```
├── docker-compose.yml    # Main orchestration file
├── Dockerfile.backend    # Django backend image
├── Dockerfile.frontend  # React frontend image
├── backend/             # Django application
│   ├── config/         # Django settings
│   ├── users/          # User management
│   └── leaves/         # Leave management
└── frontend/            # React application
    ├── src/
    │   ├── pages/     # Page components
    │   ├── components/
    │   ├── hooks/
    │   └── services/
    └── vite.config.js
```

## How It Works

1. **Database (PostgreSQL)** - Stores all user data, leave applications, and balances
2. **Backend (Django)** - REST API for authentication, leave CRUD operations
3. **Frontend (React)** - User interface with role-based access control

### Role-Based Access
- **Makers** - Apply for leave, view own applications
- **Checkers** - Review pending requests (cannot approve)
- **Approvers** - Approve/reject leave requests
- **Admins** - Full system access

## Troubleshooting

```bash
# Reset database
docker-compose down -v
docker-compose up --build

# Check container status
docker-compose ps

# View backend logs
docker-compose logs backend

# Access container shell
docker-compose exec backend sh
docker-compose exec frontend sh
```
