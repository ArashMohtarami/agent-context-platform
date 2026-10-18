import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TupleEditor } from './GrantEditor';
import type { Grant } from '../management/contracts';
import { readA, writeB } from '../management/testing';
function Editor() {const [value, setValue] = useState<Grant[]>([]); return <><TupleEditor label="Policy" value={value} onChange={setValue} available={[readA, writeB]}/><output aria-label="Exact policy">{JSON.stringify(value)}</output></>;}
describe('coupled policy authoring', () => {
  it('starts NONE and changing action clears resources instead of forming a Cartesian product', async () => {
    const user = userEvent.setup(); render(<Editor/>);
    expect(screen.getByText('NONE — no grants selected.')).toBeVisible();
    await user.click(screen.getByRole('button', {name: 'Add policy row'}));
    expect(screen.getByLabelText('Policy selection 1')).toHaveValue('none');
    await user.selectOptions(screen.getByLabelText('Policy selection 1'), 'include');
    await user.type(screen.getByLabelText('Policy IDs 1'), 'a, second-id');
    expect(screen.getByLabelText('Exact policy').textContent).toContain('"ids":["a","second-id"]');
    await user.selectOptions(screen.getByLabelText('Policy action 1'), 'context:write|project');
    expect(screen.getByLabelText('Policy selection 1')).toHaveValue('none');
    expect(screen.queryByLabelText('Policy IDs 1')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Exact policy').textContent).toContain('"ids":[]');
  });
  it('ALL in the organization requires an explicit selection and is never an empty list default', async () => {
    const user = userEvent.setup(); render(<Editor/>); await user.click(screen.getByRole('button', {name: 'Add policy row'}));
    expect(screen.getByLabelText('Exact policy').textContent).toContain('"selector":"none"');
    await user.selectOptions(screen.getByLabelText('Policy selection 1'), 'all_in_org');
    expect(screen.getByLabelText('Exact policy').textContent).toContain('"selector":"all_in_org"');
  });
});
