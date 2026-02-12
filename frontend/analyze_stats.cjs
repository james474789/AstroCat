
const fs = require('fs');
const stats = JSON.parse(fs.readFileSync('stats.json', 'utf8'));
const skyCoverage = stats.sky_coverage;

console.log(`Total points: ${skyCoverage.length}`);

// Target 1: RA 20h (300 deg), Dec 38
// Target 2: RA 14:56 (14.933h -> 224 deg), Dec 80.9


// Target 1: RA 20h (300 deg), Dec 38
// Target 2: RA 14:56 (14.933h -> 224 deg), Dec 80.9

const target1 = skyCoverage.find(p => Math.abs(p.ra - 300) < 5 && Math.abs(p.dec - 38) < 5);
// Search for anything with Dec near 80.9
const target2 = skyCoverage.find(p => Math.abs(p.dec - 80.9) < 1);

console.log('Target 1 (20h/38d):', target1);
console.log('Target 2 (Any RA / ~80.9d):', target2);

if (target2) {
    console.log(`Target 2 found at RA: ${target2.ra} (Hours: ${target2.ra / 15})`);
    console.log('Index of Target 2:', skyCoverage.indexOf(target2));
}


// Check if there are duplicate points or close neighbors
const neighbors = skyCoverage.filter(p => Math.abs(p.ra - 300) < 10 && Math.abs(p.dec - 38) < 10);
console.log('Neighbors of Target 1:', neighbors);
