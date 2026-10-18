import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, Notice, Panel, Select, Table, exactTime } from '../components/ui';
import { TupleEditor, utcInput, utcValue } from '../access/GrantEditor';
import { deliverySchema } from '../management/contracts';
import type { credentialSchema } from '../management/contracts';
import type { Client, Grant } from '../management/contracts';
import type { z } from 'zod';
import { ConfirmAction, GrantTable, id, StepUp, useCommand, useConsole, useDirty } from '../management/shared';

export function SetupSnippets() {
  const [client, setClient] = useState('codex');
  const env = {MCP_API_BASE_URL: 'https://YOUR_PLATFORM_ORIGIN', MCP_API_TOKEN_FILE: '/absolute/private/path/agent-context-token', MCP_PROJECT_IDS: 'REVIEWED_PROJECT_ID'};
  const config = client === 'codex' ? '[mcp_servers.agent_context]\ncommand = "/absolute/platform/scripts/codex-mcp-server.sh"\n[mcp_servers.agent_context.env]\n' + Object.entries(env).map(([key, value]) => key + ' = "' + value + '"').join('\n') : JSON.stringify({mcpServers: {agent_context: {command: '/absolute/platform/scripts/codex-mcp-server.sh', env, ...(client === 'gemini_cli' ? {trust: false, timeout: 120000} : {})}}}, null, 2);
  return <Panel title="Private-file client setup" description="Static placeholders only. Use your issued machine key, never your human browser session. The reviewed grant remains the server-enforced upper bound."><Select label="Supported machine client" value={client} onChange={e => setClient(e.target.value)}><option value="codex">Codex</option><option value="claude_code">Claude Code</option><option value="gemini_cli">Gemini CLI</option></Select><p>Save the copied key outside the repository with directory mode 0700 and file mode 0600. A password manager or mounted secret is appropriate. Keep tokens out of shell history, command arguments and committed files.</p><pre><code>{'umask 077\nmkdir -p /absolute/private/path\nchmod 700 /absolute/private/path\nread -rsp "Paste machine key: " ACP_MACHINE_INPUT\nprintf "%s" "$ACP_MACHINE_INPUT" > /absolute/private/path/agent-context-token\nunset ACP_MACHINE_INPUT\nchmod 600 /absolute/private/path/agent-context-token'}</code></pre><p>Local stdio configuration: {client === 'codex' ? '.codex/config.toml' : client === 'claude_code' ? '.mcp.json' : '.gemini/settings.json'}. Replace every placeholder, including the absolute launcher path. The local launcher reads the private token file.</p><pre><code>{config}</code></pre><p className="hint">For remote HTTP and client trust/confirmation settings, use the repository client guide. Template rate-limit labels are descriptive; they are not enforced limits.</p></Panel>;
}
export function Credentials({client, effective, credentials}: {client: Client; effective: Grant[]; credentials: z.infer<typeof credentialSchema>[]}) {
  const {auth, data, base, allowed} = useConsole(); const command = useCommand(); const [open, setOpen] = useState(false); const [tuples, setTuples] = useState<Grant[]>([]); const [expiry, setExpiry] = useState(utcInput(12)); const [replaces, setReplaces] = useState('');
  const [issuanceId, setIssuanceId] = useState(''); const [reviewed, setReviewed] = useState(false); const [secret, setSecret] = useState(''); const [receipt, setReceipt] = useState(''); const [copied, setCopied] = useState(false); const [copyError, setCopyError] = useState('');
  const refresh = auth.refresh; const active = useRef(true); useDirty(open || Boolean(secret));
  const recipient = client.sponsor_id === data.me.user_id;
  const issue = effective.some(g => g.selector !== 'none' && g.action !== 'reviews:decide') && recipient && allowed('credentials:issue:own', 'member', data.me.user_id) && client.status === 'active' && Date.parse(client.expires_at) > Date.now();
  useEffect(() => {active.current = true; return () => {active.current = false;};}, []);
  useEffect(() => {
    const clear = () => {setSecret(''); setOpen(false); setTuples([]); setReviewed(false);};
    const hidden = () => {if (document.visibilityState === 'hidden') clear();};
    window.addEventListener('pagehide', clear); document.addEventListener('visibilitychange', hidden);
    const timer = secret ? window.setTimeout(() => {clear(); void refresh();}, 120000) : undefined;
    return () => {window.removeEventListener('pagehide', clear); document.removeEventListener('visibilitychange', hidden); window.clearTimeout(timer);};
  }, [secret, refresh]);
  async function generate() {
    await command.run(async () => {
      const result = await auth.act(base + '/managed-clients/' + id(client.client_id) + '/credentials', deliverySchema, {issuance_id: issuanceId, expected_client_revision: client.revision, tuples, expires_at: utcValue(expiry), ...(replaces ? {replaces_key_id: replaces} : {})});
      if (!active.current) return;
      setReceipt(result.key_id); setSecret(result.delivered ? result.token ?? '' : ''); setOpen(false); setCopied(false); setCopyError('');
      if (!result.delivered) await auth.refresh();
    }, false);
  }
  async function dismiss() {setSecret(''); setTuples([]); setReviewed(false); setReceipt(''); await auth.refresh();}
  return <><Panel title="Machine credential metadata" description="Policy approval is separate from recipient issuance. Tokens are never recoverable from metadata, retries, audit or outbox."><StepUp/>{!recipient && <Notice>Only the verified recipient or service custodian may generate and receive this client’s key. Managers can inspect or revoke permitted metadata.</Notice>}<Button disabled={!issue || data.security.step_up_required} onClick={() => {setTuples([]); setReviewed(false); setReplaces(''); setIssuanceId(crypto.randomUUID()); setOpen(true);}}>Generate my machine key</Button>
    {receipt && !secret && <Notice>Issuance receipt {receipt}. The secret is unavailable after delivery. Revoke and replace if delivery was lost.</Notice>}
    {credentials.length ? <Table label="Machine keys, safe metadata only" headings={['Key ID', 'Recipient', 'Expiry', 'Status', 'Lifecycle']}>{credentials.map(c => <tr key={c.key_id}><td><code>{c.key_id}</code><small>Revision {c.revision}</small></td><td>{c.recipient_id}</td><td>{exactTime(c.expires_at)}</td><td><span>{c.status}</span></td><td><div className="actions"><Button variant="secondary" disabled={!issue || c.status !== 'active' || data.security.step_up_required} onClick={() => {setReplaces(c.key_id); setTuples([]); setReviewed(false); setIssuanceId(crypto.randomUUID()); setOpen(true);}}>Rotate my key</Button><ConfirmAction label="Revoke key" description="Revoke future REST/MCP/job authorization for this key. Already committed work remains." path={base + '/managed-clients/' + id(client.client_id) + '/credentials/' + c.key_id + '/revoke'} body={{expected_revision: c.revision}} disabled={c.status !== 'active' || !(allowed('credentials:revoke:org') || allowed('credentials:revoke:team', 'team', client.team_id ?? '') || recipient && allowed('credentials:revoke:own'))} danger/></div></td></tr>)}</Table> : <p className="hint">No credential receipts for this client.</p>}
  </Panel>
  <Modal title={replaces ? 'Rotate recipient key' : 'Generate recipient key'} description="Choose an exact subset of the current effective client policy. Secret delivery happens once; retries return metadata only." open={open} onOpenChange={value => {if (!command.busy) {setOpen(value); setTuples([]); setReviewed(false);}}}>
    <Input label="Key expiry (UTC)" type="datetime-local" required value={expiry} onChange={e => {setExpiry(e.target.value); setReviewed(false);}}/><TupleEditor label="Credential grant" value={tuples} available={effective} onChange={value => {setTuples(value); setReviewed(false);}}/>
    {replaces && <Notice tone="warning">Key {replaces} will be revoked atomically when its replacement is issued.</Notice>}{command.error && <Notice tone="danger">{command.error}</Notice>}{reviewed ? <><GrantTable grants={tuples}/><p>Expires {utcValue(expiry)} · client revision {client.revision}</p><Button busy={command.busy} onClick={() => {void generate();}}>Confirm one-time generation</Button></> : <Button disabled={!tuples.some(g => g.selector !== 'none') || !expiry} onClick={() => setReviewed(true)}>Review credential</Button>}
  </Modal>
  <Modal title="Your one-time machine key" description="Visible for up to two minutes. Copy explicitly and save privately. Leaving, hiding or switching this view clears it. A lost key must be revoked and replaced." open={Boolean(secret)} onOpenChange={value => {if (!value) void dismiss();}}><p>Receipt: <code>{receipt}</code></p><code className="one-time-secret" aria-label="One-time machine key">{secret}</code><div className="actions"><Button onClick={() => {void navigator.clipboard.writeText(secret).then(() => {setCopied(true); setCopyError('');}).catch(() => setCopyError('Clipboard access was refused. Select the visible key and copy it using your browser, then clear this view.'));}}>Copy machine key</Button><Button variant="secondary" onClick={() => {void dismiss();}}>I saved it — clear key</Button></div>{copyError && <Notice tone="warning">{copyError}</Notice>}{copied && <Notice tone="success">Copied by your explicit action. Clear your clipboard after private setup.</Notice>}</Modal>
  <SetupSnippets/></>;
}
