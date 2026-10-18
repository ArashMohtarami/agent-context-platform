import type { z } from 'zod';
import type { Bootstrap, SessionResult } from './contracts';
import { csrfSchema } from './contracts';

export class StaleContext extends Error {
  constructor() { super('The organization context changed.'); }
}
export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) { super('The request was not accepted.'); }
  get csrf() { return this.status === 403 && this.detail === 'browser request was not accepted'; }
}
interface Options {
  body?: unknown;
  method?: 'POST' | 'PATCH' | 'PUT';
  signal?: AbortSignal | undefined;
  bootstrap?: boolean;
  csv?: boolean;
}

/** Only same-origin session APIs. Credentials and CSRF live in memory. */
export class SessionApi {
  generation = 0;
  private csrf = '';
  private contextId = '';
  private controllers = new Set<AbortController>();

  clear() {
    this.generation++;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }
  forget() { this.clear(); this.csrf = ''; this.contextId = ''; }
  context(data: Bootstrap) { this.contextId = data.me.policy_context.context_id; }
  issued(data: SessionResult) {
    this.csrf = data.csrf_token;
    this.contextId = data.policy_context?.context_id ?? '';
  }
  async initialize() {
    const result = await this.request('/auth/csrf', csrfSchema, {bootstrap: true});
    this.csrf = result.csrf_token;
  }
  download<T>(path: string, schema: z.ZodType<T>) {
    return this.request(path, schema, {csv: true});
  }
  async request<T>(path: string, schema: z.ZodType<T>, options: Options = {}): Promise<T> {
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('://')) throw new Error('Invalid API path');
    const epoch = this.generation;
    const controller = new AbortController();
    this.controllers.add(controller);
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, {once: true});
    if (options.signal?.aborted) controller.abort();
    try {
      const headers: Record<string, string> = {Accept: 'application/json'};
      if (!options.bootstrap && this.contextId) headers['X-Session-Context'] = this.contextId;
      const mutation = options.body !== undefined;
      if (mutation) { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = this.csrf; }
      const response = await fetch('/api/v6' + path, {
        method: mutation ? options.method ?? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers, signal: controller.signal,
        ...(mutation ? {body: JSON.stringify(options.body)} : {}),
      });
      const value: unknown = response.status === 204 ? undefined : options.csv && response.ok ? await csvResponse(response) : await response.json();
      if (epoch !== this.generation) throw new StaleContext();
      if (!response.ok) {
        const detail = typeof value === 'object' && value !== null && 'detail' in value && typeof value.detail === 'string' ? value.detail : '';
        throw new ApiError(response.status, detail);
      }
      return schema.parse(value);
    } catch (error) {
      if (epoch !== this.generation) throw new StaleContext();
      throw error;
    } finally {
      options.signal?.removeEventListener('abort', abort);
      this.controllers.delete(controller);
    }
  }
}

export function namespace(data: Bootstrap, epoch: number) {
  const context = data.me.policy_context;
  return ['organization', context.org_id, context.policy_version, data.me.policy_revision ?? 0,
    context.membership_revision, context.context_id, epoch] as const;
}
export function providerNavigate(url: string) {
  const target = new URL(url);
  if (target.protocol !== 'https:' || target.username || target.password) throw new Error('Provider redirect was not accepted.');
  window.location.assign(target.href);
}


async function csvResponse(response: Response): Promise<unknown> {
  if (!response.headers.get('Content-Type')?.startsWith('text/csv') || response.headers.get('Cache-Control') !== 'no-store') throw new Error('Report response was not accepted.');
  const reader = response.body?.getReader(); if (!reader) throw new Error('Report body unavailable.');
  const decoder = new TextDecoder(); let content = ''; let size = 0;
  try {for (;;) {const part = await reader.read(); if (part.done) break; size += part.value.byteLength; if (size > 2 * 1024 * 1024) {await reader.cancel(); throw new Error('Report exceeds capacity.');} content += decoder.decode(part.value, {stream: true});} content += decoder.decode();}
  finally {reader.releaseLock();}
  const metadata: unknown = JSON.parse(response.headers.get('X-Summary-Metadata') ?? 'null');
  return {content, metadata};
}
