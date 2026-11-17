import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { usePoll } from './hooks.js';
import { get } from './api.js';

vi.mock('./api.js', () => ({ get: vi.fn(), SignedOut: class extends Error {} }));
beforeEach(() => { get.mockReset(); });

it('ignores a previous resource response after switching logs', async () => {
  const resolvers = {};
  get.mockImplementation(path => new Promise(resolve => { resolvers[path] = resolve; }));
  const { result, rerender } = renderHook(({ path }) => usePoll(path, 0), { initialProps: { path: '/old' } });
  rerender({ path: '/new' });
  await act(async () => resolvers['/new'](['new log']));
  await act(async () => resolvers['/old'](['old log']));
  expect(result.current.data).toEqual(['new log']);
});

it('does not pile up polling requests while one is pending', async () => {
  let finish;
  get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { unmount } = renderHook(() => usePoll('/slow', 5));
  await act(async () => new Promise(resolve => setTimeout(resolve, 30)));
  expect(get).toHaveBeenCalledTimes(1);
  unmount();
  await act(async () => finish([]));
});

it('keeps data and its last successful update time when polling fails', async () => {
  get.mockResolvedValueOnce(['saved']).mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => usePoll('/data', 0));
  await waitFor(() => expect(result.current.data).toEqual(['saved']));
  const updated = result.current.updatedAt;
  await act(async () => result.current.reload());
  expect(result.current.error).toBeTruthy();
  expect(result.current.data).toEqual(['saved']);
  expect(result.current.updatedAt).toBe(updated);
});
