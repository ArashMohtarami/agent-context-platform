import { z } from 'zod';
import { grantSchema, timestamp } from '../api/contracts';

export type Grant = z.infer<typeof grantSchema>;
export const selectorSchema = z.object({mode: z.enum(['none', 'include', 'all_in_org']), ids: z.array(z.string())});
export const parentSchema = z.object({
  _id: z.string(), generation: z.number(), revision: z.number(), tuples: z.array(grantSchema),
  ceiling: z.array(grantSchema), targets: selectorSchema, allowed_roles: z.array(z.string()),
  remaining_depth: z.number(), expires_at: timestamp, team_id: z.string().nullable(),
});
export type Parent = z.infer<typeof parentSchema>;
export const teamSchema = z.object({team_id: z.string(), name: z.string(), status: z.string(), revision: z.number(), generation: z.number()});
export const contextSchema = z.object({
  policy_revision: z.number(), parents: z.array(parentSchema), teams: z.array(teamSchema), roles: z.array(z.string()),
  request_parents: z.array(z.object({id: z.string(), generation: z.number(), issuer_id: z.string(), team_id: z.string().nullable(), expires_at: timestamp})),
});
export type Authoring = z.infer<typeof contextSchema>;
export const memberSchema = z.object({org_id: z.string(), user_id: z.string(), display_name: z.string(), role: z.string(), status: z.string(), revision: z.number(),
  team_membership_status: z.string().optional(), team_membership_revision: z.number().optional()});
export type Member = z.infer<typeof memberSchema>;
export function pageSchema<T>(item: z.ZodType<T>) {return z.object({items: z.array(item), next_cursor: z.string().nullable(), policy_revision: z.number()});}
export const reviewedSchema = z.object({
  role: z.string(), parent_id: z.string(), expected_parent_generation: z.number(), tuples: z.array(grantSchema), ceiling: z.array(grantSchema),
  targets: selectorSchema, allowed_roles: z.array(z.string()), remaining_depth: z.number(), expires_at: timestamp,
  team_id: z.string().nullable(), expected_team_generation: z.number().nullable(),
});
export type Reviewed = z.infer<typeof reviewedSchema>;
export const invitationSchema = z.object({_id: z.string(), org_id: z.string(), user_id: z.string(), issuer_id: z.string(), policy: reviewedSchema, status: z.string(), revision: z.number(), expires_at: timestamp});
export const requestSchema = z.object({_id: z.string(), user_id: z.string(), policy: reviewedSchema, policy_revision: z.number(), status: z.string(), revision: z.number(), expires_at: timestamp, expired: z.boolean()});
export const previewSchema = z.object({subject_id: z.string(), policy_revision: z.number(), tuples: z.array(grantSchema), ceiling: z.array(grantSchema), expires_at: timestamp, role: z.string(), membership_revision: z.number(), existing_assignments_changed: z.literal(false)});
export const assignmentSchema = parentSchema.omit({team_id: true}).extend({subject_id: z.string(), status: z.string(), team_id: z.string().nullable().optional()});
export const assignmentsSchema = z.object({assignments: z.array(assignmentSchema)});
export const explanationSchema = z.object({assignment_id: z.string(), effective: z.boolean(), reason: z.string().optional(), generation: z.number().optional(), expires_at: timestamp.optional()});
export const accessSchema = z.object({subject_id: z.string(), policy_revision: z.number(), tuples: z.array(grantSchema), assignments: z.array(explanationSchema), role_has_corpus_bypass: z.literal(false)});
export const clientSchema = z.object({org_id: z.string(), client_id: z.string(), subject_kind: z.enum(['human', 'service_account']), subject_id: z.string(), sponsor_id: z.string(), team_id: z.string().nullable(), parent_id: z.string(), parent_generation: z.number(), issuer_id: z.string(), tuples: z.array(grantSchema), scopes: z.array(z.string()), profile_id: z.string().nullable(), profile_revision: z.number().nullable(), revision: z.number(), status: z.string(), expires_at: timestamp});
export type Client = z.infer<typeof clientSchema>;
export const credentialSchema = z.object({client_id: z.string(), key_id: z.string(), recipient_id: z.string(), issuance_id: z.string(), status: z.string(), revision: z.number(), expires_at: timestamp, replaces_key_id: z.string().nullable().optional()});
export const deliverySchema = credentialSchema.extend({delivered: z.boolean(), token: z.string().optional()});
export const inspectionSchema = z.object({client: clientSchema, credentials: z.array(credentialSchema), effective_tuples: z.array(grantSchema), rate_limit_profile_enforced: z.literal(false)});
export const clientPreviewSchema = z.object({client_id: z.string(), effective_tuples: z.array(grantSchema), copied_scopes: z.array(z.string()), profile_revision: z.number().nullable(), rate_limit_profile_enforced: z.literal(false), policy_revision: z.number()});
export const templateSchema = z.object({profile_id: z.string(), name: z.string(), scopes: z.array(z.string()), status: z.enum(['active', 'disabled']), revision: z.number(), rate_limit_profile: z.string().nullable()});
export type Template = z.infer<typeof templateSchema>;
export const impactSchema = z.object({profile_id: z.string(), revision: z.number(), assigned_client_count: z.number(), assigned_clients_changed: z.literal(false), rate_limit_profile_enforced: z.literal(false)});
export const serviceSchema = z.object({service_account_id: z.string(), name: z.string(), custodian_id: z.string(), team_id: z.string(), revision: z.number(), status: z.string(), expires_at: timestamp});
export const machineSessionSchema = z.object({session_id: z.string(), client_id: z.string(), client_type: z.string(), project_id: z.string().nullable(), started_at: timestamp, last_seen_at: timestamp, ended_at: timestamp.nullable(), status: z.string(), revision: z.number()});
export const bindingSchema = z.object({binding_id: z.string(), project_id: z.string(), binding_type: z.string(), value: z.string(), revision: z.number()});
export const transfersSchema = z.object({items: z.array(z.object({_id: z.string(), owner_id: z.string(), target_id: z.string(), policy_revision: z.number(), status: z.string(), expires_at: timestamp})), truncated: z.boolean()});
