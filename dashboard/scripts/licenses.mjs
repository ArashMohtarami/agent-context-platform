import { readFile, writeFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const lock = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
const permitted = new Set(['MIT', 'MIT-0', 'CC-BY-4.0', 'Apache-2.0', 'ISC', 'BSD-2-Clause', 'BSD-3-Clause', 'MPL-2.0', '0BSD', 'BlueOak-1.0.0', 'CC0-1.0']);
const inventory = []; const runtime = [];
for (const [key, item] of Object.entries(lock.packages).sort()) {
  if (!key) continue;
  const packageRoot = path.join(root, key);
  let pkg;
  try {pkg = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));}
  catch (error) {
    if (error.code !== 'ENOENT' || !item.optional) throw error;
    pkg = {name: key.split('node_modules/').at(-1), version: item.version, license: item.license};
  }
  const license = typeof pkg.license === 'string' ? pkg.license : pkg.license?.type;
  if (!permitted.has(license)) throw new Error(`Review license: ${pkg.name}@${pkg.version}: ${license}`);
  inventory.push({name: pkg.name, version: pkg.version, license, development: !!item.dev, source: item.resolved, integrity: item.integrity});
  if (!item.dev) {
    const files = (await readdir(packageRoot)).filter(name => /^(licen[cs]e|notice)(\.|$)/i.test(name)).sort();
    if (!files.length && pkg.name !== 'react-remove-scroll-bar') throw new Error(`Missing distributed license: ${pkg.name}`);
    const supplement = !files.length ? await readFile(path.join(root, 'licenses/react-remove-scroll-bar-LICENSE'), 'utf8') : '';
    runtime.push(`\n${pkg.name}@${pkg.version} — ${license}\n${item.resolved}\n` + (await Promise.all(files.map(file => readFile(path.join(packageRoot, file), 'utf8')))).join('\n') + supplement);
  }
}
await writeFile(path.join(root, 'dependency-licenses.json'), JSON.stringify(inventory, null, 2) + '\n');
await writeFile(path.join(root, 'THIRD_PARTY_NOTICES.txt'), 'Agent Context dashboard runtime dependencies\nGenerated from the exact npm lockfile and distributed license files.\n' + runtime.join('\n').replace(/\r\n?/g, '\n'));
console.log(`Reviewed ${inventory.length} locked packages; preserved ${runtime.length} runtime notices.`);
