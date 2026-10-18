import { z } from 'zod';
import { timestamp } from '../api/contracts';

export const page = <T,>(item: z.ZodType<T>) => z.object({items: z.array(item), next_cursor: z.string().nullable()});
export const projectSchema = z.object({project_id: z.string(), name: z.string(), description: z.string(), repository: z.string(), environment: z.string(), tags: z.array(z.string()), created_at: timestamp, updated_at: timestamp});
const authored = {content: z.string(), source: z.string(), status: z.enum(['active', 'archived', 'superseded']), revision: z.number().int(), actor: z.record(z.string(), z.unknown()), promotion_provenance: z.record(z.string(), z.unknown()).nullable(), created_at: timestamp, updated_at: timestamp};
export const contextSchema = z.object({...authored, context_id: z.string(), project_id: z.string(), title: z.string(), type: z.string(), importance: z.number(), tags: z.array(z.string()), external_id: z.string().nullable()});
export const memorySchema = z.object({...authored, memory_id: z.string(), project_id: z.string().nullable(), client_id: z.string().nullable(), scope: z.enum(['global', 'project', 'agent', 'project_agent']), summary: z.string().nullable(), type: z.string(), importance: z.number(), confidence: z.number(), tags: z.array(z.string()), external_id: z.string().nullable(), supersedes: z.string().nullable(), superseded_by: z.string().nullable()});
export const memoriesSchema = page(memorySchema).extend({scope: z.string(), project_id: z.string().nullable(), client_id: z.string().nullable()});
export const contextsSchema = page(contextSchema).extend({project_id: z.string()});
export const skillSchema = z.object({skill_id: z.string(), name: z.string(), version: z.string(), description: z.string(), category: z.string(), trust_level: z.string(), status: z.string(), is_active: z.boolean(), source_path: z.string(), content_hash: z.string(), tags: z.array(z.string()), created_at: timestamp, updated_at: timestamp});
export const skillDetailSchema = z.object({skill: skillSchema, versions: z.array(z.object({version: z.string(), status: z.string(), is_active: z.boolean(), content_hash: z.string(), created_at: timestamp, updated_at: timestamp})), chunks: z.array(z.object({chunk_id: z.string(), section: z.string().nullable(), content: z.string(), chunk_index: z.number(), tokens: z.number(), metadata: z.record(z.string(), z.unknown())})), next_chunk_cursor: z.string().nullable()});
export const searchSchema = z.object({query: z.string(), project_id: z.string().nullable(), token_count: z.number(), token_budget: z.number(), results: z.array(z.object({key: z.string(), source: z.string(), title: z.string(), content: z.string(), tokens: z.number(), score: z.number(), metadata: z.record(z.string(), z.unknown())})), retrieval: z.object({query_id: z.string(), searched_sources: z.array(z.string()), skipped_sources: z.array(z.string()), retrieval_method: z.string(), diagnostics: z.record(z.string(), z.unknown())})});
export type Context = z.infer<typeof contextSchema>;
export type Memory = z.infer<typeof memorySchema>;
export type SearchResult = z.infer<typeof searchSchema>;
