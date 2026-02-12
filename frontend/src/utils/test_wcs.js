import { pixelToSky } from './wcs.js';

function runTest(name, input, expected, tolerance = 0.001) {
    console.log(`Running Test: ${name}`);
    const result = pixelToSky(
        input.x, input.y,
        input.width, input.height,
        input.raCenter, input.decCenter,
        input.pixelScale, input.rotation
    );

    if (!result) {
        console.error("FAILED: Result is null");
        return;
    }

    const raDiff = Math.abs(result.ra - expected.ra);
    const decDiff = Math.abs(result.dec - expected.dec);

    const pass = raDiff < tolerance && decDiff < tolerance;

    if (pass) {
        console.log("PASS");
    } else {
        console.error(`FAILED: Expected (${expected.ra}, ${expected.dec}), Got (${result.ra}, ${result.dec})`);
        console.error(`Diffs: RA ${raDiff}, Dec ${decDiff}`);
    }
}

// Case 1: Standard No Rotation
// Center (100, 100). Size 200x200.
// Scale 3600 arcsec/px = 1 deg/px.
// RA increases Left (East). Dec increases Up (FITS) -> Up (Web, pixel y decreases).
// Center px: 100, 100.
// Point (99, 100) -> Left 1 px.
// Should be East 1 deg. RA = 100 + 1/cos(0) = 101.
// Point (100, 99) -> Web Up 1 px.
// Should be North 1 deg. Dec = 0 + 1 = 1.
runTest("No Rotation, Center",
    { x: 100, y: 100, width: 200, height: 200, raCenter: 100, decCenter: 0, pixelScale: 3600, rotation: 0 },
    { ra: 100, dec: 0 }
);

runTest("No Rotation, Left 1px",
    { x: 99, y: 100, width: 200, height: 200, raCenter: 100, decCenter: 0, pixelScale: 3600, rotation: 0 },
    { ra: 101, dec: 0 }
);

runTest("No Rotation, Up 1px",
    { x: 100, y: 99, width: 200, height: 200, raCenter: 100, decCenter: 0, pixelScale: 3600, rotation: 0 },
    { ra: 100, dec: 1 }
);

// Case 2: 180 Rotation
// N becomes Down. E becomes Right.
// Move Left (x decr) -> West -> RA decr.
// Move Up (y decr) -> South -> Dec decr.
runTest("180 Rotation, Left 1px",
    { x: 99, y: 100, width: 200, height: 200, raCenter: 100, decCenter: 0, pixelScale: 3600, rotation: 180 },
    { ra: 99, dec: 0 }
);

runTest("180 Rotation, Up 1px",
    { x: 100, y: 99, width: 200, height: 200, raCenter: 100, decCenter: 0, pixelScale: 3600, rotation: 180 },
    { ra: 100, dec: -1 }
);

console.log("Tests Complete");
