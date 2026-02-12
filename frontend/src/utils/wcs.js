/**
 * Convert pixel coordinates to RA/Dec using a TAN projection.
 *
 * @param {number} x - The x pixel coordinate (0-indexed, from top-left)
 * @param {number} y - The y pixel coordinate (0-indexed, from top-left)
 * @param {number} width - Image width in pixels
 * @param {number} height - Image height in pixels
 * @param {number} raCenter - CRVAL1: RA at reference pixel (degrees)
 * @param {number} decCenter - CRVAL2: Dec at reference pixel (degrees)
 * @param {number} pixelScale - Plate scale in arcsec/pixel
 * @param {number} rotation - Rotation in degrees (standard astronomical rotation, N up, E left)
 * @returns {{ra: number, dec: number} | null} - The RA/Dec coordinates or null if invalid inputs
 */
/**
 * Convert pixel coordinates to RA/Dec using a TAN projection.
 *
 * @param {number} x - The x pixel coordinate (0-indexed, from top-left)
 * @param {number} y - The y pixel coordinate (0-indexed, from top-left)
 * @param {number} width - Image width in pixels
 * @param {number} height - Image height in pixels
 * @param {number} raCenter - CRVAL1: RA at reference pixel (degrees)
 * @param {number} decCenter - CRVAL2: Dec at reference pixel (degrees)
 * @param {number} pixelScale - Plate scale in arcsec/pixel
 * @param {number} rotation - Rotation in degrees (standard astronomical rotation, N up, E left)
 * @param {number} parity - Parity (1 = Normal, -1 = Flipped)
 * @returns {{ra: number, dec: number} | null} - The RA/Dec coordinates or null if invalid inputs
 */
export function pixelToSky(x, y, width, height, raCenter, decCenter, pixelScale, rotation, parity = 1) {
    if (raCenter == null || decCenter == null || pixelScale == null || rotation == null) {
        return null;
    }

    // 1. Web Standard Coordinates (Top-Left Origin)
    // We do NOT invert Y here.
    const dx = x - (width / 2);
    const dy = y - (height / 2);

    // 2. CD Matrix Construction
    // S = pixelScale / 3600.0;
    const s = pixelScale / 3600.0;

    // Rotation (Applied Positively)
    const rad = rotation * (Math.PI / 180.0);
    const cos_a = Math.cos(rad);
    const sin_a = Math.sin(rad);

    // Scale Logic (Matching Backend)
    // s_y = -scale (Web Y Increases Down)
    // s_x = -scale * parity (Parity 1 = Normal/East-Left -> Negative Scale)

    const s_x = -s * parity;
    const s_y = -s;

    const cd11 = s_x * cos_a;
    const cd12 = -s_y * sin_a;
    const cd21 = s_x * sin_a;
    const cd22 = s_y * cos_a;

    // xi, eta in degrees
    const xi = cd11 * dx + cd12 * dy;
    const eta = cd21 * dx + cd22 * dy;

    // 3. Sky Projection (TAN/Gnomonic) de-projection
    // Using approximation valid for small fields (most amateur images)
    // RA = RA_center + xi / cos(Dec_center)
    // Dec = Dec_center + eta

    // Convert degrees to radians for trig
    const decCenterRad = decCenter * (Math.PI / 180.0);

    // RA delta needs to be corrected for cos(dec) term (standard projection behavior)
    let raDelta = 0;
    if (Math.abs(decCenter) < 89.9) {
        raDelta = xi / Math.cos(decCenterRad);
    } else {
        raDelta = xi; // Fallback near pole (imprecise)
    }

    const decDelta = eta;

    let ra = raCenter + raDelta;
    let dec = decCenter + decDelta;

    // 4. Normalize
    // RA in [0, 360)
    ra = ra % 360;
    if (ra < 0) ra += 360;

    // Dec in [-90, 90] - clamping 
    if (dec > 90) dec = 90;
    if (dec < -90) dec = -90;

    return { ra, dec };
}
