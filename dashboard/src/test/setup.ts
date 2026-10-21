import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
// Async data views may need multiple renders on shared CI hosts. Browser
// latency budgets are measured separately against the real deployed API.
configure({asyncUtilTimeout: 5000});
afterEach(() => cleanup());
Object.defineProperty(window, 'matchMedia', {writable: true, value: vi.fn().mockImplementation(() => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}))});
