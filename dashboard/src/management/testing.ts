import type { Authoring, Client, Grant } from './contracts';
export const later = () => new Date(Date.now() + 3600000).toISOString();
export const readA: Grant = {action: 'context:read', resource_kind: 'project', selector: 'include', ids: ['a']};
export const writeB: Grant = {action: 'context:write', resource_kind: 'project', selector: 'include', ids: ['b']};
export function authoring(): Authoring {return {policy_revision: 2, roles: ['contributor', 'team_lead'], teams: [{team_id: 'alpha', name: 'Fictional Alpha team', revision: 1, generation: 1, status: 'active'}], request_parents: [], parents: [{_id: 'root', generation: 3, revision: 2, tuples: [], ceiling: [readA, writeB], targets: {mode: 'include', ids: ['worker']}, allowed_roles: ['contributor', 'team_lead'], remaining_depth: 2, expires_at: later(), team_id: null}]};}
export function managedClient(): Client {return {org_id: 'org-a', client_id: 'coding-worker', subject_kind: 'human', subject_id: 'person', sponsor_id: 'person', team_id: 'alpha', parent_id: 'root', parent_generation: 3, issuer_id: 'lead', tuples: [readA], scopes: ['context:read'], profile_id: null, profile_revision: null, revision: 4, status: 'active', expires_at: later()};}
export const page = <T,>(items: T[]) => ({items, next_cursor: null, policy_revision: 2});
