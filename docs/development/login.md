# Login and Multi-User Handling Report

## Executive Summary

The Immich repository implements a robust authentication and user management system supporting local password-based login, generic OAuthProvider integration, and API key access. Multi-user support is built-in, with a clear distinction between administrative operations (managed via `UserAdminController`) and user self-management (managed via `UserController`).

## 1. Login Handling

### First-Time Use
The application has a specific flow for the initial setup, ensuring the first administrator is created securely.
- **Endpoint**: `POST /auth/admin-sign-up`
- **Logic**:
    - Checks the server configuration `setup.allow` to ensure setup is enabled.
    - Verifies that **no admin user currently exists** in the database.
    - Creates the first user with `isAdmin: true`.
    - Returns the user object and login tokens, effectively logging them in.

### Subsequent Use & Login Flow
Standard login for all users (admins and regular users) follows this flow:
- **Endpoint**: `POST /auth/login`
- **Logic**:
    - **Credentials**: Accepts email and password.
    - **Validation**: Checks coordinates against the `user` table. Passwords are hashed using `bcrypt` and compared.
    - **Session Creation**: On success, a new session is created in the `session` table. The session token is hashed (SHA-256) before storage.
    - **Response**: Returns an `accessToken` and sets a cookie `immich_access_token`.
- **Alternative Methods**:
    - **OAuth**: Supported via `/auth/oauth/authorize` and callback flow. Can auto-register new users if configured.
    - **API Keys**: Access via `x-api-key` header.
    - **Shared Links**: Temporary access via share keys/slugs.

### Authentication Persistence
- **Tokens**: The system uses Bearer tokens (`Authorization: Bearer <token>`) or Cookies (`immich_access_token`).
- **Validation**: `AuthService.validate` checks the token against active sessions in the database. It validates device details (OS, App Version) and session expiration.

## 2. Multi-User Handling

The application is designed from the ground up to support multiple users.

### Data Model
- **User Entity**: stored in the `user` table. Key fields include:
    - `id` (UUID)
    - `email` (Unique identifier)
    - `password` (Hashed)
    - `isAdmin` (Boolean flag for privileges)
    - `storageLabel` (For file organization)
    - `quotaSizeInBytes` (Storage limits)
    - `oauthId` (For linked OAuth accounts)
- **User Metadata**: Flexible key-value storage (`user_metadata` table) for preferences, license keys, and onboarding status.

### User Management
- **Creation**:
    - **Admin-Created**: There is no public sign-up endpoint. Administrators create users via `POST /user` (mapped to `UserAdminController.create`).
    - **OAuth Auto-Registration**: If enabled, users logging in via OAuth for the first time are automatically created.
- **Administration**:
    - `UserAdminController` allows admins to search, update, and delete any user.
    - Admins can manage storage quotas and quotas.
- **Self-Management**:
    - `UserController` allows users to retrieve their own profile (`GET /user/me`) or update their own details (`PUT /user/me`).

### Authorization (RBAC)
- **Permissions**: The `@Authenticated` decorator creates granular permission checks (e.g., `Permission.UserRead`, `Permission.UserCreate`).
- **Guards**: `AuthGuard` validates the session and checks if the user has the required permissions or requires Admin status. Admin-only routes are strictly prohibited for non-admin users.

## 3. Server Architecture Overview
- **Framework**: NestJS (Node.js)
- **Database**: PostgreSQL (accessed via Kysely query builder)
- **Controllers**:
    - `AuthController`: Login, Logout, Change Password (backend/server/src/controllers/auth.controller.ts).
    - `UserController`: Self-service user actions (backend/server/src/controllers/user.controller.ts).
    - `UserAdminController`: Admin-only user management (backend/server/src/controllers/user-admin.controller.ts).
