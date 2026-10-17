import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';
import { later, managedClient, page, readA } from '../management/testing';

describe('recipient-only ephemeral credential delivery', () => {
  it('never offers another worker key generation', async () => {
    const client = {...managedClient(), sponsor_id: 'another-worker'};
    fakeApi(fixture(), path => path.endsWith('/managed-clients/coding-worker') ? reply({client, credentials: [], effective_tuples: [readA], rate_limit_profile_enforced: false}) : path.includes('/catalog/sessions?') ? reply(page([])) : undefined);
    mount('/o/org-a/clients/coding-worker');
    expect(await screen.findByRole('button', {name: 'Generate my machine key'})).toBeDisabled();
    expect(screen.queryByRole('button', {name: 'Copy machine key'})).not.toBeInTheDocument();
  });
  it.each(['page hide', 'timeout'])('uses explicit clipboard action, no query token cache, and clears on %s', async reason => {
    const timers = vi.spyOn(window, 'setTimeout');
    const user = userEvent.setup(); const data = fixture(); data.grants = [{action: 'credentials:issue:own', resource_kind: 'member', selector: 'include', ids: ['person']}];
    const write = vi.fn().mockResolvedValue(undefined); Object.defineProperty(navigator, 'clipboard', {value: {writeText: write}, configurable: true});
    const token = 'synthetic-only-machine-secret';
    fakeApi(data, (path, init) => {
      if (path.endsWith('/managed-clients/coding-worker')) return reply({client: managedClient(), credentials: [], effective_tuples: [readA], rate_limit_profile_enforced: false});
      if (path.includes('/catalog/sessions?')) return reply(page([]));
      if (path.endsWith('/credentials')) {expect(JSON.parse(String(init.body)).expected_client_revision).toBe(4); return reply({client_id: 'coding-worker', key_id: 'a'.repeat(16), recipient_id: 'person', issuance_id: 'receipt', status: 'active', revision: 1, expires_at: later(), delivered: true, token});}
      return undefined;
    }); const mounted = mount('/o/org-a/clients/coding-worker');
    await user.click(await screen.findByRole('button', {name: 'Generate my machine key'}));
    await user.click(screen.getByRole('button', {name: 'Add credential grant row'}));
    await user.selectOptions(screen.getByLabelText('Credential grant selection 1'), 'include');
    await user.type(screen.getByLabelText('Credential grant IDs 1'), 'a');
    await user.click(screen.getByRole('button', {name: 'Review credential'}));
    await user.click(screen.getByRole('button', {name: 'Confirm one-time generation'}));
    expect(await screen.findByLabelText('One-time machine key')).toHaveTextContent(token);
    expect(write).not.toHaveBeenCalled();
    expect(JSON.stringify(mounted.queries.getQueryCache().getAll().map(q => q.state.data))).not.toContain(token);
    expect(mounted.queries.getMutationCache().getAll()).toHaveLength(0);
    await user.click(screen.getByRole('button', {name: 'Copy machine key'})); expect(write).toHaveBeenCalledExactlyOnceWith(token);
    if (reason === 'page hide') act(() => window.dispatchEvent(new Event('pagehide')));
    else {const expire = timers.mock.calls.find(([, delay]) => delay === 120000)?.[0]; expect(typeof expire).toBe('function'); if (typeof expire === 'function') await act(async () => {expire();});}
    expect(screen.queryByText(token)).not.toBeInTheDocument();
  });
});
