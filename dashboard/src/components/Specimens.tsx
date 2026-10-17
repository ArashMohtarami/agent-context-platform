import { useState } from 'react';
import { Button, Empty, ErrorState, Input, Modal, Notice, Panel, Select, Skeleton, Status, Table } from './ui';
import { PageFrame, ThemeButton } from '../shell/Shell';
import { Link } from 'react-router-dom';

export function Specimens() {
  const [dialog, setDialog] = useState(false); const [drawer, setDrawer] = useState(false);
  return <PageFrame><header className="specimen-header"><Link to="/sign-in">Agent Context</Link><ThemeButton/></header><header className="page-heading"><span className="eyebrow">Design system · Sample content</span><h1>Interface foundations</h1><p>Component specimens for keyboard, contrast, reflow and both themes. No live organization data.</p></header>
    <div className="security-grid"><Panel title="Actions"><div className="actions"><Button>Primary action</Button><Button variant="secondary">Secondary</Button><Button variant="danger">Destructive</Button><Button disabled>Unavailable</Button><Button busy>Submitting</Button></div></Panel>
    <Panel title="Fields"><Input label="Example text field" placeholder="Enter a value" hint="Visible label and persistent guidance."/><Input label="Invalid example field" error="Explain how to correct the value." aria-invalid="true"/><Select label="Example selection"><option>None selected</option><option>Selected sample</option></Select></Panel>
    <Panel title="Notices & status"><Notice>Context and guidance.</Notice><Notice tone="success">The action completed.</Notice><Notice tone="warning">A decision needs attention.</Notice><Notice tone="danger">The request failed safely.</Notice><div className="actions"><Status tone="success">Active</Status><Status tone="warning">Expiring</Status><Status tone="danger">Revoked</Status><Status>Unknown</Status></div></Panel>
    <Panel title="Dialogs & drawers"><p>Escape closes the surface and returns keyboard focus to its trigger.</p><div className="actions"><Button onClick={() => setDialog(true)}>Open example dialog</Button><Button variant="secondary" onClick={() => setDrawer(true)}>Open example drawer</Button></div></Panel>
    <Panel title="Loading"><Skeleton label="Loading example"/></Panel><Panel title="Empty"><Empty title="No sample records">An authorized empty result is different from a failed request.</Empty></Panel>
    <Panel title="Error"><ErrorState onRetry={() => undefined}>Example error state. No private values are echoed.</ErrorState></Panel>
    <Panel title="Table"><Table label="Sample table" headings={['Record', 'State', 'Action']}><tr><td>Example record</td><td><Status tone="success">Active sample</Status></td><td><Button variant="secondary">Inspect sample</Button></td></tr></Table></Panel></div>
    <Modal title="Example confirmation" description="Sample content. Confirm or cancel using the keyboard." open={dialog} onOpenChange={setDialog}><Input label="Example dialog input"/><div className="actions"><Button variant="secondary" onClick={() => setDialog(false)}>Cancel</Button><Button onClick={() => setDialog(false)}>Confirm sample</Button></div></Modal>
    <Modal title="Example drawer" description="Sample secondary content in a focus-managed drawer." open={drawer} onOpenChange={setDrawer} drawer><p>Supporting details stay inside this surface.</p><Button onClick={() => setDrawer(false)}>Close drawer</Button></Modal>
  </PageFrame>;
}
