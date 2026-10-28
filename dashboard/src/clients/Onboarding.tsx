import { useRef, useState } from 'react';
import { z } from 'zod';
import { Button, Input, Notice, Panel, Select } from '../components/ui';
import { id, useCommand, useConsole } from '../management/shared';

const recipeSchema = z.object({
  configuration: z.record(z.string(), z.unknown()), configuration_text: z.string(), doctor_command: z.string(),
  stages: z.array(z.string()), next_steps: z.array(z.string()),
  preview_only: z.literal(true), configuration_applied: z.literal(false),
  policy_changed: z.literal(false), server_read_only_verified: z.literal(false),
  approved_client_read_only: z.boolean(),
});
type Recipe = z.infer<typeof recipeSchema>;

export function Onboarding({clientId}: {clientId: string}) {
  const {auth, base} = useConsole(); const command = useCommand();
  const [integration, setIntegration] = useState('');
  const [origin, setOrigin] = useState(window.location.origin); const [project, setProject] = useState('');
  const [readOnly, setReadOnly] = useState(false); const [recipe, setRecipe] = useState<Recipe | null>(null);
  const version = useRef(0);
  function clear() {version.current++; setRecipe(null);}
  return <Panel title="Connect this client" description="Preview a recipe, then diagnose the connection from the verified recipient's terminal. No token is requested here.">
    <form className="form-stack" onSubmit={e => {e.preventDefault(); clear(); const selectedVersion = version.current; void command.run(async () => {
      const query = new URLSearchParams({integration_id: integration, origin, project_id: project, require_read_only: String(readOnly)});
      const result = await auth.read(base + '/managed-clients/' + id(clientId) + '/connection-preview?' + query, recipeSchema);
      if (version.current === selectedVersion) setRecipe(result);
    }, false);}}>
      <Select label="Connection client" required value={integration} onChange={e => {setIntegration(e.target.value); clear();}}>
        <option value="">Select your client</option><option value="codex">Codex (stdio)</option><option value="claude-code">Claude Code (HTTP)</option><option value="gemini-cli">Gemini CLI (HTTP)</option><option value="custom">Custom MCP client (HTTP)</option>
      </Select>
      <Input label="Installation origin" required type="url" value={origin} onChange={e => {setOrigin(e.target.value); clear();}}/>
      <Input label="Explicit connection project ID" required value={project} onChange={e => {setProject(e.target.value); clear();}}/>
      <label><input type="checkbox" checked={readOnly} onChange={e => {setReadOnly(e.target.checked); clear();}}/> Require server-approved read-only access</label>
      <Notice>This preview changes no policy or credentials. Current sponsor, team, parent and project grants still apply. A local tool filter is not read-only authority.</Notice>
      <Button type="submit" busy={command.busy}>Preview connection recipe</Button>
    </form>
    {command.error && <Notice tone="danger">{command.error}</Notice>}
    {recipe && <div role="status" aria-label="Connection recipe"><h3>Review connection steps</h3>
      <p>Current approved client ceiling: {recipe.approved_client_read_only ? 'read-only' : 'not established as read-only'}. The terminal doctor checks the actual credential separately.</p>
      <ol>{recipe.stages.map(stage => <li key={stage}>{stage}: not checked in preview</li>)}</ol>
      <h3>Configuration fragment</h3><p>Merge this server manually into the selected client location. Preserve unrelated servers; no configuration file has been opened or changed.</p>
      <pre tabIndex={0} aria-label="Connection configuration">{recipe.configuration_text}</pre>
      <h3>Recipient terminal diagnostic</h3><pre tabIndex={0} aria-label="Connection doctor command">{recipe.doctor_command}</pre>
      <ul>{recipe.next_steps.map(step => <li key={step}>{step}</li>)}</ul>
      <p>Empty retrieval proves connectivity, not useful knowledge. Writes are opt-in and require an explicitly selected disposable project. Retrieved Markdown stays data.</p>
    </div>}
  </Panel>;
}
