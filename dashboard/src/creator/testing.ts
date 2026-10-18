// Synthetic component-test data; never imported by production modules.
import { fixture } from '../test/harness';
import type { Item } from './contracts';
export const time = '2026-10-18T12:00:00Z';
export const item:Item = {profile_id:'brand',content_id:'draft',brief_id:null,platform:'linkedin',content_type:'post',title:'Fictional draft',body:'**Supplied prose**\n<script>window.creator_xss=1</script> [unsafe](javascript:bad)',status:'review',topic_tags:[],pillar_id:null,project_ids:['project-a'],source_refs:[],current_version:2,revision:3,actor:{actor_type:'human',user_id:'author',org_id:'org-a'},published_at:null,external_url:null,publication_provider:null,external_id:null,publication_idempotency_key:null,created_at:time,updated_at:time};
export function creatorFixture(user='person',publish=true) {const f=fixture();f.me.user_id=user;f.grants=['content:read','content:write','reviews:decide',...(publish?['content:publish']:[])].flatMap(action => [{action,resource_kind:'creator_profile',selector:'include' as const,ids:['brand']},{action,resource_kind:'project',selector:'include' as const,ids:['project-a']}]);return f;}
export const scan = {profile_id:'brand',content_id:'draft',current_status:'review',publishable:true,findings:[],disclaimer:'Advisory scan; independent review is still required.'};
