# Security Audit Report

## Executive Summary
This report details the findings of a static and architectural security analysis performed on the **AstroCat** repository. The audit focused on secret scanning, dependency analysis, OWASP Top 10 vulnerabilities, and insecure coding patterns.

**Overall Status**: Moderate Risk. No critical remote code execution (RCE) or SQL injection vulnerabilities were found in the application logic. However, high-severity vulnerabilities exist in third-party dependencies.

---

## 1. Vulnerability Summary

| Severity | Category | Category | Description |
|:--:|:--|:--|:--|
| **High** | Dependency | Vulnerable `Pillow` Version | Used `Pillow==10.2.0` which contains known CVEs (e.g., CVE-2024-28219). |
| **Medium** | Dependency | Unmaintained Library | `python-jose` is unmaintained and deprecated. |
| **Medium** | Config | Insecure Defaults | Default passwords and debug mode enabled in example configurations. |
| **Low** | Crypto | Weak Hashing | Usage of `MD5` for file fingerprinting. |

---

## 2. Detailed Findings

### A. Critical Severity
*No Critical security issues were identified during this audit.*

### B. High Severity

#### 1. Vulnerable Dependency: Pillow
- **Location**: `backend/requirements.txt`
- **Current Version**: `10.2.0`
- **Risk**: High. This version is vulnerable to several CVEs, most notably **CVE-2024-28219** (Buffer overflow in `cms_transform`) which can lead to denials of service or potentially remote code execution via a maliciously crafted image file. Since this application processes user-uploaded images, this is a significant vector.
- **Remediation**: Upgrade to `Pillow>=10.3.0` (Recommended: `latest`).

### C. Medium Severity

#### 1. Unmaintained Dependency: python-jose
- **Location**: `backend/requirements.txt`
- **Issue**: The `python-jose` library is no longer actively maintained.
- **Risk**: Medium. Lack of security patches involves long-term risk.
- **Remediation**: Migrate to `PyJWT` for JWT handling.

#### 2. Insecure Configuration Defaults
- **Location**: `.env.example`, `docker-compose-example.yml`
- **Issue**:
    - `POSTGRES_PASSWORD=AstroCat_secret_password`
    - `SECRET_KEY=your-super-secret-key-change-in-production`
    - `DEBUG=true`
- **Risk**: Medium. If these example files are copied to production without modification, the application will be vulnerable to unauthorized access and information leakage (via debug tracebacks).
- **Remediation**: Ensure deployment documentation emphasizes changing these values. Consider adding a startup check that fails if `SECRET_KEY` is the default value in production mode.

### D. Low Severity

#### 1. Weak Hashing Algorithm (MD5)
- **Location**: `backend/app/tasks/bulk.py`, `backend/app/services/thumbnails.py`
- **Code Snippet**: `hashlib.md5(path.encode()).hexdigest()`
- **Issue**: MD5 is considered cryptographically broken.
- **Context**: Used here for file path fingerprinting/caching keys, not for password storage (which correctly uses `bcrypt`).
- **Risk**: Low. Hash collisions could theoretically cause cache poisoning or thumbnail overwrites, but an attack is impractical.
- **Remediation**: Switch to `SHA256` or `BLAKE2` for future-proofing and better collision resistance.

#### 2. Raw SQL Usage
- **Location**: `backend/app/services/matching.py`, `scripts/*.py`
- **Issue**: Extensive use of `session.execute(text(...))`.
- **Context**: The usage was audited and found to be **safe** (using parameterized queries). However, raw SQL increases the maintenance burden and risk of future injection errors.
- **Remediation**: Prefer SQLAlchemy ORM methods or Core expression language over raw `text()` strings where possible.

---

## 3. Performance & ReDoS Checks

A review of Regular Expressions used in the application was conducted to identify ReDoS (Regular Expression Denial of Service) vulnerabilities.

- **Files Audited**: `backend/app/extractors/exif_extractor.py`, `backend/app/services/astrometry_service.py`
- **Findings**:
    - Regexes found are simple matching patterns (e.g., `rb'<xmp:Rating>(\d+)</xmp:Rating>'`).
    - **Complexity**: O(n) linear time complexity.
    - **Benchmark**: Not applicable as no nested quantifiers or potentially catastrophic backtracking patterns were identified.

## 4. Next Steps

1.  **Immediate**: Update `Pillow` in `backend/requirements.txt`.
2.  **Short Term**: Plan migration from `python-jose` to `PyJWT`.
3.  **Process**: Implement a pre-commit hook or CI step to run `safety check` or `pip-audit` to catch vulnerable dependencies automatically.
