import { z } from 'zod';
import { timestamp } from '../api/contracts';
export const querySchema = z.object({source:z.enum(['gateway_activity','governance']),scope:z.enum(['own','team','org']),start:timestamp,end:timestamp,timezone:z.string(),bucket:z.enum(['hour','day']),team_id:z.string().nullable(),project_id:z.string().nullable(),client_id:z.string().nullable(),outcome:z.string().nullable()});
export type Filters = z.infer<typeof querySchema>;
export const coverageSchema = z.object({source:z.string(),state:z.enum(['missing','unavailable','stale','absent_history','partial','complete']),population:z.string(),watermark:timestamp.nullable(),coverage_start:timestamp.nullable(),retained_from:timestamp.nullable(),checked_at:timestamp.nullable(),storage_retention:z.string(),served_history_days:z.number(),report_ttl_seconds:z.number(),collection_contract:z.string(),gaps:z.array(z.object({start:timestamp,end:timestamp}))});
export type Coverage = z.infer<typeof coverageSchema>;
export const measureSchema = z.object({value:z.number().nullable(),sample_count:z.number(),denominator:z.number().nullable(),state:z.string(),source:z.string(),unit:z.string(),definition:z.string()});
export const summarySchema = z.object({org_id:z.string(),policy_revision:z.number(),query:querySchema,generated_at:timestamp,coverage:coverageSchema,observed_count:z.number(),observation_state:z.string(),outcomes:z.record(z.string(),z.number()),buckets:z.array(z.object({start:timestamp,end:timestamp,count:z.number()})),sparse_series:z.literal(true),measures:z.record(z.string(),measureSchema)});
export type Summary = z.infer<typeof summarySchema>;
export const eventSchema = z.object({event_id:z.string(),occurred_at:timestamp,action:z.string(),outcome:z.string(),actor_kind:z.string(),actor_id:z.string().nullable(),sponsor_id:z.string().nullable(),service_account_id:z.string().nullable(),team_id:z.string().nullable(),project_id:z.string().nullable(),attribution:z.enum(['verified_event_time','legacy_unattributed'])});
export type Event = z.infer<typeof eventSchema>;
export const activitySchema = z.object({items:z.array(eventSchema),next_cursor:z.string().nullable(),observed_count:z.number(),coverage:coverageSchema,policy_revision:z.number()});
export const auditSchema = z.object({event:eventSchema,fields:z.array(z.object({field:z.string(),before:z.string().nullable(),after:z.string().nullable()})),state:z.enum(['recorded_fields','unavailable']),redaction:z.string(),coverage:coverageSchema});
export const reportSchema = z.object({report_id:z.string(),download_path:z.string(),expires_at:timestamp,max_rows:z.number()});
export const downloadSchema = z.object({content:z.string(),metadata:z.object({source:z.string(),timezone:z.string(),start:timestamp,end:timestamp,observed_count:z.number(),coverage:coverageSchema})});
export const filterParams = (f:Filters) => new URLSearchParams(Object.entries(f).filter(([,v])=>v!==null).map(([k,v])=>[k,v!])).toString();
export function date(value:string|null,zone='UTC') {return value ? new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'long',timeZone:zone}).format(new Date(value)) : 'Not declared';}
export function initial(scope:Filters['scope'], teams:string[], search:string):Filters {
  const params=new URLSearchParams(search);const now=new Date();const defaults:Filters={source:'gateway_activity',scope,start:new Date(now.getTime()-86400000).toISOString(),end:now.toISOString(),timezone:'UTC',bucket:'day',team_id:scope==='team'?teams[0]??null:null,project_id:null,client_id:null,outcome:null};
  const requested=Object.fromEntries([...params]);const result=querySchema.safeParse({...defaults,...requested,scope});return result.success?result.data:defaults;
}
