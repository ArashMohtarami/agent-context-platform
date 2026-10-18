import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { lineDiff, VersionDiff } from './Versions';
import { creatorFixture, item } from './testing';
describe('creator immutable versions', () => {
  it('compares supplied lines without executing stored HTML or URLs', () => {
    const {container}=render(<VersionDiff before={'old\nsame'} after={'new\nsame\n<script>alert(1)</script>'}/>);
    expect(lineDiff('old\nsame','new\nsame').filter(d=>d.kind!=='same').map(d=>d.text)).toEqual(['new','old']);
    expect(container.querySelector('script')).toBeNull();expect(screen.getByText(/<script>/)).toBeVisible();
    expect(container.querySelector('[data-change="remove"]')).toHaveTextContent('− old');
  });
  it('preserves unsaved prose on conflict and uses only an explicitly compared new revision', async () => {
    let calls=0;fakeApi(creatorFixture(),(path,init)=>{
      if(path.endsWith('/items/draft/versions') && init.method==='POST') {const b=JSON.parse(String(init.body));expect(b.body).toContain('MY UNSAVED PROSE');expect(b.expected_revision).toBe(calls?4:3);expect(b.status).toBeUndefined();return ++calls===1?reply({detail:'changed'},409):reply({...item,body:b.body,revision:5,status:'drafting',current_version:3});}
      if(path.endsWith('/items/draft'))return reply({...item,revision:calls?4:3});
      if(path.endsWith('/versions')||path.endsWith('/metrics'))return reply({items:[],next_cursor:null});
    });mount('/o/org-a/creator/brand/items/draft');const u=userEvent.setup();await u.click(await screen.findByRole('button',{name:'Revise body'}));
    await u.clear(screen.getByLabelText('Draft Markdown'));await u.type(screen.getByLabelText('Draft Markdown'),'MY UNSAVED PROSE');await u.type(screen.getByLabelText('Revision reason'),'Clarify');await u.click(screen.getByRole('button',{name:'Save new version'}));
    expect(await screen.findByText(/Your unsaved draft is preserved/)).toBeVisible();expect(screen.getByLabelText('Draft Markdown')).toHaveValue('MY UNSAVED PROSE');expect(screen.getByRole('button',{name:'Save new version'})).toBeDisabled();
    await u.click(screen.getByRole('button',{name:'Load current revision'}));await u.click(await screen.findByRole('button',{name:'Use current revision with my draft'}));await u.click(screen.getByRole('button',{name:'Save new version'}));expect(calls).toBe(2);
  });
});
