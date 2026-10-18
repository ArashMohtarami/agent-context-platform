import { useState } from 'react';
import { Notice, Panel, Select, exactTime } from '../components/ui';
import { Actor, Records } from './shared';
import { versionSchema } from './contracts';
import type { Version } from './contracts';
export type Difference = {kind: 'same' | 'add' | 'remove'; text: string};
export function lineDiff(before: string, after: string): Difference[] {
  const a = before.split('\n').slice(0,400); const b = after.split('\n').slice(0,400);
  const grid = Array.from({length: a.length + 1}, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) grid[i]![j] = a[i] === b[j] ? grid[i+1]![j+1]! + 1 : Math.max(grid[i+1]![j]!, grid[i]![j+1]!);
  const rows: Difference[] = []; let i = 0; let j = 0;
  while (i < a.length || j < b.length) {if (i < a.length && j < b.length && a[i] === b[j]) {rows.push({kind:'same',text:a[i++]!}); j++;} else if (j < b.length && (i === a.length || grid[i]![j+1]! >= grid[i+1]![j]!)) rows.push({kind:'add',text:b[j++]!}); else rows.push({kind:'remove',text:a[i++]!});}
  return rows;
}
export function VersionDiff({before, after}: {before: string; after: string}) {return <><p className="hint">Line comparison · removed lines use −, added lines use +.</p>{before.split('\n').length > 400 || after.split('\n').length > 400 ? <Notice>Comparison is bounded to the first 400 lines of each version. Full stored text remains available in history.</Notice> : null}<pre className="creator-diff" aria-label="Version differences">{lineDiff(before,after).map((d,i) => <span key={i} data-change={d.kind}>{d.kind === 'add' ? '+ ' : d.kind === 'remove' ? '− ' : '  '}{d.text}{'\n'}</span>)}</pre></>;}
export function Versions({path}: {path: string}) {return <Panel title="Immutable version history" description="Versions are body snapshots taken when authored. Lifecycle decisions use the current record revision, which is separate from its body version."><Records path={path + '/versions'} schema={versionSchema} label="versions" status={false}>{rows => <HistoryPage key={rows.map(r => r.version).join(',')} versions={rows}/>}</Records></Panel>;}
function HistoryPage({versions}: {versions: Version[]}) {const [before, setBefore] = useState(Math.min(...versions.map(v => v.version))); const [after, setAfter] = useState(Math.max(...versions.map(v => v.version))); const old = versions.find(v => v.version === before); const current = versions.find(v => v.version === after); return <><div className="table-toolbar"><Select label="Earlier body version" value={before} onChange={e => setBefore(Number(e.target.value))}>{versions.map(v => <option value={v.version} key={v.version}>Version {v.version}</option>)}</Select><Select label="Later body version" value={after} onChange={e => setAfter(Number(e.target.value))}>{versions.map(v => <option value={v.version} key={v.version}>Version {v.version}</option>)}</Select></div>{old && current && <VersionDiff before={old.body} after={current.body}/>}<div className="knowledge-cards">{versions.map(v => <article className="knowledge-card" key={v.version}><h3>Body version {v.version}</h3><p>{v.status} when authored · {exactTime(v.created_at)}</p><Actor actor={v.actor} label="Version author"/><p>{v.change_reason}</p><details><summary>Stored text, version {v.version}</summary><pre>{v.body}</pre></details></article>)}</div></>;}
