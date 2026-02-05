# AstroCat Security Assessment Report

**Date:** February 3, 2026  
**Assessor:** Security Research Team  
**Overall Risk Level:** HIGH

---

## Executive Summary

This comprehensive security assessment of the AstroCat astronomical image database reveals **multiple critical and high-severity vulnerabilities** that require immediate attention. While the application demonstrates good practices in some areas (parameterized queries, bcrypt password hashing), several security gaps expose it to significant risks.

**Key Concerns:**
- Insecure default secrets and authentication configuration
- Vulnerable dependencies (Pillow CVE-2024-28219)
- Missing rate limiting and CSRF protection
- Path traversal vulnerabilities
- Insecure Docker configuration

---

## Critical Findings

### 🔴 1. Weak Default Secret Key (CRITICAL)
**Location:** `backend/app/config.py:23`

```python
secret_key: str = "change-me-in-production"
```

**Impact:** The JWT signing key has an insecure default that could be used in production, allowing attackers to forge authentication tokens and gain unauthorized access to any account, including admin accounts.

**Attack Scenario:**
1. Attacker discovers default secret key in source code
2. Generates valid JWT tokens for any user/admin account
3. Bypasses all authentication and authorization controls

**CVSS Score:** 9.8 (Critical)

**Recommendation:**
- Remove the default value entirely
- Add startup validation to fail if SECRET_KEY is default/weak
- Generate cryptographically random keys: `openssl rand -hex 32`
- Document key generation in deployment guide

---

### 🔴 2. Insecure Cookie Configuration (CRITICAL)
**Location:** `backend/app/api/auth.py:51-56`

```python
response.set_cookie(
    key="access_token",
    value=access_token,
    httponly=True,
    max_age=7 * 24 * 60 * 60,
    samesite="lax",
    secure=False,  # ⚠️ Set to True if using HTTPS
)
```

**Impact:** 
- `secure=False` allows cookies to be transmitted over unencrypted HTTP
- Enables man-in-the-middle (MITM) attacks
- Cookie hijacking leads to account takeover
- Long expiration (7 days) increases risk window

**CVSS Score:** 8.1 (High)

**Recommendation:**
- Set `secure=True` for production
- Add environment variable to control this setting
- Consider shorter token lifetimes (1-2 days)
- Implement token refresh mechanism

---

### 🔴 3. Authentication Bypass Risk (CRITICAL)
**Location:** `backend/app/api/dependencies.py:47-53`

```python
async def get_current_user(
    user: User | None = Depends(get_current_user_optional)
) -> User:
    if settings.auth_required and not user:
        raise HTTPException(...)
    return user  # ⚠️ Returns None if auth_required=False
```

**Impact:** 
- When `AUTH_REQUIRED=false`, all endpoints receive `None` as current_user
- Code may not handle None properly causing authorization bypass

**CVSS Score:** 8.8 (High)

**Recommendation:**
- Always require authentication in production
- Remove the `auth_required` toggle
- Fix type hint: `-> User` (never None in protected endpoints)

---

## High Severity Findings

### 🟠 4. Path Traversal Vulnerability (HIGH)
**Location:** `backend/app/api/filesystem.py:69-75`, `backend/app/api/images.py:881`

**Issues:**
1. Insufficient path validation - symlink attacks possible
2. FileResponse serves files directly without additional validation
3. No sanitization of user-provided filenames

**CVSS Score:** 7.5 (High)

**Recommendation:**
```python
def validate_path_safety(target: Path, allowed_roots: List[Path]) -> bool:
    try:
        resolved = target.resolve(strict=True)
        if target.is_symlink():
            return False
        for root in allowed_roots:
            try:
                common = os.path.commonpath([resolved, root])
                if Path(common) == root:
                    return True
            except ValueError:
                continue
        return False
    except (OSError, RuntimeError):
        return False
```

---

### 🟠 5. SQL Injection Risk via Raw SQL (HIGH)
**Location:** `backend/app/api/search.py:35-46`, `backend/app/services/matching.py`

**Current Status:** SAFE (properly parameterized)

**Risk:** Extensive raw SQL usage increases maintenance risk. At least 15 instances of `text()` usage across codebase.

**CVSS Score:** 7.3 (High - potential risk)

**Recommendation:**
- Migrate to GeoAlchemy2 spatial functions where possible
- Implement mandatory code review for any raw SQL
- Add SQL injection tests to CI/CD

---

### 🟠 6. No Rate Limiting (HIGH)
**Location:** Application-wide

**Impact:** API endpoints vulnerable to:
- Brute force attacks on `/api/auth/login`
- Denial of Service through expensive queries
- User enumeration via timing attacks

