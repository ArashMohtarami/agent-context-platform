import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { creatorFixture } from './testing';
it('submits the displayed default platform and stored evidence keys, never caller-created evidence',async()=>{
  let sent:Record<string,unknown>|null=null;fakeApi(creatorFixture(),(path,init)=>{
    if(path.includes('/records/'))return reply({items:[],next_cursor:null});
    if(path.endsWith('/briefs')){sent=JSON.parse(String(init.body)) as Record<string,unknown>;return reply({detail:'fixture stops before persistence'},422);}
  });mount('/o/org-a/creator/brand?tab=planning');const u=userEvent.setup();await u.click(await screen.findByRole('button',{name:'Save content brief'}));
  for(const [label,value] of [['Content brief ID','brief'],['Saved idea ID','idea'],['Brief objective','Supplied objective'],['Primary message','Supplied message'],['Research project ID (optional)','study'],['Stored claim IDs (comma separated)','claim'],['Stored citation keys (claim ID / citation ID, one per line)','claim / citation']])await u.type(screen.getByLabelText(label!,{exact:true}),value!);
  await u.click(screen.getByRole('button',{name:'Save save content brief'}));expect(await screen.findByText(/draft is preserved/)).toBeVisible();expect(sent).toMatchObject({profile_id:'brand',platform:'linkedin',claim_ids:['claim'],citations:[{claim_id:'claim',citation_id:'citation'}]});expect(sent).not.toHaveProperty('claim_refs');expect(sent).not.toHaveProperty('citation_refs');
});
