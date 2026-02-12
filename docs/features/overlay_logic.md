# Overlay Rotation and Coordinate Logic

This document details the logic used to calculate pixel coordinates for catalog overlays in `backend/app/api/images.py`.

## 1. Coordinate System
We use the **Web Standard (Top-Left Origin)** coordinate system for the final pixel outputs.
- **Origin (0,0)**: Top-Left corner of the image.
- **X Axis**: Increases to the Right.
- **Y Axis**: Increases Down.

## 2. Input Parameters
The following values are retrieved from the database or FITS header:
- **Center Coordinates**: `ra_center`, `dec_center` (Degrees).
- **Pixel Scale**: `scale` (Degrees per pixel).
- **Rotation**: `rotation_degrees` (Degrees).
- **Parity**: `astrometry_parity`.
    - `1` (Normal): Standard Orientation (East is Left).
    - `-1`: Horizontal Flip (Mirror, East is Right).

## 3. Logic Implementation

### Rotation Direction
We apply the `rotation_degrees` directly.
- Testing confirms that for our Web/Top-Left coordinate system, applying the positive angle $\theta$ results in the expected visual rotation.
- Example: $\theta=270^\circ$ places North to the Right (270° CCW from Up).

$$ \theta_{apply} = \text{rotation\_degrees} $$

### Scale and Parity map
We define the scaling factors for the CD matrix based on the Parity rule:

1.  **Vertical Scale (`s_y`)**:
    - Always set to `-scale`.
    - **Reason**: In FITS/Sky tangent plane, $\eta$ (North) increases Up. In Web/Screen, $Y$ increases Down. To map "North Up" to "Pixel Up" (decreasing Y), we need a negative factor.
    - $s_y = -scale$

2.  **Horizontal Scale (`s_x`)**:
    - **Parity 1 (Normal)**: `s_x = -scale`.
        - **Reason**: Standard Sky orientation has East to the **Left**.
        - $\Delta x > 0$ (Right-ward) maps to Lower RA (West).
        - $s_x = -scale$.
    - **Parity -1 (Flipped)**: `s_x = +scale`.
        - **Reason**: Horizontal Flip means East is to the **Right**.
        - $s_x = +scale$.

$$ s_x = -\text{scale} \times \text{parity} $$
$$ s_y = -scale $$

### CD Matrix Construction
The CD matrix is constructed using the standard rotation formula applied to these scales:

$$
CD = \begin{bmatrix}
s_x \cos(\theta_{apply}) & -s_y \sin(\theta_{apply}) \\
s_x \sin(\theta_{apply}) & s_y \cos(\theta_{apply})
\end{bmatrix}
$$

## 4. Verification Check
- **Rot=270 (Image 3430)**:
    - $\theta = 270$ (or $-90$ in trig).
    - Result: North Vector points **Right**.
    - This matches the User's expectation (Standard CCW behavior: Up -> Left -> Down -> Right).

This combination of Web-Standard coordinates and Positive Rotation application produces the correct results for all tested cases (0, 90, 270).

## 5. Workflow Integration
This logic is applied dynamically in the `GET /api/images/{id}` endpoint.
1.  **Rescan/Upload**: The async task (`monitor_submission_task`) retrieves `ra`, `dec`, `pixscale`, `parity`, and `orientation` from Astrometry.net and saves them to the `images` table.
2.  **Display**: When the frontend requests the image, the backend constructs the WCS object using these stored values and calculates the `pixel_x` and `pixel_y` for every matched catalog object on the fly.