**CVSS Score:** 7.5 (High)

**Recommendation:**
```python
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@router.post("/login")
@limiter.limit("5/15minutes")
async def login(...):
    ...
```

---

### 🟠 7. Vulnerable Dependencies (HIGH)
**Location:** `backend/requirements.txt`

| Package | Current | CVE | Risk |
|---------|---------|-----|------|
| **pillow** | 10.2.0 | CVE-2024-28219 | Buffer overflow → RCE |
| **python-jose** | 3.3.0 | Unmaintained | No security patches |
| **fastapi** | 0.109.0 | Multiple | Security fixes missing |

**CVSS Score:** 8.2 (High)

**Recommendation:**
```txt
pillow>=11.0.0
pyjwt>=2.8.0  # Replace python-jose
fastapi>=0.115.0
```

---

### 🟠 8. Insecure Docker Configuration (HIGH)
**Location:** `docker-compose.yml`

**Issues:**
1. **Exposed PostgreSQL Port:** `"127.0.0.1:8088:5432"`
2. **Weak Default Credentials:** `AstroCat_secret_password`
3. **Network Credentials in Plain Text:** SMB credentials exposed
4. **Development Code in Production:** Source code mounted

**CVSS Score:** 7.8 (High)

**Recommendation:**
- Remove port bindings for PostgreSQL/Redis
- Use Docker secrets for credentials
- Separate dev/prod compose files

---

## Medium Severity Findings

### 🟡 9. Missing CSRF Protection (MEDIUM)
Cookie-based authentication without CSRF tokens enables cross-site request forgery.

**CVSS Score:** 6.5 (Medium)

**Recommendation:** Implement `fastapi-csrf-protect` middleware

---

### 🟡 10. Information Disclosure (MEDIUM)
Health check endpoints expose detailed error messages including database connection strings and internal paths.

**CVSS Score:** 5.3 (Medium)

**Recommendation:** Return generic error messages to clients, log details server-side only

---

### 🟡 11. Weak Password Policy (MEDIUM)
No enforcement of minimum length, complexity, or common password checks.

**CVSS Score:** 5.3 (Medium)

**Recommendation:**
```python
class PasswordValidator:
    MIN_LENGTH = 12
    @classmethod
    def validate(cls, password: str) -> List[str]:
        errors = []
        if len(password) < cls.MIN_LENGTH:
            errors.append(f"Password must be at least {cls.MIN_LENGTH} characters")
        # Add complexity checks...
        return errors
```

---

### 🟡 12. Session Management Issues (MEDIUM)
**Issues:**
- No token revocation on logout (JWT still valid)
- No refresh token mechanism
- No concurrent session limits

**CVSS Score:** 6.1 (Medium)

**Recommendation:** Implement token blacklist in Redis with revocation capability

---

### 🟡 13. Insufficient Input Validation (MEDIUM)
**Examples:**
- Large page_size values could cause DoS
- No bounds checking on coordinate parameters
- No file upload size limits

**CVSS Score:** 5.8 (Medium)

---

## Low Severity / Informational

### ℹ️ 14. MD5 Usage for File Hashing (LOW)
Used for cache keys (non-security purpose), but MD5 is cryptographically broken.

**Recommendation:** Switch to SHA-256

### ℹ️ 15. Missing Security Headers (LOW)
Frontend nginx.conf lacks:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`
- `Strict-Transport-Security`

### ℹ️ 16. No Audit Logging (INFO)
No logging of authentication attempts, authorization failures, or admin actions.

---

## Positive Security Practices ✅

1. **✅ Password Hashing:** Proper bcrypt usage via passlib
2. **✅ SQL Parameterization:** Consistent use of bound parameters
3. **✅ HTTP-only Cookies:** Prevents JavaScript access
4. **✅ Docker Non-root User:** Production container uses dedicated user
5. **✅ CORS Configuration:** Explicit origin whitelisting
6. **✅ Database Connection Pooling:** Proper resource management

---

## Remediation Roadmap

### 🔴 IMMEDIATE (Within 24 hours)

| Priority | Finding | Action | Effort |
|----------|---------|--------|--------|
| 1 | Vulnerable Pillow | Update to ≥11.0.0 | 30 min |
| 2 | Default Secret Key | Remove default, add validation | 1 hour |
| 3 | Insecure Cookies | Set `secure=True`, reduce expiry | 30 min |
| 4 | Docker Secrets | Remove default passwords | 1 hour |

**Commands:**
```bash
pip install pillow==11.0.0 pyjwt==2.8.0 fastapi==0.115.0
openssl rand -hex 32 > .secret_key
```

---

### 🟠 SHORT-TERM (Within 1 week)

| Priority | Finding | Action | Effort |
|----------|---------|--------|--------|
| 5 | Rate Limiting | Implement slowapi | 4 hours |
| 6 | Auth Bypass | Remove auth_required toggle | 2 hours |
| 7 | CSRF Protection | Add CSRF middleware | 3 hours |
| 8 | Path Traversal | Improve path validation | 4 hours |

**Estimated Total:** 2-3 developer days

---

### 🟡 MEDIUM-TERM (Within 1 month)

| Priority | Finding | Action | Effort |
|----------|---------|--------|--------|
| 10 | Password Policy | Implement validation | 4 hours |
| 11 | Session Management | Add token blacklist | 6 hours |
| 12 | Security Headers | Configure nginx | 1 hour |
| 13 | Audit Logging | Implement audit system | 8 hours |

**Estimated Total:** 4-5 developer days

---

## Testing & Validation

### Automated Security Testing

```bash
# Static Analysis
pip install bandit
bandit -r backend/app/

