// Synthetic component fixtures, excluded from the production entry graph.
import type { Bootstrap } from '../api/contracts';
import { fixture } from '../test/harness';
export const stamp = '2026-10-01T12:00:00Z';
export const actor = {actor_type: 'human', user_id: 'person', org_id: 'org-a'};
export function knowledgeFixture(): Bootstrap {const data = fixture(); data.grants = [
  {action: 'projects:read', resource_kind: 'project_directory', selector: 'include', ids: ['a', 'b']},
  ...['context:read', 'memory:read'].map(action => ({action, resource_kind: 'project', selector: 'include' as const, ids: ['a']})),
  ...['context:write', 'memory:write'].map(action => ({action, resource_kind: 'project', selector: 'include' as const, ids: ['b']})),
  ...['memory:read', 'memory:write', 'search:read'].map(action => ({action, resource_kind: 'organization_knowledge', selector: 'include' as const, ids: ['org-a']})),
  {action: 'search:read', resource_kind: 'project', selector: 'include', ids: ['a', 'b']},
  {action: 'skills:read', resource_kind: 'skill', selector: 'include', ids: ['canonical']},
]; return data;}
export const memory = {memory_id: 'a'.repeat(24), scope: 'global' as const, project_id: null, client_id: null, type: 'decision', content: 'Fictional intentional memory', summary: 'Fictional decision', importance: 0.5, confidence: 0.8, tags: [], source: 'test', status: 'active' as const, external_id: null, supersedes: null, superseded_by: null, actor, revision: 2, created_at: stamp, updated_at: stamp, promotion_provenance: null};
export const context = {context_id: 'b'.repeat(24), project_id: 'a', type: 'convention', title: 'Fictional context', content: 'Stored context', importance: 0.5, tags: [], source: 'test', status: 'active' as const, external_id: null, actor, revision: 2, created_at: stamp, updated_at: stamp, promotion_provenance: null};
export const project = {project_id: 'a', name: 'Fictional A', description: 'Synthetic project only', repository: 'fictional/a', environment: 'test', tags: [], created_at: stamp, updated_at: stamp};
export const searchResult = {query: 'isolation', project_id: 'a', token_count: 10, token_budget: 2048, results: [{key: 'context:a:permitted', source: 'project_context', title: 'Permitted citation', content: '**Safe bold**\n\n<script>window.xss=1</script>\n\n[bad](javascript:alert%281%29)\n\n![tracking](https://example.test/pixel)\n\n[Good](https://example.test/docs)', tokens: 10, score: 0.5, metadata: {project_id: 'a', source: 'human-workspace', revision: 2}}], retrieval: {query_id: 'synthetic', searched_sources: ['project_context'], skipped_sources: [], retrieval_method: 'hybrid', diagnostics: {vector_matches: 1, lexical_matches: 1}}};
