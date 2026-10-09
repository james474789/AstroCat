// Page stylesheets must be scoped so one page can't restyle another (U1 P1a).
// Every rule in src/pages/*.css has to start with .page-<name> (page content)
// or .dlg-<name> (content of a portalled <Dialog>). Run via `npm run lint:scope`.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import postcss from 'postcss';

const dir = 'src/pages';
const allowed = /^\.(page|dlg)-[a-z0-9-]+/;
let bad = 0;

for (const file of readdirSync(dir).filter((f) => f.endsWith('.css'))) {
  const root = postcss.parse(readFileSync(join(dir, file), 'utf8'), { from: file });
  root.walkRules((rule) => {
    if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return;
    for (const sel of rule.selectors) {
      if (!allowed.test(sel.trim())) {
        bad += 1;
        console.error(`${file}:${rule.source.start.line}  unscoped selector "${sel.trim()}" (prefix with .page-<name> or .dlg-<name>)`);
      }
    }
  });
}

if (bad) {
  console.error(`\n${bad} unscoped selector(s).`);
  process.exit(1);
}
