import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { EvidenceCard } from './Evidence';
import { claim, evidenceItem, project, researchFixture } from './testing';
describe('research evidence', () => {
  it('shows source class, snapshot, time and locator while keeping untrusted evidence inert', () => {
    const {container} = render(<EvidenceCard item={evidenceItem}/>);
    expect(screen.getByText('Retained evidence').tagName).toBe('STRONG');
    expect(screen.getByText('snapshot')).toBeVisible(); expect(screen.getByText(/line_start/)).toBeVisible();
    expect(screen.getByText(/user_provided/)).toBeVisible(); expect(screen.getByText(/2026/)).toBeVisible();
    expect(container.querySelector('script,img,iframe')).toBeNull(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
  it('separates unassessed confidence from bounded supporting and contradicting evidence', async () => {
    fakeApi(researchFixture(), path => {
      if (path.endsWith('/projects/study')) return reply(project);
      if (path.includes('/records/claims')) return reply({items: [claim], next_cursor: null});
      if (path.endsWith('/claims/claim/evidence')) return reply({research_project_id: 'study', claim_id: 'claim', supporting_evidence: [evidenceItem], contradicting_evidence: [], qualifying_evidence: [], background_evidence: [], coverage: {completeness: 'bounded', citation_limit: 50}});
      if (path.includes('/records/')) return reply({items: [], next_cursor: null});
    });
    mount('/o/org-a/research/study?tab=evidence');
    await userEvent.click(await screen.findByRole('button', {name: 'Inspect evidence'}));
    expect(await screen.findByText(/up to 50 citations/)).toBeVisible();
    expect(screen.getByText(/Assessment: unreviewed · confidence not assessed/)).toBeVisible();
    expect(screen.getByText(/No contradicting citations/)).toBeVisible();
  });
});
