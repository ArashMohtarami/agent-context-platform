import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { candidate, project, researchFixture, time } from './testing';
describe('research review', () => {
  it('requires independent authorship even when the UI has approve permissions', async () => {
    fakeApi(researchFixture('author'), path => {
      if (path.endsWith('/projects/study')) return reply(project);
      if (path.includes('/records/candidates')) return reply({items: [candidate], next_cursor: null});
      if (path.includes('/records/')) return reply({items: [], next_cursor: null});
    }); mount('/o/org-a/research/study?tab=review');
    await userEvent.click(await screen.findByRole('button', {name: 'Review Fictional finding'}));
    expect(screen.getByRole('button', {name: 'Confirm decision'})).toBeDisabled();
    expect(screen.getByText(/independent verified reviewer/)).toBeVisible();
  });
  it('posts exact revision, refuses a stale decision and requires explicit reload before retry', async () => {
    let calls = 0; const api = fakeApi(researchFixture(), (path, init) => {
      if (path.endsWith('/projects/study')) return reply(project);
      if (path.includes('/records/candidates')) return reply({items: [candidate], next_cursor: null});
      if (path.includes('/records/')) return reply({items: [], next_cursor: null});
      if (path.endsWith('/record/candidates/candidate')) return reply({...candidate, revision: 4});
      if (path.endsWith('/candidates/candidate/decision')) {expect(JSON.parse(String(init.body)).expected_revision).toBe(calls ? 4 : 3); return ++calls === 1 ? reply({detail: 'changed'}, 409) : reply({candidate: {...candidate, revision: 5, status: 'approved'}, event: {event_id: 'event', candidate_id: 'candidate', reviewer: 'person', decision: 'approved', decided_at: time, reason: 'Independent review'}});}
    }); mount('/o/org-a/research/study?tab=review');
    const user = userEvent.setup(); await user.click(await screen.findByRole('button', {name: 'Review Fictional finding'}));
    await user.type(screen.getByLabelText('Decision rationale'), 'Independent review'); await user.click(screen.getByRole('button', {name: 'Confirm decision'}));
    expect(await screen.findByText(/Reload the current revision and review again/)).toBeVisible();
    expect(screen.getByRole('button', {name: 'Confirm decision'})).toBeDisabled();
    await user.click(screen.getByRole('button', {name: 'Reload current revision'}));
    expect(await screen.findByText('Status: pending_review · revision 4')).toBeVisible();
    await user.click(screen.getByRole('button', {name: 'Confirm decision'}));
    expect(calls).toBe(2); const request = api.requests.find(r => r.path.endsWith('/decision'))!;
    expect(new Headers(request.init.headers).get('X-CSRF-Token')).toBe('test-csrf');
    expect(new Headers(request.init.headers).has('Authorization')).toBe(false);
  });
});
