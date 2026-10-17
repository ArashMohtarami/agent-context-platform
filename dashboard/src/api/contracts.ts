import { z } from 'zod';
export const timestamp = z.iso.datetime({offset: true});

export const grantSchema = z.object({
  action: z.string(), resource_kind: z.string(),
  selector: z.enum(['none', 'include', 'all_in_org']), ids: z.array(z.string()),
});
export type Grant = z.infer<typeof grantSchema>;
export const policySchema = z.object({
  org_id: z.string(), session_id: z.string(), membership_revision: z.number(),
  policy_version: z.number(), context_id: z.string(),
});
export const meSchema = z.object({
  user_id: z.string(), display_name: z.string(), session_id: z.string(),
  membership: z.object({org_id: z.string(), display_name: z.string(), role: z.string(), status: z.string()}),
  auth_method: z.enum(['local', 'oidc']), assurance: z.string(),
  policy_context: policySchema, policy_revision: z.number().optional(),
});
export type Me = z.infer<typeof meSchema>;
export const organizationSchema = z.object({
  org_id: z.string(), name: z.string(), role: z.string(),
  auth_policy: z.enum(['local_only', 'sso_preferred', 'sso_required']),
});
export type Organization = z.infer<typeof organizationSchema>;
export const securitySchema = z.object({
  auth_method: z.enum(['local', 'oidc']), assurance: z.string(),
  mfa_enrolled: z.boolean(), local_password_available: z.boolean(),
  local_enrollment_allowed: z.boolean(), step_up_required: z.boolean(),
  recovery_codes_remaining: z.number(),
  identities: z.array(z.object({kind: z.enum(['local', 'oidc']), issuer: z.string().nullable(), verified_at: timestamp})),
  session: z.object({public_id: z.string(), created_at: timestamp, expires_at: timestamp, idle_expires_at: timestamp}),
});
export type Security = z.infer<typeof securitySchema>;
export const bootstrapSchema = z.object({
  me: meSchema, organizations: z.array(organizationSchema),
  grants: z.array(grantSchema), security: securitySchema,
});
export type Bootstrap = z.infer<typeof bootstrapSchema>;
export const csrfSchema = z.object({csrf_token: z.string()});
export const sessionResultSchema = z.object({
  authenticated: z.literal(true), csrf_token: z.string(),
  policy_context: policySchema.optional(), recovery_codes: z.array(z.string()).optional(),
});
export type SessionResult = z.infer<typeof sessionResultSchema>;
export const loginSchema = z.object({challenge: z.string(), mfa_required: z.boolean()});
export const redirectSchema = z.object({authorization_url: z.url()});
export const logoutSchema = z.object({end_session_url: z.url()});
export const sessionSchema = z.object({
  public_id: z.string(), org_id: z.string(), created_at: timestamp, last_seen: timestamp,
  expires_at: timestamp, idle_expires_at: timestamp, revoked: z.boolean(),
});
export const sessionsSchema = z.object({sessions: z.array(sessionSchema)});
export const effectiveSchema = z.object({
  org_id: z.string(), subject_id: z.string(), policy_revision: z.number(),
  tuples: z.array(grantSchema), role_has_corpus_bypass: z.literal(false),
});
export const enrollmentSchema = z.object({seed: z.string(), provisioning_uri: z.string()});
export const voidSchema = z.unknown().transform(() => undefined);
export const messageSchema = z.object({message: z.string()});
