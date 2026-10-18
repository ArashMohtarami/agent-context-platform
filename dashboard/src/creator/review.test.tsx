import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { creatorFixture, item, scan } from './testing';
describe('creator revision-bound review',()=>{
  it('denies a human author even with operation and review grants; stored Markdown is inert',async()=>{
    fakeApi(creatorFixture('author'),path=>{if(path.endsWith('/items/draft'))return reply(item);if(path.endsWith('/versions')||path.endsWith('/metrics'))return reply({items:[],next_cursor:null});});
    const {container}=mount('/o/org-a/creator/brand/items/draft');await userEvent.click(await screen.findByRole('button',{name:'Inspect publication review'}));expect(screen.getByRole('button',{name:'Approve revision 3'})).toBeDisabled();expect(screen.getByText(/independent verified reviewer/)).toBeVisible();expect(container.querySelector('.knowledge-markdown script,.knowledge-markdown img,.knowledge-markdown iframe')).toBeNull();expect(screen.queryByRole('link',{name:'unsafe'})).not.toBeInTheDocument();
  });
  it('keeps advisory scan separate and requires reloading a stale approval',async()=>{
    let approved=0;const requests=fakeApi(creatorFixture(),(path,init)=>{
      if(path.endsWith('/items/draft'))return reply({...item,revision:approved?4:3});
      if(path.endsWith('/versions')||path.endsWith('/metrics'))return reply({items:[],next_cursor:null});
      if(path.endsWith('/scan'))return reply(scan);
      if(path.endsWith('/approve')){expect(JSON.parse(String(init.body)).expected_revision).toBe(approved?4:3);return ++approved===1?reply({detail:'changed'},409):reply({content:{...item,status:'approved',revision:5},review:scan});}
    });mount('/o/org-a/creator/brand/items/draft');const u=userEvent.setup();await u.click(await screen.findByRole('button',{name:'Inspect publication review'}));await u.click(screen.getByRole('button',{name:'Run advisory safety scan'}));expect(await screen.findByText(scan.disclaimer)).toBeVisible();expect(approved).toBe(0);
    await u.click(screen.getByRole('button',{name:'Approve revision 3'}));expect(await screen.findByText(/Reload the current record/)).toBeVisible();expect(screen.getByRole('button',{name:'Approve revision 3'})).toBeDisabled();await u.click(screen.getByRole('button',{name:'Reload current content'}));await u.click(await screen.findByRole('button',{name:'Approve revision 4'}));expect(approved).toBe(2);
    const request=requests.requests.find(r=>r.path.endsWith('/approve'))!;expect(new Headers(request.init.headers).has('Authorization')).toBe(false);expect(new Headers(request.init.headers).get('X-CSRF-Token')).toBe('test-csrf');
  });
});
