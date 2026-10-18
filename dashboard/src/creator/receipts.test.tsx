import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { creatorFixture, item, time } from './testing';
describe('manual creator receipts',()=>{
  it('does not equate export/read authority with publication permission',async()=>{
    fakeApi(creatorFixture('person',false),path=>{if(path.endsWith('/items/draft'))return reply({...item,status:'approved'});if(path.endsWith('/versions')||path.endsWith('/metrics'))return reply({items:[],next_cursor:null});});mount('/o/org-a/creator/brand/items/draft');expect(await screen.findByRole('button',{name:'Export approved revision'})).toBeEnabled();expect(screen.queryByRole('button',{name:'Record manual receipt'})).not.toBeInTheDocument();
  });
  it('requires explicit manual confirmation and a current revision without external provider options',async()=>{
    let calls=0;fakeApi(creatorFixture(),(path,init)=>{if(path.endsWith('/items/draft'))return reply({...item,status:'approved'});if(path.endsWith('/versions')||path.endsWith('/metrics'))return reply({items:[],next_cursor:null});if(path.endsWith('/receipt')){expect(JSON.parse(String(init.body))).toEqual({profile_id:'brand',expected_revision:3,provider:'manual',confirmed_manual_publication:true});calls++;return reply({content:{...item,status:'published',revision:4},provider:'manual',external_id:null,external_url:null,published_at:time,idempotency_key:'manual-key',already_published:false});}});mount('/o/org-a/creator/brand/items/draft');const u=userEvent.setup();await u.click(await screen.findByRole('button',{name:'Record manual receipt'}));expect(screen.getByRole('button',{name:'Confirm manual receipt'})).toBeDisabled();await u.click(screen.getByRole('checkbox'));await u.click(screen.getByRole('button',{name:'Confirm manual receipt'}));expect(calls).toBe(1);expect(localStorage.length).toBe(0);expect(sessionStorage.length).toBe(0);
  });
});