# Dependency Scanning
pip install pip-audit
pip-audit --strict

# Secret Scanning
pip install trufflehog
trufflehog filesystem .

# Container Scanning
trivy image astrocat-backend:latest
```

### Manual Testing Checklist

- [ ] Test authentication with weak passwords
- [ ] Verify rate limiting on login endpoint
- [ ] Attempt path traversal attacks
- [ ] Test CSRF protection
- [ ] Verify secure cookie flags
- [ ] Test with expired/invalid JWTs
- [ ] Attempt SQL injection in search parameters
- [ ] Test authorization bypass attempts
- [ ] Verify error messages don't leak info
- [ ] Test file upload restrictions

---

## Compliance Considerations

### OWASP Top 10 2021 Coverage

| Risk | Present | Severity | Status |
|------|---------|----------|--------|
| A01: Broken Access Control | Yes | High | Partially mitigated |
| A02: Cryptographic Failures | Yes | Critical | Needs attention |
| A03: Injection | Low Risk | Medium | Well protected |
| A04: Insecure Design | Yes | Medium | Needs review |
| A05: Security Misconfiguration | Yes | High | Needs attention |
| A06: Vulnerable Components | Yes | High | Update required |
| A07: Authentication Failures | Yes | Critical | Needs attention |
| A08: Software & Data Integrity | Partial | Low | Acceptable |
| A09: Security Logging Failures | Yes | Medium | Not implemented |
| A10: SSRF | No | - | Not applicable |

---

## Appendix A: Exploitation Examples

### JWT Forgery with Default Secret

```python
import jwt
from datetime import datetime, timedelta

SECRET_KEY = "change-me-in-production"  # From source code

payload = {
    "sub": "admin@example.com",
    "exp": datetime.utcnow() + timedelta(days=7)
}

forged_token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
# Use in request: curl -b "access_token={forged_token}" ...
```

### Path Traversal

```bash
# Exploit filesystem API
curl "http://localhost:8089/api/filesystem/list?path=../../../etc"

# Symlink attack
ln -s /etc/passwd /data/images/pwned.txt
curl "http://localhost:8089/api/filesystem/list?path=/data/images/pwned.txt"
```

### Brute Force Login

```python
import requests

BASE_URL = "http://localhost:8089"
passwords = ["password", "admin123", "AstroCat123"]

for password in passwords:
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@example.com", "password": password}
    )
    if response.status_code == 200:
        print(f"✓ Found password: {password}")
        break
    # No rate limiting - can try thousands per minute
```

---

## Conclusion

AstroCat demonstrates solid software engineering practices but requires immediate security hardening before production deployment. The identified vulnerabilities, particularly around authentication and secrets management, present significant risks that must be addressed.

### Key Takeaways:

1. **Critical Issues:** Default secrets, insecure cookies, and vulnerable dependencies are the highest priority
2. **Quick Wins:** Many issues can be resolved with configuration changes (no code required)
3. **Technical Debt:** Raw SQL usage and missing rate limiting require more substantial refactoring
4. **Good Foundation:** Proper password hashing and parameterized queries provide solid base

### Recommended Next Steps:

1. **Week 1:** Address all CRITICAL findings (1 day)
2. **Week 2-3:** Implement HIGH severity fixes (3 days)
3. **Month 1:** Complete MEDIUM severity items (5 days)

**Total Estimated Effort:** 10-15 developer days for core security hardening

---

**Report Prepared By:** Security Research Team  
**Date:** February 3, 2026  
**Classification:** CONFIDENTIAL

---

*This report should be treated as confidential and shared only with authorized personnel.*
