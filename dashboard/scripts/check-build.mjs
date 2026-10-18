import { readdir, readFile, copyFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
const assets = await readdir(new URL('../dist/assets/', import.meta.url));
let bytes = 0;
for (const asset of assets.filter(name => name.endsWith('.js'))) {
  const content = await readFile(new URL('../dist/assets/' + asset, import.meta.url));
  if (/localStorage|sessionStorage|access_token|refresh_token|id_token|VITE_[A-Z_]+|Bearer /.test(content.toString('utf8'))) throw new Error('Credential or persistent browser storage reference requires review');
  bytes += gzipSync(content).length;
}
if (bytes > 300 * 1024) throw new Error(`Initial JavaScript exceeds 300 KiB gzip: ${bytes}`);
const html = await readFile(new URL('../dist/index.html', import.meta.url), 'utf8');
if (/https?:\/\//.test(html) || /sourceMappingURL/.test(html)) throw new Error('External dependency or source map in entrypoint');
console.log(`JavaScript gzip (all chunks): ${bytes} bytes; budget: ${300 * 1024} bytes`);

await copyFile(new URL('../THIRD_PARTY_NOTICES.txt', import.meta.url), new URL('../dist/licenses.txt', import.meta.url));
