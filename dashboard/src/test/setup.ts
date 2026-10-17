import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
afterEach(() => cleanup());
Object.defineProperty(window, 'matchMedia', {writable: true, value: vi.fn().mockImplementation(() => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}))});
