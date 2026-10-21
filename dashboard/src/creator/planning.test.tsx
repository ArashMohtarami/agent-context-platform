import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { creatorFixture, time } from './testing';

it('offers the real content API methods and supports lexical planning without embeddings', async () => {
  const methods: string[] = [];
  fakeApi(creatorFixture(), (path, init) => {
    if (path.endsWith('/profiles/brand')) return reply({profile_id: 'brand', name: 'Fictional creator', status: 'active', voice: {tone: [], avoid: []}, audiences: [], domains: [], writing_preferences: [], formatting_preferences: [], terminology_preferences: [], language_preferences: [], tone_preferences: [], topics_of_interest: [], topics_to_avoid: [], content_objectives: [], revision: 1, created_at: time, updated_at: time});
    if (path.includes('/records/')) return reply({items: [], next_cursor: null});
    if (path.endsWith('/novelty')) {
      const method = (JSON.parse(String(init.body)) as {method: string}).method;
      methods.push(method);
      return method === 'hybrid' ? reply({detail: 'content retrieval is temporarily unavailable'}, 503) : reply({classification: 'new-topic', advisory: true, topic_overlap: 0, angle_overlap: 0, similar_content: []});
    }
  });
  mount('/o/org-a/creator/brand?tab=planning');
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', {name: 'Check novelty'}));
  expect(screen.getByLabelText('Search method')).toHaveValue('lexical');
  expect(screen.queryByRole('option', {name: 'semantic'})).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Proposed topic'), 'Bounded retries');
  await user.type(screen.getByLabelText('Proposed angle'), 'Public guidance');
  await user.selectOptions(screen.getByLabelText('Search method'), 'hybrid');
  await user.click(screen.getByRole('button', {name: 'Run check novelty'}));
  expect(await screen.findByText(/requested service is temporarily unavailable/)).toBeVisible();
  await user.selectOptions(screen.getByLabelText('Search method'), 'vector');
  await user.click(screen.getByRole('button', {name: 'Run check novelty'}));
  expect(await screen.findByText(/advisory history comparison/)).toBeVisible();
  expect(methods).toEqual(['hybrid', 'vector']);
});

it('submits the displayed default platform and stored evidence keys, never caller-created evidence',async()=>{
  let sent:Record<string,unknown>|null=null;fakeApi(creatorFixture(),(path,init)=>{
    if(path.includes('/records/'))return reply({items:[],next_cursor:null});
    if(path.endsWith('/briefs')){sent=JSON.parse(String(init.body)) as Record<string,unknown>;return reply({detail:'fixture stops before persistence'},422);}
  });mount('/o/org-a/creator/brand?tab=planning');const u=userEvent.setup();await u.click(await screen.findByRole('button',{name:'Save content brief'}));
  for(const [label,value] of [['Content brief ID','brief'],['Saved idea ID','idea'],['Brief objective','Supplied objective'],['Primary message','Supplied message'],['Research project ID (optional)','study'],['Stored claim IDs (comma separated)','claim'],['Stored citation keys (claim ID / citation ID, one per line)','claim / citation']])await u.type(screen.getByLabelText(label!,{exact:true}),value!);
  await u.click(screen.getByRole('button',{name:'Save save content brief'}));expect(await screen.findByText(/draft is preserved/)).toBeVisible();expect(sent).toMatchObject({profile_id:'brand',platform:'linkedin',claim_ids:['claim'],citations:[{claim_id:'claim',citation_id:'citation'}]});expect(sent).not.toHaveProperty('claim_refs');expect(sent).not.toHaveProperty('citation_refs');
});
